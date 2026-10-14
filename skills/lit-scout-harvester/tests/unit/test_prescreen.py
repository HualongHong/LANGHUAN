"""Tests for src.modules.prescreen."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import LLMConfig
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.modules.prescreen import PrescreenClassifier


@pytest.fixture
def classifier(tmp_path, monkeypatch):
    config = LLMConfig(api_key="sk-test")
    cache = CacheManager(str(tmp_path / "cache.db"))
    client = LLMClient(config, cache)

    async def fake_chat(*args, **kwargs):
        return json.dumps({"score": 0.85})

    monkeypatch.setattr(client, "chat", fake_chat)
    return PrescreenClassifier(client, cache)


@pytest.mark.asyncio
async def test_prescreen_passes_above_threshold(classifier):
    record = {"TI": "Science education study", "AB": "Abstract"}
    result = await classifier.classify(record, "is_sci_edu", threshold=0.5)
    assert result["score"] == 0.85
    assert result["passed"] is True


@pytest.mark.asyncio
async def test_prescreen_fails_below_threshold(classifier):
    record = {"TI": "Science education study", "AB": "Abstract"}
    result = await classifier.classify(record, "is_sci_edu", threshold=0.9)
    assert result["passed"] is False


@pytest.mark.asyncio
async def test_prescreen_cache(classifier):
    record = {"TI": "T", "AB": "A"}
    await classifier.classify(record, "is_sci_edu")

    called = False

    async def no_chat(*args, **kwargs):
        nonlocal called
        called = True
        return json.dumps({"score": 0.0})

    classifier.llm_client.chat = no_chat
    result = await classifier.classify(record, "is_sci_edu")
    assert called is False
    assert result["score"] == 0.85
