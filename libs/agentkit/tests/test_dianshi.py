"""Tests for agentkit.dianshi — MCP JSON-RPC fail-open 客户端，fake transport 零网络。"""

from __future__ import annotations

import json

import pytest

from agentkit.dianshi import DianshiClient, RawResponse, resolve_token


def _json(payload: dict, status: int = 200, content_type: str = "application/json") -> RawResponse:
    return RawResponse(status=status, headers={"Content-Type": content_type}, body=json.dumps(payload).encode())


def _tool_result(text: str, is_error: bool = False) -> RawResponse:
    return _json({"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": text}], "isError": is_error,
    }})


class FakeTransport:
    def __init__(self, handler):
        self._handler = handler
        self.calls: list = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        return self._handler(method, url, headers, body)


def _client(handler, **kwargs):
    return DianshiClient(token="sso-test-token", transport=FakeTransport(handler), **kwargs)


class TestEnvelopeAndAuth:
    def test_jsonrpc_payload_shape(self):
        seen = {}

        def handler(m, url, headers, body):
            seen["url"] = url
            seen["auth"] = headers.get("Authorization")
            seen["accept"] = headers.get("Accept")
            payload = json.loads(body.decode())
            seen["method"] = payload["method"]
            seen["params"] = payload["params"]
            assert payload["jsonrpc"] == "2.0"
            assert isinstance(payload["id"], int)
            return _tool_result('{"references": []}')

        client = _client(handler)
        out = client.reference_search("catalysis", limit=5)
        assert out == {"references": []}
        assert seen["url"] == "https://dianshi.opendatalab.org.cn/api/mcp"
        assert seen["auth"] == "Bearer sso-test-token"
        assert "text/event-stream" in seen["accept"]
        assert seen["method"] == "tools/call"
        assert seen["params"]["name"] == "reference_search"
        assert seen["params"]["arguments"] == {"query": "catalysis", "limit": 5}

    def test_sse_response_parsing(self):
        body = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\\"ok\\": true}"}],"isError":false}}\n'
            "\n"
        )
        raw = RawResponse(status=200, headers={"Content-Type": "text/event-stream"}, body=body.encode())
        client = _client(lambda m, u, h, b: raw)
        assert client.health_check() == {"ok": True}

    def test_sse_without_content_type_heuristic(self):
        body = 'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{}"}],"isError":false}}\n'
        raw = RawResponse(status=200, headers={}, body=body.encode())
        client = _client(lambda m, u, h, b: raw)
        assert client.health_check() == {}

    def test_tools_list_returns_tools(self):
        raw = _json({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "health_check"}]}})
        client = _client(lambda m, u, h, b: raw)
        assert client.tools_list() == [{"name": "health_check"}]


class TestFailOpen:
    def test_401_returns_none(self):
        client = _client(lambda m, u, h, b: RawResponse(status=401, headers={}, body=b"{}"))
        assert client.reference_search("x") is None
        assert "401" in client.last_error

    def test_429_returns_none(self):
        client = _client(lambda m, u, h, b: RawResponse(status=429, headers={}, body=b"{}"))
        assert client.health_check() is None
        assert "429" in client.last_error

    def test_502_returns_none(self):
        client = _client(lambda m, u, h, b: RawResponse(status=502, headers={}, body=b"upstream"))
        assert client.health_check() is None

    def test_network_error_returns_none(self):
        def handler(m, u, h, b):
            raise ConnectionError("network error: boom")

        client = _client(handler)
        assert client.reference_search("x") is None
        assert "network" in client.last_error

    def test_jsonrpc_error_returns_none(self):
        raw = _json({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "method not found"}})
        client = _client(lambda m, u, h, b: raw)
        assert client.tools_list() is None
        assert "-32601" in client.last_error

    def test_tool_is_error_returns_none(self):
        client = _client(lambda m, u, h, b: _tool_result("bad smiles", is_error=True))
        assert client.health_check() is None
        assert "isError" in client.last_error

    def test_non_json_tool_text_wrapped_raw(self):
        client = _client(lambda m, u, h, b: _tool_result("plain text payload"))
        assert client.health_check() == {"raw": "plain text payload"}

    def test_envelope_without_result(self):
        raw = _json({"jsonrpc": "2.0", "id": 1})
        client = _client(lambda m, u, h, b: raw)
        assert client.health_check() is None


class TestReferenceGetByIdCache:
    def _handler_with_count(self):
        state = {"n": 0}

        def handler(m, url, headers, body):
            state["n"] += 1
            return _tool_result('{"reference_id": 42, "doi": "10.1/x"}')

        return handler, state

    def test_cache_hit_within_ttl(self, tmp_path):
        from agentkit.cache import CacheManager

        handler, state = self._handler_with_count()
        cache = CacheManager(tmp_path / "c.db")
        client = _client(handler, cache=cache)
        out1 = client.reference_get_by_id(42)
        out2 = client.reference_get_by_id(42)
        assert out1 == out2 == {"reference_id": 42, "doi": "10.1/x"}
        assert state["n"] == 1, "第二次读取应命中缓存"

    def test_cache_ttl_expiry(self, tmp_path):
        from agentkit.cache import CacheManager

        handler, state = self._handler_with_count()
        cache = CacheManager(tmp_path / "c.db")
        clock = {"t": 1000.0}
        client = _client(handler, cache=cache, now=lambda: clock["t"])
        client.reference_get_by_id(42)
        clock["t"] += 8 * 24 * 3600  # 8 天后
        client.reference_get_by_id(42)
        assert state["n"] == 2, "TTL 过期后应重新请求"


class TestResolveToken:
    def test_explicit_wins(self, monkeypatch):
        monkeypatch.setenv("DIANSHI_SSO_TOKEN", "env-token")
        assert resolve_token("explicit-token") == "explicit-token"

    def test_env_primary_then_alias(self, monkeypatch):
        monkeypatch.delenv("DIANSHI_SSO_TOKEN", raising=False)
        monkeypatch.setenv("DIANSHI_API_TOKEN", "alias-token")
        assert resolve_token() == "alias-token"

    def test_unconfigured(self, monkeypatch):
        monkeypatch.delenv("DIANSHI_SSO_TOKEN", raising=False)
        monkeypatch.delenv("DIANSHI_API_TOKEN", raising=False)
        client = DianshiClient()
        assert client.configured is False
