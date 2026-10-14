"""Tests for pipeline batch review enqueue via enqueue_contexts (2B wrap-up).

Default behavior: the scheduler hands ALL contexts to
``ReviewQueue.enqueue_contexts()`` (three-route policy, fixed-seed random
audit sample reproducible across runs). ``caliber.review.batch_enqueue=False``
falls back to the legacy per-context flag-based enqueue unchanged.
"""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import CaliberConfig, load_caliber, load_config
from lit_scout_harvester.core.pipeline import PipelineScheduler


def base_config(tmp_path, review_db="review.db"):
    cfg = load_config("config/default.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    cfg.skeleton.language_detection = False
    cfg.skeleton.restore_author_address = False
    cfg.review.enabled = True
    cfg.review.queue_db = str(tmp_path / review_db)
    return cfg


def plain_records(n, prefix="WOS:plain"):
    return [{"UT": f"{prefix}{i}", "TI": f"Title {i}"} for i in range(n)]


@pytest.mark.asyncio
async def test_batch_enqueue_default_with_reproducible_audit_sample(tmp_path):
    cfg = base_config(tmp_path, review_db="a.db")
    caliber = load_caliber()
    records = plain_records(400)

    scheduler = PipelineScheduler(cfg, CacheManager(str(tmp_path / "cache_a.db")))
    await scheduler.run(records)
    queue_a = scheduler.review_queue

    cfg2 = base_config(tmp_path, review_db="b.db")
    scheduler2 = PipelineScheduler(cfg2, CacheManager(str(tmp_path / "cache_b.db")))
    await scheduler2.run(records)
    queue_b = scheduler2.review_queue

    # 抽检批次种子可复现：两个实例、两个数据库，抽中集合逐一致
    ids_a = {item["record_id"] for item in queue_a.list(status="pending", limit=1000)}
    ids_b = {item["record_id"] for item in queue_b.list(status="pending", limit=1000)}
    assert ids_a == ids_b
    assert 0 < len(ids_a) < 400  # ~5% audit sample, no low-confidence route
    for item in queue_a.list(status="pending", limit=1000):
        assert "audit_sample" in item["flags"]
    assert queue_a.get_meta("audit_seed") == str(caliber.audit.default_seed)
    assert queue_a.get_meta("audit_rate") == str(caliber.audit.audit_rate)


@pytest.mark.asyncio
async def test_batch_enqueue_keeps_low_confidence_route(tmp_path):
    """auto_flag_low_confidence 映射为三路中的 low_confidence 路由。"""
    cfg = base_config(tmp_path)
    cfg.skeleton.restore_author_address = True  # AU without C1 → confidence 0.0
    cfg.postprocess.min_confidence = 1.0
    records = [
        {"UT": f"WOS:low{i}", "AU": ["Smith, J"], "TI": "Title", "AB": "Abstract"}
        for i in range(3)
    ]
    scheduler = PipelineScheduler(cfg, CacheManager(str(tmp_path / "cache.db")))
    await scheduler.run(records)

    stats = scheduler.review_queue.stats()
    assert stats["pending"] == 3
    for item in scheduler.review_queue.list(status="pending"):
        assert "low_confidence" in item["flags"]


@pytest.mark.asyncio
async def test_batch_enqueue_hard_fields_full_check(tmp_path, monkeypatch):
    """caliber.audit.hard_fields 在 batch 模式下仍然全检入队。"""
    from lit_scout_harvester.core.config_loader import ModuleConfig
    from lit_scout_harvester.modules.base import ExtractionModule

    class WeirdFieldModule(ExtractionModule):
        name = "weird_field_stub"

        async def process(self, ctx, config):
            if ctx.raw.get("weird_field"):
                return {
                    "weird_field": {"value": ctx.raw["weird_field"], "confidence": 0.9}
                }
            return {}

    caliber = load_caliber()
    caliber.audit.hard_fields = ["weird_field"]
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber", lambda: caliber
    )
    cfg = base_config(tmp_path)
    cfg.modules["weird_field_stub"] = ModuleConfig(enabled=True)
    records = [
        {"UT": "WOS:hard", "TI": "T1", "weird_field": ["x"]},
        {"UT": "WOS:normal", "TI": "T2"},
    ]
    scheduler = PipelineScheduler(
        cfg,
        CacheManager(str(tmp_path / "cache.db")),
        modules=[WeirdFieldModule()],
    )
    await scheduler.run(records)

    pending = {i["record_id"] for i in scheduler.review_queue.list(status="pending")}
    assert "WOS:hard" in pending


@pytest.mark.asyncio
async def test_fallback_switch_restores_legacy_flag_enqueue(tmp_path, monkeypatch):
    caliber = CaliberConfig(review={"batch_enqueue": False})
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber", lambda: caliber
    )
    cfg = base_config(tmp_path)
    cfg.skeleton.restore_author_address = True  # AU without C1 → confidence 0.0
    cfg.postprocess.min_confidence = 1.0  # force low_confidence flag
    records = [
        {"UT": f"WOS:low{i}", "AU": ["Smith, J"], "TI": "Title", "AB": "Abstract"}
        for i in range(3)
    ]
    scheduler = PipelineScheduler(cfg, CacheManager(str(tmp_path / "cache.db")))
    await scheduler.run(records)

    # 旧行为：逐 ctx flag 入队，低置信度 3 条全进；无 flag 的不进
    stats = scheduler.review_queue.stats()
    assert stats["pending"] == 3
    assert scheduler.review_queue.get_meta("audit_seed") is None  # 旧路径不写抽检种子


@pytest.mark.asyncio
async def test_fallback_no_flags_no_enqueue(tmp_path, monkeypatch):
    caliber = CaliberConfig(review={"batch_enqueue": False})
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber", lambda: caliber
    )
    cfg = base_config(tmp_path)
    scheduler = PipelineScheduler(cfg, CacheManager(str(tmp_path / "cache.db")))
    await scheduler.run(plain_records(50, prefix="WOS:clean"))

    assert scheduler.review_queue.stats()["total"] == 0
