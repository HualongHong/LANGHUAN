"""Tests for agentkit.credentials（D7 凭据接缝）+ agentkit.llm 接入点。

全部离线：monkeypatch 环境变量/注册表，不读真实 .env，不发起任何请求。
"""

from __future__ import annotations

import pytest

import agentkit.credentials as credentials
from agentkit.credentials import (
    CompositeCredentialProvider,
    CredentialProvider,
    EnvCredentialProvider,
    LeaseRef,
    get_provider,
    set_provider,
)
from agentkit.llm.providers import DeepSeekProvider


class FakeBrokerProvider(CredentialProvider):
    """脚本化 broker：按 ref 字典返回密钥，记录被询问的 ref。"""

    def __init__(self, mapping, lease_ref=None):
        self._mapping = dict(mapping)
        self.refs_asked: list[str] = []
        self._lease_ref = lease_ref

    def resolve(self, ref: str):
        self.refs_asked.append(ref)
        return self._mapping.get(ref)

    def lease(self, ref: str):
        return self._lease_ref


@pytest.fixture
def restore_provider():
    """保存/恢复进程级 credential provider，避免跨用例污染。"""
    original = get_provider()
    try:
        yield
    finally:
        set_provider(original)


# ---------------------------------------------------------------------------
# EnvCredentialProvider
# ---------------------------------------------------------------------------


class TestEnvCredentialProvider:
    def test_hit_via_default_mapping(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "env-deepseek-secret")
        provider = EnvCredentialProvider()
        assert provider.resolve("deepseek:api_key") == "env-deepseek-secret"

    def test_miss_returns_none(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        provider = EnvCredentialProvider()
        assert provider.resolve("deepseek:api_key") is None

    def test_empty_string_treated_as_missing(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "")
        provider = EnvCredentialProvider()
        assert provider.resolve("deepseek:api_key") is None

    def test_unknown_ref_falls_back_to_raw_env_name(self, monkeypatch):
        monkeypatch.setenv("MY_OWN_KEY", "custom")
        provider = EnvCredentialProvider()
        assert provider.resolve("MY_OWN_KEY") == "custom"

    def test_known_none_mapping_short_circuits(self, monkeypatch):
        # agent_bridge 队列 provider 无需凭据：即使碰巧存在同名 env 也不取。
        monkeypatch.setenv("agent_bridge:api_key", "should-not-be-used")
        provider = EnvCredentialProvider()
        assert provider.resolve("agent_bridge:api_key") is None

    def test_custom_mapping_overrides_default(self):
        provider = EnvCredentialProvider(
            ref_to_env={"minimax:api_key": "MY_MINIMAX"}, environ={"MY_MINIMAX": "x"}
        )
        assert provider.resolve("minimax:api_key") == "x"
        assert provider.resolve("deepseek:api_key") is None  # 默认映射已被替换

    def test_injected_environ_is_used(self):
        provider = EnvCredentialProvider(environ={"QWEN_API_KEY": "injected"})
        assert provider.resolve("qwen:api_key") == "injected"


# ---------------------------------------------------------------------------
# CompositeCredentialProvider
# ---------------------------------------------------------------------------


class TestCompositeCredentialProvider:
    def test_broker_hit_does_not_fall_to_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")
        broker = FakeBrokerProvider({"deepseek:api_key": "broker-secret"})
        composite = CompositeCredentialProvider([broker, EnvCredentialProvider()])
        assert composite.resolve("deepseek:api_key") == "broker-secret"

    def test_broker_miss_falls_to_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")
        broker = FakeBrokerProvider({})
        composite = CompositeCredentialProvider([broker, EnvCredentialProvider()])
        assert composite.resolve("deepseek:api_key") == "env-secret"

    def test_all_miss_returns_none(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        broker = FakeBrokerProvider({})
        composite = CompositeCredentialProvider([broker, EnvCredentialProvider()])
        assert composite.resolve("deepseek:api_key") is None

    def test_first_hit_wins_by_priority_order(self):
        low = FakeBrokerProvider({"k:api_key": "low"})
        high = FakeBrokerProvider({"k:api_key": "high"})
        composite = CompositeCredentialProvider([high, low])
        assert composite.resolve("k:api_key") == "high"

    def test_lease_passes_through(self):
        lease = LeaseRef(lease_id="L1", ttl=300.0, issued_at=1.0, scope="deepseek:api_key")
        broker = FakeBrokerProvider({}, lease_ref=lease)
        composite = CompositeCredentialProvider([broker, EnvCredentialProvider()])
        assert composite.lease("deepseek:api_key") is lease

    def test_default_lease_is_none(self):
        assert EnvCredentialProvider().lease("deepseek:api_key") is None


# ---------------------------------------------------------------------------
# 全局注册入口
# ---------------------------------------------------------------------------


class TestGlobalProvider:
    def test_default_provider_is_env(self):
        set_provider(None)
        assert isinstance(get_provider(), EnvCredentialProvider)

    def test_set_and_reset(self, restore_provider):
        broker = FakeBrokerProvider({})
        set_provider(broker)
        assert get_provider() is broker
        set_provider(None)
        assert isinstance(get_provider(), EnvCredentialProvider)


# ---------------------------------------------------------------------------
# agentkit.llm 接入点（providers 取 key 走 credential 链）
# ---------------------------------------------------------------------------


def _make_deepseek(api_key=None):
    return DeepSeekProvider(model="deepseek-chat", api_key=api_key, base_url="https://api.deepseek.com")


class TestLLMHook:
    def test_broker_supplies_key_when_api_key_empty(self, restore_provider, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        broker = FakeBrokerProvider({"deepseek:api_key": "broker-secret"})
        set_provider(CompositeCredentialProvider([broker, EnvCredentialProvider()]))
        provider = _make_deepseek(api_key=None)
        assert provider.api_key == "broker-secret"
        assert broker.refs_asked == ["deepseek:api_key"]

    def test_env_fallback_when_no_broker(self, restore_provider, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "env-secret")
        set_provider(None)  # 默认 EnvCredentialProvider
        provider = _make_deepseek(api_key=None)
        assert provider.api_key == "env-secret"

    def test_miss_keeps_original_value(self, restore_provider, monkeypatch):
        # resolve 全链 Miss → 保持原值（None），报错语义与现状一致（请求时才失败）。
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        set_provider(None)
        provider = _make_deepseek(api_key=None)
        assert provider.api_key is None

    def test_explicit_key_never_overridden_by_broker(self, restore_provider, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        broker = FakeBrokerProvider({"deepseek:api_key": "broker-secret"})
        set_provider(CompositeCredentialProvider([broker, EnvCredentialProvider()]))
        provider = _make_deepseek(api_key="explicit-key")
        assert provider.api_key == "explicit-key"
        assert broker.refs_asked == []  # 显式 key 时不打扰 broker

    def test_bridge_provider_unaffected(self, restore_provider, monkeypatch):
        # agent_bridge 走 "" 占位：不触发解析、不被 env 兜底改写。
        from agentkit.llm.bridge_provider import AgentBridgeProvider

        monkeypatch.delenv("agent_bridge:api_key", raising=False)
        set_provider(None)
        provider = AgentBridgeProvider()
        assert provider.api_key == ""
