"""模型运营商注册表 —— 两段式标注架构的 provider 集中管理（纯标准库）。

背景：LSH 的两段式标注架构中，阶段 A（option_set_builder）用生成式 LLM
遍历语料构造选项集，阶段 B（decision_select）用决策模型在选项内选择并输出
概率排序（依赖 logprobs）。本模块以 JSON（config/providers.json）为唯一持久
层，集中管理「标注模型 / 选项决策模型」两个角色槽分别使用哪个运营商入口。

设计要点：
- 纯标准库（json / dataclasses / os / re），不依赖 pyyaml / streamlit，
  方便在任意 venv 下单测；对 pipeline YAML 的改写是文本级手术，只动
  ``llm:`` 顶层段内受管的键，其余行（含注释）逐字保留。
- 密钥红线：本模块只经 ``api_key_env`` 环境变量名引用 key，api_key 本体
  永不入库；``build_provider_config`` 只从 ``os.environ`` 现取（缺省 None）。
- 向后兼容：角色槽为空时不写不改；annotation 落到 llm 主段（等价旧行为），
  decision 落到 ``llm.models.decision`` 命名 profile（模块经
  ``model_profile: "decision"`` 引用）。
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field, fields as dataclass_fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

TYPE_OPENAI_COMPATIBLE = "openai_compatible"
TYPE_OPENAI_COMPATIBLE_DECISION = "openai_compatible_decision"
TYPE_JEV_LEGACY = "jev_legacy"
TYPE_SSH_TUNNEL = "ssh_tunnel"
TYPE_MODEL_DEPLOYMENT = "model_deployment"
PROVIDER_TYPES = (
    TYPE_OPENAI_COMPATIBLE,
    TYPE_OPENAI_COMPATIBLE_DECISION,
    TYPE_JEV_LEGACY,
    TYPE_SSH_TUNNEL,
    TYPE_MODEL_DEPLOYMENT,
)

ROLE_ANNOTATION = "annotation"
ROLE_DECISION = "decision"
ROLES = (ROLE_ANNOTATION, ROLE_DECISION)

# decision 角色写入 llm.models 时使用的 profile 名（模块侧 model_profile 引用同名）。
DECISION_PROFILE_NAME = "decision"

DEFAULT_ANNOTATION_PROVIDER = "deepseek"
DEFAULT_DECISION_PROVIDER = "deepseek"

# llm_providers.PROVIDER_REGISTRY 中已登记的厂商名；命中则 build_provider_config
# 直接以 entry.id 作为 provider 名，否则回退通用 openai_compatible 构造器。
_VENDOR_PROVIDER_IDS = frozenset(
    {
        "deepseek",
        "openai",
        "kimi",
        "moonshot",
        "qwen",
        "dashscope",
        "minimax",
        "gemini",
        "claude",
        "anthropic",
        "agent_bridge",
        "agent",
    }
)

_PATH_LIKE = Union[str, Path]


class RegistryError(Exception):
    """provider 注册表相关错误（角色未配置、指向缺失、YAML 无 llm 段等）。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ProviderEntry:
    """单个运营商入口。api_key_env 只存环境变量名，永不存 key 本体。

    ssh_* / remote_* / local_port 仅 type=ssh_tunnel 时有意义：描述
    ``ssh -L`` 端口转发的连接参数（HTTP 层面仍按 openai_compatible 驱动，
    type 区分是为了隧道生命周期管理与 UI 分组）。
    """

    id: str = ""
    operator: str = ""  # 显示名
    type: str = TYPE_OPENAI_COMPATIBLE
    base_url: str = ""
    api_key_env: str = ""  # 环境变量名（如 DEEPSEEK_API_KEY）
    ssh_host: str = ""  # ssh_tunnel：SSH 服务器地址
    ssh_port: int = 22  # ssh_tunnel：SSH 端口
    ssh_user: str = ""  # ssh_tunnel：SSH 登录用户
    ssh_key_path: str = ""  # ssh_tunnel：私钥路径（空 = 走默认密钥/ssh-agent）
    remote_host: str = ""  # ssh_tunnel：转发目标地址（远端侧 OpenAI 端点 host）
    remote_port: int = 0  # ssh_tunnel：转发目标端口
    local_port: int = 0  # ssh_tunnel：本地监听端口（base_url 指向 127.0.0.1:<local_port>）
    models: List[str] = field(default_factory=list)
    enabled: bool = False
    supports_logprobs: bool = False  # decision 角色要求 True
    reserved: bool = False  # 预留不可用入口（原厂下线/未对接）
    note: str = ""  # 中文备注


