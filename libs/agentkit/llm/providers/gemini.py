"""Google Gemini provider.

Uses the Gemini REST API so that no extra SDK dependency is required.
Extracted from Lit-Scout-Harvester ``core/llm_providers/gemini_provider.py``
in LangHuan Phase 2 P1a; behavior unchanged. ``aiohttp`` is imported lazily.

Note: ``_convert_messages`` wraps ``content`` as a single text part; multimodal
content-parts messages are only supported by the OpenAI-compatible providers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agentkit.llm.base import LLMProvider, ProviderError, require_aiohttp

logger = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    """Gemini REST API provider."""

    name = "gemini"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if not self.base_url:
            self.base_url = "https://generativelanguage.googleapis.com"
        self.base_url = self.base_url.rstrip("/")

    def normalize_model_name(self, model: str) -> str:
        return model if model else "gemini-1.5-flash"

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert OpenAI-style messages to Gemini contents."""
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        return contents

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        aiohttp = require_aiohttp()
        model = self.normalize_model_name(self.model)
        url = (
            f"{self.base_url}/v1beta/models/{model}:generateContent"
            f"?key={self.api_key}"
        )
        headers = {"Content-Type": "application/json"}
        contents = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature if temperature is not None else 0.0,
            },
        }
        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if response_format and response_format.get("type") == "json_object":
            payload["generationConfig"]["responseMimeType"] = "application/json"

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(connector=self._connector(), timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise ProviderError(f"HTTP {resp.status}: {body[:500]}")
                data = await resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ProviderError("Gemini response contained no candidates")
                parts = candidates[0].get("content", {}).get("parts", [])
                texts = [p.get("text", "") for p in parts]
                content = "".join(texts)
                if not content:
                    raise ProviderError("Gemini response content was empty")
                usage = data.get("usageMetadata")
                if usage:
                    self.last_usage = self._usage(
                        prompt=usage.get("promptTokenCount"),
                        completion=usage.get("candidatesTokenCount"),
                        total=usage.get("totalTokenCount"),
                    )
                return content
