"""Tests for the three-route enqueue policy in core.review_queue (C5)."""

from __future__ import annotations

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.review_queue import ReviewQueue


def _ctx(rid, extracted=None, confidence=None, raw=None):
    ctx = PipelineContext(rid, raw or {"TI": "T"}, object())
    for key, value in (extracted or {}).items():
        ctx.set_extracted(key, value)
    for key, value in (confidence or {}).items():
        ctx.confidence[key] = value
    return ctx


def _audit_record_ids(queue, result):
    ids = []
    for item_id in result["audit_sample"]:
        ids.append(queue.get(item_id)["record_id"])
    return sorted(ids)


def test_default_enqueue_includes_random_audit_batch(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    ctxs = [_ctx(f"r{i}") for i in range(400)]
    result = queue.enqueue_contexts(ctxs, audit_rate=0.05, seed=7)

    assert result["seed"] == 7
    assert result["rate"] == 0.05
    assert len(result["audit_sample"]) > 0
    assert queue.get_meta("audit_seed") == "7"
    assert queue.get_meta("audit_rate") == "0.05"
    for item_id in result["audit_sample"]:
        item = queue.get(item_id)
        assert "audit_sample" in item["flags"]

    # 同种子同输入可复现（跨实例逐字节一致）
    queue2 = ReviewQueue(str(tmp_path / "q2.db"))
    result2 = queue2.enqueue_contexts(ctxs, audit_rate=0.05, seed=7)
    assert _audit_record_ids(queue2, result2) == _audit_record_ids(queue, result)

    # 换种子不同
    queue3 = ReviewQueue(str(tmp_path / "q3.db"))
    result3 = queue3.enqueue_contexts(ctxs, audit_rate=0.05, seed=8)
    assert _audit_record_ids(queue3, result3) != _audit_record_ids(queue, result)


def test_hard_fields_full_check(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    hard = _ctx("hard1", extracted={"weird_field": ["x"]})
    normal = _ctx("normal1", extracted={"common_field": ["y"]})
    result = queue.enqueue_contexts(
        [hard, normal], audit_rate=0.0, seed=1, hard_fields=["weird_field"]
    )

    assert len(result["hard_field"]) == 1
    assert len(result["audit_sample"]) == 0
    item = queue.get(result["hard_field"][0])
    assert item["record_id"] == "hard1"
    assert "hard_field:weird_field" in item["flags"]
    assert queue.stats()["pending"] == 1


def test_confidence_mode_off_by_default(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    low = _ctx("low1", confidence={"f": 0.1})
    result = queue.enqueue_contexts([low], audit_rate=0.0, seed=1)
    assert result["audit_sample"] == []
    assert result["hard_field"] == []
    assert result["low_confidence"] == []
    assert queue.stats()["total"] == 0


def test_confidence_mode_legacy_optional(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    low = _ctx("low1", confidence={"f": 0.1})
    high = _ctx("high1", confidence={"f": 0.9})
    result = queue.enqueue_contexts(
        [low, high],
        audit_rate=0.0,
        seed=1,
        confidence_mode=True,
        confidence_threshold=0.3,
    )
    assert len(result["low_confidence"]) == 1
    item = queue.get(result["low_confidence"][0])
    assert item["record_id"] == "low1"
    assert "low_confidence" in item["flags"]


def test_routes_union_flags_and_dedupe(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    both = _ctx("both", extracted={"weird_field": ["x"]})
    result = queue.enqueue_contexts(
        [both], audit_rate=1.0, seed=1, hard_fields=["weird_field"]
    )
    # 只有一条入队（抽检 + 难点字段并集 flags，按首选路由分组）
    assert queue.stats()["pending"] == 1
    item = queue.get(result["audit_sample"][0])
    assert "audit_sample" in item["flags"]
    assert "hard_field:weird_field" in item["flags"]

    # 已 pending 的同记录再次入队被跳过
    again = queue.enqueue_contexts([both], audit_rate=1.0, seed=1, hard_fields=["weird_field"])
    assert again["skipped"] == ["both"]
    assert queue.stats()["pending"] == 1


def test_single_enqueue_backward_compatible(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    ctx = _ctx("r1", confidence={"a": 0.9, "b": 0.2})
    ctx.add_flag("low_confidence")
    item_id = queue.enqueue(ctx)
    assert item_id is not None

    item = queue.get(item_id)
    assert item["record_id"] == "r1"
    assert item["flags"] == ["low_confidence"]
    assert item["field_confidence"] == {"a": 0.9, "b": 0.2}
    assert item["min_confidence"] == 0.2

    listed = queue.list()
    assert listed[0]["id"] == item_id


def test_list_order_by_confidence(tmp_path):
    queue = ReviewQueue(str(tmp_path / "q.db"))
    none_id = queue.enqueue(_ctx("none"))
    low_id = queue.enqueue(_ctx("low", confidence={"f": 0.1}))
    high_id = queue.enqueue(_ctx("high", confidence={"f": 0.8}))

    ordered = queue.list(order_by_confidence=True)
    assert [it["id"] for it in ordered] == [low_id, high_id, none_id]


def test_existing_db_migrates_with_new_columns(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT NOT NULL,
            raw_record TEXT NOT NULL,
            processed TEXT,
            extracted TEXT,
            flags TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewer_note TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO review_queue(record_id, raw_record, flags, created_at)"
        " VALUES ('legacy', '{}', 'low_confidence', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    queue = ReviewQueue(str(db))
    item = queue.list()[0]
    assert item["record_id"] == "legacy"
    assert item["field_confidence"] == {}
    assert item["min_confidence"] is None
    assert queue.get_meta("audit_seed") is None