@dataclass
class ProviderRegistry:
    """运营商列表 + 两个角色槽（存 provider id）。"""

    providers: List[ProviderEntry] = field(default_factory=list)
    annotation_provider: Optional[str] = None
    decision_provider: Optional[str] = None

    def resolve_provider(self, role: str) -> ProviderEntry:
        """按角色槽解析出 ProviderEntry；未配置/指向缺失给出清晰错误。"""
        if role == ROLE_ANNOTATION:
            slot = self.annotation_provider
        elif role == ROLE_DECISION:
            slot = self.decision_provider
        else:
            raise RegistryError(f"未知角色: {role!r}（可选 {ROLE_ANNOTATION} / {ROLE_DECISION}）")
        if not slot:
            raise RegistryError(f"角色 {role} 未配置 provider")
        for entry in self.providers:
            if entry.id == slot:
                return entry
        known = ", ".join(e.id for e in self.providers) or "（无）"
        raise RegistryError(f"角色 {role} 指向的 provider '{slot}' 不存在（现有: {known}）")

    def get(self, provider_id: str) -> Optional[ProviderEntry]:
        for entry in self.providers:
            if entry.id == provider_id:
                return entry
        return None


# ---------------------------------------------------------------------------
# 预置运营商（出厂内容，与 config/providers.json 一致）
# ---------------------------------------------------------------------------


def _presets() -> List[ProviderEntry]:
    return [
        ProviderEntry(
            id="deepseek",
            operator="DeepSeek",
            type=TYPE_OPENAI_COMPATIBLE,
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            models=["deepseek-chat", "deepseek-reasoner"],
            enabled=True,
            supports_logprobs=True,
            note="标注 + 决策均可用（logprobs 已验证）",
        ),
        ProviderEntry(
            id="minimax",
            operator="MiniMax",
            type=TYPE_OPENAI_COMPATIBLE,
            base_url="https://api.minimaxi.com/v1",
            api_key_env="MINIMAX_API_KEY",
            models=[],
            enabled=False,
            note="接入后填实际模型名",
        ),
        ProviderEntry(
            id="qwen",
            operator="阿里云百炼",
            type=TYPE_OPENAI_COMPATIBLE,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="QWEN_API_KEY",
            models=["qwen-max", "qwen-plus"],
            enabled=False,
            note="阿里云百炼 OpenAI 兼容端点",
        ),
        ProviderEntry(
            id="tencent",
            operator="腾讯混元",
            type=TYPE_OPENAI_COMPATIBLE,
            base_url="https://api.lkeap.cloud.tencent.com/v1",
            api_key_env="TENCENT_API_KEY",
            models=["hunyuan-pro"],
            enabled=False,
            reserved=True,
            note="当前不可访问，入口保留",
        ),
        ProviderEntry(
            id="jev_original",
            operator="JEV 原厂",
            type=TYPE_JEV_LEGACY,
            base_url="https://jev.invalid",
            api_key_env="JEV_API_KEY",
            models=[],
            enabled=False,
            reserved=True,
            note="原厂服务已不可访问，保留入口待恢复",
        ),
        ProviderEntry(
            id="shanghai_sc_llm",
            operator="上海超算云平台·LLM",
            type=TYPE_OPENAI_COMPATIBLE,
            base_url="https://llm.shsc.cn/v1",
            api_key_env="SHANGHAI_SC_API_KEY",
            models=[],
            enabled=False,
            note="基座大模型，接入后填实际地址与模型名",
        ),
        ProviderEntry(
            id="shanghai_sc_jev",
            operator="上海超算·类JEV决策模型",
            type=TYPE_OPENAI_COMPATIBLE_DECISION,
            base_url="https://llm.shsc.cn/v1",
            api_key_env="SHANGHAI_SC_API_KEY",
            models=[],
            enabled=False,
            supports_logprobs=True,
            note="类 JEV 决策模型：选项内选择 + 概率输出",
        ),
        ProviderEntry(
            id="ssh_selfhost",
            operator="自建服务器（SSH 隧道）",
            type=TYPE_SSH_TUNNEL,
            base_url="http://127.0.0.1:8377/v1",
            api_key_env="SELFHOST_API_KEY",
            ssh_host="",
            ssh_port=22,
            ssh_user="",
            ssh_key_path="",
            remote_host="",
            remote_port=0,
            local_port=8377,
            models=[],
            enabled=False,
            supports_logprobs=True,
            note="ssh -L 隧道转发到自建 OpenAI 兼容端点；用 scripts/model_tunnel.py 管理隧道",
        ),
        ProviderEntry(
            id="aliyun_model_deploy",
            operator="阿里云百炼·模型部署",
            type=TYPE_MODEL_DEPLOYMENT,
            base_url="https://<deployment-id>.<region>.aliyuncs.com/compatible-mode/v1",
            api_key_env="ALIYUN_DEPLOY_API_KEY",
            models=[],
            enabled=False,
            supports_logprobs=True,
            note="阿里云百炼模型部署专用端点（见帮助文档），地址按部署实例替换",
        ),
        ProviderEntry(
            id="scnet_model_deploy",
            operator="scnet.cn 超算·模型部署",
            type=TYPE_MODEL_DEPLOYMENT,
            base_url="https://<scnet-endpoint>/v1",
            api_key_env="SCNET_DEPLOY_API_KEY",
            models=[],
            enabled=False,
            supports_logprobs=True,
            note="scnet.cn 超算 model-deploy 专用端点，地址与 key 按平台分配填写",
        ),
    ]


