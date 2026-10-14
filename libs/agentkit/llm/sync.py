"""Synchronous wrappers around async LLM providers/clients.

For sync scripts (e.g. Anchor-Verify-Strategizer) that cannot hold an event
loop: each call runs ``asyncio.run`` over the provider/client ``chat``
coroutine. Do not use inside a running event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


def chat_sync(
    provider_or_client: Any,
    messages: List[Dict[str, Any]],
    response_format: Optional[Dict[str, str]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> str:
    """Run one async ``chat(...)`` call synchronously via ``asyncio.run``.

    Args:
        provider_or_client: an ``agentkit.llm`` provider or ``LLMClient``.
        messages: OpenAI-style message list.
        response_format / temperature / max_tokens: forwarded to ``chat``.
        **kwargs: extra ``chat`` keyword arguments (e.g. ``model_profile``
            for ``LLMClient``).

    Raises:
        RuntimeError: if called from inside a running event loop.
    """
    return asyncio.run(
        provider_or_client.chat(
            messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
    )


__all__ = ["chat_sync"]
