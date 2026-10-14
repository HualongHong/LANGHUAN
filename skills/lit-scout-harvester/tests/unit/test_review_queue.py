"""Tests for src.core.review_queue."""

from __future__ import annotations

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.review_queue import ReviewQueue


def test_enqueue_and_get(tmp_path):
    queue = ReviewQueue(str(tmp_path / "review.db"))
    cache = object()
    ctx = PipelineContext("r1", {"TI": "Title"}, cache)
    ctx.add_flag("low_confidence")
    item_id = queue.enqueue(ctx)

    assert item_id is not None
    item = queue.get(item_id)
    assert item["record_id"] == "r1"
    assert item["flags"] == ["low_confidence"]
    assert item["status"] == "pending"


def test_duplicate_record_not_enqueued(tmp_path):
    queue = ReviewQueue(str(tmp_path / "review.db"))
    cache = object()
    ctx = PipelineContext("r1", {"TI": "Title"}, cache)
    ctx.add_flag("low_confidence")
    assert queue.enqueue(ctx) is not None
    assert queue.enqueue(ctx) is None


def test_update_status(tmp_path):
    queue = ReviewQueue(str(tmp_path / "review.db"))
    cache = object()
    ctx = PipelineContext("r1", {"TI": "Title"}, cache)
    ctx.add_flag("low_confidence")
    item_id = queue.enqueue(ctx)
    assert queue.update_status(item_id, "approved", "Looks good")
    item = queue.get(item_id)
    assert item["status"] == "approved"
    assert item["reviewer_note"] == "Looks good"


def test_stats(tmp_path):
    queue = ReviewQueue(str(tmp_path / "review.db"))
    cache = object()
    for i in range(3):
        ctx = PipelineContext(f"r{i}", {"TI": "T"}, cache)
        ctx.add_flag("low_confidence")
        queue.enqueue(ctx)
    queue.update_status(1, "approved")
    stats = queue.stats()
    assert stats["pending"] == 2
    assert stats["approved"] == 1
    assert stats["total"] == 3
