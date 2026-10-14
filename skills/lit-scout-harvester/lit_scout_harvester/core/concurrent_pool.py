"""Concurrency helpers for async HTTP and threaded CPU/IO work.

This module centralizes the connection pool and thread-pool configuration so
that ``LLMClient`` and non-async callers (e.g. R/Bibliometrix subprocesses)
can share the same resource limits declared in ``LLMConfig``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import aiohttp


class AsyncConnectionPool:
    """Lazy aiohttp TCP connector governed by ``LLMConfig`` limits."""

    def __init__(
        self,
        limit: int = 20,
        keepalive: bool = True,
    ):
        self.limit = limit
        self.keepalive = keepalive
        self._connector: Optional[aiohttp.TCPConnector] = None

    def get_connector(self) -> aiohttp.TCPConnector:
        if self._connector is None or self._connector.closed:
            self._connector = aiohttp.TCPConnector(
                limit=self.limit,
                limit_per_host=self.limit,
                force_close=not self.keepalive,
                enable_cleanup_closed=True,
            )
        return self._connector

    async def close(self) -> None:
        if self._connector is not None and not self._connector.closed:
            await self._connector.close()
            self._connector = None


class ConcurrentPool:
    """Combined async connection pool + thread pool."""

    def __init__(
        self,
        max_connections: int = 20,
        max_threads: int = 4,
        connection_keepalive: bool = True,
    ):
        self.async_pool = AsyncConnectionPool(
            limit=max_connections,
            keepalive=connection_keepalive,
        )
        self.thread_pool = ThreadPoolExecutor(
            max_workers=max_threads,
            thread_name_prefix="lsh_",
        )

    async def close(self) -> None:
        await self.async_pool.close()
        self.thread_pool.shutdown(wait=True)
