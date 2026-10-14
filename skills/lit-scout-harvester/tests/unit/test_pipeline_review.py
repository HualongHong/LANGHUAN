"""Tests for pipeline auto-enqueue into review queue."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list


@pytest.mark.asyncio
async def test_pipeline_enqueues_low_confidence(tmp_path):
    cfg = load_config("config/default.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    cfg.skeleton.language_detection = False
    cfg.postprocess.min_confidence = 1.0  # force low_confidence flag
    cfg.review.queue_db = str(tmp_path / "review.db")

    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache)
    # Use records without addresses to force confidence 0.0 for all of them.
    records = [
        {"UT": f"WOS:test{i}", "AU": ["Smith, J"], "TI": "Title", "AB": "Abstract"}
        for i in range(3)
    ]
    await scheduler.run(records)

    stats = scheduler.review_queue.stats()
    assert stats["pending"] == 3
