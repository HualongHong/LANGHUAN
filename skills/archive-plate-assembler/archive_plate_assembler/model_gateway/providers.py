"""APA 的 agentkit.llm provider 适配层（shim，参照 LSH llm_providers 写法）。

为什么需要本模块：agentkit.llm 自带的 HTTP providers 在请求期依赖可选的
aiohttp，而 APA 的既有部署基线是 httpx（pyproject 已声明）。这里提供一个
httpx 驱动的 OpenAI 兼容 provider，并经 ``LLMClient._get_provider``
（agentkit 预留的技能侧覆盖点）把 OpenAI 兼容族（openai / qwen / minimax /
kimi / deepseek / openai_compatible）路由到它；``agent`` / ``agent_bridge``
路由到 APA 的宿主工单 provider（model_gateway.bridge），其余名字回落
agentkit 全局注册表。不改动 agentkit 的 PROVIDER_REGISTRY。

注意：chat 是 async 接口但内部用同步 httpx（APA 管道严格串行，无并发
需求）；每次请求新建 httpx.Client，不留持久连接。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from agentkit.llm.base import LLMProvider, ProviderError
from agentkit.llm.client import LLMClient
from agentkit.llm.registry import get_provider

from .bridge import AgentBridgeProvider

#: 路由到 httpx OpenAI 兼容 provider 的注册名。minimax 也在其中：APA 一直
#: 走 MiniMax 的 OpenAI 兼容端点（api.minimaxi.com/v1/chat/completions），
#: 而不是 agentkit MiniMaxProvider 的 chatcompletion_v2 端点。
_HTTPX_COMPAT_NAMES = frozenset({
    "openai_compatible",
    "openai",
    "deepseek",
    "kimi",
    "moonshot",
    "qwen",
    "dashscope",
    "minimax",
})

#: 宿主工单队列 provider 名。
BRIDGE_PROVIDER_NAMES = frozenset({"agent", "agent_bridge"})


class HttpxOpenAICompatibleProvider(LLMProvider):
    """httpx 驱动的 OpenAI ``/chat/completions`` provider（多模态 content parts 原样透传）。

    非 200 响应抛 ``ProviderError("HTTP <status>: ...")``，网络/解析错误抛
    不带状态码的 ProviderError——网关层据此前缀映射 retryable/status_code。
    """

    name = "openai_compatible_httpx"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if not self.base_url:
            raise ProviderError("base_url is required for OpenAI-compatible providers")
        self.base_url = self.base_url.rstrip("/")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
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
        try:
            with httpx.Client() as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"network error: {exc}") from exc

        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise ProviderError(f"response was not valid JSON: {exc}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("LLM response contained no choices")
        content = (choices[0].get("message") or {}).get("content") or ""
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


def get_apa_provider(name: str, **kwargs: Any) -> LLMProvider:
    """APA 的 provider 路由：OpenAI 兼容族 → httpx provider；agent/agent_bridge →
    APA 工单队列 provider；其余回落 agentkit 注册表。"""
    key = name.lower().strip()
    if key in _HTTPX_COMPAT_NAMES:
        return HttpxOpenAICompatibleProvider(**kwargs)
    if key in BRIDGE_PROVIDER_NAMES:
        return AgentBridgeProvider(**kwargs)
    return get_provider(name, **kwargs)


class APALLMClient(LLMClient):
    """把 provider 构造路由到 :func:`get_apa_provider` 的 LLMClient。"""

    _get_provider = staticmethod(get_apa_provider)


__all__ = [
    "APALLMClient",
    "BRIDGE_PROVIDER_NAMES",
    "HttpxOpenAICompatibleProvider",
    "get_apa_provider",
]
