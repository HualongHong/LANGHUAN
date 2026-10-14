"""Tests for src.core.pipeline."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list


@pytest.fixture
def default_config():
    cfg = load_config("config/default.yaml")
    # Disable LLM-dependent steps to keep unit tests fast and offline.
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    return cfg


@pytest.mark.asyncio
async def test_pipeline_runs_skeleton(default_config, tmp_path):
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(default_config, cache)
    records = parse_wos_plaintext_to_list("tests/fixtures/sample.wos", limit=5)
    contexts = await scheduler.run(records)

    assert len(contexts) == 5
    for ctx in contexts:
        assert ctx.record_id
        assert "author_addresses" in ctx.processed
        assert len(ctx.processed["author_addresses"]) == len(ctx.raw.get("AU", []))
        assert ctx.processed.get("detected_language") == "en"


@pytest.mark.asyncio
async def test_pipeline_flags_low_confidence(default_config, tmp_path):
    cache = CacheManager(str(tmp_path / "cache.db"))
    default_config.postprocess.min_confidence = 1.0
    scheduler = PipelineScheduler(default_config, cache)
    # Use a record without an address to force confidence 0.0.
    records = [{"UT": "WOS:test", "AU": ["Smith, J"], "TI": "Title", "AB": "Abstract"}]
    contexts = await scheduler.run(records)
    assert "low_confidence" in contexts[0].flags
