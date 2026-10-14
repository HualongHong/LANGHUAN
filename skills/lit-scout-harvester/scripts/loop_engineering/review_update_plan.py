"""Human review CLI for pending update plans."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from scripts.loop_engineering.common import move_plan, plan_dirs


def _parse_front_matter(text: str) -> Dict[str, Any]:
    if text.strip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    return {}


def list_pending_plans(update_plan_dir: Path) -> List[Path]:
    dirs = plan_dirs(update_plan_dir)
    return sorted(dirs["pending"].glob("UPDATE_PLAN_*.md"))


def show_plan(plan_path: Path) -> None:
    text = plan_path.read_text(encoding="utf-8")
    front = _parse_front_matter(text)
    print(f"Plan ID: {front.get('plan_id')}")
    print(f"Update type: {front.get('update_type')}")
    print(f"Issue ID: {front.get('issue_id')}")
    print(f"Affected field: {front.get('affected_field')}")
    print("\n" + text)


def review_plan(
    update_plan_dir: Path,
    plan_id: str,
    action: str,
    auto_approve: bool = False,
) -> bool:
    dirs = plan_dirs(update_plan_dir)
    pending = list_pending_plans(update_plan_dir)
    plan_path = next((p for p in pending if plan_id in p.name), None)
    if plan_path is None:
        print(f"Pending plan matching '{plan_id}' not found.")
        return False

    if auto_approve:
        action = "approve"

    if action == "approve":
        move_plan(plan_path, dirs["approved"])
        print(f"Approved {plan_path.name}")
        return True
    if action == "reject":
        move_plan(plan_path, dirs["rejected"])
        print(f"Rejected {plan_path.name}")
        return True
    if action == "postpone":
        print(f"Postponed {plan_path.name} (remains pending)")
        return True

    print(f"Unknown action: {action}")
    return False


def _interactive_review(update_plan_dir: Path, auto_approve: bool) -> int:
    pending = list_pending_plans(update_plan_dir)
    if not pending:
        print("No pending plans to review.")
        return 0

    reviewed = 0
    for plan_path in pending:
        show_plan(plan_path)
        if auto_approve:
            choice = "a"
        else:
            choice = input("[a]pprove / [r]eject / [p]ostpone? ").strip().lower()

        if choice == "a":
            review_plan(update_plan_dir, plan_path.name, "approve")
            reviewed += 1
        elif choice == "r":
            review_plan(update_plan_dir, plan_path.name, "reject")
            reviewed += 1
        elif choice == "p":
            review_plan(update_plan_dir, plan_path.name, "postpone")
        else:
            print("Skipped.")

    print(f"Reviewed {len(pending)} plan(s); {reviewed} moved.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Review pending update plans")
    parser.add_argument("--update-plan-dir", type=Path, default=Path("update_plan"))
    parser.add_argument("--auto-approve", action="store_true", help="Approve all pending plans")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List pending plans")

    p_show = sub.add_parser("show", help="Show a plan")
    p_show.add_argument("plan_id", help="Plan filename or substring")

    p_review = sub.add_parser("review", help="Interactive review all pending plans")

    args = parser.parse_args(argv)

    if args.command == "list" or args.command is None:
        for p in list_pending_plans(args.update_plan_dir):
            print(p.name)
        return 0

    if args.command == "show":
        pending = list_pending_plans(args.update_plan_dir)
        plan_path = next((p for p in pending if args.plan_id in p.name), None)
        if plan_path is None:
            print(f"Plan '{args.plan_id}' not found.")
            return 1
        show_plan(plan_path)
        return 0

    if args.command == "review":
        return _interactive_review(args.update_plan_dir, args.auto_approve)

    return 1


if __name__ == "__main__":
    sys.exit(main())
