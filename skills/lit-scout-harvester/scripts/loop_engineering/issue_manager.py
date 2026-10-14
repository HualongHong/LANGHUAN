"""Dual-channel maintenance of ``Men_issues.md``.

- ``propose``: create pending issues from a feedback digest.
- ``review``: interactive confirmation of auto-proposed issues.
- ``confirm``/``edit``/``delete``: programmatic issue management.
- ``solve``: mark an issue solved and remove it from the active ledger.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from scripts.loop_engineering.common import (
    SOURCE_AUTO,
    SOURCE_HUMAN,
    STATUS_CONFIRMED,
    STATUS_PENDING,
    STATUS_SOLVED,
    load_feedback_digest,
    load_men_issues,
    next_issue_id,
    now_iso,
    save_men_issues,
)


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------


def propose_issues(
    men_issues_path: Path,
    digest_path: Path,
    min_count: int = 3,
    min_rate: float = 0.05,
) -> List[Dict[str, Any]]:
    """Propose pending issues from a feedback digest.

    Only issues that do not already exist (by issue_type+field) are added.
    """
    digest = load_feedback_digest(digest_path)
    existing = load_men_issues(men_issues_path)
    existing_keys = {
        (i.get("issue_type"), i.get("affected_field")) for i in existing
    }

    new_issues: List[Dict[str, Any]] = []
    for item in digest.get("general_issues", []):
        issue_type = item.get("issue_type", "unknown")
        field = item.get("field")
        count = item.get("count", 0)
        rate = item.get("rate", 0.0)

        if (issue_type, field) in existing_keys:
            continue
        if count < min_count and rate < min_rate:
            continue

        new_issues.append(
            {
                "issue_id": next_issue_id(existing + new_issues),
                "issue_description": _build_description(item),
                "proposed_solution": item.get("proposed_solution", ""),
                "source": SOURCE_AUTO,
                "status": STATUS_PENDING,
                "affected_field": field,
                "suggested_update_type": item.get("suggested_update_type", "schema_tuning"),
                "evidence": [
                    {
                        "run_name": "feedback_digest",
                        "count": count,
                        "examples": item.get("example_record_ids", []),
                    }
                ],
                "created_at": now_iso(),
                "resolved_at": None,
            }
        )

    if new_issues:
        save_men_issues(men_issues_path, existing + new_issues)

    return new_issues


def _build_description(item: Dict[str, Any]) -> str:
    issue_type = item.get("issue_type", "unknown")
    field = item.get("field")
    count = item.get("count", 0)
    if issue_type == "low_confidence" and field:
        return f"字段 `{field}` 出现 {count} 次低置信度输出"
    if field:
        return f"模块/字段 `{field}` 出现 {count} 次 `{issue_type}` 问题"
    return f"出现 {count} 次 `{issue_type}` 问题"


def list_pending(men_issues_path: Path) -> List[Dict[str, Any]]:
    issues = load_men_issues(men_issues_path)
    return [i for i in issues if i.get("status") == STATUS_PENDING and i.get("source") == SOURCE_AUTO]


def list_confirmed(men_issues_path: Path) -> List[Dict[str, Any]]:
    issues = load_men_issues(men_issues_path)
    return [i for i in issues if i.get("status") == STATUS_CONFIRMED]


def _update_issue(
    men_issues_path: Path,
    issue_id: str,
    updater: Any,
) -> bool:
    issues = load_men_issues(men_issues_path)
    for issue in issues:
        if issue.get("issue_id") == issue_id:
            updater(issue)
            save_men_issues(men_issues_path, issues)
            return True
    return False


def confirm_issue(
    men_issues_path: Path,
    issue_id: str,
    edited_description: Optional[str] = None,
    edited_solution: Optional[str] = None,
) -> bool:
    def _updater(issue: Dict[str, Any]) -> None:
        if edited_description is not None:
            issue["issue_description"] = edited_description
        if edited_solution is not None:
            issue["proposed_solution"] = edited_solution
        issue["status"] = STATUS_CONFIRMED

    return _update_issue(men_issues_path, issue_id, _updater)


def edit_issue(
    men_issues_path: Path,
    issue_id: str,
    description: str,
    solution: str,
) -> bool:
    return confirm_issue(men_issues_path, issue_id, description, solution)


def delete_issue(men_issues_path: Path, issue_id: str) -> bool:
    issues = load_men_issues(men_issues_path)
    new_issues = [i for i in issues if i.get("issue_id") != issue_id]
    if len(new_issues) == len(issues):
        return False
    save_men_issues(men_issues_path, new_issues)
    return True


def solve_issue(
    men_issues_path: Path,
    issue_id: str,
    archive_dir: Optional[Path] = None,
) -> bool:
    """Mark an issue solved, optionally archive it, and remove from active ledger."""
    issues = load_men_issues(men_issues_path)
    target: Optional[Dict[str, Any]] = None
    remaining: List[Dict[str, Any]] = []
    for issue in issues:
        if issue.get("issue_id") == issue_id:
            target = issue
        else:
            remaining.append(issue)

    if target is None:
        return False

    target["status"] = STATUS_SOLVED
    target["resolved_at"] = now_iso()

    if archive_dir is not None:
        ensure_dir(archive_dir)
        archive_path = archive_dir / f"{issue_id}.yaml"
        archive_path.write_text(
            f"---\n{yaml.safe_dump({'issue': target}, allow_unicode=True, sort_keys=False)}---\n",
            encoding="utf-8",
        )

    save_men_issues(men_issues_path, remaining)
    return True


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_issue(issue: Dict[str, Any]) -> None:
    print(f"ID: {issue.get('issue_id')}")
    print(f"Description: {issue.get('issue_description')}")
    print(f"Proposed solution: {issue.get('proposed_solution')}")
    evidence = issue.get("evidence", [])
    if evidence:
        ev = evidence[0]
        print(f"Evidence: count={ev.get('count')}, examples={ev.get('examples', [])}")
    print()


def _interactive_review(men_issues_path: Path) -> int:
    pending = list_pending(men_issues_path)
    if not pending:
        print("No pending auto-proposed issues to review.")
        return 0

    confirmed = 0
    for issue in pending:
        _print_issue(issue)
        choice = input("[c]onfirm / [e]dit / [d]elete / [s]kip? ").strip().lower()
        if choice == "c":
            confirm_issue(men_issues_path, issue["issue_id"])
            confirmed += 1
            print("Confirmed.\n")
        elif choice == "e":
            desc = input("New description: ").strip()
            sol = input("New proposed solution: ").strip()
            edit_issue(men_issues_path, issue["issue_id"], desc or issue["issue_description"], sol or issue["proposed_solution"])
            confirmed += 1
            print("Edited and confirmed.\n")
        elif choice == "d":
            delete_issue(men_issues_path, issue["issue_id"])
            print("Deleted.\n")
        else:
            print("Skipped.\n")

    print(f"Reviewed {len(pending)} issues; {confirmed} confirmed.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Manage Men_issues.md")
    parser.add_argument(
        "--men-issues",
        type=Path,
        default=Path("data/Men_issues.md"),
        help="Path to Men_issues.md",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_propose = sub.add_parser("propose", help="Propose pending issues from feedback digest")
    p_propose.add_argument("--digest", type=Path, default=Path("feedback/feedback_digest.json"))
    p_propose.add_argument("--min-count", type=int, default=3)
    p_propose.add_argument("--min-rate", type=float, default=0.05)

    p_review = sub.add_parser("review", help="Interactive review of pending auto issues")

    p_list = sub.add_parser("list", help="List confirmed issues")

    p_confirm = sub.add_parser("confirm", help="Confirm a pending issue")
    p_confirm.add_argument("issue_id")
    p_confirm.add_argument("--description", default=None)
    p_confirm.add_argument("--solution", default=None)

    p_delete = sub.add_parser("delete", help="Delete an issue")
    p_delete.add_argument("issue_id")

    p_solve = sub.add_parser("solve", help="Mark an issue solved and remove it")
    p_solve.add_argument("issue_id")
    p_solve.add_argument("--archive-dir", type=Path, default=None)

    args = parser.parse_args(argv)

    if args.command == "propose":
        new = propose_issues(args.men_issues, args.digest, args.min_count, args.min_rate)
        print(f"Proposed {len(new)} new issue(s).")
        for issue in new:
            print(f"  - {issue['issue_id']}: {issue['issue_description']}")
        return 0

    if args.command == "review":
        return _interactive_review(args.men_issues)

    if args.command == "list":
        for issue in list_confirmed(args.men_issues):
            print(f"{issue['issue_id']} [{issue.get('source')}] {issue['issue_description']}")
        return 0

    if args.command == "confirm":
        if confirm_issue(args.men_issues, args.issue_id, args.description, args.solution):
            print(f"Confirmed {args.issue_id}.")
            return 0
        print(f"Issue {args.issue_id} not found.")
        return 1

    if args.command == "delete":
        if delete_issue(args.men_issues, args.issue_id):
            print(f"Deleted {args.issue_id}.")
            return 0
        print(f"Issue {args.issue_id} not found.")
        return 1

    if args.command == "solve":
        if solve_issue(args.men_issues, args.issue_id, args.archive_dir):
            print(f"Solved {args.issue_id}.")
            return 0
        print(f"Issue {args.issue_id} not found.")
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
