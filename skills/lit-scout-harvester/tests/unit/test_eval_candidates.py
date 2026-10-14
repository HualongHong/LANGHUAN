"""Tests for scripts.eval_candidates (K-scan metrics, 3C)."""

from __future__ import annotations

import json

import pytest

from scripts.eval_candidates import (
    evaluate_k,
    labels_of,
    load_candidates_cache,
    load_gold,
    make_deterministic_chooser,
    record_key,
    scan_k,
    write_candidates_cache,
)
from lit_scout_harvester.modules.prescreen import CandidateProposer


OPTION_SET = {
    "artifact": "option_set",
    "field": "sampleType",
    "options": [
        {"label": "lake sediments", "evidence_count": 40},
        {"label": "stalagmites", "evidence_count": 30},
        {"label": "ice cores", "evidence_count": 20},
        {"label": "tree rings", "evidence_count": 10},
    ],
}

LEXICON = {
    "lake sediments": ["lake sediment"],
    "ice cores": ["ice core"],
}

GOLD = [
    {"record_id": "g1", "TI": "an ice core record", "sampleType": "ice cores"},
    {"record_id": "g2", "TI": "a lake sediment study", "sampleType": "lake sediments"},
    {"record_id": "g3", "TI": "a mystery record", "sampleType": "stalagmites"},
    {"record_id": "g4", "TI": "annual rings study", "sampleType": "tree rings"},
    {"record_id": "g5", "TI": "no gold here"},  # excluded from denominators
]


def first_candidate_chooser(record, cand_labels):
    return cand_labels[0] if cand_labels else None


def make_proposer():
    return CandidateProposer(OPTION_SET, lexicon=LEXICON, k=3)


def test_labels_of_parses_list_and_string():
    assert labels_of(["a", " b "]) == ["a", "b"]
    assert labels_of("a; b") == ["a", "b"]
    assert labels_of("") == []
    assert labels_of(None) == []


def test_hand_computed_example_k3():
    proposer = make_proposer()
    row = evaluate_k(
        GOLD,
        "sampleType",
        lambda rec: proposer.propose(rec, k=3),
        choose_fn=first_candidate_chooser,
        k=3,
    )
    # r1/r2 matched; r3 filled within top-3 (stalagmites ∈ fill); r4 (tree
    # rings, frequency rank 4) missed. The chooser picks cands[0]: right for
    # r1/r2, wrong for r3 (fill head is lake sediments).
    assert row["judged"] == 4  # g5 has no gold
    assert row["label_recall"] == 0.75
    assert row["record_coverage"] == 0.75
    assert row["selectable"] == 3
    assert row["selection_accuracy"] == pytest.approx(2 / 3)
    assert row["end_to_end"] == 0.5
    # 端到端 = 候选召回 × 候选内选择准确率（单标签严格成立）
    assert row["end_to_end"] == pytest.approx(row["record_coverage"] * row["selection_accuracy"])


def test_hand_computed_example_full_universe():
    proposer = make_proposer()
    row = evaluate_k(
        GOLD,
        "sampleType",
        lambda rec: proposer.propose(rec, k=None),
        choose_fn=first_candidate_chooser,
        k=None,
    )
    # Full universe: recall is perfect, but the naive first-candidate chooser
    # only picks right where a deterministic match heads the list (g1, g2).
    assert row["label_recall"] == 1.0
    assert row["record_coverage"] == 1.0
    assert row["selection_accuracy"] == 0.5
    assert row["end_to_end"] == 0.5


def test_no_chooser_reports_none_accuracy():
    proposer = make_proposer()
    row = evaluate_k(GOLD, "sampleType", lambda rec: proposer.propose(rec, k=3))
    assert row["selection_accuracy"] is None
    assert row["end_to_end"] == 0.0  # nothing was chosen


def test_k_scan_four_tiers():
    proposer = make_proposer()
    rows = scan_k(
        GOLD,
        "sampleType",
        proposer,
        choose_fn=first_candidate_chooser,
        k_values=(3, 5, 8, None),
    )
    assert [r["k"] for r in rows] == [3, 5, 8, "full"]
    assert rows[0]["label_recall"] == 0.75
    assert rows[0]["end_to_end"] == 0.5
    for row in rows[1:]:
        # universe has 4 labels: K=5/8/full all propose everything → recall 1;
        # the naive chooser still misses g3/g4 (fill head ≠ gold).
        assert row["label_recall"] == 1.0
        assert row["record_coverage"] == 1.0
        assert row["selection_accuracy"] == 0.5
        assert row["end_to_end"] == 0.5


def test_scan_is_deterministic():
    rows1 = scan_k(GOLD, "sampleType", make_proposer(), first_candidate_chooser)
    rows2 = scan_k(GOLD, "sampleType", make_proposer(), first_candidate_chooser)
    assert rows1 == rows2


def test_deterministic_chooser_uses_frequencies():
    freqs = {"lake sediments": 40, "ice cores": 20}
    chooser = make_deterministic_chooser(freqs)
    assert chooser({}, ["ice cores", "lake sediments"]) == "lake sediments"
    # ties keep candidate order
    assert chooser({}, ["ice cores", "stalagmites"]) == "ice cores"


def test_wrong_pick_lowers_accuracy_and_end_to_end():
    proposer = make_proposer()
    # A chooser that always picks a wrong candidate inside the candidates.
    row = evaluate_k(
        GOLD,
        "sampleType",
        lambda rec: proposer.propose(rec, k=3),
        choose_fn=lambda rec, cands: "stalagmites",
        k=3,
    )
    # reachable: g1(ice cores), g2(lake sediments), g3(stalagmites); only g3 right
    assert row["selectable"] == 3
    assert row["selection_accuracy"] == pytest.approx(1 / 3)
    assert row["end_to_end"] == pytest.approx(1 / 4)


def test_candidates_cache_roundtrip(tmp_path):
    cache = {record_key(rec, i): {"candidates": [{"label": "ice cores"}]}
             for i, rec in enumerate(GOLD[:2])}
    path = tmp_path / "cand.json"
    write_candidates_cache(str(path), cache)
    assert load_candidates_cache(str(path)) == cache


def test_load_gold_json_and_csv(tmp_path):
    jpath = tmp_path / "gold.json"
    jpath.write_text(json.dumps(GOLD[:2], ensure_ascii=False), encoding="utf-8")
    assert len(load_gold(str(jpath))) == 2

    cpath = tmp_path / "gold.csv"
    cpath.write_text("record_id,TI,sampleType\ng1,ice core,ice cores\n", encoding="utf-8")
    rows = load_gold(str(cpath))
    assert rows[0]["record_id"] == "g1"


def test_cli_smoke(tmp_path, capsys):
    from scripts.eval_candidates import main

    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(GOLD, ensure_ascii=False), encoding="utf-8")
    lex_path = tmp_path / "lex.json"
    lex_path.write_text(json.dumps(LEXICON, ensure_ascii=False), encoding="utf-8")
    os_path = tmp_path / "os.json"
    os_path.write_text(json.dumps(OPTION_SET, ensure_ascii=False), encoding="utf-8")

    rc = main(
        [
            "--gold", str(gold_path),
            "--field", "sampleType",
            "--option-set", str(os_path),
            "--lexicon", str(lex_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "label_recall" in out
    for k in ("3", "5", "8", "full"):
        assert k in out
