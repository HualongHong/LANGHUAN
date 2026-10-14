"""Credential provider 接缝（D7 · Phase 4）：为宿主 credential broker 预留的
可选适配层。

背景（DSH Capsule 插件 Developer Preview 原则）：Extension 传递 Operation，
Broker 持有 Authority；Secret 不应成为 Extension 的长期状态。LangHuan 各技能
现状是从 ``.env`` 长期持有 provider key（经 ``agentkit.config.load_env`` 显式
加载进 ``os.environ`` 后读取）。

本模块把"按引用解析凭据"抽象成接口：

- 宿主（如 DSH Capsule）提供 credential broker 时，经
  :func:`set_provider` 注册一个 broker 适配实现；解析经 broker 完成，
  **不落盘、不跨 operation 缓存**（租约语义见 :class:`LeaseRef`）。
- 宿主没有 broker 时 **fail-open**：回落到
  :class:`EnvCredentialProvider`（现有 ``.env`` 路径），行为与现状逐字节
  一致——Capsule 永不成为硬依赖（LangHuan 分发到 DSH/Kimi/Codex/TRAE 等
  多宿主）。

红线：**LangHuan 自身永不强制要求 broker**。默认 provider 就是
:class:`EnvCredentialProvider`；技能侧不传不配时一切照旧。

用法（宿主适配示例）::

    from agentkit.credentials import (
        CompositeCredentialProvider, CredentialProvider, EnvCredentialProvider,
        LeaseRef, set_provider,
    )

    class CapsuleBrokerProvider(CredentialProvider):
        def resolve(self, ref: str) -> str | None:
            ...  # 向宿主 broker 请求租约并返回密钥；未授权返回 None
        def lease(self, ref: str) -> LeaseRef | None:
            ...  # 回传租约元数据（现阶段仅透传记录）

    set_provider(CompositeCredentialProvider([CapsuleBrokerProvider(),
                                              EnvCredentialProvider()]))

引用（ref）约定：``<provider>:<field>``，如 ``deepseek:api_key``、
``minimax:group_id``。缺 key 的报错语义由各调用方维持现状（技能侧怎么
报错还怎么报错），本层全链 Miss 一律返回 ``None``，绝不抛错。

依赖：纯标准库；import 无任何副作用（不读 .env、不改 ``os.environ``）。
"""

from __future__ import annotations

import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

#: 内置的 ref → 环境变量名映射。值为 ``None`` 表示"已知无需环境变量"
#: （如 agent_bridge 宿主工单队列零 API key），解析直接返回 None 而不做
#: env 回退。未收录的 ref 按 env 变量名直查（兼容技能自有命名）。
DEFAULT_REF_ENV_MAP: dict[str, Optional[str]] = {
    "deepseek:api_key": "DEEPSEEK_API_KEY",
    "openai:api_key": "OPENAI_API_KEY",
    "openai_compatible:api_key": "OPENAI_API_KEY",
    "kimi:api_key": "KIMI_API_KEY",
    "qwen:api_key": "QWEN_API_KEY",
    "minimax:api_key": "MINIMAX_API_KEY",
    "minimax:group_id": "MINIMAX_GROUP_ID",
    "gemini:api_key": "GEMINI_API_KEY",
    "claude:api_key": "CLAUDE_API_KEY",
    "agent_bridge:api_key": None,  # 宿主工单队列无需凭据
}


@dataclass(frozen=True)
class LeaseRef:
    """宿主 broker 的租约元数据（对应 Capsule 租约概念）。

    现阶段 LangHuan 各技能**仅透传记录**（broker 实现可自行回传、调用方
    可自行落日志），不参与任何缓存/续租/吊销决策；为将来"按租约治理长期
    状态"预留的字段形态。全部可选：拿不到完整元数据的 broker 填多少算多少。
    """

    lease_id: str = ""
    ttl: Optional[float] = None  # 租约有效期（秒）
    issued_at: Optional[float] = None  # 签发时间（epoch 秒）
    scope: Optional[str] = None  # 租约授权范围（如 operation 名/资源名）


