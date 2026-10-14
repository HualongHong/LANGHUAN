"""Tests for the review_cli audit subcommand and Wilson interval (C5)."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.review_queue import ReviewQueue
from scripts.review_cli import main, wilson_score_interval


def _ctx(rid):
    return PipelineContext(rid, {"TI": "T"}, object())


def test_wilson_hand_computed():
    # 手算：k=3, n=4, z=1.96 -> 中心 (0.75+0.4802)/1.9604，半宽 1.96*sqrt(0.1069)/1.9604
    low, high = wilson_score_interval(3, 4)
    assert low == pytest.approx(0.3006360524, abs=1e-9)
    assert high == pytest.approx(0.9544139374, abs=1e-9)


def test_wilson_edges_and_validation():
    assert wilson_score_interval(0, 0) == (0.0, 0.0)
    assert wilson_score_interval(0, 10) == pytest.approx((0.0, 0.2775401688), abs=1e-9)
    assert wilson_score_interval(10, 10) == pytest.approx((1.0 / 1.38416, 1.0), abs=1e-9)
    with pytest.raises(ValueError):
        wilson_score_interval(5, 4)
    with pytest.raises(ValueError):
        wilson_score_interval(-1, 4)


def test_audit_sample_mode_is_deterministic(tmp_path, capsys):
    ids_file = tmp_path / "ids.txt"
    ids = [f"r{i:04d}" for i in range(500)]
    ids_file.write_text("\n".join(ids) + "\n", encoding="utf-8")
    db = str(tmp_path / "q.db")

    argv = ["--db-path", db, "audit", "--ids-file", str(ids_file), "--rate", "0.1", "--seed", "5"]
    assert main(argv) == 0
    out1 = capsys.readouterr().out
    assert main(argv) == 0
    out2 = capsys.readouterr().out
    assert out1 == out2

    sampled = [line for line in out1.splitlines() if line.strip()]
    assert 0 < len(sampled) < len(ids)
    assert set(sampled).issubset(set(ids))


def test_audit_sample_mode_missing_file(tmp_path, capsys):
    rc = main(["--db-path", str(tmp_path / "q.db"), "audit", "--ids-file", str(tmp_path / "nope.txt")])
    assert rc == 1


def _make_reviewed_audit_batch(db_path, n_total=4, n_approved=3):
    queue = ReviewQueue(db_path)
    ctxs = [_ctx(f"r{i}") for i in range(n_total)]
    result = queue.enqueue_contexts(ctxs, audit_rate=1.0, seed=1)
    item_ids = result["audit_sample"]
    for i, item_id in enumerate(item_ids):
        status = "approved" if i < n_approved else "rejected"
        queue.update_status(item_id, status)
    return queue, item_ids


def test_audit_report_accuracy_and_interval(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    _make_reviewed_audit_batch(db, n_total=4, n_approved=3)
    assert main(["--db-path", db, "audit"]) == 0
    out = capsys.readouterr().out
    assert "audit_sample" in out
    assert "3/4 = 0.750" in out
    assert "Wilson" in out
    assert "[0.3006, 0.9544]" in out


def test_audit_report_ignores_non_audit_items(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    queue, item_ids = _make_reviewed_audit_batch(db, n_total=4, n_approved=3)
    # 非抽检批次项不影响统计
    other_id = queue.enqueue(_ctx("other"))
    queue.update_status(other_id, "rejected")
    assert main(["--db-path", db, "audit"]) == 0
    out = capsys.readouterr().out
    assert "3/4 = 0.750" in out


def test_audit_report_no_reviewed_items(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    queue = ReviewQueue(db)
    queue.enqueue_contexts([_ctx("r1")], audit_rate=1.0, seed=1)
    assert main(["--db-path", db, "audit"]) == 0
    out = capsys.readouterr().out
    assert "No reviewed audit items" in out


def test_audit_report_empty_queue(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    assert main(["--db-path", db, "audit"]) == 0
    out = capsys.readouterr().out
    assert "No reviewed audit items" in out
