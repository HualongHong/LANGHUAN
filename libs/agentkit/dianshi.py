"""点石（Dianshi）化学数据 MCP 客户端（OpenDataLab / OpenXLab 体系）。

2026-11-12 端点行为实测（官方 MCP 页核对 + 本机无 token 探测）：

- 端点 ``POST {BASE}/api/mcp``（Next.js API route），Streamable HTTP：
  JSON-RPC 2.0，须带 ``Accept: application/json, text/event-stream``；
  响应可为纯 JSON 或 SSE 帧（``event: message`` + ``data: {...}``），
  本客户端两种都解析；
- 认证 ``Authorization: Bearer <SSO_TOKEN>``——OpenXLab SSO 会话换发，
  **无独立长期 API key**；浏览器登录后前端经 ``GET /api/mcp/client-config``
  （带会话 cookie）取内嵌 token 的完整 MCP client 配置，token 过期需重取；
- 工具面 14 个：物质检索 4（substance_search / get_by_id / similarity /
  substructure，RDKit）、反应检索 6、文献检索 2（``reference_search``
  PostgreSQL 全文检索 / ``reference_get_by_id`` 详情含 DOI/摘要/关系计数）、
  ``health_check``；每工具独立计配额，服务端对 limit 做钳制。

本客户端显式封装 ``health_check`` / ``reference_search`` /
``reference_get_by_id`` 三个文献向工具；其余物质/反应工具经通用
:meth:`DianshiClient.call_tool` 直通，入参 schema 以 tools/list 实测为准。

**fail-open 契约**（与 agentkit.sciverse 相同，平台是增强层不是依赖层）：
无凭据、网络错误、401/429/5xx、JSON-RPC error、工具 isError、响应解析
失败——一律记 WARN 并返回 ``None``，绝不抛穿调用方 pipeline。

凭据解析顺序：构造参数 → ``DIANSHI_SSO_TOKEN`` → ``DIANSHI_API_TOKEN``
（兼容别名）。无端点级配额记账：429 直接返回 None，由调用方退让或降级。
``reference_get_by_id`` 结果经 :class:`~agentkit.cache.CacheManager` 磁盘
缓存（TTL 7 天，信封内存 ``ts``），命中不发请求；检索类接口不缓存。

端点可用 ``DIANSHI_BASE_URL`` 覆盖（默认 https://dianshi.opendatalab.org.cn）。

纯标准库；HTTP 经可注入 transport（测试零网络）。
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional

from agentkit.cache import CacheManager
from agentkit.fetch import no_redirect_opener

logger = logging.getLogger(__name__)

BASE_URL = "https://dianshi.opendatalab.org.cn"
MCP_PATH = "/api/mcp"
ENV_TOKEN = "DIANSHI_SSO_TOKEN"
ENV_TOKEN_ALT = "DIANSHI_API_TOKEN"  # 兼容别名
ENV_BASE = "DIANSHI_BASE_URL"
CACHE_TTL_SECONDS = 7 * 24 * 3600

# Transport: (method, url, headers, body, timeout) -> RawResponse
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], "RawResponse"]

_rpc_ids = itertools.count(1)
_OPENER = no_redirect_opener()


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


def default_transport(
    method: str, url: str, headers: Mapping[str, str], body: Optional[bytes], timeout: float
) -> RawResponse:
    """urllib 诚实实现：不伪造 UA，3xx 原样返回。"""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as resp:
            return RawResponse(status=resp.status, headers=dict(resp.headers), body=resp.read())
    except urllib.error.HTTPError as exc:
        return RawResponse(status=exc.code, headers=dict(exc.headers or {}), body=exc.read() or b"")
    except urllib.error.URLError as exc:
        raise ConnectionError(f"network error: {exc}") from exc


def resolve_token(explicit: Optional[str] = None) -> Optional[str]:
    """凭据解析：参数 → DIANSHI_SSO_TOKEN → DIANSHI_API_TOKEN。"""
    if explicit:
        return explicit.strip() or None
    for env_name in (ENV_TOKEN, ENV_TOKEN_ALT):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return None


class DianshiClient:
    """点石 MCP JSON-RPC 客户端；全部方法 fail-open 返回 Optional。"""

    def __init__(
        self,
        token: Optional[str] = None,
        transport: Optional[Transport] = None,
        timeout: float = 30.0,
        cache: Optional["CacheManager"] = None,
        now: Callable[[], float] = time.time,
    ):
        self._token = resolve_token(token)
        self._transport = transport or default_transport
        self._timeout = timeout
        self._cache = cache
        self._now = now
        self._base = os.environ.get(ENV_BASE, BASE_URL).rstrip("/")
        self.last_error: Optional[str] = None  # 最近一次失败原因（诊断用）

    # ------------------------------------------------------------------ 状态

    @property
    def configured(self) -> bool:
        return bool(self._token)

    @property
    def endpoint(self) -> str:
        return f"{self._base}{MCP_PATH}"

    # ------------------------------------------------------------ 显式工具

    def tools_list(self) -> Optional[List[Dict[str, Any]]]:
        """列出已注册工具（官方文档健康检查第一步；计入工具配额）。"""
        out = self._rpc("tools/list", {})
        if out is None:
            return None
        tools = out.get("tools")
        return tools if isinstance(tools, list) else None

    def health_check(self) -> Optional[Dict[str, Any]]:
        """数据库连通性 + 核心表计数。"""
        return self.call_tool("health_check", {})

    def reference_search(self, query: str, limit: int = 10) -> Optional[Dict[str, Any]]:
        """PostgreSQL 全文文献检索；服务端对 limit 做钳制（建议 5-10 起步）。"""
        return self.call_tool("reference_search", {"query": query, "limit": limit})

    def reference_get_by_id(
        self, reference_id: Any, use_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """按点石内部 reference_id 取详情（DOI/摘要预览/关系计数）；7 天缓存。"""
        key = f"dianshi:reference:{reference_id}"
        if use_cache:
            cached = self._cache_get(key)
            if cached is not None:
                return cached
        out = self.call_tool("reference_get_by_id", {"reference_id": reference_id})
        if out is not None and use_cache:
            self._cache_set(key, out)
        return out

    # ------------------------------------------------------------ 通用直通

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """tools/call 通用入口（物质/反应等其余 11 个工具经此调用）。

        返回工具 JSON 文本载荷解析后的 dict；载荷非 JSON 时包成
        ``{"raw": <text>}``；工具层 isError 视为失败返回 None。
        """
        envelope = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if envelope is None:
            return None
        if envelope.get("isError"):
            text = self._content_text(envelope)
            self._fail(f"tool {name!r} isError: {text[:200]!r}")
            return None
        text = self._content_text(envelope)
        if not text:
            self._fail(f"tool {name!r} returned empty content")
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        return payload if isinstance(payload, dict) else {"raw": text}

    # ------------------------------------------------------------ JSON-RPC 层

    def _rpc(self, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """发一条 JSON-RPC 请求；返回 result 对象（fail-open）。"""
        payload = {
            "jsonrpc": "2.0",
            "id": next(_rpc_ids),
            "method": method,
            "params": params,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            raw = self._transport("POST", self.endpoint, headers, body, self._timeout)
        except (ConnectionError, OSError) as exc:
            self._fail(f"network: {exc}")
            return None
        if raw.status in (401, 403):
            self._fail(f"HTTP {raw.status} auth (SSO token missing/expired?)")
            return None
        if raw.status == 429:
            self._fail("HTTP 429 quota/rate limited")
            return None
        if not 200 <= raw.status < 300:
            self._fail(f"HTTP {raw.status}: {raw.body[:200]!r}")
            return None
        return self._decode_envelope(raw)

    def _decode_envelope(self, raw: RawResponse) -> Optional[Dict[str, Any]]:
        """解析响应体（纯 JSON 或 SSE 帧），返回 result 对象。"""
        text = raw.body.decode("utf-8", errors="replace")
        content_type = ""
        for key, value in raw.headers.items():
            if key.lower() == "content-type":
                content_type = str(value).lower()
                break
        if "text/event-stream" in content_type:
            text = self._sse_data(text)
            if text is None:
                self._fail("SSE response without data frame")
                return None
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            # 无 content-type 提示的兜底：按 SSE 帧解后再试一次
            sse = self._sse_data(text)
            if sse is None:
                self._fail(f"non-JSON response: {text[:120]!r}")
                return None
            try:
                doc = json.loads(sse)
            except json.JSONDecodeError as exc:
                self._fail(f"non-JSON response: {exc}")
                return None
        if not isinstance(doc, dict):
            self._fail("unexpected JSON payload (not an object)")
            return None
        error = doc.get("error")
        if isinstance(error, dict):
            self._fail(f"jsonrpc error {error.get('code')}: {error.get('message')}")
            return None
        result = doc.get("result")
        if not isinstance(result, dict):
            self._fail("envelope without result object")
            return None
        return result

    @staticmethod
    def _sse_data(text: str) -> Optional[str]:
        """从 SSE 帧提取 data 行拼接的负载（无帧返回 None）。"""
        lines = [ln[5:].lstrip() for ln in text.splitlines() if ln.startswith("data:")]
        return "\n".join(lines) if lines else None

    @staticmethod
    def _content_text(result: Dict[str, Any]) -> str:
        content = result.get("content")
        if not isinstance(content, list):
            return ""
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)

    # ---------------------------------------------------------------- 缓存

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        if self._cache is None:
            return None
        try:
            blob = self._cache.get(key)
        except Exception as exc:  # 缓存是增强层，损坏不阻断主流程
            logger.warning("dianshi cache read failed: %s", exc)
            return None
        if not blob:
            return None
        try:
            envelope = json.loads(blob)
            if self._now() - envelope.get("ts", 0) > CACHE_TTL_SECONDS:
                return None
            data = envelope.get("data")
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, AttributeError):
            return None

    def _cache_set(self, key: str, data: Dict[str, Any]) -> None:
        if self._cache is None:
            return
        try:
            envelope = json.dumps({"ts": self._now(), "data": data}, ensure_ascii=False)
            self._cache.set(key, envelope)
        except Exception as exc:
            logger.warning("dianshi cache write failed: %s", exc)

    def _fail(self, reason: str) -> None:
        self.last_error = reason
        logger.warning("dianshi call failed: %s", reason)


__all__ = [
    "BASE_URL",
    "CACHE_TTL_SECONDS",
    "ENV_TOKEN",
    "MCP_PATH",
    "RawResponse",
    "DianshiClient",
    "default_transport",
    "resolve_token",
]