class CredentialProvider(ABC):
    """凭据解析抽象接口。所有实现必须幂等、轻量、可重复调用。

    实现约束（宿主 broker 适配时遵守）：
    - 返回的密钥**不得落盘**、**不得跨 operation 缓存**；
    - 解析失败/未授权一律返回 ``None``（fail-open），由调用方回落或报错；
    - 解析成本高的实现自行控制调用频率（agentkit 每次构造 provider 都会调）。
    """

    @abstractmethod
    def resolve(self, ref: str) -> Optional[str]:
        """按引用解析凭据值；无法解析时返回 ``None``（绝不抛错）。"""
        raise NotImplementedError

    def lease(self, ref: str) -> Optional[LeaseRef]:
        """回传 ``ref`` 最近一次解析对应的租约元数据；默认 ``None``。

        broker 有租约概念时可覆写（现阶段 LangHuan 仅透传记录）。
        """
        return None


class EnvCredentialProvider(CredentialProvider):
    """回落实现：按现有 ``.env`` 加载惯例从 ``os.environ`` 解析。

    惯例：技能入口显式调用 ``agentkit.config.load_env()`` 把技能根 ``.env``
    加载进 ``os.environ`` 后，本 provider 直接从 ``os.environ`` 取值——与技能
    现状读取的是同一个变量池，fail-open 路径与现状逐字节等价。

    Args:
        ref_to_env: 自定义 ref → 环境变量名映射；缺省用
            :data:`DEFAULT_REF_ENV_MAP`。值为 ``None`` 的条目表示该 ref
            无需环境变量（直接 Miss）。
        environ: 取值映射；缺省 ``os.environ``（测试可注入隔离）。
    """

    def __init__(
        self,
        ref_to_env: Optional[Mapping[str, Optional[str]]] = None,
        environ: Optional[Mapping[str, str]] = None,
    ):
        self._ref_to_env = dict(DEFAULT_REF_ENV_MAP if ref_to_env is None else ref_to_env)
        self._environ = os.environ if environ is None else environ

    def resolve(self, ref: str) -> Optional[str]:
        if ref in self._ref_to_env:
            env_name = self._ref_to_env[ref]
            if env_name is None:
                return None
        else:
            # 未收录的 ref 按环境变量名直查（兼容技能自有命名习惯）。
            env_name = ref
        value = self._environ.get(env_name)
        return value if value else None  # 空串视为未配置（与 `os.environ.get(...) or None` 一致）


class CompositeCredentialProvider(CredentialProvider):
    """按优先级链解析：前面的 provider 优先，首个非 ``None`` 命中即返回。

    典型用法：``CompositeCredentialProvider([broker_provider,
    EnvCredentialProvider()])``——broker 在前、env 在后；全链 Miss 返回
    ``None``，由调用方决定报错（报错语义与未引入本层时一致）。
    """

    def __init__(self, providers: Sequence[CredentialProvider]):
        self._providers = tuple(providers)

    def resolve(self, ref: str) -> Optional[str]:
        for provider in self._providers:
            value = provider.resolve(ref)
            if value is not None:
                return value
        return None

    def lease(self, ref: str) -> Optional[LeaseRef]:
        for provider in self._providers:
            lease = provider.lease(ref)
            if lease is not None:
                return lease
        return None


# ---------------------------------------------------------------------------
# 进程级注册/取用入口（默认 EnvCredentialProvider；LangHuan 永不强制 broker）
# ---------------------------------------------------------------------------

_provider: CredentialProvider = EnvCredentialProvider()


def set_provider(provider: Optional[CredentialProvider]) -> None:
    """注册进程级 credential provider；传 ``None`` 复位为默认 env 回落。

    宿主接入 broker 时在入口（如 agent_run / 插件加载点）调用一次即可；
    普通技能与无 broker 宿主无需调用。
    """
    global _provider
    _provider = provider if provider is not None else EnvCredentialProvider()


def get_provider() -> CredentialProvider:
    """取当前进程级 credential provider（默认 :class:`EnvCredentialProvider`）。"""
    return _provider


def now_utc() -> float:
    """当前 epoch 秒（UTC 视角的进程本地时钟），供 broker 实现填 ``LeaseRef.issued_at``。"""
    return time.time()


__all__ = [
    "DEFAULT_REF_ENV_MAP",
    "CompositeCredentialProvider",
    "CredentialProvider",
    "EnvCredentialProvider",
    "LeaseRef",
    "get_provider",
    "now_utc",
    "set_provider",
]
