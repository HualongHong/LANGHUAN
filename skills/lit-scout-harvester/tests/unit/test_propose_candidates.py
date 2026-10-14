"""Tests for prescreen.CandidateProposer (candidate narrowing, 3C)."""

from __future__ import annotations

from lit_scout_harvester.modules.prescreen import (
    CandidateProposer,
    PrescreenClassifier,
    PrescreenModule,
    propose_candidates,
)


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
    "lake sediments": ["lake sediment", "lacustrine"],
    "ice cores": ["ice core"],
}


def test_k_is_configurable():
    proposer = CandidateProposer(OPTION_SET, lexicon=LEXICON, k=2)
    record = {"TI": "lake sediment record"}
    candidates = proposer.propose(record)
    assert len(candidates) == 2
    # per-call override
    assert len(proposer.propose(record, k=3)) == 3


def test_deterministic_match_first_with_evidence():
    proposer = CandidateProposer(OPTION_SET, lexicon=LEXICON, k=3)
    record = {"TI": "An ice core study"}

    candidates = proposer.propose(record)

    labels = [c["label"] for c in candidates]
    assert labels[0] == "ice cores"
    assert candidates[0]["matched"] is True
    assert candidates[0]["evidence"] == ["ice core"]


def test_multiple_terms_accumulate_evidence():
    proposer = CandidateProposer(
        OPTION_SET, lexicon={"lake sediments": ["lake sediment", "lacustrine"]}, k=4
    )
    record = {"TI": "lake sediment", "AB": "lacustrine deposits"}
    candidates = proposer.propose(record)
    top = candidates[0]
    assert top["label"] == "lake sediments"
    assert top["evidence"] == ["lacustrine", "lake sediment"]  # sorted, deterministic


def test_fill_by_frequency_when_matches_short_of_k():
    proposer = CandidateProposer(OPTION_SET, lexicon=LEXICON, k=4)
    record = {"TI": "completely unrelated"}  # no lexicon hits

    candidates = proposer.propose(record)

    assert all(not c["matched"] for c in candidates)
    # frequency fill: evidence_count desc — lake sediments, stalagmites, ice cores
    assert [c["label"] for c in candidates] == [
        "lake sediments",
        "stalagmites",
        "ice cores",
        "tree rings",
    ]
    assert all(c["evidence"] == [] for c in candidates)


def test_matches_always_precede_frequency_fill():
    proposer = CandidateProposer(OPTION_SET, lexicon=LEXICON, k=3)
    record = {"TI": "ice core"}  # only one match; k=3 needs two filled
    candidates = proposer.propose(record)
    assert [c["label"] for c in candidates] == ["ice cores", "lake sediments", "stalagmites"]
    assert candidates[0]["matched"] is True
    assert candidates[1]["matched"] is False
    assert candidates[2]["matched"] is False


def test_accepts_plain_text_record():
    proposer = CandidateProposer(
        ["lake sediments", "ice cores"], lexicon=LEXICON, k=2, frequencies=None
    )
    candidates = proposer.propose("a lake sediment core")
    assert candidates[0]["label"] == "lake sediments"


def test_determinism_across_instances():
    r1 = CandidateProposer(OPTION_SET, lexicon=LEXICON, k=3).propose({"TI": "ice core"})
    r2 = CandidateProposer(OPTION_SET, lexicon=LEXICON, k=3).propose({"TI": "ice core"})
    assert r1 == r2


def test_k_capped_by_universe_size():
    proposer = CandidateProposer(["a", "b"], lexicon={}, k=10)
    candidates = proposer.propose({"TI": "x"})
    assert len(candidates) == 2


def test_module_level_wrapper():
    candidates = propose_candidates(
        {"TI": "ice core"}, OPTION_SET, k=2, lexicon=LEXICON
    )
    assert candidates[0]["label"] == "ice cores"
    assert len(candidates) == 2


def test_prescreen_binary_interface_unchanged():
    # 签名兼容：二元预筛选类与模块保持原构造签名
    import inspect

    params = list(inspect.signature(PrescreenClassifier.__init__).parameters)
    assert params == ["self", "llm_client", "cache"]
    params = list(inspect.signature(PrescreenModule.__init__).parameters)
    assert params == ["self", "llm_client", "cache"]
    assert PrescreenModule.name == "prescreen"
