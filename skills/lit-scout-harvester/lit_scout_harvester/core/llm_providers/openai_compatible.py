"""OpenAI-compatible providers — thin shim over ``agentkit.llm.providers``.

The implementation moved to the skill-agnostic library in LangHuan Phase 2
P1a; this module only re-exports it so existing LSH imports keep working.
"""

from agentkit.llm.providers.openai_compatible import (
    DeepSeekProvider,
    KimiProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    QwenProvider,
)

__all__ = [
    "DeepSeekProvider",
    "KimiProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "QwenProvider",
]
