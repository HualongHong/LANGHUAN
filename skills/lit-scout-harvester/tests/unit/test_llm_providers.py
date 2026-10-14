"""Tests for LLM provider registry and multi-provider client fallback."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import LLMConfig, LLMProfileConfig
from lit_scout_harvester.core.llm_client import LLMClient, LLMError
from lit_scout_harvester.core.llm_providers import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    KimiProvider,
    MiniMaxProvider,
    OpenAIProvider,
    ProviderError,
    QwenProvider,
    get_provider,
)


def test_get_provider_deepseek():
    p = get_provider("deepseek", model="", api_key="sk", base_url="https://api.deepseek.com/v1")
    assert isinstance(p, DeepSeekProvider)
    assert p.normalize_model_name("") == "deepseek-chat"


def test_get_provider_openai():
    p = get_provider("openai", model="", api_key="sk", base_url="https://api.openai.com/v1")
    assert isinstance(p, OpenAIProvider)
    assert p.normalize_model_name("") == "gpt-3.5-turbo"


def test_get_provider_kimi():
    p = get_provider("kimi", model="", api_key="sk", base_url="https://api.moonshot.cn/v1")
    assert isinstance(p, KimiProvider)
    assert p.normalize_model_name("") == "moonshot-v1-8k"


def test_get_provider_qwen():
    p = get_provider("qwen", model="", api_key="sk", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert isinstance(p, QwenProvider)
    assert p.normalize_model_name("") == "qwen-turbo"


def test_get_provider_minimax():
    p = get_provider("minimax", model="", api_key="sk", base_url="")
    assert isinstance(p, MiniMaxProvider)
    assert p.normalize_model_name("") == "abab6.5-chat"


def test_get_provider_gemini():
    p = get_provider("gemini", model="", api_key="sk", base_url="")
    assert isinstance(p, GeminiProvider)
    assert p.normalize_model_name("") == "gemini-1.5-flash"


def test_get_provider_claude():
    p = get_provider("claude", model="", api_key="sk", base_url="")
    assert isinstance(p, ClaudeProvider)
    assert p.normalize_model_name("") == "claude-3-haiku-20240307"


def test_get_provider_unsupported_raises():
    with pytest.raises(ProviderError):
        get_provider("unknown", model="m", api_key="sk", base_url="https://x")


@pytest.mark.asyncio
async def test_llm_client_uses_fallback_on_failure(tmp_path, monkeypatch):
    config = LLMConfig(
        api_key="sk-primary",
        models={
            "fallback": LLMProfileConfig(
                provider="openai",
                model="gpt-4o-mini",
                api_key="sk-fallback",
                base_url="https://api.openai.com/v1",
            )
        },
        fallback_providers=["fallback"],
    )
    cache = CacheManager(str(tmp_path / "cache.db"))
    client = LLMClient(config, cache)

    async def failing_chat(*args, **kwargs):
        raise ProviderError("primary down")

    async def ok_chat(*args, **kwargs):
        return "fallback-ok"

    monkeypatch.setattr(client._providers["__primary__"], "chat", failing_chat)
    monkeypatch.setattr(client._providers["fallback"], "chat", ok_chat)

    result = await client.chat(messages=[{"role": "user", "content": "hi"}])
    assert result == "fallback-ok"


@pytest.mark.asyncio
async def test_llm_client_raises_after_all_fail(tmp_path, monkeypatch):
    config = LLMConfig(api_key="sk-primary")
    cache = CacheManager(str(tmp_path / "cache.db"))
    client = LLMClient(config, cache)

    monkeypatch.setattr(
        client._providers["__primary__"],
        "chat",
        lambda *a, **k: (_ for _ in ()).throw(ProviderError("down")),
    )

    with pytest.raises(LLMError):
        await client.chat(messages=[{"role": "user", "content": "hi"}])
