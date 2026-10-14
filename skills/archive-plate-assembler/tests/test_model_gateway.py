"""model_gateway 单元测试：不发起真实网络请求，全部通过 monkeypatch _post 模拟。"""

from __future__ import annotations

import json

import pytest

import archive_plate_assembler.model_gateway.gateway as gateway_module
from archive_plate_assembler.model_gateway import (
    ModelGateway,
    ModelGatewayError,
    MockGateway,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """为两个角色配置假密钥；禁止 load_dotenv 读取真实 .env；跳过真实退避等待。"""
    monkeypatch.setattr(gateway_module, "load_dotenv", lambda: None)
    monkeypatch.setenv("MINIMAX_API_KEY", "fake-minimax-key")
    monkeypatch.setenv("MINIMAX_MODEL", "fake-minimax-model")
    monkeypatch.setenv("QWEN_API_KEY", "fake-qwen-key")
    monkeypatch.setenv("QWEN_MODEL", "fake-qwen-model")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "3")
    monkeypatch.setattr(ModelGateway, "_backoff_sleep", staticmethod(lambda attempt: None))


def _ok_body(payload: dict) -> dict:
    """构造 OpenAI 兼容的正常响应体。"""
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


# ---------------------------------------------------------------------------
# 1. 正常 JSON 响应解析（含 ```json 围栏剥离）
# ---------------------------------------------------------------------------

def test_chat_vision_parses_plain_json(monkeypatch):
    monkeypatch.setattr(ModelGateway, "_post",
                        lambda self, config, payload: _ok_body({"answer": 2}))
    gw = ModelGateway()
    assert gw.chat_vision("A", "1+1?") == {"answer": 2}


def test_chat_vision_strips_code_fence(monkeypatch):
    body = {"choices": [{"message": {"content": "```json\n{\"answer\": 2}\n```"}}]}
    monkeypatch.setattr(ModelGateway, "_post", lambda self, config, payload: body)
    gw = ModelGateway()
    assert gw.chat_vision("B", "1+1?") == {"answer": 2}


