"""Collect feedback from the review queue and recent outputs.

Produces ``feedback/feedback_digest.json`` containing general/specific issues
and global metrics. General issues can later be proposed to ``Men_issues.md``
by ``issue_manager.py propose``.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from scripts.loop_engineering.common import (
    UPDATE_TYPES,
    ensure_dir,
    load_feedback_digest,
    now_iso,
    save_feedback_digest,
)


def _parse_flag(flag: str) -> Tuple[str, Optional[str]]:
    """Parse a flag like 'low_confidence:indicator' -> (type, field)."""
    if ":" in flag:
        return flag.split(":", 1)
    return flag, None


def _collect_review_queue(db_path: Path) -> List[Dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT record_id, flags, extracted FROM review_queue WHERE status = 'pending'"
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        flags = row["flags"].split("; ") if row["flags"] else []
        extracted = row["extracted"] or "{}"
        try:
            extracted_obj = json.loads(extracted)
        except json.JSONDecodeError:
            extracted_obj = {}
        items.append(
            {
                "record_id": row["record_id"],
                "flags": flags,
                "extracted": extracted_obj,
            }
        )
    return items


def _aggregate_issues(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Aggregate flags into buckets keyed by (flag_type, field/module)."""
    buckets: Dict[str, Dict[str, Any]] = {}

    for item in items:
        for flag in item.get("flags", []):
            flag_type, detail = _parse_flag(flag)
            # Use detail as affected field/module when available.
            key_parts = [flag_type, detail] if detail else [flag_type]
            key = ":".join(key_parts)
            bucket = buckets.setdefault(
                key,
                {
                    "issue_type": flag_type,
                    "field": detail,
                    "count": 0,
                    "example_record_ids": [],
                },
            )
            bucket["count"] += 1
            if len(bucket["example_record_ids"]) < 5:
                bucket["example_record_ids"].append(item["record_id"])

    return buckets


def _classify_issues(
    buckets: Dict[str, Dict[str, Any]], total_reviewed: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Classify aggregated buckets into general/specific issues."""
    general: List[Dict[str, Any]] = []
    specific: List[Dict[str, Any]] = []

    for key, bucket in buckets.items():
        count = bucket["count"]
        rate = count / total_reviewed if total_reviewed else 0.0
        issue_type = bucket["issue_type"]
        field = bucket["field"]
        update_type = UPDATE_TYPES.get(issue_type, "schema_tuning")

        # Thresholds for general vs specific.
        is_general = False
        if issue_type == "low_confidence" and (count >= 5 or (count >= 3 and rate >= 0.05)):
            is_general = True
        elif issue_type in {"module_error", "parser_error", "pipeline_error"} and count >= 3:
            is_general = True
        elif count >= 5:
            is_general = True

        issue = {
            "issue_id": key.replace(":", "_").upper(),
            "issue_type": issue_type,
            "field": field,
            "count": count,
            "rate": round(rate, 4),
            "example_record_ids": bucket["example_record_ids"],
            "suggested_update_type": update_type,
            "proposed_solution": _propose_solution(issue_type, field, update_type),
        }

        if is_general:
            general.append(issue)
        else:
            specific.append(issue)

    return general, specific


def _propose_solution(issue_type: str, field: Optional[str], update_type: str) -> str:
    field_text = f" `{field}`" if field else ""
    if update_type == "schema_tuning":
        return (
            f"在对应 schema 中为{field_text}字段追加 prompt_hints 与示例，"
            "约束模型输出并提升置信度。"
        )
    if update_type == "parser_fix":
        return f"检查并增强解析器对{field_text}相关输入的处理逻辑。"
    if update_type == "output_format":
        return f"调整 output_writer 对{field_text}的序列化或展平方式。"
    if update_type == "performance":
        return "分析热点路径并优化并发/批量策略。"
    if update_type == "model_switch":
        return "尝试更换模型 profile 或调低 temperature 以提升输出质量。"
    return "进一步人工分析根因后制定修复方案。"


def _compute_metrics(items: List[Dict[str, Any]]) -> Dict[str, float]:
    total = len(items)
    if total == 0:
        return {
            "total_reviewed": 0,
            "low_confidence_rate": 0.0,
            "module_error_rate": 0.0,
            "parser_error_rate": 0.0,
        }

    low_confidence = sum(
        1 for item in items if any(f.startswith("low_confidence") for f in item.get("flags", []))
    )
    module_error = sum(
        1 for item in items if any(f.startswith("module_error") for f in item.get("flags", []))
    )
    parser_error = sum(
        1
        for item in items
        if any(f.startswith(("parser_error", "pipeline_error")) for f in item.get("flags", []))
    )

    return {
        "total_reviewed": total,
        "low_confidence_rate": round(low_confidence / total, 4),
        "module_error_rate": round(module_error / total, 4),
        "parser_error_rate": round(parser_error / total, 4),
    }


def collect_feedback(
    review_queue_db: Path,
    feedback_path: Path,
    previous_digest: Optional[Path] = None,
) -> Dict[str, Any]:
    """Collect feedback and write a digest file.

    Args:
        review_queue_db: Path to the review queue SQLite database.
        feedback_path: Path where ``feedback_digest.json`` will be written.
        previous_digest: Optional previous digest to merge metrics.

    Returns:
        The generated digest dictionary.
    """
    items = _collect_review_queue(review_queue_db)
    buckets = _aggregate_issues(items)
    general, specific = _classify_issues(buckets, len(items))
    metrics = _compute_metrics(items)

    digest: Dict[str, Any] = {
        "generated_at": now_iso(),
        "summary": {
            "total_reviewed": metrics["total_reviewed"],
            "general_issues": len(general),
            "specific_issues": len(specific),
        },
        "general_issues": general,
        "specific_issues": specific,
        "metrics": metrics,
    }

    if previous_digest and previous_digest.exists():
        prev = load_feedback_digest(previous_digest)
        digest["previous_metrics"] = prev.get("metrics", {})

    ensure_dir(feedback_path.parent)
    save_feedback_digest(feedback_path, digest)
    return digest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect pipeline feedback into a digest")
    parser.add_argument(
        "--review-queue",
        type=Path,
        default=Path("data/review_queue.db"),
        help="Path to review_queue SQLite database",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("feedback/feedback_digest.json"),
        help="Output digest path",
    )
    parser.add_argument(
        "--previous",
        type=Path,
        default=None,
        help="Previous digest for metric comparison",
    )
    args = parser.parse_args(argv)

    digest = collect_feedback(args.review_queue, args.output, args.previous)
    print(json.dumps(digest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
