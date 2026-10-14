"""Tests for src.core.config_loader."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from lit_scout_harvester.core.config_loader import ConfigError, PipelineConfig, load_config


@pytest.fixture
def temp_config_dir():
    """Provide a temporary directory for config files."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def write_config(directory: Path, filename: str, data: Dict[str, Any]) -> Path:
    path = directory / filename
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
    return path


def test_load_minimal_config(temp_config_dir, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    data = {
        "global": {"max_concurrency": 5},
        "llm": {"api_key": "${DEEPSEEK_API_KEY}"},
        "modules": {"gdp_weighter": {"enabled": True}},
    }
    path = write_config(temp_config_dir, "minimal.yaml", data)
    cfg = load_config(path)
    assert isinstance(cfg, PipelineConfig)
    assert cfg.global_.max_concurrency == 5
    assert cfg.llm.api_key == "sk-test"
    assert cfg.modules["gdp_weighter"].enabled is True


def test_extends_merge_and_override(temp_config_dir, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    base = {
        "global": {"max_concurrency": 10, "batch_size": 50},
        "llm": {"api_key": "${DEEPSEEK_API_KEY}"},
        "modules": {"gdp_weighter": {"enabled": False}},
    }
    child = {
        "extends": "base.yaml",
        "global": {"max_concurrency": 20},
        "modules": {"sci_edu_extractor": {"enabled": True, "schema_path": "s.json"}},
    }
    write_config(temp_config_dir, "base.yaml", base)
    path = write_config(temp_config_dir, "child.yaml", child)
    cfg = load_config(path)
    assert cfg.global_.max_concurrency == 20
    assert cfg.global_.batch_size == 50
    assert cfg.modules["gdp_weighter"].enabled is False
    assert cfg.modules["sci_edu_extractor"].enabled is True
    assert cfg.modules["sci_edu_extractor"].schema_path == "s.json"


def test_environment_interpolation_with_default(temp_config_dir, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    data = {
        "llm": {
            "api_key": "${DEEPSEEK_API_KEY}",
            "base_url": "${DEEPSEEK_BASE_URL:https://default.example.com/v1}",
        },
        "modules": {},
    }
    path = write_config(temp_config_dir, "interp.yaml", data)
    cfg = load_config(path)
    assert cfg.llm.api_key == "sk-test"
    assert cfg.llm.base_url == "https://default.example.com/v1"

    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://custom.example.com/v1")
    cfg2 = load_config(path)
    assert cfg2.llm.base_url == "https://custom.example.com/v1"


def test_default_yaml_loads(temp_config_dir, monkeypatch):
    """Ensure the repository default.yaml can be loaded when the env var is set."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    cfg = load_config(Path("config/default.yaml"))
    assert cfg.llm.provider == "deepseek"
    assert cfg.skeleton.translate_to_chinese is True
    assert "gdp_weighter" in cfg.modules


def test_missing_required_llm_api_key_raises(temp_config_dir, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    data = {
        "llm": {"base_url": "https://api.example.com"},
        "modules": {},
    }
    path = write_config(temp_config_dir, "bad.yaml", data)
    with pytest.raises((ConfigError,)):
        load_config(path)


def test_missing_extends_file_raises(temp_config_dir, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    data = {"extends": "nonexistent.yaml", "modules": {}}
    path = write_config(temp_config_dir, "orphan.yaml", data)
    with pytest.raises(ConfigError):
        load_config(path)
