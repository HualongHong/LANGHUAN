"""Anthropic Claude provider.

Extracted from Lit-Scout-Harvester ``core/llm_providers/claude_provider.py``
in LangHuan Phase 2 P1a; behavior unchanged. ``aiohttp`` is imported lazily.

Note: ``_convert_messages`` passes ``content`` through as-is (string expected);
multimodal content-parts messages are only exercised against the
OpenAI-compatible providers today.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agentkit.llm.base import LLMProvider, ProviderError, require_aiohttp

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    """Anthropic Messages API provider."""

    name = "claude"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if not self.base_url:
            self.base_url = "https://api.anthropic.com"
        self.base_url = self.base_url.rstrip("/")

    def normalize_model_name(self, model: str) -> str:
        return model if model else "claude-3-haiku-20240307"

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Convert OpenAI-style messages to Claude system/user/assistant format."""
        system = ""
        converted = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                converted.append({"role": msg["role"], "content": msg["content"]})
        result: Dict[str, Any] = {"messages": converted}
        if system:
            result["system"] = system
        return result

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        aiohttp = require_aiohttp()
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        payload: Dict[str, Any] = {
            "model": self.normalize_model_name(self.model),
            "max_tokens": max_tokens if max_tokens else 1024,
            "temperature": temperature if temperature is not None else 0.0,
        }
        payload.update(self._convert_messages(messages))

        # Claude does not natively support json_object response_format.
        # We append a JSON instruction to the last user message when needed.
        if response_format and response_format.get("type") == "json_object":
            payload["messages"][-1]["content"] += "\nPlease output valid JSON only."

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(connector=self._connector(), timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise ProviderError(f"HTTP {resp.status}: {body[:500]}")
                data = await resp.json()
                content_blocks = data.get("content", [])
                texts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
                content = "".join(texts)
                if not content:
                    raise ProviderError("Claude response content was empty")
                usage = data.get("usage")
                if usage:
                    self.last_usage = self._usage(
                        prompt=usage.get("input_tokens"),
                        completion=usage.get("output_tokens"),
                    )
                return content
