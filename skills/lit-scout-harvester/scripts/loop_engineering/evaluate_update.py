"""Evaluate the effect of an executed update plan.

Compares metrics from a ``before`` feedback digest with an ``after`` digest and
writes an evaluation report.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scripts.loop_engineering.common import ensure_dir, load_feedback_digest, render_template


REPORT_TEMPLATE = Path(__file__).with_name("templates") / "evaluation_report_template.md"


def _metric_deltas(
    before: Dict[str, Any], after: Dict[str, Any]
) -> List[Tuple[str, Any, Any, str]]:
    rows: List[Tuple[str, Any, Any, str]] = []
    keys = set(before.keys()) | set(after.keys())
    for key in sorted(keys):
        b = before.get(key)
        a = after.get(key)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            delta = a - b
            symbol = "↑" if delta > 0 else ("↓" if delta < 0 else "-")
            rows.append((key, b, a, f"{symbol} {delta:+.4f}"))
        else:
            rows.append((key, b, a, "-"))
    return rows


def evaluate_update(
    before_digest_path: Path,
    after_digest_path: Path,
    plan_id: str,
    output_dir: Path,
) -> Path:
    """Compare two digests and write an evaluation report."""
    before = load_feedback_digest(before_digest_path).get("metrics", {})
    after = load_feedback_digest(after_digest_path).get("metrics", {})

    deltas = _metric_deltas(before, after)
    metrics_rows = "\n".join(
        f"| {name} | {before_val} | {after_val} | {delta} |"
        for name, before_val, after_val, delta in deltas
    )

    # Simple heuristic: if low_confidence_rate or error rates increased, suggest rollback.
    low_before = before.get("low_confidence_rate", 0.0)
    low_after = after.get("low_confidence_rate", 0.0)
    err_before = before.get("module_error_rate", 0.0) + before.get("parser_error_rate", 0.0)
    err_after = after.get("module_error_rate", 0.0) + after.get("parser_error_rate", 0.0)

    if low_after > low_before or err_after > err_before:
        conclusion = "关键指标出现退化，建议评估是否回滚。"
        recommendation = (
            "**建议回滚**：使用备份文件恢复 schema，或运行 `git checkout` 撤销改动。\n"
            "然后检查 prompt_hints 是否引入了过度约束。"
        )
    else:
        conclusion = "关键指标稳定或改善，更新效果符合预期。"
        recommendation = "继续观察后续运行数据；如指标持续稳定，可保留此次更新。"

    report_id = f"EVALUATION_REPORT_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    variables = {
        "report_id": report_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan_id": plan_id,
        "metrics_rows": metrics_rows,
        "conclusion": conclusion,
        "recommendation": recommendation,
    }

    report_text = render_template(REPORT_TEMPLATE, variables)
    ensure_dir(output_dir)
    report_path = output_dir / f"{report_id}.md"
    report_path.write_text(report_text, encoding="utf-8")
    return report_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate an executed update plan")
    parser.add_argument("--before", type=Path, required=True, help="Feedback digest before update")
    parser.add_argument("--after", type=Path, required=True, help="Feedback digest after update")
    parser.add_argument("--plan-id", default="unknown", help="Plan ID being evaluated")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("update_plan/executed"),
        help="Directory for evaluation report",
    )
    args = parser.parse_args(argv)

    report_path = evaluate_update(args.before, args.after, args.plan_id, args.output_dir)
    print(f"Wrote evaluation report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
