"""模型网关：双角色多模态模型调用的统一封装。

P1b 起传输/清洗/缓存由 agentkit.llm 承担（LLMClient + OpenAI 兼容 providers
+ clean=True）；本模块保留角色语义、重试策略与快照机制。

角色 profile 机制：角色名 → provider 名 + env 变量组（见 RoleProfile）。
默认 A=MiniMax（MINIMAX_*）、B=Qwen（QWEN_*），与历史行为一致；新增角色
只需 register_role(...) 注册 profile，不改网关代码。任一角色可用环境变量
APA_ROLE_<角色>_PROVIDER 覆盖 provider 名；取 agent/agent_bridge 时走宿主
工单队列（agentkit.bridge，kind 在 model_gateway.bridge 注册），无需 API key。

配置来自环境变量（由 python-dotenv 加载 .env）：
- 角色 A：MINIMAX_API_KEY / MINIMAX_BASE_URL / MINIMAX_MODEL
- 角色 B：QWEN_API_KEY / QWEN_BASE_URL / QWEN_MODEL
- 通用：MODEL_REQUEST_TIMEOUT_SECONDS（默认 120）、MODEL_MAX_RETRIES（默认 3）

缓存（默认关闭）：LLMClient(clean=True) 的缓存值是剥离 <think>/``` 围栏后
的文本，必须用独立 cache 文件。构造函数 cache_path 或环境变量
APA_LLM_CACHE_DB 指定独立 SQLite 文件（如 <out>/.apa_cache.db）后开启；
两者都未设置则不缓存（稳妥默认：不改变既有管道行为，复跑一律实调）。
bridge 模式下永不缓存（避免 collect 遍的占位符污染 apply 遍）。

快照：snapshot_dir/snapshots.jsonl 字段与追加语义不变（role/model/prompt 前
2000 字/image_count/response_text/ok/error）；P1b 起 response_text 记录的
是清洗后文本（原为供应商原始文本）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from agentkit.cache import CacheManager
from agentkit.llm.cleaning import strip_code_fence as _strip_code_fence
from agentkit.llm.cleaning import strip_think as _strip_reasoning
from agentkit.llm.sync import chat_sync

from .providers import BRIDGE_PROVIDER_NAMES, APALLMClient

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_RETRIES = 3

#: 缓存开关环境变量：值为独立 SQLite cache 文件路径（清洗后文本缓存）。
ENV_CACHE_DB = "APA_LLM_CACHE_DB"

#: 角色 provider 覆盖环境变量模板（如 APA_ROLE_A_PROVIDER=agent_bridge）。
_PROVIDER_ENV_TEMPLATE = "APA_ROLE_{role}_PROVIDER"

_JSON_MODE_HINT = "\n\n请只输出 JSON。"

_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")


class ModelGatewayError(Exception):
    """模型网关调用失败。

    status_code 为 HTTP 状态码（网络错误/JSON 解析失败时为 None）；
    retryable 标记该错误是否值得重试（网络错误、5xx、429、响应解析失败）。
    """

    def __init__(self, message: str, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class RoleProfile:
    """一个角色的供应商 profile：provider 名 + 凭据/端点/模型的环境变量组。

    Attributes:
        provider: agentkit 注册表中的 provider 名（agent/agent_bridge 走宿主
            工单队列）；可被 APA_ROLE_<角色>_PROVIDER 环境变量覆盖。
        api_key_env / base_url_env / model_env: 凭据、端点、模型名的环境变量名。
        default_base_url / default_model: 对应环境变量未设置时的默认值。
    """

    provider: str
    api_key_env: str
    base_url_env: str
    default_base_url: str
    model_env: str
    default_model: str | None = None


_ROLE_PROFILES: dict[str, RoleProfile] = {
    "A": RoleProfile(
        provider="minimax",
        api_key_env="MINIMAX_API_KEY",
        base_url_env="MINIMAX_BASE_URL",
        default_base_url="https://api.minimaxi.com/v1",
        model_env="MINIMAX_MODEL",
        default_model=None,
    ),
    "B": RoleProfile(
        provider="qwen",
        api_key_env="QWEN_API_KEY",
        base_url_env="QWEN_BASE_URL",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model_env="QWEN_MODEL",
        default_model="qwen-vl-max",
    ),
}


def register_role(role: str, profile: RoleProfile, *, override: bool = False) -> None:
    """注册一个角色 profile；同名需 override=True 才覆盖。

    Raises:
        ModelGatewayError: 角色名已存在且未指定 override。
    """
    key = role.strip().upper()
    if not override and key in _ROLE_PROFILES:
        raise ModelGatewayError(f"角色已注册: {role!r}（覆盖请用 override=True）")
    _ROLE_PROFILES[key] = profile


def registered_roles() -> tuple[str, ...]:
    """按注册顺序返回全部角色名。"""
    return tuple(_ROLE_PROFILES)


def _image_to_data_url(path: str) -> str:
    """读取图片文件并转为 base64 data URL（image/png）。"""
    data = Path(path).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


class _NullCache:
    """缓存关闭时的空实现（LLMClient 要求 cache 鸭子类型 get/set）。"""

    def get(self, content_hash: str) -> None:
        return None

    def set(self, content_hash: str, value: str) -> None:
        return None


_NULL_CACHE = _NullCache()


class ModelGateway:
    """同步模型网关（传输经 agentkit.llm，每次调用内部 asyncio.run）。

    snapshot_dir 存在时，每次 chat_vision 调用都会向
    snapshot_dir/snapshots.jsonl 追加一条快照记录。
    cache_path（或 APA_LLM_CACHE_DB）存在时启用独立 SQLite 内容缓存。
    """

    def __init__(self, snapshot_dir: str | None = None, cache_path: str | None = None):
        load_dotenv()
        self.snapshot_dir = Path(snapshot_dir) if snapshot_dir else None
        self.timeout = float(os.environ.get("MODEL_REQUEST_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))
        try:
            self.max_retries = int(os.environ.get("MODEL_MAX_RETRIES", DEFAULT_MAX_RETRIES))
        except ValueError:
            self.max_retries = DEFAULT_MAX_RETRIES
        self.max_retries = max(1, self.max_retries)
        resolved_cache = cache_path or os.environ.get(ENV_CACHE_DB) or None
        self._cache = CacheManager(resolved_cache) if resolved_cache else None
        self._clients: dict[tuple, APALLMClient] = {}

    def close(self) -> None:
        self._clients.clear()

    def __enter__(self) -> "ModelGateway":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _role_config(self, role: str) -> dict:
        """返回指定角色的供应商配置；缺少 API key 时抛 ModelGatewayError。"""
        profile = _ROLE_PROFILES.get(role)
        if profile is None:
            raise ModelGatewayError(
                f"未知角色: {role!r}（已注册角色: {', '.join(registered_roles())}）"
            )
        provider = (
            os.environ.get(_PROVIDER_ENV_TEMPLATE.format(role=role)) or profile.provider
        ).lower().strip()
        if provider in BRIDGE_PROVIDER_NAMES:
            # 宿主工单队列：无需真实凭据，端点无意义，模型名仅作工单标签。
            return {
                "provider": "agent_bridge",
                "api_key": os.environ.get(profile.api_key_env) or "agent-bridge",
                "base_url": None,
                "model": os.environ.get(profile.model_env) or profile.default_model or "host-agent",
            }
        api_key = os.environ.get(profile.api_key_env)
        if not api_key:
            raise ModelGatewayError(f"角色 {role} 未配置 API key：缺少环境变量 {profile.api_key_env}")
        model = os.environ.get(profile.model_env) or profile.default_model
        if not model:
            raise ModelGatewayError(f"角色 {role} 未配置模型名：缺少环境变量 {profile.model_env}")
        return {
            "provider": provider,
            "api_key": api_key,
            "base_url": (os.environ.get(profile.base_url_env) or profile.default_base_url).rstrip("/"),
            "model": model,
        }

    def _client_for(self, config: dict) -> APALLMClient:
        """按角色配置复用 agentkit LLMClient（clean=True，缓存按策略接入）。

        agentkit 的重试不区分可重试/不可重试（无 status_code 语义），故客户端
        max_retries=0，重试策略留在 chat_vision（保持 4xx 不重试的既有语义）。
        """
        key = (config["provider"], config["model"], config["base_url"])
        client = self._clients.get(key)
        if client is None:
            client_config = SimpleNamespace(
                provider=config["provider"],
                model=config["model"],
                api_key=config["api_key"],
                base_url=config["base_url"],
                request_timeout=max(1, int(self.timeout)),
                temperature=0.1,
                max_retries=0,
                retry_delay=1.0,
                fallback_providers=[],
                models={},
            )
            cache = _NULL_CACHE
            if self._cache is not None and config["provider"] not in BRIDGE_PROVIDER_NAMES:
                cache = self._cache
            client = APALLMClient(client_config, cache, clean=True)
            self._clients[key] = client
        return client

    @staticmethod
    def _map_transport_error(exc: Exception) -> ModelGatewayError:
        """把 agentkit 侧异常映射为带 status_code/retryable 语义的 ModelGatewayError。

        agentkit 的 ProviderError/LLMError 不带状态码；HTTP providers 的错误
        消息以 "HTTP <status>:" 开头，据此前缀还原状态码：429/5xx 可重试，
        其余 4xx 不可重试；无状态码（网络错误、解析失败等）一律可重试。
        """
        message = str(exc)
        match = _HTTP_STATUS_RE.search(message)
        if match:
            status_code = int(match.group(1))
            return ModelGatewayError(
                message,
                status_code=status_code,
                retryable=(status_code == 429 or status_code >= 500),
            )
        return ModelGatewayError(f"网络错误: {message}", retryable=True)

    def _post(self, config: dict, payload: dict) -> dict:
        """传输层：经 agentkit.llm 客户端完成一次 chat 调用，返回 OpenAI 风格响应体。

        保留为私有接缝方法便于测试 monkeypatch（与历史测试约定一致）；真实
        路径不再直接发 HTTP——传输/清洗/缓存均由 agentkit.llm 承担。
        失败时抛 ModelGatewayError（带 status_code / retryable）。
        """
        client = self._client_for(config)
        try:
            text = chat_sync(
                client,
                payload["messages"],
                response_format=payload.get("response_format"),
                temperature=payload.get("temperature"),
            )
        except Exception as exc:
            raise self._map_transport_error(exc) from exc
        return {"choices": [{"message": {"content": text}}]}

    @staticmethod
    def _backoff_sleep(attempt: int) -> None:
        """指数退避：第 attempt 次失败后休眠 2**attempt 秒。"""
        time.sleep(2 ** attempt)

    def _write_snapshot(self, record: dict) -> None:
        if self.snapshot_dir is None:
            return
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.snapshot_dir / "snapshots.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def chat_vision(
        self,
        role: str,
        prompt: str,
        images: list[str] | None = None,
        json_mode: bool = True,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> dict:
        """调用指定角色的多模态模型，返回解析后的 dict。

        - role: 已注册角色名（默认 "A"=MiniMax / "B"=Qwen）。
        - images: 图片文件路径列表，读文件转 base64 data URL（image/png）。
        - json_mode: 设 response_format={"type": "json_object"}，并在 prompt 追加提示。
        - 重试策略：网络错误、5xx、429、JSON 解析失败指数退避重试，
          最多 MODEL_MAX_RETRIES 次尝试；其余 4xx 不重试直接抛 ModelGatewayError。
        """
        config = self._role_config(role)

        if json_mode:
            prompt = prompt + _JSON_MODE_HINT

        content: list[dict] = [{"type": "text", "text": prompt}]
        for image_path in images or []:
            content.append({
                "type": "image_url",
                "image_url": {"url": _image_to_data_url(image_path)},
            })

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})

        payload: dict = {
            "model": config["model"],
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        snapshot = {
            "role": role,
            "model": config["model"],
            "prompt": prompt[:2000],
            "image_count": len(images or []),
            "response_text": None,
            "ok": False,
            "error": None,
        }

        last_error: ModelGatewayError | None = None
        for attempt in range(self.max_retries):
            try:
                body = self._post(config, payload)
                text = body["choices"][0]["message"]["content"]
                result = json.loads(_strip_code_fence(_strip_reasoning(text)))
                snapshot["response_text"] = text
                snapshot["ok"] = True
                self._write_snapshot(snapshot)
                return result
            except ModelGatewayError as exc:
                last_error = exc
                if not exc.retryable:
                    break
                if attempt < self.max_retries - 1:
                    self._backoff_sleep(attempt)
            except (KeyError, IndexError, TypeError) as exc:
                last_error = ModelGatewayError(f"响应结构异常: {exc}", retryable=True)
                if attempt < self.max_retries - 1:
                    self._backoff_sleep(attempt)
            except json.JSONDecodeError as exc:
                last_error = ModelGatewayError(f"模型输出不是合法 JSON: {exc}", retryable=True)
                if attempt < self.max_retries - 1:
                    self._backoff_sleep(attempt)

        assert last_error is not None
        snapshot["error"] = str(last_error)
        self._write_snapshot(snapshot)
        raise last_error
