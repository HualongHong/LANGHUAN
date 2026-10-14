"""MiniMax provider (proprietary API).

Extracted from Lit-Scout-Harvester ``core/llm_providers/minimax_provider.py``
in LangHuan Phase 2 P1a; behavior unchanged. ``aiohttp`` is imported lazily.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agentkit.llm.base import LLMProvider, ProviderError, require_aiohttp

logger = logging.getLogger(__name__)


class MiniMaxProvider(LLMProvider):
    """MiniMax chat completion provider.

    Uses the MiniMax ``/v1/text/chatcompletion_v2`` endpoint.
    Requires ``MINIMAX_API_KEY`` and optionally ``MINIMAX_GROUP_ID``.
    """

    name = "minimax"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.group_id = self.extra.get("group_id") or ""
        if not self.base_url:
            self.base_url = "https://api.minimax.chat"
        self.base_url = self.base_url.rstrip("/")

    def normalize_model_name(self, model: str) -> str:
        return model if model else "abab6.5-chat"

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        aiohttp = require_aiohttp()
        url = f"{self.base_url}/v1/text/chatcompletion_v2"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.normalize_model_name(self.model),
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.0,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if self.group_id:
            payload["group_id"] = self.group_id

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(connector=self._connector(), timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise ProviderError(f"HTTP {resp.status}: {body[:500]}")
                data = await resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ProviderError("MiniMax response contained no choices")
                message = choices[0].get("message", {})
                content = message.get("content", "")
                if not content:
                    raise ProviderError("MiniMax response content was empty")
                usage = data.get("usage")
                if usage:
                    self.last_usage = self._usage(
                        prompt=usage.get("prompt_tokens"),
                        completion=usage.get("completion_tokens"),
                        total=usage.get("total_tokens"),
                    )
                return content
