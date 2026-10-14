"""Tests for scripts/acceptance.py — per-field release gate (C8)."""

from __future__ import annotations

import csv
import json

from scripts.acceptance import build_acceptance_rows, load_field_metrics, main
from scripts.evaluate_schema import evaluate
from lit_scout_harvester.core.config_loader import AcceptanceThresholds, load_caliber


def _passing_and_failing_metrics():
    return {
        "good_field": {"majority_baseline": 0.25, "model_accuracy": 0.5},
        "bad_field": {"majority_baseline": 0.9, "model_accuracy": 0.9},
    }


def test_acceptance_boundary_values():
    """Exactly at the 15 pt line passes; just below fails."""
    rows = build_acceptance_rows(
        {
            "at_line": {"majority_baseline": 0.5, "model_accuracy": 0.65},
            "below_line": {"majority_baseline": 0.5, "model_accuracy": 0.649},
        },
        AcceptanceThresholds(),
    )
    by_field = {r["field"]: r for r in rows}
    assert by_field["at_line"]["net_gain"] == 15.0
    assert by_field["at_line"]["result"] == "PASS"
    assert by_field["below_line"]["net_gain"] == 14.9
    assert by_field["below_line"]["result"] == "FAIL"
    assert by_field["below_line"]["verdict"] == "weak"


def test_acceptance_uses_caliber_thresholds(tmp_path):
    caliber_path = tmp_path / "caliber.json"
    caliber_path.write_text(
        json.dumps({"acceptance": {"net_gain_threshold_pt": 10.0}}), encoding="utf-8"
    )
    rows = build_acceptance_rows(
        {"field": {"majority_baseline": 0.5, "model_accuracy": 0.6}},
        load_caliber(caliber_path).acceptance,
    )
    assert rows[0]["net_gain"] == 10.0
    assert rows[0]["result"] == "PASS"


def test_load_field_metrics_from_csv(tmp_path):
    path = tmp_path / "metrics.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["field", "majority_baseline", "model_accuracy"])
        writer.writeheader()
        writer.writerow({"field": "f1", "majority_baseline": 0.25, "model_accuracy": 0.5})
    metrics = load_field_metrics(path)
    assert metrics["f1"]["majority_baseline"] == 0.25
    assert "net_gain" not in metrics["f1"]  # derived by build_acceptance_rows


def test_main_fail_on_reject_exit_codes(tmp_path, capsys):
    gold = [{"record_id": f"g{i}", "label": "climate"} for i in range(9)] + [
        {"record_id": "h0", "label": "health"}
    ]
    report = evaluate(gold, [{"record_id": r["record_id"], "label": "climate"} for r in gold], ["label"])
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    # Gate mode: failing field -> non-zero exit.
    assert main(["--eval", str(report_path), "--fail-on-reject"]) == 1
    # Report-only mode: exit stays zero.
    assert main(["--eval", str(report_path)]) == 0
    out = capsys.readouterr().out
    assert "use_rules" in out
    assert "FAIL" in out


def test_main_all_pass(tmp_path):
    # Mode share 0.4; a perfect model nets +60 pt -> PASS.
    labels = ["alpha", "alpha", "alpha", "alpha", "beta", "beta", "gamma", "gamma", "delta", "delta"]
    gold = [{"record_id": f"g{i}", "label": lab} for i, lab in enumerate(labels)]
    pred = [{"record_id": r["record_id"], "label": r["label"]} for r in gold]
    report = evaluate(gold, pred, ["label"])
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    out_path = tmp_path / "acceptance.json"
    assert main(["--eval", str(report_path), "--fail-on-reject", "--output", str(out_path)]) == 0
    doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert doc["fields"][0]["field"] == "label"
    assert doc["fields"][0]["net_gain"] == 60.0
    assert doc["fields"][0]["result"] == "PASS"
