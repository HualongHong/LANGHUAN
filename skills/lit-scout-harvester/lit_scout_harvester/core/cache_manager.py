"""SQLite-backed content hash cache — thin shim over ``agentkit.cache``.

The implementation moved to the skill-agnostic library in LangHuan Phase 2
P1a (``agentkit.cache.core.CacheManager``); this module only re-exports it so
existing LSH imports keep working unchanged.
"""

from agentkit.cache import CacheManager

__all__ = ["CacheManager"]
