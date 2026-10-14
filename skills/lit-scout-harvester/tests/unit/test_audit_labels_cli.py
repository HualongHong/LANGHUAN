"""Tests for scripts/audit_labels.py (C6 CLI smoke)."""

from __future__ import annotations

import json

from scripts.audit_labels import main


def _write_gold_csv(tmp_path):
    csv_path = tmp_path / "gold.csv"
    rows = ["sampleType"] + ["OTHER; A; B"] * 9 + ["A"] * 6 + ["B"] * 5
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return csv_path


def test_cli_smoke_human_readable(tmp_path, capsys):
    csv_path = _write_gold_csv(tmp_path)
    rc = main(["--input", str(csv_path), "--field", "sampleType", "--segment-size", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "残差桶" in out
    assert "Lift" in out
    assert "稀释曲线" in out
    assert "结论与建议" in out
    assert "OTHER" in out
    # 三段结论
    assert "\n1. " in out
    assert "\n2. " in out
    assert "\n3. " in out


def test_cli_json_mode(tmp_path, capsys):
    csv_path = _write_gold_csv(tmp_path)
    rc = main(["--input", str(csv_path), "--field", "sampleType", "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["n_records"] == 20
    assert report["field"] == "sampleType"
    labels = [c["label"] for c in report["residual_candidates"]]
    assert "OTHER" in labels
    assert report["lift_matrix"]
    assert report["dilution_curve"]


def test_cli_threshold_override(tmp_path, capsys):
    csv_path = _write_gold_csv(tmp_path)
    rc = main(
        [
            "--input",
            str(csv_path),
            "--field",
            "sampleType",
            "--residual-share-min",
            "0.8",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "未发现残差桶候选" in out
