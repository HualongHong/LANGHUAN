"""Sciverse 科研证据数据客户端（OpenDataLab 体系 Sci-Base 层）。

封装六个官方工具端点（openapi.yaml v1.0.1 核对，2026-11-12）：

- :func:`SciverseClient.list_catalog` —— ``GET /meta-catalog`` 字段/算子/枚举发现；
- :func:`SciverseClient.search_metadata` —— ``POST /meta-search`` 结构化元数据检索
  （papers/authors/sources/patents 四集合，filters/sort/cursor 分页）；
- :func:`SciverseClient.semantic_search` —— ``POST /agentic-search`` 语义片段检索
  （mode: fast/balanced/quality）；
- :func:`SciverseClient.list_paper_relations` —— ``POST /meta-paper-relations``
  引用/被引/相关工作分页；
- :func:`SciverseClient.read_content` —— ``GET /content`` 按 Unicode 码点区间读原文；
- :func:`SciverseClient.get_resource` —— ``GET /resource`` 取文献内图片字节。

**fail-open 契约**（平台是增强层不是依赖层）：无凭据、网络错误、401/429/5xx、
响应解析失败 —— 一律记 WARN 并返回 ``None``，绝不抛穿调用方 pipeline。
LSH 的 enrichment 据 ``None`` 跳过该记录，行为与平台不可用时一致。

凭据解析顺序：构造参数 → ``SCIVERSE_API_KEY`` → ``SCIVERSE_API_TOKEN`` →
``~/.sciverse/credentials.json``（官方 CLI 落盘格式，兼容 api_token/token
两种键）。无端点级配额记账：429 直接返回 None，由调用方决定退让或降级。

``read_content`` / ``get_resource`` 结果经 :class:`~agentkit.cache.CacheManager`
磁盘缓存（TTL 7 天，信封内存 ``ts``），命中不发请求；检索类接口不缓存（保新鲜）。

端点可用 ``SCIVERSE_BASE_URL`` 覆盖（默认 https://api.sciverse.space）。

纯标准库；HTTP 经可注入 transport（测试零网络）。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple

from agentkit.cache import CacheManager
from agentkit.fetch import no_redirect_opener

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sciverse.space"
ENV_KEY = "SCIVERSE_API_KEY"
ENV_TOKEN = "SCIVERSE_API_TOKEN"  # 官方 SDK 环境变量名，作兼容回落
ENV_BASE = "SCIVERSE_BASE_URL"
CREDENTIALS_PATH = Path.home() / ".sciverse" / "credentials.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600
_OPENER = no_redirect_opener()

# Transport: (method, url, headers, body, timeout) -> RawResponse
Transport = Callable[[str, str, Mapping[str, str], Optional[bytes], float], "RawResponse"]


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
    """凭据解析：参数 → SCIVERSE_API_KEY → SCIVERSE_API_TOKEN → credentials.json。"""
    if explicit:
        return explicit.strip() or None
    for env_name in (ENV_KEY, ENV_TOKEN):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    try:
        doc = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(doc, dict):
        for key in ("api_token", "token", "access_token"):
            value = str(doc.get(key) or "").strip()
            if value:
                return value
    return None


class SciverseClient:
    """Sciverse Sci-Base 六端点客户端；全部方法 fail-open 返回 Optional。"""

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

    # ------------------------------------------------------------ 六端点

    def list_catalog(
        self,
        collection: str = "papers",
        include_sample_values: bool = False,
    ) -> Optional[Dict]:
        query = urllib.parse.urlencode(
            {"collection": collection, "include_sample_values": str(include_sample_values).lower()}
        )
        return self._get_json(f"{self._base}/meta-catalog?{query}")

    def search_metadata(
        self,
        collection: str = "papers",
        query: Optional[str] = None,
        filters: Optional[list] = None,
        sort: Optional[list] = None,
        fields: Optional[list] = None,
        page: int = 1,
        page_size: int = 25,
        cursor: Optional[str] = None,
        freshness_boost: Optional[str] = None,
        impact_boost: Optional[str] = None,
        language_affinity: Optional[str] = None,
    ) -> Optional[Dict]:
        payload: Dict[str, object] = {"collection": collection, "page": page, "page_size": page_size}
        if query:
            payload["query"] = query
        if filters:
            payload["filters"] = filters
        if sort:
            payload["sort"] = sort
        if fields:
            payload["fields"] = fields
        if cursor:
            payload["cursor"] = cursor
        for key, value in (("freshness_boost", freshness_boost),
                           ("impact_boost", impact_boost),
                           ("language_affinity", language_affinity)):
            if value:
                payload[key] = value
        return self._post_json(f"{self._base}/meta-search", payload)

    def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        mode: str = "balanced",
        filters: Optional[Dict] = None,
    ) -> Optional[Dict]:
        payload: Dict[str, object] = {"query": query, "top_k": top_k, "mode": mode}
        if filters:
            payload["filters"] = filters
        return self._post_json(f"{self._base}/agentic-search", payload)

    def list_paper_relations(
        self,
        unique_id: str,
        relation: str,
        page: int = 1,
        page_size: int = 25,
    ) -> Optional[Dict]:
        payload = {"unique_id": unique_id, "relation": relation, "page": page, "page_size": page_size}
        return self._post_json(f"{self._base}/meta-paper-relations", payload)

    def read_content(
        self,
        doc_id: str,
        offset: int = 0,
        limit: int = 4096,
        collection: str = "papers",
    ) -> Optional[Dict]:
        cache_key = f"sciverse:content:{collection}:{doc_id}:{offset}:{limit}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        query = urllib.parse.urlencode(
            {"collection": collection, "doc_id": doc_id, "offset": offset, "limit": limit}
        )
        result = self._get_json(f"{self._base}/content?{query}")
        if result is not None:
            self._cache_set(cache_key, result)
        return result

    def get_resource(self, file_name: str) -> Optional[Tuple[bytes, str]]:
        """取文献图片字节；返回 (bytes, mime) 或 None。禁止路径穿越入参。"""
        if file_name.startswith("/") or ".." in file_name or "\\" in file_name:
            self.last_error = f"invalid resource path: {file_name!r}"
            logger.warning("sciverse get_resource rejected path: %s", file_name)
            return None
        cache_key = f"sciverse:resource:{file_name}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            try:
                raw = base64.b64decode(cached["b64"], validate=True)
            except (KeyError, TypeError, ValueError):
                # 缓存可损坏：忽略坏条目并回源，不让增强层中断主管线。
                self._fail(f"invalid cached resource payload: {file_name}")
            else:
                return raw, cached.get("mime", "image/png")
        query = urllib.parse.urlencode({"file_name": file_name})
        raw = self._get_bytes(f"{self._base}/resource?{query}")
        if raw is None:
            return None
        mime = "image/png"
        for key, value in raw[1].items():
            if key.lower() == "content-type":
                mime = str(value)
                break
        self._cache_set(cache_key, {"b64": base64.b64encode(raw[0]).decode("ascii"), "mime": mime})
        return raw[0], mime

    # -------------------------------------------------------------- HTTP 层

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, url: str, body: Optional[bytes]) -> Optional[RawResponse]:
        headers = self._headers()
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            raw = self._transport(method, url, headers, body, self._timeout)
        except (ConnectionError, OSError) as exc:
            self._fail(f"network: {exc}")
            return None
        if raw.status in (401, 403):
            self._fail(f"HTTP {raw.status} auth (token missing/invalid?)")
            return None
        if raw.status == 429:
            self._fail("HTTP 429 quota/rate limited")
            return None
        if not 200 <= raw.status < 300:
            self._fail(f"HTTP {raw.status}: {raw.body[:200]!r}")
            return None
        return raw

    def _get_json(self, url: str) -> Optional[Dict]:
        raw = self._request("GET", url, None)
        if raw is None:
            return None
        return self._decode_json(raw.body)

    def _post_json(self, url: str, payload: Dict) -> Optional[Dict]:
        raw = self._request("POST", url, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if raw is None:
            return None
        return self._decode_json(raw.body)

    def _get_bytes(self, url: str) -> Optional[Tuple[bytes, Mapping[str, str]]]:
        raw = self._request("GET", url, None)
        if raw is None:
            return None
        return raw.body, raw.headers

    def _decode_json(self, body: bytes) -> Optional[Dict]:
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            self._fail(f"non-JSON response: {exc}")
            return None
        if not isinstance(payload, dict):
            self._fail("unexpected JSON payload (not an object)")
            return None
        return payload

    # ---------------------------------------------------------------- 缓存

    def _cache_get(self, key: str) -> Optional[Dict]:
        if self._cache is None:
            return None
        try:
            blob = self._cache.get(key)
        except Exception as exc:  # 缓存是增强层，损坏不阻断主流程
            logger.warning("sciverse cache read failed: %s", exc)
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

    def _cache_set(self, key: str, data: Dict) -> None:
        if self._cache is None:
            return
        try:
            envelope = json.dumps({"ts": self._now(), "data": data}, ensure_ascii=False)
            self._cache.set(key, envelope)
        except Exception as exc:
            logger.warning("sciverse cache write failed: %s", exc)

    def _fail(self, reason: str) -> None:
        self.last_error = reason
        logger.warning("sciverse call failed: %s", reason)


__all__ = [
    "BASE_URL",
    "CACHE_TTL_SECONDS",
    "CREDENTIALS_PATH",
    "RawResponse",
    "SciverseClient",
    "default_transport",
    "resolve_token",
]
