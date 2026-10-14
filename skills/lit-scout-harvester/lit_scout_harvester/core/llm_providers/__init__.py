"""LLM provider registry and factory — thin shim over ``agentkit.llm``.

The registry moved to the skill-agnostic library in LangHuan Phase 2 P1a.
This shim re-exports the agentkit registry but overrides the ``agent_bridge``
entries with the LSH subclass (historical default queue dir
``.lit_scout_llm``), so existing LSH behavior is preserved exactly.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from agentkit.llm.base import LLMProvider, ProviderError
from agentkit.llm.providers import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    KimiProvider,
    MiniMaxProvider,
    OpenAIProvider,
    QwenProvider,
)
from agentkit.llm.registry import PROVIDER_REGISTRY as _AGENTKIT_REGISTRY

from lit_scout_harvester.core.llm_providers.agent_bridge import AgentBridgeProvider

PROVIDER_REGISTRY: Dict[str, Type[LLMProvider]] = {
    **_AGENTKIT_REGISTRY,
    "agent_bridge": AgentBridgeProvider,
    "agent": AgentBridgeProvider,
}


def register_provider(name: str, cls: Type[LLMProvider], *, override: bool = False) -> None:
    """Register ``cls`` under ``name`` in the LSH registry (skill extension hook)."""
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
    "LLMProvider",
    "ProviderError",
    "get_provider",
    "register_provider",
    "PROVIDER_REGISTRY",
    "DeepSeekProvider",
    "OpenAIProvider",
    "KimiProvider",
    "QwenProvider",
    "MiniMaxProvider",
    "GeminiProvider",
    "ClaudeProvider",
    "AgentBridgeProvider",
]
