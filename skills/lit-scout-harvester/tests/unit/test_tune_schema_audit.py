"""Tests for the pre-comparison label audit in scripts/tune_schema.py (C6)."""

from __future__ import annotations

import json

import pytest

import scripts.tune_schema as tune_schema
from lit_scout_harvester.core.config_loader import LLMConfig, PipelineConfig
from scripts.tune_schema import (
    AUDIT_WARNING_HEAD,
    format_audit_warning,
    run_label_audit,
)

HEADER = "title,abstract,author,pubYear,source,DOI,sampleType,indicator,sampleSource,timeScale,sciQuestion,contribution"


def _gold_csv(tmp_path, name, rows):
    import csv

    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER.split(","))
        writer.writerows(rows)
    return path


def _residual_rows():
    return (
        ["t%d" % i, "ab", "au", "2020", "src", "10.1/rs%d" % i, "OTHER; A; B", "", "", "", "", ""]
        for i in range(9)
    )


def _residual_csv(tmp_path):
    rows = list(_residual_rows())
    rows += [["t%d" % i, "ab", "au", "2020", "src", "10.1/a%d" % i, "A", "", "", "", "", ""] for i in range(6)]
    rows += [["t%d" % i, "ab", "au", "2020", "src", "10.1/b%d" % i, "B", "", "", "", "", ""] for i in range(5)]
    return _gold_csv(tmp_path, "residual.csv", rows)


def _clean_csv(tmp_path):
    rows = [["t%d" % i, "ab", "au", "2020", "src", "10.1/c%d" % i, "A", "", "", "", "", ""] for i in range(8)]
    rows += [["t%d" % i, "ab", "au", "2020", "src", "10.1/d%d" % i, "B", "", "", "", "", ""] for i in range(7)]
    return _gold_csv(tmp_path, "clean.csv", rows)


def test_run_label_audit_detects_residual_bucket():
    gold = [{"sampleType": ["OTHER", "A", "B"]} for _ in range(9)]
    gold += [{"sampleType": ["A"]} for _ in range(6)]
    gold += [{"sampleType": ["B"]} for _ in range(5)]
    findings = run_label_audit(gold, ["sampleType"])
    labels = [c["label"] for c in findings["residual_candidates"]]
    assert "OTHER" in labels
    warning = format_audit_warning(findings)
    assert warning is not None
    assert warning.startswith(AUDIT_WARNING_HEAD)
    assert "sampleType:OTHER" in warning


def test_format_audit_warning_none_when_clean():
    gold = [{"sampleType": ["A"]}, {"sampleType": ["B"]}]
    assert format_audit_warning(run_label_audit(gold, ["sampleType"])) is None


def test_strict_audit_exits_nonzero_before_pipeline(tmp_path, capsys):
    csv_path = _residual_csv(tmp_path)
    rc = tune_schema.entrypoint(
        ["--annotations", str(csv_path), "--wos", "tests/fixtures/sample.wos", "--strict-audit"]
    )
    assert rc != 0
    out = capsys.readouterr().out
    assert AUDIT_WARNING_HEAD in out
    assert "residual_candidates" in out


def test_strict_audit_passes_when_clean(monkeypatch, tmp_path, capsys):
    _stub_pipeline(monkeypatch)
    csv_path = _clean_csv(tmp_path)
    rc = _run_main(tmp_path, csv_path, extra=("--strict-audit",))
    assert rc == 0
    assert AUDIT_WARNING_HEAD not in capsys.readouterr().out
    report = json.loads((tmp_path / "out" / "tuning_report.json").read_text(encoding="utf-8"))
    assert "audit_warning" not in report


def _stub_pipeline(monkeypatch):
    """Keep main() offline: stub config loading, cache/LLM clients and variant runs."""

    def fake_load_config(path):
        return PipelineConfig(
            version="1.0",
            llm=LLMConfig(provider="deepseek", model="m", api_key="k", base_url="http://localhost"),
            modules={},
        )

    class _Dummy:
        def __init__(self, *args, **kwargs):
            pass

    async def fake_run_variant(config, cache, llm_client, matched_sample, schema_path, variant):
        preds = []
        for m in matched_sample:
            pred = {"record_id": m["gold"]["record_id"]}
            for field in tune_schema.GOLD_FIELDS:
                pred[field] = m["gold"][field]
            preds.append(pred)
        return preds

    monkeypatch.setattr(tune_schema, "load_config", fake_load_config)
    monkeypatch.setattr(tune_schema, "CacheManager", _Dummy)
    monkeypatch.setattr(tune_schema, "LLMClient", _Dummy)
    monkeypatch.setattr(tune_schema, "run_variant", fake_run_variant)


def _run_main(tmp_path, csv_path, extra=()):
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out"
    return tune_schema.entrypoint(
        [
            "--annotations",
            str(csv_path),
            "--wos",
            "tests/fixtures/sample.wos",
            "--schema",
            str(schema),
            "--n",
            "5",
            "--output-dir",
            str(out_dir),
            "--variants",
            "baseline",
            *extra,
        ]
    )


def test_report_head_carries_audit_warning(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    rc = _run_main(tmp_path, _residual_csv(tmp_path))
    assert rc == 0
    report = json.loads((tmp_path / "out" / "tuning_report.json").read_text(encoding="utf-8"))
    # 报告头部第一键即警告
    first_key = next(iter(report))
    assert first_key == "audit_warning"
    assert report["audit_warning"].startswith(AUDIT_WARNING_HEAD)


def test_report_without_residual_has_no_warning(monkeypatch, tmp_path):
    _stub_pipeline(monkeypatch)
    rc = _run_main(tmp_path, _clean_csv(tmp_path))
    assert rc == 0
    report = json.loads((tmp_path / "out" / "tuning_report.json").read_text(encoding="utf-8"))
    assert "audit_warning" not in report
    assert next(iter(report)) == "sample_size"