def test_chat_vision_payload_shape(monkeypatch, tmp_path):
    """验证 payload 结构：json_mode、图片 data URL、prompt 追加提示。"""
    captured = {}

    def fake_post(self, config, payload):
        captured["payload"] = payload
        captured["config"] = config
        return _ok_body({"ok": True})

    monkeypatch.setattr(ModelGateway, "_post", fake_post)
    png = tmp_path / "page.png"
    png.write_bytes(b"\x89PNG fake")

    gw = ModelGateway()
    gw.chat_vision("B", "描述此图", images=[str(png)], system="你是助手")

    payload = captured["payload"]
    assert payload["model"] == "fake-qwen-model"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    content = payload["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"].endswith("请只输出 JSON。")
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert captured["config"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_chat_vision_no_json_mode(monkeypatch):
    captured = {}

    def fake_post(self, config, payload):
        captured["payload"] = payload
        return _ok_body({"ok": True})

    monkeypatch.setattr(ModelGateway, "_post", fake_post)
    gw = ModelGateway()
    gw.chat_vision("A", "你好", json_mode=False)
    payload = captured["payload"]
    assert "response_format" not in payload
    assert payload["messages"][0]["content"][0]["text"] == "你好"


# ---------------------------------------------------------------------------
# 2. 429 后重试成功
# ---------------------------------------------------------------------------

def test_retry_after_429(monkeypatch):
    attempts = {"n": 0}

    def flaky_post(self, config, payload):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ModelGatewayError("HTTP 429", status_code=429, retryable=True)
        return _ok_body({"answer": 2})

    monkeypatch.setattr(ModelGateway, "_post", flaky_post)
    gw = ModelGateway()
    assert gw.chat_vision("A", "1+1?") == {"answer": 2}
    assert attempts["n"] == 2


def test_retry_exhausted_raises(monkeypatch):
    def always_429(self, config, payload):
        raise ModelGatewayError("HTTP 429", status_code=429, retryable=True)

    monkeypatch.setattr(ModelGateway, "_post", always_429)
    gw = ModelGateway()
    with pytest.raises(ModelGatewayError):
        gw.chat_vision("A", "1+1?")


def test_retry_on_content_json_decode_error(monkeypatch):
    attempts = {"n": 0}

    def bad_then_good(self, config, payload):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return {"choices": [{"message": {"content": "这不是JSON"}}]}
        return _ok_body({"answer": 2})

    monkeypatch.setattr(ModelGateway, "_post", bad_then_good)
    gw = ModelGateway()
    assert gw.chat_vision("A", "1+1?") == {"answer": 2}
    assert attempts["n"] == 2


# ---------------------------------------------------------------------------
# 3. 400 不重试直接抛错
# ---------------------------------------------------------------------------

def test_400_not_retried(monkeypatch):
    attempts = {"n": 0}

    def bad_request(self, config, payload):
        attempts["n"] += 1
        raise ModelGatewayError("HTTP 400", status_code=400, retryable=False)

    monkeypatch.setattr(ModelGateway, "_post", bad_request)
    gw = ModelGateway()
    with pytest.raises(ModelGatewayError) as exc_info:
        gw.chat_vision("A", "1+1?")
    assert exc_info.value.status_code == 400
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# 4. 缺 key 抛 ModelGatewayError
# ---------------------------------------------------------------------------

def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    gw = ModelGateway()
    with pytest.raises(ModelGatewayError, match="QWEN_API_KEY"):
        gw.chat_vision("B", "1+1?")


def test_unknown_role_raises():
    gw = ModelGateway()
    with pytest.raises(ModelGatewayError, match="未知角色"):
        gw.chat_vision("C", "1+1?")


# ---------------------------------------------------------------------------
# 5. 快照 jsonl 追加写入
# ---------------------------------------------------------------------------

def test_snapshot_written_on_success_and_failure(monkeypatch, tmp_path):
    def good_post(self, config, payload):
        return _ok_body({"answer": 2})

    monkeypatch.setattr(ModelGateway, "_post", good_post)
    snap_dir = tmp_path / "snaps"
    gw = ModelGateway(snapshot_dir=str(snap_dir))
    gw.chat_vision("A", "1+1?")

    def bad_post(self, config, payload):
        raise ModelGatewayError("HTTP 400", status_code=400, retryable=False)

    monkeypatch.setattr(ModelGateway, "_post", bad_post)
    with pytest.raises(ModelGatewayError):
        gw.chat_vision("B", "会失败")

    lines = (snap_dir / "snapshots.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["role"] == "A"
    assert first["model"] == "fake-minimax-model"
    assert first["ok"] is True
    assert first["error"] is None
    assert first["image_count"] == 0
    assert json.loads(first["response_text"]) == {"answer": 2}

    second = json.loads(lines[1])
    assert second["role"] == "B"
    assert second["ok"] is False
    assert "HTTP 400" in second["error"]


# ---------------------------------------------------------------------------
# 6. MockGateway
# ---------------------------------------------------------------------------

def test_mock_gateway_substring_match():
    mock = MockGateway({"海豚": {"name": "海豚"}, "贝类": {"name": "贝类"}})
    assert mock.chat_vision("A", "请提取这段关于海豚的文字") == {"name": "海豚"}
    assert mock.chat_vision("B", "识别图中的贝类") == {"name": "贝类"}


def test_mock_gateway_no_match_raises():
    mock = MockGateway({"海豚": {"name": "海豚"}})
    with pytest.raises(KeyError):
        mock.chat_vision("A", "完全不相关的内容")


def test_mock_gateway_records_calls():
    mock = MockGateway({"x": {"ok": True}})
    mock.chat_vision("A", "包含 x 的 prompt", images=["a.png", "b.png"])
    mock.chat_vision("B", "另一个 x prompt")
    assert len(mock.calls) == 2
    assert mock.calls[0] == {"role": "A", "prompt": "包含 x 的 prompt", "image_count": 2}
    assert mock.calls[1] == {"role": "B", "prompt": "另一个 x prompt", "image_count": 0}


def test_mock_gateway_callable_fixtures():
    mock = MockGateway(lambda prompt: {"echo": prompt})
    assert mock.chat_vision("A", "hello") == {"echo": "hello"}


def test_strip_reasoning_block():
    """MiniMax-M3 等推理模型输出的 <think> 块必须被剥离后再解析 JSON。"""
    from archive_plate_assembler.model_gateway.gateway import (
        _strip_code_fence, _strip_reasoning)
    import json
    text = '<think>用户问 1+1……</think>\n\n```json\n{"answer": 2}\n```'
    assert json.loads(_strip_code_fence(_strip_reasoning(text))) == {"answer": 2}