PRESETS: List[ProviderEntry] = _presets()
PRESET_IDS = tuple(e.id for e in PRESETS)


def default_registry() -> ProviderRegistry:
    """出厂注册表：10 个预置运营商 + 角色槽默认 DeepSeek。"""
    return ProviderRegistry(
        providers=copy.deepcopy(PRESETS),
        annotation_provider=DEFAULT_ANNOTATION_PROVIDER,
        decision_provider=DEFAULT_DECISION_PROVIDER,
    )


def preset_entry(preset_id: str) -> ProviderEntry:
    """按 id 取预置条目的深拷贝（供「从预置添加」使用）。"""
    for entry in PRESETS:
        if entry.id == preset_id:
            return copy.deepcopy(entry)
    raise RegistryError(f"未知预置运营商: {preset_id!r}（可选: {', '.join(PRESET_IDS)}）")


# ---------------------------------------------------------------------------
# JSON 持久化
# ---------------------------------------------------------------------------

_ENTRY_FIELD_NAMES = tuple(f.name for f in dataclass_fields(ProviderEntry))


def _entry_to_dict(entry: ProviderEntry) -> Dict[str, Any]:
    return {name: getattr(entry, name) for name in _ENTRY_FIELD_NAMES}


def _entry_from_dict(data: Dict[str, Any]) -> ProviderEntry:
    known = {k: v for k, v in data.items() if k in _ENTRY_FIELD_NAMES}
    if known.get("models") is None:
        known["models"] = []
    return ProviderEntry(**known)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_registry(path: _PATH_LIKE) -> ProviderRegistry:
    """从 JSON 加载注册表；文件缺失时返回出厂默认，字段缺省回退。"""
    path = Path(path)
    if not path.exists():
        return default_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"无法解析 providers.json: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("providers.json 顶层必须是 JSON 对象")
    providers = data.get("providers") or []
    return ProviderRegistry(
        providers=[_entry_from_dict(p) for p in providers if isinstance(p, dict)],
        annotation_provider=data.get("annotation_provider") or None,
        decision_provider=data.get("decision_provider") or None,
    )


