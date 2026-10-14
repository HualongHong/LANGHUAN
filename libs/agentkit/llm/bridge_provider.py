"""Generic agent-bridge LLM provider: the host agent answers jobs from disk.

Extracted/generalized from Lit-Scout-Harvester
``core/llm_providers/agent_bridge.py`` in LangHuan Phase 2 P1a. This provider
composes ``agentkit.bridge.BridgeQueue``; **domain kinds are registered by the
consuming skill** (``agentkit.bridge.register_kind``), not here.

Skills keeping a historical default queue directory subclass and override
``default_queue_dir`` (LSH uses ``.lit_scout_llm``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from agentkit.bridge import DEFAULT_QUEUE_DIR, BridgeQueue, MissingAnswerError
from agentkit.llm.base import LLMProvider, ProviderError


class AgentBridgeProvider(LLMProvider):
    """LLM provider backed by an on-disk request/answer queue."""

    name = "agent_bridge"

    #: Fallback queue directory when neither the ``queue_dir`` extra nor the
    #: ``AGENTKIT_LLM_DIR`` env var is set. Subclass to keep a skill's
    #: historical directory.
    default_queue_dir: str = DEFAULT_QUEUE_DIR

    def __init__(
        self,
        model: str = "host-agent",
        api_key: str = "",
        base_url: Optional[str] = None,
        timeout: int = 120,
        pool: Optional[Any] = None,
        **kwargs: Any,
    ):
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            pool=pool,
            **kwargs,
        )
        self._queue = BridgeQueue(
            model=self.model,
            queue_dir=self.extra.get("queue_dir"),
            mode=self.extra.get("mode"),
            default_queue_dir=self.default_queue_dir,
        )

    @property
    def queue_dir(self) -> Path:
        return self._queue.queue_dir

    @property
    def mode(self) -> str:
        return self._queue.mode

    @property
    def requests_path(self) -> Path:
        return self._queue.requests_path

    @property
    def missing_jobs(self) -> List[str]:
        return self._queue.missing_jobs

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Serve one request from the answer store, recording it when unanswered.

        Raises:
            ProviderError: in ``strict`` mode when the job has no answer yet.
        """
        self._queue.model = self.model
        try:
            return self._queue.handle_chat(
                messages,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except MissingAnswerError as exc:
            raise ProviderError(str(exc)) from exc
