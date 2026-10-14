"""Abstract base class for LLM providers (skill-agnostic, pure stdlib).

Extracted from Lit-Scout-Harvester ``core/llm_providers/base.py`` in LangHuan
Phase 2 P1a. ``aiohttp`` is **not** imported here: HTTP provider modules pull
it in lazily via :func:`require_aiohttp`, so importing the provider registry
never fails on hosts without the optional ``[llm]`` extra installed.

Message bodies are passed through verbatim — ``content`` may be a plain
string or an OpenAI multimodal content-parts list; providers must not assume
``str``.

凭据接缝（D7）：构造时 ``api_key`` 为空则经
``agentkit.credentials.get_provider().resolve("<name>:api_key")`` 解析——宿主
注册 credential broker 时走 broker，否则 fail-open 回落 ``.env`` 路径
（``EnvCredentialProvider`` 与技能现状读同一个 ``os.environ``，行为等价）。
显式传入的 key 永不被覆盖；resolve Miss 时保持原值（报错语义维持现状）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from agentkit.credentials import get_provider as _get_credential_provider


class ProviderError(Exception):
    """Raised when an LLM provider request fails."""


def require_aiohttp():
    """Import ``aiohttp`` lazily, raising a clear error when it is missing.

    HTTP providers call this at request time so that module import stays
    stdlib-only. Install the optional extra: ``pip install aiohttp>=3.9``
    (or ``langhuan-agentkit[llm]``).
    """
    try:
        import aiohttp
    except ImportError as exc:  # pragma: no cover - depends on host env
        raise ProviderError(
            "aiohttp is required for HTTP LLM providers; "
            "install it with `pip install 'aiohttp>=3.9'` "
            "(optional extra: langhuan-agentkit[llm])."
        ) from exc
    return aiohttp


class LLMProvider(ABC):
    """Abstract base class for a single LLM vendor."""

    name: str = ""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
        timeout: int = 120,
        pool: Optional[Any] = None,
        **kwargs: Any,
    ):
        if not api_key:
            # 凭据接缝（D7）：仅当未显式配置 key 时经 credential provider
            # 链解析（broker 优先、env 回落）；Miss 保持原值，行为与现状一致。
            resolved = _get_credential_provider().resolve(f"{self.name}:api_key")
            if resolved is not None:
                api_key = resolved
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.pool = pool
        self.extra = kwargs
        # Token usage of the most recent successful ``chat`` call, normalized
        # to {"prompt_tokens", "completion_tokens", "total_tokens"} when the
        # vendor reports it; ``None`` otherwise. Read/reset by the client.
        self.last_usage: Optional[Dict[str, int]] = None

    def _connector(self):
        if self.pool is not None:
            return self.pool.async_pool.get_connector()
        return None

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Send a chat request and return the generated content string."""
        raise NotImplementedError

    def normalize_model_name(self, model: str) -> str:
        """Normalize a user-facing model name to the provider's canonical name."""
        return model

    @property
    def max_batch_size(self) -> int:
        """Return a vendor-specific recommended batch size (records per request)."""
        return 50

    @staticmethod
    def _usage(
        prompt: Optional[int] = None,
        completion: Optional[int] = None,
        total: Optional[int] = None,
    ) -> Dict[str, int]:
        """Build a normalized usage record, deriving ``total`` when absent."""
        if total is None:
            total = (prompt or 0) + (completion or 0)
        return {
            "prompt_tokens": int(prompt or 0),
            "completion_tokens": int(completion or 0),
            "total_tokens": int(total or 0),
        }