def save_registry(path: _PATH_LIKE, registry: ProviderRegistry) -> None:
    """原子写（临时文件 + os.replace）；字段顺序稳定，round-trip 逐字节一致。"""
    path = Path(path)
    data = {
        "version": "1.0",
        "annotation_provider": registry.annotation_provider,
        "decision_provider": registry.decision_provider,
        "providers": [_entry_to_dict(e) for e in registry.providers],
    }
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate(registry: ProviderRegistry) -> List[str]:
    """返回错误列表（空 = 通过）。只查结构，不联网、不读 key 值。"""
    errors: List[str] = []
    ids = [e.id for e in registry.providers]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errors.append(f"provider id 重复: {dup}")
    for entry in registry.providers:
        if entry.enabled:
            if not entry.base_url:
                errors.append(f"'{entry.id}' 已启用但 base_url 为空")
            if not entry.api_key_env:
                errors.append(f"'{entry.id}' 已启用但 api_key_env 为空")
            if entry.type == TYPE_SSH_TUNNEL:
                if not entry.ssh_host:
                    errors.append(f"'{entry.id}' 已启用（ssh_tunnel）但 ssh_host 为空")
                if not entry.ssh_user:
                    errors.append(f"'{entry.id}' 已启用（ssh_tunnel）但 ssh_user 为空")
                if not entry.local_port:
                    errors.append(f"'{entry.id}' 已启用（ssh_tunnel）但 local_port 未填写")
    for role, slot in (
        (ROLE_ANNOTATION, registry.annotation_provider),
        (ROLE_DECISION, registry.decision_provider),
    ):
        if not slot:
            continue
        entry = registry.get(slot)
        if entry is None:
            errors.append(f"角色 {role} 指向不存在的 provider: {slot}")
            continue
        if role == ROLE_DECISION and not entry.supports_logprobs:
            errors.append(
                f"决策角色 provider '{slot}' 不支持 logprobs（阶段 B 概率输出必需）"
            )
    return errors


# ---------------------------------------------------------------------------
# provider 构造参数
# ---------------------------------------------------------------------------


def provider_name_for(entry: ProviderEntry) -> str:
    """entry → llm_providers.PROVIDER_REGISTRY 可解析的 provider 名。

    ssh_tunnel / model_deployment 在 HTTP 层面都按 openai_compatible 驱动
    （type 区分仅供隧道生命周期管理与 UI 分组），jev_legacy 走专属构造器。
    """
    if entry.type == TYPE_JEV_LEGACY:
        return TYPE_JEV_LEGACY
    if entry.type in (TYPE_SSH_TUNNEL, TYPE_MODEL_DEPLOYMENT):
        return TYPE_OPENAI_COMPATIBLE
    if entry.id in _VENDOR_PROVIDER_IDS:
        return entry.id
    return TYPE_OPENAI_COMPATIBLE


def primary_model(entry: ProviderEntry) -> str:
    return entry.models[0] if entry.models else ""


def build_provider_config(
    entry: ProviderEntry, role: Optional[str] = None
) -> Dict[str, Any]:
    """组装可直接喂给现有 llm_providers 构造器的参数字典。

    api_key 从 os.environ[entry.api_key_env] 现取，缺省 None（不落盘、不回读文件）。
    decision 角色额外标注 requires_logprobs=True。
    ssh_tunnel / model_deployment 附 transport 标记（"ssh_tunnel" / "direct"）
    供消费方参考——HTTP 调用本身均按 openai_compatible 路径发起。
    """
    if role is None:
        role = ROLE_DECISION if entry.type == TYPE_OPENAI_COMPATIBLE_DECISION else ROLE_ANNOTATION
    if role not in ROLES:
        raise RegistryError(f"未知角色: {role!r}（可选 {ROLE_ANNOTATION} / {ROLE_DECISION}）")
    config: Dict[str, Any] = {
        "provider": provider_name_for(entry),
        "model": primary_model(entry),
        "api_key": os.environ.get(entry.api_key_env) if entry.api_key_env else None,
        "base_url": entry.base_url,
        "role": role,
        "supports_logprobs": entry.supports_logprobs,
        "api_key_env": entry.api_key_env,
    }
    if entry.type == TYPE_SSH_TUNNEL:
        config["transport"] = "ssh_tunnel"
    elif entry.type == TYPE_MODEL_DEPLOYMENT:
        config["transport"] = "direct"
    if role == ROLE_DECISION:
        config["requires_logprobs"] = True
    return config


# ---------------------------------------------------------------------------
# 写入 pipeline YAML（文本级手术，仅动 llm: 段内受管键）
# ---------------------------------------------------------------------------

