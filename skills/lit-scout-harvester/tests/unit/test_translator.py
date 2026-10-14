"""Tests for src.lang.translator."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import LLMConfig
from lit_scout_harvester.core.llm_client import LLMClient
from lit_scout_harvester.lang.translator import Translator


@pytest.fixture
def temp_cache(tmp_path):
    return CacheManager(str(tmp_path / "cache.db"))


@pytest.fixture
def mock_llm_client(temp_cache, monkeypatch):
    config = LLMConfig(api_key="sk-test")
    client = LLMClient(config, temp_cache)

    async def fake_chat(*args, **kwargs):
        return json.dumps({"translations": ["你好", "世界"]})

    monkeypatch.setattr(client, "chat", fake_chat)
    return client


@pytest.mark.asyncio
async def test_translate_records_adds_translations(mock_llm_client, temp_cache):
    translator = Translator(mock_llm_client, temp_cache, batch_size=10)
    records = [{"TI": "Hello", "AB": "World"}]
    result = await translator.translate_records(records, fields=["TI", "AB"])
    assert result[0]["TI_zh"] == "你好"
    assert result[0]["AB_zh"] == "世界"


@pytest.mark.asyncio
async def test_translate_records_use_cache(mock_llm_client, temp_cache):
    translator = Translator(mock_llm_client, temp_cache, batch_size=10)
    records = [{"TI": "Hello"}]
    await translator.translate_records(records, fields=["TI"])

    # Second call with the same text should not invoke the LLM.
    called = False

    async def no_chat(*args, **kwargs):
        nonlocal called
        called = True
        return json.dumps({"translations": ["x"]})

    mock_llm_client.chat = no_chat
    records2 = [{"TI": "Hello"}]
    await translator.translate_records(records2, fields=["TI"])
    assert records2[0]["TI_zh"] == "你好"
    assert called is False


@pytest.mark.asyncio
async def test_translate_records_skips_chinese(mock_llm_client, temp_cache):
    translator = Translator(
        mock_llm_client, temp_cache, batch_size=10, identifier=lambda t: "zh"
    )
    records = [{"TI": "你好"}]
    result = await translator.translate_records(records, fields=["TI"])
    assert result[0]["TI_zh"] == "你好"
