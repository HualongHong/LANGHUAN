"""Tests for src.lang.abstract_segmenter."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import LLMConfig
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.lang.abstract_segmenter import AbstractSegmenter


@pytest.fixture
def temp_cache(tmp_path):
    return CacheManager(str(tmp_path / "cache.db"))


@pytest.fixture
def mock_llm_client(temp_cache, monkeypatch):
    config = LLMConfig(api_key="sk-test")
    client = LLMClient(config, temp_cache)

    async def fake_chat(*args, **kwargs):
        return json.dumps(
            {
                "segments": [
                    {
                        "background": "Background text.",
                        "research_content": "Methods and results.",
                        "discussion": "Implications.",
                    }
                ]
            }
        )

    monkeypatch.setattr(client, "chat", fake_chat)
    return client


@pytest.mark.asyncio
async def test_segment_records_adds_segments(mock_llm_client, temp_cache):
    segmenter = AbstractSegmenter(mock_llm_client, temp_cache, batch_size=10)
    records = [{"AB": "Some abstract text."}]
    result = await segmenter.segment_records(records, field="AB")
    assert result[0]["segmented_abstract"]["background"] == "Background text."
    assert result[0]["segmented_abstract"]["research_content"] == "Methods and results."
    assert result[0]["segmented_abstract"]["discussion"] == "Implications."


@pytest.mark.asyncio
async def test_segment_records_handles_empty_abstract(mock_llm_client, temp_cache):
    segmenter = AbstractSegmenter(mock_llm_client, temp_cache, batch_size=10)
    records = [{"AB": ""}]
    result = await segmenter.segment_records(records, field="AB")
    assert result[0]["segmented_abstract"] == {
        "background": "",
        "research_content": "",
        "discussion": "",
    }
