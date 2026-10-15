"""Tests for core.provider_registry —— 模型运营商注册表（设置页的数据底座）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lit_scout_harvester.core import provider_registry as pr
from lit_scout_harvester.core.config_loader import load_config

SKILL_ROOT = Path(__file__).resolve().parents[2]
FACTORY_JSON = SKILL_ROOT / "config" / "providers.json"


# ---------------------------------------------------------------------------
# 出厂内容
# ---------------------------------------------------------------------------


def test_factory_json_matches_presets():
    registry = pr.load_registry(FACTORY_JSON)
    assert [e.id for e in registry.providers] == list(pr.PRESET_IDS)
    assert len(registry.providers) == 10
    assert len({e.id for e in registry.providers}) == 10  # id 唯一
    by_id = {e.id: e for e in registry.providers}
    # 预留入口
    assert by_id["tencent"].reserved is True and by_id["tencent"].enabled is False
    assert by_id["jev_original"].reserved is True and by_id["jev_original"].enabled is False
    assert by_id["jev_original"].type == pr.TYPE_JEV_LEGACY
    assert "不可访问" in by_id["tencent"].note
    assert "保留入口" in by_id["jev_original"].note
    # 上海超算双入口
    assert by_id["shanghai_sc_llm"].type == pr.TYPE_OPENAI_COMPATIBLE
    assert by_id["shanghai_sc_jev"].type == pr.TYPE_OPENAI_COMPATIBLE_DECISION
    assert by_id["shanghai_sc_jev"].supports_logprobs is True
    # deepseek 出厂唯一启用 + logprobs
    assert by_id["deepseek"].enabled is True and by_id["deepseek"].supports_logprobs is True
    # 出厂角色槽
    assert registry.annotation_provider == "deepseek"
    assert registry.decision_provider == "deepseek"


def test_factory_json_new_presets():
    """出厂 3 个新预置：ssh_tunnel + 两个 model_deployment 专用端点。"""
    registry = pr.load_registry(FACTORY_JSON)
    by_id = {e.id: e for e in registry.providers}
    # SSH 隧道自建服务器
    ssh = by_id["ssh_selfhost"]
    assert ssh.type == pr.TYPE_SSH_TUNNEL
    assert ssh.operator == "自建服务器（SSH 隧道）"
    assert ssh.base_url == "http://127.0.0.1:8377/v1"
    assert ssh.api_key_env == "SELFHOST_API_KEY"
    assert ssh.supports_logprobs is True
    assert ssh.enabled is False
    assert "model_tunnel.py" in ssh.note
    # SSH 连接字段留空待填（local_port 与 base_url 占位端口一致）
    assert ssh.ssh_host == "" and ssh.ssh_user == "" and ssh.ssh_key_path == ""
    assert ssh.remote_host == "" and ssh.remote_port == 0
    assert ssh.local_port == 8377 and ssh.ssh_port == 22
    # 阿里云百炼模型部署
    ali = by_id["aliyun_model_deploy"]
    assert ali.type == pr.TYPE_MODEL_DEPLOYMENT
    assert ali.base_url == "https://<deployment-id>.<region>.aliyuncs.com/compatible-mode/v1"
    assert ali.api_key_env == "ALIYUN_DEPLOY_API_KEY"
    assert ali.supports_logprobs is True and ali.enabled is False
    # scnet.cn 超算模型部署
    scnet = by_id["scnet_model_deploy"]
    assert scnet.type == pr.TYPE_MODEL_DEPLOYMENT
    assert scnet.base_url == "https://<scnet-endpoint>/v1"
    assert scnet.api_key_env == "SCNET_DEPLOY_API_KEY"
    assert scnet.supports_logprobs is True and scnet.enabled is False
    # 三个新类型都进了 PROVIDER_TYPES
    assert pr.TYPE_SSH_TUNNEL in pr.PROVIDER_TYPES
    assert pr.TYPE_MODEL_DEPLOYMENT in pr.PROVIDER_TYPES
    # 新预置出厂校验通过（disabled，ssh 字段留空不报错）
    assert pr.validate(registry) == []


def test_presets_do_not_alias_module_state():
    registry = pr.default_registry()
    registry.providers[0].base_url = "https://tampered.example/v1"
    assert pr.PRESETS[0].base_url == "https://api.deepseek.com/v1"


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def test_roundtrip_byte_identical(tmp_path):
    registry = pr.load_registry(FACTORY_JSON)
    out = tmp_path / "providers.json"
    pr.save_registry(out, registry)
    assert out.read_bytes() == FACTORY_JSON.read_bytes()


def test_load_missing_file_returns_defaults(tmp_path):
    registry = pr.load_registry(tmp_path / "nope.json")
    assert [e.id for e in registry.providers] == list(pr.PRESET_IDS)
    assert registry.annotation_provider == "deepseek"


def test_load_field_fallbacks(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps(
            {
                "providers": [{"id": "custom", "base_url": "https://x/v1"}],
                "annotation_provider": "custom",
            }
        ),
        encoding="utf-8",
    )
    registry = pr.load_registry(path)
    entry = registry.get("custom")
    assert entry is not None
    assert entry.operator == ""  # 缺省回退
    assert entry.models == []
    assert entry.enabled is False
    assert entry.reserved is False
    assert registry.decision_provider is None


def test_load_invalid_json_raises(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(pr.RegistryError):
        pr.load_registry(path)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _registry_with(**kwargs) -> pr.ProviderRegistry:
    registry = pr.default_registry()
    for key, value in kwargs.items():
        setattr(registry, key, value)
    return registry


def test_validate_ok_by_default():
    assert pr.validate(pr.default_registry()) == []


def test_validate_duplicate_id():
    registry = pr.default_registry()
    dup = pr.ProviderEntry(id="deepseek", operator="副本")
    registry.providers.append(dup)
    errors = pr.validate(registry)
    assert any("重复" in e and "deepseek" in e for e in errors)


def test_validate_enabled_missing_base_url():
    registry = pr.default_registry()
    registry.get("minimax").enabled = True
    registry.get("minimax").base_url = ""
    errors = pr.validate(registry)
    assert any("minimax" in e and "base_url" in e for e in errors)


def test_validate_decision_requires_logprobs():
    registry = pr.default_registry()
    # qwen 未声明 logprobs → 决策角色报错
    registry.decision_provider = "qwen"
    errors = pr.validate(registry)
    assert any("logprobs" in e and "qwen" in e for e in errors)
    # 换回 deepseek → 通过
    registry.decision_provider = "deepseek"
    assert pr.validate(registry) == []


def test_validate_reserved_disabled_no_error():
    registry = pr.default_registry()
    assert pr.validate(registry) == []  # tencent / jev_original 均为 reserved+disabled


def test_validate_role_points_to_missing_id():
    registry = _registry_with(decision_provider="ghost")
    errors = pr.validate(registry)
    assert any("ghost" in e for e in errors)


# ---------------------------------------------------------------------------
# ssh_tunnel / model_deployment 新类型
# ---------------------------------------------------------------------------


def _enabled_ssh_entry(**overrides) -> pr.ProviderEntry:
    entry = pr.preset_entry("ssh_selfhost")
    entry.enabled = True
    entry.ssh_host = "bastion.example.com"
    entry.ssh_user = "deploy"
    entry.ssh_key_path = "/home/deploy/.ssh/id_ed25519"
    entry.remote_host = "10.0.0.8"
    entry.remote_port = 8080
    entry.local_port = 8377
    for key, value in overrides.items():
        setattr(entry, key, value)
    return entry


def test_validate_ssh_tunnel_enabled_requires_fields():
    registry = pr.ProviderRegistry(providers=[_enabled_ssh_entry()])
    assert pr.validate(registry) == []
    for field in ("ssh_host", "ssh_user", "local_port"):
        entry = _enabled_ssh_entry(**{field: "" if field != "local_port" else 0})
        errors = pr.validate(pr.ProviderRegistry(providers=[entry]))
        assert any("ssh_selfhost" in e and field in e for e in errors), field
    # disabled 时不强制 ssh 字段（出厂预置即如此）
    disabled = pr.preset_entry("ssh_selfhost")
    assert pr.validate(pr.ProviderRegistry(providers=[disabled])) == []


def test_validate_model_deployment_enabled_normal_rules():
    entry = pr.preset_entry("aliyun_model_deploy")
    entry.enabled = True
    assert pr.validate(pr.ProviderRegistry(providers=[entry])) == []
    entry.base_url = ""
    errors = pr.validate(pr.ProviderRegistry(providers=[entry]))
    assert any("base_url" in e for e in errors)


def test_provider_name_for_new_types_falls_back_to_openai_compatible():
    # HTTP 层面两种新类型都按 openai_compatible 驱动
    assert pr.provider_name_for(pr.preset_entry("ssh_selfhost")) == pr.TYPE_OPENAI_COMPATIBLE
    assert pr.provider_name_for(pr.preset_entry("aliyun_model_deploy")) == pr.TYPE_OPENAI_COMPATIBLE
    assert pr.provider_name_for(pr.preset_entry("scnet_model_deploy")) == pr.TYPE_OPENAI_COMPATIBLE


def test_build_provider_config_transport_marks():
    ssh_cfg = pr.build_provider_config(pr.preset_entry("ssh_selfhost"))
    assert ssh_cfg["provider"] == pr.TYPE_OPENAI_COMPATIBLE
    assert ssh_cfg["transport"] == "ssh_tunnel"
    assert ssh_cfg["base_url"] == "http://127.0.0.1:8377/v1"
    # decision 角色：新类型声明了 supports_logprobs，可指派
    dec_cfg = pr.build_provider_config(pr.preset_entry("ssh_selfhost"), role="decision")
    assert dec_cfg["transport"] == "ssh_tunnel"
    assert dec_cfg["requires_logprobs"] is True
    ali_cfg = pr.build_provider_config(pr.preset_entry("aliyun_model_deploy"))
    assert ali_cfg["provider"] == pr.TYPE_OPENAI_COMPATIBLE
    assert ali_cfg["transport"] == "direct"
    scnet_cfg = pr.build_provider_config(pr.preset_entry("scnet_model_deploy"), role="decision")
    assert scnet_cfg["transport"] == "direct"
    assert scnet_cfg["requires_logprobs"] is True
    # 既有类型不带 transport 键（向后兼容）
    assert "transport" not in pr.build_provider_config(pr.preset_entry("deepseek"))


def test_ssh_fields_roundtrip_through_save_load(tmp_path):
    registry = pr.ProviderRegistry(providers=[_enabled_ssh_entry()])
    out = tmp_path / "p.json"
    pr.save_registry(out, registry)
    loaded = pr.load_registry(out)
    entry = loaded.get("ssh_selfhost")
    assert entry.ssh_host == "bastion.example.com"
    assert entry.ssh_port == 22
    assert entry.ssh_user == "deploy"
    assert entry.ssh_key_path == "/home/deploy/.ssh/id_ed25519"
    assert entry.remote_host == "10.0.0.8"
    assert entry.remote_port == 8080
    assert entry.local_port == 8377


# ---------------------------------------------------------------------------
# resolve_provider
# ---------------------------------------------------------------------------


def test_resolve_provider_both_roles():
    registry = pr.default_registry()
    ann = registry.resolve_provider("annotation")
    dec = registry.resolve_provider("decision")
    assert ann.id == "deepseek"
    assert dec.id == "deepseek"


def test_resolve_provider_unknown_role():
    registry = pr.default_registry()
    with pytest.raises(pr.RegistryError, match="未知角色"):
        registry.resolve_provider("translator")


def test_resolve_provider_unconfigured_role():
    registry = _registry_with(decision_provider=None)
    with pytest.raises(pr.RegistryError, match="未配置"):
        registry.resolve_provider("decision")


def test_resolve_provider_missing_id_clear_error():
    registry = _registry_with(annotation_provider="no_such_provider")
    with pytest.raises(pr.RegistryError) as exc_info:
        registry.resolve_provider("annotation")
    assert "no_such_provider" in str(exc_info.value)
    assert "deepseek" in str(exc_info.value)  # 提示现有 id


# ---------------------------------------------------------------------------
# build_provider_config
# ---------------------------------------------------------------------------


def test_build_provider_config_reads_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
    entry = pr.preset_entry("deepseek")
    cfg = pr.build_provider_config(entry)
    assert cfg["api_key"] == "sk-from-env"
    assert cfg["api_key_env"] == "DEEPSEEK_API_KEY"
    assert cfg["provider"] == "deepseek"
    assert cfg["model"] == "deepseek-chat"
    assert cfg["base_url"] == "https://api.deepseek.com/v1"
    assert cfg["role"] == "annotation"


def test_build_provider_config_missing_key_defaults_none(monkeypatch):
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    entry = pr.preset_entry("minimax")  # models 为空
    cfg = pr.build_provider_config(entry)
    assert cfg["api_key"] is None
    assert cfg["model"] == ""


def test_build_provider_config_decision_role():
    entry = pr.preset_entry("shanghai_sc_jev")
    cfg = pr.build_provider_config(entry)  # decision 类型默认 decision 角色
    assert cfg["role"] == "decision"
    assert cfg["requires_logprobs"] is True
    assert cfg["supports_logprobs"] is True
    # 显式 annotation 角色时不强加 requires_logprobs
    cfg2 = pr.build_provider_config(entry, role="annotation")
    assert cfg2["role"] == "annotation"
    assert "requires_logprobs" not in cfg2


def test_provider_name_mapping():
    assert pr.provider_name_for(pr.preset_entry("deepseek")) == "deepseek"
    assert pr.provider_name_for(pr.preset_entry("minimax")) == "minimax"
    assert pr.provider_name_for(pr.preset_entry("qwen")) == "qwen"
    assert pr.provider_name_for(pr.preset_entry("shanghai_sc_llm")) == "openai_compatible"
    assert pr.provider_name_for(pr.preset_entry("jev_original")) == "jev_legacy"


def test_openai_compatible_registered_in_llm_providers():
    from lit_scout_harvester.core.llm_providers import (
        PROVIDER_REGISTRY,
        OpenAICompatibleProvider,
    )

    assert PROVIDER_REGISTRY["openai_compatible"] is OpenAICompatibleProvider
    provider = PROVIDER_REGISTRY["openai_compatible"](
        model="m", api_key="sk", base_url="https://x/v1"
    )
    assert provider.base_url == "https://x/v1"  # 构造参数与现有构造器兼容


# ---------------------------------------------------------------------------
# apply_to_pipeline_config
# ---------------------------------------------------------------------------

MINIMAL_YAML = """\
version: "1.0"
global:
  max_concurrency: 5
