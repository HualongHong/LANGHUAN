"""Cache identity must describe actual providers, not only profile aliases."""

from types import SimpleNamespace

from agentkit.llm.client import LLMClient


def test_request_hash_separates_actual_provider_and_endpoint():
    client = object.__new__(LLMClient)
    payload = {"messages": [{"role": "user", "content": "same"}]}
    providers = [
        SimpleNamespace(name="deepseek", base_url="https://api.deepseek.com/v1"),
        SimpleNamespace(name="kimi", base_url="https://api.deepseek.com/v1"),
        SimpleNamespace(name="deepseek", base_url="https://gateway.example.com/v1"),
    ]
    keys = [client._request_hash("shared", "same-model", payload, provider) for provider in providers]
    assert len(set(keys)) == 3
    assert keys[0] == client._request_hash("shared", "same-model", payload, providers[0])


def test_has_fallbacks_reflects_requested_provider_chain():
    client = object.__new__(LLMClient)
    client._providers = {"__primary__": object(), "backup": object()}
    client._fallback_order = []
    assert client.has_fallbacks() is False
    client._fallback_order = ["backup"]
    assert client.has_fallbacks() is True
    assert client.has_fallbacks("backup") is False