_TOP_LEVEL_KEY_RE = re.compile(r"^[^\s#][^:]*:(\s|$)")
_ENV_REF_TEMPLATE = "${%s}"


def _quote_yaml(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _split_trailing_comment(line: str) -> "tuple[str, str]":
    """拆出行尾注释（`` #...``，引号外），返回 (代码部分, 注释含前导空格)。"""
    in_quote = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == "#" and not in_quote and i > 0 and line[i - 1] in (" ", "\t"):
            return line[:i].rstrip(), line[i - 1 :]
    return line.rstrip(), ""


def _set_block_scalar(body: List[str], key: str, value: str, indent: int = 2) -> List[str]:
    """在 body（llm 段内容行）中设置 ``key: "value"``；存在则原位替换，缺失则追加。

    只匹配指定缩进，不会误伤 models: 下更深缩进的同名键；被替换行引号外的
    行尾注释原样保留。
    """
    prefix = " " * indent
    pat = re.compile(rf"^{re.escape(prefix)}{re.escape(key)}\s*:")
    for i, line in enumerate(body):
        if pat.match(line):
            _, comment = _split_trailing_comment(line)
            body[i] = f"{prefix}{key}: {_quote_yaml(value)}{comment}"
            return body
    body.append(f"{prefix}{key}: {_quote_yaml(value)}")
    return body


def _decision_profile_block(entry: ProviderEntry) -> List[str]:
    env_ref = _ENV_REF_TEMPLATE % entry.api_key_env
    return [
        f"    {DECISION_PROFILE_NAME}:",
        f"      provider: {_quote_yaml(provider_name_for(entry))}",
        f"      model: {_quote_yaml(primary_model(entry))}",
        f"      api_key: {_quote_yaml(env_ref)}",
        f"      base_url: {_quote_yaml(entry.base_url)}",
        "      request_timeout: 120",
    ]


def _set_decision_profile(body: List[str], entry: ProviderEntry) -> List[str]:
    """在 llm 段的 models: 下写入/替换 decision 命名 profile，保留其它 profile。"""
    block = _decision_profile_block(entry)
    models_idx: Optional[int] = None
    for i, line in enumerate(body):
        if re.match(r"^  models\s*:", line):
            models_idx = i
            break
    if models_idx is None:
        body.append("  models:")
        body.extend(block)
        return body
    # models 子块范围：其后连续缩进 >= 4 的非空行。
    j = models_idx + 1
    while j < len(body) and body[j].startswith("    ") and body[j].strip():
        j += 1
    sub = body[models_idx + 1 : j]
    for k, line in enumerate(sub):
        if re.match(r"^    " + re.escape(DECISION_PROFILE_NAME) + r"\s*:", line):
            # 替换既有 decision profile：整块为后续 6 空格缩进行。
            end = k + 1
            while end < len(sub) and sub[end].startswith("      "):
                end += 1
            body[models_idx + 1 + k : models_idx + 1 + end] = block
            return body
    body[models_idx + 1 : models_idx + 1] = block
    return body


def apply_to_pipeline_config(yaml_path: _PATH_LIKE, registry: ProviderRegistry) -> List[str]:
    """把 annotation/decision 选中项写进 pipeline YAML 的 llm 段。

    - annotation → llm 主段 provider/model/api_key（写作 "${ENV}" 引用）/base_url；
    - decision → llm.models.decision 命名 profile（模块侧 model_profile: "decision"）。
    - 角色槽为空则不写对应部分；两个角色都为空时文件不动。
    - 只改 llm 段内受管键，段外内容与段内注释、其它键逐字保留。

    返回已执行的动作描述列表。
    """
    path = Path(yaml_path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    start: Optional[int] = None
    for i, line in enumerate(lines):
        if _TOP_LEVEL_KEY_RE.match(line) and line.split(":", 1)[0].strip() == "llm":
            start = i
            break
    if start is None:
        raise RegistryError(f"{path} 中找不到顶层 llm: 段")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _TOP_LEVEL_KEY_RE.match(lines[j]):
            end = j
            break

    body = lines[start + 1 : end]
    actions: List[str] = []

    if registry.annotation_provider:
        entry = registry.resolve_provider(ROLE_ANNOTATION)
        body = _set_block_scalar(body, "provider", provider_name_for(entry))
        body = _set_block_scalar(body, "model", primary_model(entry))
        body = _set_block_scalar(body, "api_key", _ENV_REF_TEMPLATE % entry.api_key_env)
        body = _set_block_scalar(body, "base_url", entry.base_url)
        actions.append(f"annotation → 主段 provider={entry.id} model={primary_model(entry) or '（待填）'}")

    if registry.decision_provider:
        entry = registry.resolve_provider(ROLE_DECISION)
        body = _set_decision_profile(body, entry)
        actions.append(
            f"decision → llm.models.{DECISION_PROFILE_NAME} provider={entry.id} "
            f"model={primary_model(entry) or '（待填）'}"
        )

    if not actions:
        return actions

    trailing_newline = "\n" if text.endswith("\n") else ""
    new_text = "\n".join(lines[: start + 1] + body + lines[end:]) + trailing_newline
    if new_text != text:
        _atomic_write_text(path, new_text)
    return actions


# ---------------------------------------------------------------------------
# 中央 env 文件（key 只经环境变量名引用，值永不回读展示）
# ---------------------------------------------------------------------------


def default_central_env_path() -> Path:
    """install_plugin.sh 安装的中央 env：~/.agents/skills/langhuan.env。"""
    return Path.home() / ".agents" / "skills" / "langhuan.env"


def skill_root() -> Path:
    return Path(__file__).resolve().parents[2]


def detect_env_file(
    skill_root_path: Optional[_PATH_LIKE] = None, user_path: Optional[_PATH_LIKE] = None
) -> Path:
    """env 文件自动探测：中央 langhuan.env（存在优先）→ 技能根 .env → 用户指定路径。"""
    central = default_central_env_path()
    if central.exists():
        return central
    root = Path(skill_root_path) if skill_root_path is not None else skill_root()
    local = root / ".env"
    if local.exists():
        return local
    if user_path:
        return Path(user_path)
    return local


def upsert_env_value(path: _PATH_LIKE, env_name: str, value: str) -> None:
    """读-改-写追加/更新 env 文件中的 NAME=value 行，保留其它行；原子写。

    只写不读：调用方（password 输入）提供值，本函数不回传文件内的既有值。
    """
    path = Path(path)
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(env_name)}=")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: List[str] = []
    replaced = False
    for line in lines:
        if pat.match(line):
            if not replaced:
                prefix = "export " if line.lstrip().startswith("export ") else ""
                out.append(f"{prefix}{env_name}={value}")
                replaced = True
            continue  # 丢弃重复行
        out.append(line)
    if not replaced:
        out.append(f"{env_name}={value}")
    _atomic_write_text(path, "\n".join(out) + "\n")


def env_file_has_key(path: Optional[_PATH_LIKE], env_name: str) -> bool:
    """env 文件是否已配置某 key（只查存在性，不返回值）。"""
    if not path:
        return False
    path = Path(path)
    if not path.exists():
        return False
    pat = re.compile(rf"^\s*(export\s+)?{re.escape(env_name)}=")
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return any(pat.match(line) for line in content.splitlines())


__all__ = [
    "RegistryError",
    "ProviderEntry",
    "ProviderRegistry",
    "PRESETS",
    "PRESET_IDS",
    "PROVIDER_TYPES",
    "ROLES",
    "ROLE_ANNOTATION",
    "ROLE_DECISION",
    "TYPE_OPENAI_COMPATIBLE",
    "TYPE_OPENAI_COMPATIBLE_DECISION",
    "TYPE_JEV_LEGACY",
    "TYPE_SSH_TUNNEL",
    "TYPE_MODEL_DEPLOYMENT",
    "DECISION_PROFILE_NAME",
    "DEFAULT_ANNOTATION_PROVIDER",
    "DEFAULT_DECISION_PROVIDER",
    "default_registry",
    "preset_entry",
    "load_registry",
    "save_registry",
    "validate",
    "provider_name_for",
    "primary_model",
    "build_provider_config",
    "apply_to_pipeline_config",
    "default_central_env_path",
    "skill_root",
    "detect_env_file",
    "upsert_env_value",
    "env_file_has_key",
]
