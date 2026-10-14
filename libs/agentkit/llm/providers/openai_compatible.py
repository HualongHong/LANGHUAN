"""OpenAI-compatible chat provider used by DeepSeek, OpenAI, Kimi, Qwen.

Extracted from Lit-Scout-Harvester ``core/llm_providers/openai_compatible.py``
in LangHuan Phase 2 P1a; behavior unchanged. ``aiohttp`` is imported lazily at
request time (see ``agentkit.llm.base.require_aiohttp``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agentkit.llm.base import LLMProvider, ProviderError, require_aiohttp

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider(LLMProvider):
    """Provider for vendors exposing the OpenAI ``/chat/completions`` endpoint."""

    name = "openai_compatible"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if not self.base_url:
            raise ProviderError("base_url is required for OpenAI-compatible providers")
        # Ensure base_url has no trailing slash.
        self.base_url = self.base_url.rstrip("/")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        aiohttp = require_aiohttp()
        payload: Dict[str, Any] = {
            "model": self.normalize_model_name(self.model),
            "messages": messages,
            "temperature": temperature if temperature is not None else 0.0,
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(connector=self._connector(), timeout=timeout) as session:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise ProviderError(f"HTTP {resp.status}: {body[:500]}")
                data = await resp.json()
                choices = data.get("choices", [])
                if not choices:
                    raise ProviderError("LLM response contained no choices")
                content = choices[0].get("message", {}).get("content", "")
                if not content:
                    raise ProviderError("LLM response content was empty")
                usage = data.get("usage")
                if usage:
                    self.last_usage = self._usage(
                        prompt=usage.get("prompt_tokens"),
                        completion=usage.get("completion_tokens"),
                        total=usage.get("total_tokens"),
                    )
                return content


class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"

    def normalize_model_name(self, model: str) -> str:
        return model if model else "deepseek-chat"


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"

    def normalize_model_name(self, model: str) -> str:
        return model if model else "gpt-3.5-turbo"


class KimiProvider(OpenAICompatibleProvider):
    """Moonshot Kimi uses an OpenAI-compatible API."""

    name = "kimi"

    def normalize_model_name(self, model: str) -> str:
        return model if model else "moonshot-v1-8k"

    @property
    def max_batch_size(self) -> int:
        return 30


class QwenProvider(OpenAICompatibleProvider):
    """Alibaba Qwen uses an OpenAI-compatible API via DashScope."""

    name = "qwen"

    def normalize_model_name(self, model: str) -> str:
        return model if model else "qwen-turbo"

    @property
    def max_batch_size(self) -> int:
        return 30
