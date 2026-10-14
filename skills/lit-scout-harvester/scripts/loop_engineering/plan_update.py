"""Generate update plans from confirmed Men_issues and feedback digest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from scripts.loop_engineering.common import (
    UPDATE_TYPES,
    ensure_dir,
    load_feedback_digest,
    load_men_issues,
    plan_dirs,
    render_template,
)


TEMPLATE_PATH = Path(__file__).with_name("templates") / "update_plan_template.md"


def _infer_update_type(issue: Dict[str, Any]) -> str:
    if issue.get("suggested_update_type") in UPDATE_TYPES.values():
        return issue["suggested_update_type"]
    issue_type = issue.get("issue_type", "")
    return UPDATE_TYPES.get(issue_type, "schema_tuning")


def _find_default_schema_path() -> str:
    candidates = [
        "lit_scout_harvester/schemas/sample_rst.json",
        "lit_scout_harvester/schemas/generic.json",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "lit_scout_harvester/schemas/sample_rst.json"


def _build_plan_markdown(issue: Dict[str, Any], plan_id: str) -> str:
    update_type = _infer_update_type(issue)
    affected_field = issue.get("affected_field") or issue.get("field") or "unknown"
    schema_path = _find_default_schema_path()

    root_cause = (
        f"字段/模块 `{affected_field}` 在多次运行中重复出现问题，"
        f"错误类型为 `{issue.get('issue_type', 'unknown')}`。"
    )

    effort_map = {
        "schema_tuning": "低（仅修改 schema prompt_hints）",
        "parser_fix": "中（需修改 parser 并补测试）",
        "output_format": "低（仅修改 output_writer）",
        "performance": "高（需 profiling 与并发调优）",
        "schema_extension": "中（需新增 schema 字段与 prompt）",
        "model_switch": "低（切换模型 profile）",
    }
    effort = effort_map.get(update_type, "中")

    rollback = (
        "从 `update_plan/executed/<plan_id>/schema.json.bak` 恢复原始 schema，"
        "或 git checkout 相关文件。"
    )

    acceptance = (
        f"1. 单元测试通过；\n"
        f"2. 相关字段的低置信度/错误率下降或保持；\n"
        f"3. 人工抽检 `{affected_field}` 输出符合预期。"
    )

    variables = {
        "plan_id": plan_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "update_type": update_type,
        "issue_id": issue.get("issue_id", ""),
        "affected_field": affected_field,
        "issue_description": issue.get("issue_description", ""),
        "root_cause": root_cause,
        "proposed_solution": issue.get("proposed_solution", ""),
        "schema_path": schema_path,
        "affected_files": f"`{schema_path}`",
        "effort": effort,
        "rollback": rollback,
        "acceptance": acceptance,
    }

    return render_template(TEMPLATE_PATH, variables)


def _parse_front_matter(plan_text: str) -> Dict[str, Any]:
    if plan_text.strip().startswith("---"):
        parts = plan_text.split("---", 2)
        if len(parts) >= 3:
            import yaml

            return yaml.safe_load(parts[1]) or {}
    return {}


def generate_plans(
    men_issues_path: Path,
    update_plan_dir: Path,
    digest_path: Optional[Path] = None,
    max_pending: int = 10,
) -> List[Path]:
    """Generate pending update plans from confirmed Men_issues.

    Args:
        men_issues_path: Path to Men_issues.md.
        update_plan_dir: Base directory for update plans.
        digest_path: Optional feedback digest for enrichment.
        max_pending: Maximum number of pending plans allowed.

    Returns:
        List of newly created pending plan file paths.
    """
    confirmed = load_men_issues(men_issues_path)
    confirmed = [i for i in confirmed if i.get("status") == "confirmed"]

    dirs = plan_dirs(update_plan_dir)
    existing_pending = list(dirs["pending"].glob("UPDATE_PLAN_*.md"))

    created: List[Path] = []
    if len(existing_pending) >= max_pending:
        return created

    digest = load_feedback_digest(digest_path) if digest_path else {}
    digest_issues = {d.get("issue_id"): d for d in digest.get("general_issues", [])}

    for issue in confirmed:
        if len(existing_pending) + len(created) >= max_pending:
            break

        issue_id = issue.get("issue_id")
        if any(issue_id in p.name for p in existing_pending):
            continue

        # Enrich with digest info if available.
        if issue_id in digest_issues:
            issue = {**digest_issues[issue_id], **issue}

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        plan_id = f"UPDATE_PLAN_{timestamp}_{issue_id}"
        plan_text = _build_plan_markdown(issue, plan_id)
        plan_path = dirs["pending"] / f"{plan_id}.md"
        plan_path.write_text(plan_text, encoding="utf-8")
        created.append(plan_path)

    return created


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate update plans from confirmed issues")
    parser.add_argument("--men-issues", type=Path, default=Path("data/Men_issues.md"))
    parser.add_argument("--digest", type=Path, default=Path("feedback/feedback_digest.json"))
    parser.add_argument("--update-plan-dir", type=Path, default=Path("update_plan"))
    parser.add_argument("--max-pending", type=int, default=10)
    args = parser.parse_args(argv)

    created = generate_plans(
        args.men_issues,
        args.update_plan_dir,
        args.digest if args.digest.exists() else None,
        args.max_pending,
    )
    print(f"Generated {len(created)} pending plan(s).")
    for p in created:
        print(f"  - {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