llm:
  provider: "kimi"
  model: "moonshot-v1-8k"
  api_key: "${KIMI_API_KEY}"
  base_url: "https://api.moonshot.cn/v1"
modules: {}
"""


def test_apply_writes_llm_section_and_loads_back(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    path = tmp_path / "pipeline.yaml"
    path.write_text(MINIMAL_YAML, encoding="utf-8")

    registry = pr.default_registry()
    actions = pr.apply_to_pipeline_config(path, registry)
    assert len(actions) == 2

    cfg = load_config(path)
    # annotation → 主段
    assert cfg.llm.provider == "deepseek"
    assert cfg.llm.model == "deepseek-chat"
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert cfg.llm.api_key == "sk-deepseek"
    # decision → 命名 profile
    profile = cfg.llm.models["decision"]
    assert profile.provider == "deepseek"
    assert profile.model == "deepseek-chat"
    assert profile.api_key == "sk-deepseek"
    assert profile.base_url == "https://api.deepseek.com/v1"
    # 段外内容不动
    assert cfg.global_.max_concurrency == 5
    assert "modules" in cfg.model_dump(by_alias=True)


def test_apply_no_roles_leaves_file_untouched(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text(MINIMAL_YAML, encoding="utf-8")
    registry = pr.ProviderRegistry(providers=pr.default_registry().providers)
    actions = pr.apply_to_pipeline_config(path, registry)
    assert actions == []
    assert path.read_text(encoding="utf-8") == MINIMAL_YAML


def test_apply_preserves_comments_and_other_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("KIMI_API_KEY", "sk-kimi")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    yaml_text = """\
version: "1.0"
llm:
  provider: "kimi"      # 旧主模型
  model: "moonshot-v1-8k"
  api_key: "${KIMI_API_KEY}"
  base_url: "https://api.moonshot.cn/v1"
  # 温度保持确定性
  temperature: 0.0
  models:
    extraction:
      provider: "deepseek"
      model: "deepseek-chat"
      api_key: "${DEEPSEEK_API_KEY}"
      base_url: "https://api.deepseek.com/v1"
      request_timeout: 120
