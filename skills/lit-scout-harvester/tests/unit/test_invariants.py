"""Tests for record-count conservation invariants (C12)."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.context import (
    ContextFactory,
    PipelineContext,
    RecordCountInvariantError,
    assert_record_invariant,
    find_duplicate_ids,
)
from lit_scout_harvester.core.output_writer import OutputWriter
from lit_scout_harvester.core.pipeline import PipelineScheduler


def test_find_duplicate_ids():
    assert find_duplicate_ids(["a", "b", "a", "c", "b", "a"]) == {"a": 3, "b": 2}
    assert find_duplicate_ids(["a", "b"]) == {}


def test_invariant_passes_for_unique_ids():
    assert_record_invariant(["r1", "r2", "r3"], stage="unit_test")


def test_invariant_raises_and_names_duplicates():
    with pytest.raises(RecordCountInvariantError) as exc_info:
        assert_record_invariant(["dup-1", "ok-1", "dup-1"], stage="unit_test")
    message = str(exc_info.value)
    assert "'dup-1'" in message
    assert "rows_in=3" in message
    assert "distinct_ids_after_dedup=2" in message
    assert "unit_test" in message


@pytest.fixture
def offline_config():
    cfg = load_config("config/default.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    return cfg


@pytest.mark.asyncio
async def test_pipeline_raises_on_duplicate_record_id(offline_config, tmp_path):
    """A duplicate UT must abort the run before anything is written (C12)."""
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(offline_config, cache)
    records = [
        {"UT": "WOS:DUP123", "AU": ["Smith, J"], "TI": "Title A"},
        {"UT": "WOS:UNIQUE1", "AU": ["Jones, K"], "TI": "Title B"},
        {"UT": "WOS:DUP123", "AU": ["Smith, J"], "TI": "Title C"},
    ]
    with pytest.raises(RecordCountInvariantError, match="WOS:DUP123"):
        await scheduler.run(records)


@pytest.mark.asyncio
async def test_pipeline_accepts_unique_records(offline_config, tmp_path):
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(offline_config, cache)
    records = [
        {"UT": "WOS:OK1", "AU": ["Smith, J"], "TI": "Title A"},
        {"UT": "WOS:OK2", "AU": ["Jones, K"], "TI": "Title B"},
    ]
    contexts = await scheduler.run(records)
    assert len(contexts) == 2
    assert scheduler.stage_counts["pipeline_input"]["distinct_ids_in"] == 2
    assert scheduler.stage_counts["pipeline_output"]["rows_out"] == 2


def test_output_writer_raises_before_writing_duplicate_ids(tmp_path):
    """Direct writer usage with duplicate ids must fail before any file lands."""
    cfg = load_config("config/default.yaml")
    cfg.global_.output_dir = str(tmp_path)
    cfg.output.format = "csv"

    factory = ContextFactory(cache=object())
    ctx1 = factory.create({"UT": "WOS:DUP9", "TI": "Title A"})
    ctx2 = factory.create({"UT": "WOS:DUP9", "TI": "Title B"})

    writer = OutputWriter(cfg)
    with pytest.raises(RecordCountInvariantError, match="WOS:DUP9"):
        writer.write([ctx1, ctx2], run_name="dup_run")

    assert not (tmp_path / "dup_run.csv").exists()
    assert not (tmp_path / "run_counts.json").exists()


def test_output_writer_passes_for_unique_ids(tmp_path):
    cfg = load_config("config/default.yaml")
    cfg.global_.output_dir = str(tmp_path)
    cfg.output.format = "csv"

    cache = object()
    ctx1 = PipelineContext("r1", {"TI": "Title"}, cache)
    ctx2 = PipelineContext("r2", {"TI": "Title"}, cache)

    writer = OutputWriter(cfg)
    paths = writer.write([ctx1, ctx2], run_name="ok_run")
    assert paths["csv"].exists()
