"""Tests for datasource-aware record loading."""

from __future__ import annotations

import pytest

from lit_scout_harvester.core.config_loader import PipelineConfig
from lit_scout_harvester.parsers.data_source import detect_datasource, load_records


def _config(datasource="wos", base="native_parser"):
    return PipelineConfig(
        llm={"api_key": "sk-test"},
        skeleton={"datasource": datasource, "base_data_source": base},
        modules={},
    )


def test_detect_wos(tmp_path):
    path = tmp_path / "sample.wos"
    path.write_text("FN Web of Science\nPT J\nTI Title\nER\n", encoding="utf-8")
    assert detect_datasource(str(path)) == "wos"


def test_detect_cnki_refworks(tmp_path):
    path = tmp_path / "sample.cnki.txt"
    path.write_text(
        "RT Journal Article\nA1 作者\nT1 标题\nLA 中文\n",
        encoding="utf-8",
    )
    assert detect_datasource(str(path)) == "cnki"


def test_detect_cnki_custom(tmp_path):
    path = tmp_path / "sample.cnki.txt"
    path.write_text("【题名】标题\n【作者】作者\n", encoding="utf-8")
    assert detect_datasource(str(path)) == "cnki"


def test_load_records_wos(tmp_path):
    path = tmp_path / "sample.wos"
    path.write_text(
        "FN Web of Science\nVR 1.0\nPT J\nAU Smith, J\nTI Sample Paper\nER\nEF\n",
        encoding="utf-8",
    )
    cfg = _config(datasource="wos")
    records = load_records(cfg, str(path))
    assert len(records) == 1
    assert records[0]["TI"] == "Sample Paper"


def test_load_records_cnki(tmp_path):
    path = tmp_path / "sample.cnki.txt"
    path.write_text(
        "RT Journal Article\nA1 作者\nT1 标题\nYR 2022\nLA 中文\n",
        encoding="utf-8",
    )
    cfg = _config(datasource="cnki")
    records = load_records(cfg, str(path))
    assert len(records) == 1
    assert records[0]["TI"] == "标题"
    assert records[0]["LA"] == "zh"


def test_load_records_auto_detect(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("【题名】标题\n【作者】作者\n", encoding="utf-8")
    cfg = _config(datasource="auto_detect")
    records = load_records(cfg, str(path))
    assert len(records) == 1
    assert records[0]["TI"] == "标题"