modules: {}
"""
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    pr.apply_to_pipeline_config(path, pr.default_registry())

    text = path.read_text(encoding="utf-8")
    # llm 段内注释与其它键保留
    assert "# 温度保持确定性" in text
    assert 'provider: "deepseek" # 旧主模型' in text  # 原位替换，行尾注释保留
    cfg = load_config(path)
    assert cfg.llm.temperature == 0.0
    # 既有 profile 保留 + decision profile 并入
    assert cfg.llm.models["extraction"].model == "deepseek-chat"
    assert cfg.llm.models["decision"].model == "deepseek-chat"


def test_apply_missing_llm_section_raises(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text('version: "1.0"\nmodules: {}\n', encoding="utf-8")
    with pytest.raises(pr.RegistryError, match="llm"):
        pr.apply_to_pipeline_config(path, pr.default_registry())


# ---------------------------------------------------------------------------
# 中央 env 文件
# ---------------------------------------------------------------------------


def test_upsert_env_value_appends_and_updates(tmp_path):
    env = tmp_path / "langhuan.env"
    env.write_text("# 中央配置\nDEEPSEEK_API_KEY=old\nOTHER=keep\n", encoding="utf-8")
    # 更新既有 key：原位替换，不动其它行
    pr.upsert_env_value(env, "DEEPSEEK_API_KEY", "new-key")
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# 中央配置"
    assert lines[1] == "DEEPSEEK_API_KEY=new-key"
    assert lines[2] == "OTHER=keep"
    # 追加新 key
    pr.upsert_env_value(env, "SHANGHAI_SC_API_KEY", "sc-key")
    content = env.read_text(encoding="utf-8")
    assert "SHANGHAI_SC_API_KEY=sc-key" in content
    assert "OTHER=keep" in content


def test_upsert_env_value_creates_file_and_keeps_export_prefix(tmp_path):
    env = tmp_path / "sub" / "custom.env"
    pr.upsert_env_value(env, "A_KEY", "1")
    assert "A_KEY=1" in env.read_text(encoding="utf-8")
    env.write_text("export B_KEY=old\n", encoding="utf-8")
    pr.upsert_env_value(env, "B_KEY", "2")
    assert env.read_text(encoding="utf-8").splitlines()[0] == "export B_KEY=2"


def test_detect_env_file_priority(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    central_dir = fake_home / ".agents" / "skills"
    central_dir.mkdir(parents=True)
    central = central_dir / "langhuan.env"
    central.write_text("# central\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    skill_root = tmp_path / "skill"
    skill_root.mkdir()
    local = skill_root / ".env"
    local.write_text("# local\n", encoding="utf-8")
    custom = tmp_path / "custom.env"

    # 中央文件存在 → 优先
    assert pr.detect_env_file(skill_root, custom) == central
    # 中央不存在 → 技能根 .env
    central.unlink()
    assert pr.detect_env_file(skill_root, custom) == local
    # 都不存在 → 用户指定路径
    local.unlink()
    assert pr.detect_env_file(skill_root, custom) == custom


def test_env_file_has_key_only_checks_existence(tmp_path):
    env = tmp_path / "x.env"
    assert pr.env_file_has_key(env, "NOPE") is False
    env.write_text("MY_KEY=secret-value\n# OTHER=1\n", encoding="utf-8")
    assert pr.env_file_has_key(env, "MY_KEY") is True
    assert pr.env_file_has_key(env, "OTHER") is False  # 注释行不算
