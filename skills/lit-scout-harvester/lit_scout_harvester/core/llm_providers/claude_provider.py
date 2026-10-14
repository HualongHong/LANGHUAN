"""Claude provider — thin shim over ``agentkit.llm.providers.claude``.

The implementation moved to the skill-agnostic library in LangHuan Phase 2
P1a; this module only re-exports it so existing LSH imports keep working.
"""

from agentkit.llm.providers.claude import ClaudeProvider

__all__ = ["ClaudeProvider"]
