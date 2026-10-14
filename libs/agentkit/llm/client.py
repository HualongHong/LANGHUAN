"""Generic async LLM client with caching, retries, fallback and usage stats.

Extracted from Lit-Scout-Harvester ``core/llm_client.py`` in LangHuan
Phase 2 P1a. Behavior is unchanged for existing callers; new capabilities
are opt-in:

- ``pool``: connection/thread pool object (duck-typed, needs
  ``async_pool.get_connector()`` and async ``close()``); ``None`` means
  one-off connectors per request.
- ``clean``: apply ``agentkit.llm.cleaning.clean_response`` to provider
  output before caching/returning (default ``False``).
- Usage stats (C6): after each successful provider call the client's
  in-memory counters accumulate ``provider.last_usage`` tokens when the
  vendor reports them; see :meth:`LLMClient.usage_stats`. An optional JSONL
  snapshot is appended per call when ``usage_log_path`` is given or
  ``AGENTKIT_LLM_USAGE_LOG`` is set (default off).

The ``config`` object is duck-typed (LSH's pydantic ``LLMConfig`` satisfies
it): provider, model, api_key, base_url, request_timeout, temperature,
max_retries, retry_delay, fallback_providers, models.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agentkit.llm.base import LLMProvider, ProviderError
from agentkit.llm.cleaning import clean_response
from agentkit.llm.registry import get_provider

logger = logging.getLogger(__name__)

#: Environment variable enabling the per-call usage JSONL snapshot.
ENV_USAGE_LOG = "AGENTKIT_LLM_USAGE_LOG"


class LLMError(Exception):
    """Raised when an LLM request fails after all retries and fallbacks."""


class LLMClient:
    """Async LLM client with content-hash caching and provider fallback.

    The client resolves a model profile from ``config.models`` when a module
    requests one; otherwise it uses the primary provider.
    """

    #: Provider factory; skills may override (classmethod/staticmethod) to
    #: route construction through their own registry (LSH does this to keep
    #: its bridge-provider subclass with the historical queue dir).
    _get_provider = staticmethod(get_provider)

    def __init__(
        self,
        config: Any,
        cache: Any,
        pool: Optional[Any] = None,
        clean: bool = False,
        usage_log_path: Optional[str] = None,
    ):
        self.config = config
        self.cache = cache
        self._pool = pool
        self._clean = clean
        self._providers: Dict[str, Any] = {}
        self._fallback_order: List[str] = []
        self._usage: Dict[str, Any] = {
            "calls": 0,
            "cache_hits": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "by_provider": {},
        }
        self._usage_log_path = usage_log_path or os.environ.get(ENV_USAGE_LOG) or None
        self._build_providers()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()

    def _build_providers(self) -> None:
        """Instantiate primary and fallback providers from config."""
        # Primary provider.
        self._providers["__primary__"] = self._get_provider(
            self.config.provider,
            model=self.config.model,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout=self.config.request_timeout,
            pool=self._pool,
        )

        # Named profiles from config.models.
        for name, profile in self.config.models.items():
            self._providers[name] = self._get_provider(
                profile.provider,
                model=profile.model,
                api_key=profile.api_key,
                base_url=profile.base_url,
                timeout=profile.request_timeout,
                pool=self._pool,
            )

        # Fallback order: explicit fallback_providers or all named profiles.
        order = self.config.fallback_providers or list(self.config.models.keys())
        self._fallback_order = [n for n in order if n in self._providers]

    def _request_hash(
        self,
        provider_name: str,
        model: str,
        payload: Dict[str, Any],
    ) -> str:
        """Return a SHA256 hash of the request payload for caching."""
        canonical = json.dumps(
            {"provider": provider_name, "model": model, **payload},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _resolve_provider(
        self,
        model_profile: Optional[str],
    ) -> List[Tuple[str, Any]]:
        """Return ordered (name, provider) tuples to try."""
        if model_profile and model_profile in self._providers:
            providers: List[Tuple[str, Any]] = [(model_profile, self._providers[model_profile])]
            for name in self._fallback_order:
                if name != model_profile:
                    providers.append((name, self._providers[name]))
            return providers
        return [("__primary__", self._providers["__primary__"])] + [
            (name, self._providers[name]) for name in self._fallback_order
        ]

    # -- usage statistics (C6) --------------------------------------------
    def _record_usage(
        self,
        provider_name: str,
        provider: Any,
        usage: Optional[Dict[str, int]],
    ) -> None:
        """Accumulate one successful provider call into the usage counters."""
        self._usage["calls"] += 1
        per = self._usage["by_provider"].setdefault(
            provider_name,
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        per["calls"] += 1
        if usage:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self._usage[key] += int(usage.get(key) or 0)
                per[key] += int(usage.get(key) or 0)
        if self._usage_log_path:
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "provider": provider_name,
                "model": getattr(provider, "model", ""),
                "usage": usage,
            }
            try:
                path = Path(self._usage_log_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:  # pragma: no cover - disk issues
                logger.warning("llm: cannot append usage log %s: %s", path, exc)

    def usage_stats(self) -> Dict[str, Any]:
        """Return a snapshot of accumulated usage counters.

        Shape: ``{"calls", "cache_hits", "prompt_tokens", "completion_tokens",
        "total_tokens", "by_provider": {name: {...}}}``. Token fields only
        grow when the vendor reports usage in its response.
        """
        return {
            **self._usage,
            "by_provider": {k: dict(v) for k, v in self._usage["by_provider"].items()},
        }

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        model_profile: Optional[str] = None,
    ) -> str:
        """Send a chat request and return the generated content string.

        Args:
            messages: OpenAI-style message list (content may be a string or
                multimodal content parts; passed through verbatim).
            response_format: Optional ``{"type": "json_object"}`` etc.
            temperature: Override the configured temperature.
            max_tokens: Override the configured max tokens.
            model_profile: Name of a model profile in ``config.models``.

        Returns:
            The content returned by the chosen provider.

        Raises:
            LLMError: If the request fails after all retries and fallbacks.
        """
        providers = self._resolve_provider(model_profile)
        last_error: Optional[Exception] = None

        for provider_name, provider in providers:
            model = provider.model
            payload: Dict[str, Any] = {
                "messages": messages,
                "temperature": temperature if temperature is not None else self.config.temperature,
            }
            if response_format:
                payload["response_format"] = response_format
            if max_tokens:
                payload["max_tokens"] = max_tokens

            cache_key = self._request_hash(provider_name, model, payload)
            cached = self.cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for provider=%s model=%s", provider_name, model)
                self._usage["cache_hits"] += 1
                return cached

            delay = self.config.retry_delay
            for attempt in range(self.config.max_retries + 1):
                try:
                    provider.last_usage = None
                    content = await provider.chat(
                        messages=messages,
                        response_format=response_format,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    if self._clean:
                        content = clean_response(content)
                    self.cache.set(cache_key, content)
                    self._record_usage(provider_name, provider, provider.last_usage)
                    return content
                except ProviderError as exc:
                    last_error = exc
                    logger.warning(
                        "Provider %s attempt %d/%d failed: %s",
                        provider_name,
                        attempt + 1,
                        self.config.max_retries + 1,
                        exc,
                    )
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(delay)
                        delay *= 2

        raise LLMError(
            f"LLM request failed after all providers and retries: {last_error}"
        )
