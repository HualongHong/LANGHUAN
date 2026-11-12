"""Tests for agentkit.sciverse — 六端点 fail-open 客户端，fake transport 零网络。"""

from __future__ import annotations

import json

import pytest

from agentkit.sciverse import RawResponse, SciverseClient, resolve_token


def _json(payload: dict, status: int = 200) -> RawResponse:
    return RawResponse(status=status, headers={"Content-Type": "application/json"}, body=json.dumps(payload).encode())


class FakeTransport:
    def __init__(self, handler):
        self._handler = handler
        self.calls: list = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        return self._handler(method, url, headers, body)


def _client(handler, **kwargs):
    return SciverseClient(token="sv-test", transport=FakeTransport(handler), **kwargs)


class TestSearchMetadata:
    def test_happy_path_posts_filters(self):
        seen = {}

        def handler(m, url, headers, body):
            seen["url"] = url
            seen["auth"] = headers.get("Authorization")
            payload = json.loads(body.decode())
            seen["payload"] = payload
            return _json({"results": [{"title": "T"}], "total_count": 1, "page": 1, "page_size": 25})

        client = _client(handler)
        out = client.search_metadata(
            query="transformer",
            filters=[{"field": "doi", "operator": "FILTER_OP_EQ", "value": "10.1/x"}],
            page_size=10,
        )
        assert out["total_count"] == 1
        assert seen["url"].endswith("/meta-search")
        assert seen["auth"] == "Bearer sv-test"
        assert seen["payload"]["filters"][0]["field"] == "doi"
        assert seen["payload"]["page_size"] == 10

    def test_auth_failure_returns_none(self):
        client = _client(lambda m, u, h, b: RawResponse(status=401, headers={}, body=b"{}"))
        assert client.search_metadata(query="x") is None
        assert "401" in client.last_error

    def test_rate_limit_returns_none(self):
        client = _client(lambda m, u, h, b: RawResponse(status=429, headers={}, body=b"{}"))
        assert client.semantic_search(query="x") is None
        assert "429" in client.last_error

    def test_bad_gateway_returns_none(self):
        client = _client(lambda m, u, h, b: RawResponse(status=502, headers={}, body=b"upstream"))
        assert client.list_catalog() is None


class TestSemanticAndRelations:
    def test_semantic_search_sends_mode(self):
        seen = {}

        def handler(m, url, headers, body):
            seen["payload"] = json.loads(body.decode())
            return _json({"hits": [{"chunk_id": "c1", "doc_id": "d1", "score": 0.9,
                                    "title": "T", "offset": 12, "chunk": "..."}]})

        client = _client(handler)
        out = client.semantic_search(query="attention", top_k=5, mode="quality")
        assert out["hits"][0]["doc_id"] == "d1"
        assert seen["payload"]["mode"] == "quality"

    def test_paper_relations_payload(self):
        seen = {}

        def handler(m, url, headers, body):
            seen["payload"] = json.loads(body.decode())
            return _json({"items": [], "total_count": 0, "page": 1, "page_size": 25})

        client = _client(handler)
        out = client.list_paper_relations("paper:10.1/x", "CITATIONS")
        assert out["total_count"] == 0
        assert seen["payload"]["relation"] == "CITATIONS"


class TestReadContentAndCache:
    def _handler_with_count(self):
        state = {"n": 0}

        def handler(m, url, headers, body):
            state["n"] += 1
            return _json({"text": "abc", "bytes_returned": 3, "next_offset": 3, "more": False})

        return handler, state

    def test_read_content_query_and_cache_hit(self, tmp_path):
        from agentkit.cache import CacheManager

        handler, state = self._handler_with_count()
        cache = CacheManager(tmp_path / "c.db")
        client = _client(handler, cache=cache)
        out1 = client.read_content("doc1", offset=0, limit=4096)
        out2 = client.read_content("doc1", offset=0, limit=4096)
        assert out1 == out2 == {"text": "abc", "bytes_returned": 3, "next_offset": 3, "more": False}
        assert state["n"] == 1, "第二次读取应命中缓存"

    def test_cache_ttl_expiry(self, tmp_path):
        from agentkit.cache import CacheManager

        handler, state = self._handler_with_count()
        cache = CacheManager(tmp_path / "c.db")
        clock = {"t": 1000.0}
        client = _client(handler, cache=cache, now=lambda: clock["t"])
        client.read_content("doc1")
        clock["t"] += 8 * 24 * 3600  # 8 天后过期
        client.read_content("doc1")
        assert state["n"] == 2

    def test_get_resource_roundtrip_and_path_guard(self, tmp_path):
        from agentkit.cache import CacheManager

        state = {"n": 0}

        def handler(m, url, headers, body):
            state["n"] += 1
            return RawResponse(status=200, headers={"Content-Type": "image/jpeg"}, body=b"JPEG")

        cache = CacheManager(tmp_path / "c.db")
        client = _client(handler, cache=cache)
        data, mime = client.get_resource("dt=x/p_1/f3.png")
        assert data == b"JPEG" and mime == "image/jpeg"
        again = client.get_resource("dt=x/p_1/f3.png")
        assert again == (b"JPEG", "image/jpeg")
        assert state["n"] == 1, "资源同样缓存"
        assert client.get_resource("../evil.png") is None
        assert client.get_resource("/abs.png") is None


class TestTokenResolution:
    def test_env_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SCIVERSE_API_KEY", "sv-env-key")
        monkeypatch.setenv("SCIVERSE_API_TOKEN", "sv-legacy")
        assert resolve_token() == "sv-env-key"
        monkeypatch.delenv("SCIVERSE_API_KEY")
        assert resolve_token() == "sv-legacy"

    def test_credentials_file_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SCIVERSE_API_KEY", raising=False)
        monkeypatch.delenv("SCIVERSE_API_TOKEN", raising=False)
        cred = tmp_path / "credentials.json"
        cred.write_text(json.dumps({"api_token": "sv-file"}))
        monkeypatch.setattr("agentkit.sciverse.CREDENTIALS_PATH", cred)
        assert resolve_token() == "sv-file"

    def test_unconfigured_client_fails_open(self):
        def handler(m, url, headers, body):
            return _json({"fields": []})

        client = SciverseClient(token="", transport=FakeTransport(handler))
        assert client.configured is False
        assert client.list_catalog() == {"fields": []}  # 无 token 也发请求（服务端 401 → None）
