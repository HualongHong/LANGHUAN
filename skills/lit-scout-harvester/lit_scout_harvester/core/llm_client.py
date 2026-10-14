"""LSH async LLM client — thin shim over ``agentkit.llm.client``.

The caching/retry/fallback engine moved to the skill-agnostic library in
LangHuan Phase 2 P1a. This shim only:

* builds LSH's ``ConcurrentPool`` from ``LLMConfig`` and hands it to the
  generic client (providers share its aiohttp connector);
* routes provider construction through the LSH registry (which maps
  ``agent_bridge`` to the LSH subclass with the historical ``.lit_scout_llm``
  queue dir).

Public API (``LLMClient`` / ``LLMError``) is unchanged.
"""

from __future__ import annotations

from agentkit.llm.client import LLMClient as _AgentkitLLMClient
from agentkit.llm.client import LLMError

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.concurrent_pool import ConcurrentPool
from lit_scout_harvester.core.config_loader import LLMConfig
from lit_scout_harvester.core.llm_providers import get_provider


class LLMClient(_AgentkitLLMClient):
    """Async LLM client with content-hash caching and provider fallback.

    The client resolves a model profile from ``config.models`` when a module
    requests one; otherwise it uses the primary provider.
    """

    _get_provider = staticmethod(get_provider)

    def __init__(self, config: LLMConfig, cache: CacheManager):
        pool = ConcurrentPool(
            max_connections=config.max_connections,
            max_threads=config.max_threads,
            connection_keepalive=config.connection_keepalive,
        )
        super().__init__(config, cache, pool=pool)


__all__ = ["LLMClient", "LLMError"]
