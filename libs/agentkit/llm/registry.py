"""LLM provider registry and factory (skill-agnostic).

Mirrors the historical Lit-Scout-Harvester registry: deepseek / openai /
kimi (alias moonshot) / qwen (alias dashscope) / minimax / gemini /
claude (alias anthropic) / agent_bridge (alias agent). Skills may register
additional providers via :func:`register_provider`, or copy
``PROVIDER_REGISTRY`` and override entries in their own shim (as LSH does to
keep its ``.lit_scout_llm`` bridge default).
"""

from __future__ import annotations

from typing import Any, Dict, Type

from agentkit.llm.base import LLMProvider, ProviderError
from agentkit.llm.bridge_provider import AgentBridgeProvider
from agentkit.llm.providers import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    KimiProvider,
    MiniMaxProvider,
    OpenAIProvider,
    QwenProvider,
)

PROVIDER_REGISTRY: Dict[str, Type[LLMProvider]] = {
    "deepseek": DeepSeekProvider,
    "openai": OpenAIProvider,
    "kimi": KimiProvider,
    "moonshot": KimiProvider,
    "qwen": QwenProvider,
    "dashscope": QwenProvider,
    "minimax": MiniMaxProvider,
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
    "anthropic": ClaudeProvider,
    "agent_bridge": AgentBridgeProvider,
    "agent": AgentBridgeProvider,
}


def register_provider(name: str, cls: Type[LLMProvider], *, override: bool = False) -> None:
    """Register ``cls`` under ``name`` (open registration for skills).

    Raises:
        ProviderError: if ``name`` is already taken and ``override`` is False.
    """
    key = name.lower().strip()
    if not override and key in PROVIDER_REGISTRY:
        raise ProviderError(f"LLM provider name already registered: {key}")
    PROVIDER_REGISTRY[key] = cls


def get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """Instantiate a provider by registered name.

    Args:
        name: Provider identifier such as ``deepseek`` or ``kimi``.
        **kwargs: Provider-specific arguments (model, api_key, base_url, ...).

    Raises:
        ProviderError: if ``name`` is not registered.
    """
    name = name.lower().strip()
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise ProviderError(f"Unsupported LLM provider: {name}")
    return cls(**kwargs)


__all__ = [
    "PROVIDER_REGISTRY",
    "get_provider",
    "register_provider",
]
