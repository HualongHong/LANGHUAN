"""Tests for batched module execution (R4)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.term_extractor import TermExtractorModule


class FakeLLMClient:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def make_context(cache, ti="T", ab="A"):
    return PipelineContext("id", {"TI": ti, "AB": ab}, cache)


@pytest.fixture
def cache(tmp_path):
    return CacheManager(tmp_path / "cache.db")


def test_term_extractor_process_batch_uses_single_call(cache):
    fake_llm = FakeLLMClient(
        responses=[
            '{"results": [{"sampleType": ["lake sediments"]}, {"sampleType": ["marine sediments"]}]}'
        ]
    )
    module = TermExtractorModule(fake_llm, cache)
    config = {"schema_path": "lit_scout_harvester/schemas/sample_rst.json"}
    ctxs = [make_context(cache, "T1", "A1"), make_context(cache, "T2", "A2")]

    results = asyncio.run(module.process_batch(ctxs, config))

    assert len(fake_llm.calls) == 1
    assert len(results) == 2
    assert results[0]["sampleType"]["value"] == ["lake sediments"]
    assert results[1]["sampleType"]["value"] == ["marine sediments"]


def test_batch_falls_back_to_per_record_on_mismatch(cache):
    fake_llm = FakeLLMClient(
        responses=[
            # Batch returns wrong count -> triggers fallback.
            '{"results": [{"sampleType": ["lake sediments"]}]}',
            # Fallback per-record responses.
            '{"results": [{"sampleType": ["lake sediments"]}]}',
            '{"results": [{"sampleType": ["marine sediments"]}]}',
        ]
    )
    module = TermExtractorModule(fake_llm, cache)
    config = {"schema_path": "lit_scout_harvester/schemas/sample_rst.json"}
    ctxs = [make_context(cache, "T1", "A1"), make_context(cache, "T2", "A2")]

    results = asyncio.run(module.process_batch(ctxs, config))

    assert len(results) == 2
    assert results[0]["sampleType"]["value"] == ["lake sediments"]
    assert results[1]["sampleType"]["value"] == ["marine sediments"]
