"""Tests for output-language resolution."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.config_loader import PipelineConfig
from lit_scout_harvester.core.language_strategy import resolve_output_language


def _config(output_language=None, datasource="wos", confirm=True, default="zh"):
    return PipelineConfig(
        llm={"api_key": "sk-test"},
        skeleton={
            "datasource": datasource,
            "output_language": output_language,
            "cnki": {"confirm_language": confirm, "default_language": default},
        },
        modules={},
    )


def test_explicit_output_language_wins():
    cfg = _config(output_language="en", datasource="cnki")
    assert resolve_output_language(cfg, "dummy.txt", interactive=False) == "en"


def test_wos_defaults_to_english():
    cfg = _config(datasource="wos")
    assert resolve_output_language(cfg, "dummy.txt", interactive=False) == "en"


def test_cnki_noninteractive_uses_default():
    cfg = _config(datasource="cnki", confirm=True, default="zh")
    assert resolve_output_language(cfg, "dummy.txt", interactive=False) == "zh"


def test_cnki_noninteractive_default_en():
    cfg = _config(datasource="cnki", confirm=False, default="en")
    assert resolve_output_language(cfg, "dummy.txt", interactive=False) == "en"


def test_auto_detect_cnki(monkeypatch, tmp_path):
    path = tmp_path / "sample.cnki.txt"
    path.write_text("RT Journal Article\nA1 作者\nT1 标题\nLA 中文\n", encoding="utf-8")
    cfg = _config(datasource="auto_detect")
    assert resolve_output_language(cfg, str(path), interactive=False) == "zh"


def test_auto_detect_wos(monkeypatch, tmp_path):
    path = tmp_path / "sample.wos"
    path.write_text("FN Web of Science\nPT J\nTI Title\nER\n", encoding="utf-8")
    cfg = _config(datasource="auto_detect")
    assert resolve_output_language(cfg, str(path), interactive=False) == "en"


def test_cnki_interactive_prompt(monkeypatch):
    cfg = _config(datasource="cnki", confirm=True, default="zh")
    monkeypatch.setattr("builtins.input", lambda _: "2")
    assert resolve_output_language(cfg, "dummy.txt", interactive=True) == "en"


def test_cnki_interactive_invalid_choice_uses_default(monkeypatch):
    cfg = _config(datasource="cnki", confirm=True, default="zh")
    monkeypatch.setattr("builtins.input", lambda _: "invalid")
    assert resolve_output_language(cfg, "dummy.txt", interactive=True) == "zh"
