"""Abstract base class for LLM providers — thin shim over ``agentkit.llm.base``.

The implementation moved to the skill-agnostic library in LangHuan Phase 2
P1a; this module only re-exports it so existing LSH imports keep working.
"""

from agentkit.llm.base import LLMProvider, ProviderError, require_aiohttp

__all__ = ["LLMProvider", "ProviderError", "require_aiohttp"]
