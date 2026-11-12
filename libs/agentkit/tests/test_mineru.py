"""Tests for agentkit.mineru — 三档客户端，全程 fake transport / 临时文件，零网络。"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from agentkit.mineru import (
    FLASH_MAX_BYTES,
    MinerUAuthError,
    MinerUClient,
    MinerUError,
    MinerURateLimited,
    MinerUTaskFailed,
    RawResponse,
)


def _json(payload: dict, status: int = 200) -> RawResponse:
    return RawResponse(status=status, headers={"Content-Type": "application/json"}, body=json.dumps(payload).encode())


class FakeTransport:
    """按 (method, url-包含路径) 路由的脚本化 transport，记录全部调用。"""

    def __init__(self, routes: dict, markdown: bytes = b"# hello\n"):
        self.routes = routes
        self.calls: list = []
        self.markdown = markdown
        self.polls = 0

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body))
        for key, handler in self.routes.items():
            m, fragment = key
            if method == m and fragment in url:
                return handler(self, method, url, headers, body)
        return RawResponse(status=404, headers={}, body=b"{}")


def _flash_routes() -> dict:
    def submit(self, m, url, headers, body):
        return _json({"code": 0, "data": {"task_id": "t-flash", "file_url": "https://oss.example/up/t-flash"}})

    def put(self, m, url, headers, body):
        assert body.startswith(b"%PDF-fake"), "PUT 必须原样携带文件字节"
        return RawResponse(status=200, headers={}, body=b"")

    def poll(self, m, url, headers, body):
        self.polls += 1
        state = "running" if self.polls == 1 else "done"
        data = {"code": 0, "data": {"task_id": "t-flash", "state": state}}
        if state == "done":
            data["data"]["markdown_url"] = "https://cdn.example/full.md"
        return _json(data)

    def md(self, m, url, headers, body):
        payload = self.markdown if isinstance(self.markdown, bytes) else self.markdown.encode()
        return RawResponse(status=200, headers={"Content-Type": "text/markdown"}, body=payload)

    return {
        ("POST", "/parse/file"): submit,
        ("PUT", "oss.example/up"): put,
        ("GET", "/parse/t-flash"): poll,
        ("GET", "cdn.example/full.md"): md,
    }


def _zip_bytes(md: str = "# zip-md\n", with_images: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("demo/full.md", md)
        zf.writestr("demo/demo_content_list.json", json.dumps([{"type": "text"}]))
        if with_images:
            zf.writestr("demo/images/img_1.png", b"PNG")
    return buf.getvalue()


def _token_routes(zip_body: bytes | None = None) -> dict:
    def batch(self, m, url, headers, body):
        assert headers.get("Authorization") == "Bearer tk-test"
        payload = json.loads(body.decode())
        assert payload["files"][0]["name"].endswith(".pdf")
        assert payload["model_version"] == "vlm"
        return _json({"code": 0, "data": {"batch_id": "b1", "file_urls": ["https://oss.example/up/b1/0"]}})

    def put(self, m, url, headers, body):
        return RawResponse(status=200, headers={}, body=b"")

    def poll(self, m, url, headers, body):
        self.polls += 1
        state = "converting" if self.polls == 1 else "done"
        item = {"file_name": "demo.pdf", "state": state}
        if state == "done":
            item["full_zip_url"] = "https://cdn.example/result.zip"
        return _json({"code": 0, "data": {"batch_id": "b1", "extract_result": [item]}})

    def zfetch(self, m, url, headers, body):
        return RawResponse(status=200, headers={"Content-Type": "application/zip"}, body=zip_body or _zip_bytes())

    return {
        ("POST", "/file-urls/batch"): batch,
        ("PUT", "oss.example/up/b1"): put,
        ("GET", "/extract-results/batch/b1"): poll,
        ("GET", "cdn.example/result.zip"): zfetch,
    }


def _make_pdf(tmp_path: Path, size: int = 100) -> Path:
    p = tmp_path / "demo.pdf"
    p.write_bytes(b"%PDF-fake" + b" " * size)
    return p


class TestFlashFileFlow:
    def test_happy_path(self, tmp_path):
        fake = FakeTransport(_flash_routes(), markdown="# 图鉴页\n")
        client = MinerUClient(transport=fake, sleeper=lambda s: None, poll_interval=0)
        doc = client.parse_file(str(_make_pdf(tmp_path)), mode="flash")
        assert doc.mode == "flash"
        assert doc.markdown == "# 图鉴页\n"
        assert doc.task_id == "t-flash"
        methods = [c[0] for c in fake.calls]
        assert methods == ["POST", "PUT", "GET", "GET", "GET"]  # submit/upload/poll/poll/md

    def test_rate_limit_falls_through_in_auto(self, tmp_path):
        flash = _flash_routes()
        flash[("POST", "/parse/file")] = lambda s, m, u, h, b: RawResponse(status=429, headers={}, body=b"")
        fake = FakeTransport({**flash, **_token_routes()})
        client = MinerUClient(token="tk-test", transport=fake, sleeper=lambda s: None, poll_interval=0)
        doc = client.parse_file(str(_make_pdf(tmp_path)), mode="auto")
        assert doc.mode == "token"  # flash 429 → 落 token 档

    def test_task_failed_raises(self, tmp_path):
        routes = _flash_routes()
        routes[("GET", "/parse/t-flash")] = lambda s, m, u, h, b: _json(
            {"code": 0, "data": {"task_id": "t-flash", "state": "failed",
                                  "err_code": -30003, "err_msg": "page count exceeds"}}
        )
        client = MinerUClient(transport=FakeTransport(routes), sleeper=lambda s: None, poll_interval=0)
        with pytest.raises(MinerUTaskFailed) as exc_info:
            client.parse_file(str(_make_pdf(tmp_path)), mode="flash")
        assert exc_info.value.err_code == -30003


class TestTokenFileFlow:
    def test_zip_unpacks_md_content_list_images(self, tmp_path):
        fake = FakeTransport(_token_routes())
        client = MinerUClient(token="tk-test", transport=fake, sleeper=lambda s: None, poll_interval=0)
        doc = client.parse_file(str(_make_pdf(tmp_path)), mode="token")
        assert doc.mode == "token"
        assert doc.markdown == "# zip-md\n"
        assert doc.content_list_json is not None
        assert "demo/images/img_1.png" in doc.images
        assert doc.images["demo/images/img_1.png"] == b"PNG"

    def test_auth_error(self, tmp_path):
        routes = _token_routes()
        routes[("POST", "/file-urls/batch")] = lambda s, m, u, h, b: _json(
            {"code": "A0202", "msg": "Token 错误"}, status=200
        )
        client = MinerUClient(token="bad", transport=FakeTransport(routes), sleeper=lambda s: None)
        with pytest.raises(MinerUAuthError):
            client.parse_file(str(_make_pdf(tmp_path)), mode="token")

    def test_mode_unavailable_without_token(self, tmp_path):
        client = MinerUClient(transport=FakeTransport({}), sleeper=lambda s: None)
        with pytest.raises(MinerUError, match="MINERU_API_TOKEN"):
            client.parse_file(str(_make_pdf(tmp_path)), mode="token")


class TestAutoTiering:
    def test_oversize_skips_flash(self, tmp_path):
        routes = _token_routes()
        fake = FakeTransport(routes)
        client = MinerUClient(token="tk-test", transport=fake, sleeper=lambda s: None, poll_interval=0)
        big = tmp_path / "big.pdf"
        big.write_bytes(b"%PDF" + b" " * (FLASH_MAX_BYTES + 1))
        doc = client.parse_file(str(big), mode="auto")
        assert doc.mode == "token"
        assert not any("/parse/file" in c[1] for c in fake.calls), "超 10MB 不应请求 flash"

    def test_no_token_no_magicpdf_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("agentkit.mineru.importlib.util.find_spec", lambda name: None)
        client = MinerUClient(transport=FakeTransport({}), sleeper=lambda s: None)
        big = tmp_path / "big.pdf"
        big.write_bytes(b"%PDF" + b" " * (FLASH_MAX_BYTES + 1))
        with pytest.raises(MinerUError, match="no mineru tier"):
            client.parse_file(str(big), mode="auto")

    def test_local_tier_used_when_only_option(self, tmp_path, monkeypatch):
        """local 档：magic_pdf 懒加载失败时（未装）应抛 MinerUError 而非穿透。"""
        monkeypatch.setattr("agentkit.mineru.importlib.util.find_spec", lambda name: object())
        # import 仍会在 from magic_pdf... 处 ImportError → MinerUError
        client = MinerUClient(transport=FakeTransport({}), sleeper=lambda s: None)
        with pytest.raises(MinerUError, match="magic-pdf"):
            client.parse_file(str(_make_pdf(tmp_path)), mode="local")

    def test_modes_available(self):
        client = MinerUClient(token="tk", transport=FakeTransport({}))
        assert client.modes_available() == {"flash": True, "token": True, "local": False}


class TestLedgerAndMisc:
    def test_daily_ledger(self, tmp_path):
        fake = FakeTransport(_flash_routes())
        client = MinerUClient(
            transport=fake, sleeper=lambda s: None, poll_interval=0, state_dir=tmp_path
        )
        client.parse_file(str(_make_pdf(tmp_path)), mode="flash")
        usage = client.usage_today()
        assert usage.get("flash_tasks") == 1
        assert (tmp_path / "mineru_quota.json").exists()

    def test_parse_url_flash(self):
        def submit(self, m, url, headers, body):
            return _json({"code": 0, "data": {"task_id": "t-flash"}})

        routes = _flash_routes()
        routes[("POST", "/parse/url")] = submit
        fake = FakeTransport(routes, markdown="# url-md\n")
        client = MinerUClient(transport=fake, sleeper=lambda s: None, poll_interval=0)
        doc = client.parse_url("https://cdn.example/demo.pdf", mode="flash")
        assert doc.markdown == "# url-md\n"
