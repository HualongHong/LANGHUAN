"""Release gate: per-field PASS/FAIL against caliber acceptance thresholds (C8).

Reads an evaluation report produced by scripts/evaluate_schema.py (JSON, or a
CSV with ``field,majority_baseline,model_accuracy`` columns) together with the
caliber registry (config/caliber.json, falling back to built-in defaults), and
prints a per-field acceptance table. A field passes only when its net gain
over the majority baseline reaches ``acceptance.net_gain_threshold_pt``;
failures carry a per-field suggestion (use_rules / manual / weak) — there is
no total score.

With ``--fail-on-reject`` the exit code is non-zero unless every evaluated
field passes, so the command can act as a publish gate in CI.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lit_scout_harvester.core.config_loader import AcceptanceThresholds, load_caliber
from scripts.evaluate_schema import verdict_for

REQUIRED_CSV_COLUMNS = ("field", "majority_baseline", "model_accuracy")


def load_field_metrics(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load per-field metrics from an evaluate_schema JSON report or a CSV."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return _load_csv_metrics(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"{path} does not look like an evaluate_schema report (missing 'fields')")
    return {name: dict(metrics) for name, metrics in fields.items()}


def _load_csv_metrics(path: Path) -> Dict[str, Dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(REQUIRED_CSV_COLUMNS).issubset(reader.fieldnames):
            raise ValueError(
                f"{path} must contain columns: {', '.join(REQUIRED_CSV_COLUMNS)}"
            )
        metrics: Dict[str, Dict[str, Any]] = {}
        for row in reader:
            name = (row.get("field") or "").strip()
            if not name:
                continue
            entry: Dict[str, Any] = {
                "majority_baseline": float(row["majority_baseline"]),
                "model_accuracy": float(row["model_accuracy"]),
            }
            if row.get("net_gain") not in (None, ""):
                entry["net_gain"] = float(row["net_gain"])
            if row.get("majority_share") not in (None, ""):
                entry["majority_share"] = float(row["majority_share"])
            metrics[name] = entry
    return metrics


def build_acceptance_rows(
    field_metrics: Dict[str, Dict[str, Any]],
    acceptance: AcceptanceThresholds,
) -> List[Dict[str, Any]]:
    """One acceptance row per field: baseline/model/net_gain/verdict/result."""
    rows = []
    for field in sorted(field_metrics):
        metrics = field_metrics[field]
        baseline = float(metrics.get("majority_baseline", 0.0))
        model = float(metrics.get("model_accuracy", 0.0))
        net_gain = metrics.get("net_gain")
        net_gain_pt = float(net_gain) if net_gain is not None else round((model - baseline) * 100, 1)
        share = float(metrics.get("majority_share", baseline))
        rows.append(
            {
                "field": field,
                "majority_baseline": round(baseline, 3),
                "model_accuracy": round(model, 3),
                "net_gain": round(net_gain_pt, 1),
                "majority_share": round(share, 3),
                "verdict": verdict_for(net_gain_pt, share, acceptance),
                "result": "PASS" if net_gain_pt >= acceptance.net_gain_threshold_pt else "FAIL",
            }
        )
    return rows


def _print_table(rows: List[Dict[str, Any]], acceptance: AcceptanceThresholds) -> None:
    header = ("field", "majority_baseline", "model_accuracy", "net_gain", "verdict", "result")
    widths = {
        "field": max(len("field"), max((len(r["field"]) for r in rows), default=0)),
        "majority_baseline": len("majority_baseline"),
        "model_accuracy": len("model_accuracy"),
        "net_gain": len("net_gain"),
        "verdict": max(len("verdict"), max((len(r["verdict"]) for r in rows), default=0)),
        "result": len("result"),
    }

    def _line(values: Dict[str, str]) -> str:
        return "  ".join(values[col].ljust(widths[col]) for col in header).rstrip()

    print(_line({col: col for col in header}))
    for row in rows:
        print(
            _line(
                {
                    "field": row["field"],
                    "majority_baseline": f"{row['majority_baseline'] * 100:.1f}%",
                    "model_accuracy": f"{row['model_accuracy'] * 100:.1f}%",
                    "net_gain": f"{row['net_gain']:+.1f}",
                    "verdict": row["verdict"],
                    "result": row["result"],
                }
            )
        )
    print(
        f"\nnet_gain threshold: >= {acceptance.net_gain_threshold_pt:g} pt  |  "
        f"manual spot-check rate: {acceptance.sample_rate:.0%} of production output"
    )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Per-field acceptance gate against caliber thresholds")
    parser.add_argument("--eval", type=Path, required=True, help="Evaluation report (evaluate_schema JSON, or CSV)")
    parser.add_argument("--caliber", type=Path, default=None, help="Path to caliber.json (default: config/caliber.json)")
    parser.add_argument("--fail-on-reject", action="store_true", help="Exit non-zero unless every field passes")
    parser.add_argument("--output", type=Path, default=None, help="Write acceptance table JSON to file")
    args = parser.parse_args(argv)

    acceptance = load_caliber(args.caliber).acceptance
    field_metrics = load_field_metrics(args.eval)
    rows = build_acceptance_rows(field_metrics, acceptance)

    if rows:
        _print_table(rows, acceptance)
    passed = sum(1 for r in rows if r["result"] == "PASS")
    print(f"{passed}/{len(rows)} fields PASS")

    if args.output:
        args.output.write_text(
            json.dumps(
                {"acceptance": acceptance.model_dump(), "fields": rows},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    if args.fail_on_reject and any(r["result"] == "FAIL" for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
