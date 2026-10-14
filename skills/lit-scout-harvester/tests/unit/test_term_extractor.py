"""Tests for src.modules.term_extractor."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import LLMConfig
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.modules.term_extractor import SciEduExtractorModule, TermExtractorModule


@pytest.fixture
def mock_llm_client_and_cache(tmp_path, monkeypatch):
    config = LLMConfig(api_key="sk-test")
    cache = CacheManager(str(tmp_path / "cache.db"))
    client = LLMClient(config, cache)

    async def fake_chat(*args, **kwargs):
        return json.dumps(
            {
                "results": [
                    {
                        "research_topic": ["conceptual change"],
                        "scientific_concepts": ["photosynthesis"],
                        "inquiry_abilities": ["hypothesizing"],
                        "instructional_strategies": ["inquiry-based instruction"],
                        "participant_group": "middle school students",
                        "educational_level": "K-12",
                        "assessment_type": ["pre/post-test"],
                        "keywords": ["science education"],
                    }
                ]
            }
        )

    monkeypatch.setattr(client, "chat", fake_chat)
    return client, cache


@pytest.mark.asyncio
async def test_term_extractor_extracts_fields(mock_llm_client_and_cache):
    client, cache = mock_llm_client_and_cache
    extractor = TermExtractorModule(client, cache)
    ctx = PipelineContext(
        "r1",
        {"TI": "Teaching photosynthesis", "AB": "Students learn photosynthesis through inquiry."},
        object(),
    )
    result = await extractor.process(
        ctx, {"schema_path": "lit_scout_harvester/schemas/sci_edu.json"}
    )
    assert result["research_topic"]["value"] == ["conceptual change"]
    assert result["participant_group"]["value"] == "middle school students"


@pytest.mark.asyncio
async def test_sci_edu_extractor_uses_default_schema(mock_llm_client_and_cache):
    client, cache = mock_llm_client_and_cache
    extractor = SciEduExtractorModule(client, cache)
    ctx = PipelineContext("r1", {"TI": "Science ed", "AB": "Abstract"}, object())
    result = await extractor.process(ctx, {})
    assert "research_topic" in result


@pytest.mark.asyncio
async def test_term_extractor_caches_result(mock_llm_client_and_cache):
    client, cache = mock_llm_client_and_cache
    extractor = TermExtractorModule(client, cache)
    ctx = PipelineContext("r1", {"TI": "T", "AB": "A"}, object())
    await extractor.process(ctx, {"schema_path": "lit_scout_harvester/schemas/sci_edu.json"})

    called = False

    async def no_chat(*args, **kwargs):
        nonlocal called
        called = True
        return "{}"

    client.chat = no_chat
    ctx2 = PipelineContext("r2", {"TI": "T", "AB": "A"}, object())
    result = await extractor.process(ctx2, {"schema_path": "lit_scout_harvester/schemas/sci_edu.json"})
    assert called is False
    assert result["research_topic"]["value"] == ["conceptual change"]
