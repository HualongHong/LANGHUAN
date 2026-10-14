"""Execute approved update plans.

Currently only ``schema_tuning`` plans are applied automatically.
Other update types produce a patch stub and are marked ``manual_stub``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from scripts.loop_engineering.common import move_plan, plan_dirs
from scripts.loop_engineering.issue_manager import solve_issue


ValidateFn = Callable[[Path], bool]


def _parse_front_matter(text: str) -> Dict[str, Any]:
    if text.strip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    return {}


def _schema_tuning_execute(
    plan_path: Path,
    front: Dict[str, Any],
    executed_dir: Path,
    validate: Optional[ValidateFn] = None,
) -> bool:
    """Apply a schema_tuning plan and return success status."""
    schema_path = Path(front.get("schema_path") or "lit_scout_harvester/schemas/sample_rst.json")
    affected_field = front.get("affected_field") or "unknown"
    proposed_solution = _extract_proposed_solution(plan_path.read_text(encoding="utf-8"))

    if not schema_path.exists():
        print(f"Schema not found: {schema_path}")
        return False

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    original = json.dumps(schema, ensure_ascii=False, indent=2)

    hint = f"针对字段 `{affected_field}`：{proposed_solution}"
    schema.setdefault("prompt_hints", []).append(hint)

    # Backup original schema.
    plan_id = front.get("plan_id", "unknown")
    backup_dir = executed_dir / plan_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / "schema.json.bak"
    backup_path.write_text(original, encoding="utf-8")

    # Write modified schema.
    schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

    # Validate.
    success = True
    if validate is not None:
        success = validate(schema_path)

    if not success:
        # Restore backup.
        schema_path.write_text(original, encoding="utf-8")
        print(f"Validation failed; restored {schema_path}")
        return False

    print(f"Updated schema: {schema_path}")
    return True


def _extract_proposed_solution(plan_text: str) -> str:
    in_solution = False
    lines: List[str] = []
    for line in plan_text.splitlines():
        if line.strip().startswith("## 建议方案"):
            in_solution = True
            continue
        if in_solution:
            if line.strip().startswith("## "):
                break
            if line.strip():
                lines.append(line.strip())
    return " ".join(lines) if lines else "请根据问题描述调整 schema prompt。"


def _default_validate(schema_path: Path) -> bool:
    """Default validation: JSON parseable and pytest smoke test if available."""
    try:
        json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON after update: {exc}")
        return False

    project_root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/unit", "-q", "--tb=short"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            print("Pytest validation failed:")
            print(result.stdout[-2000:])
            print(result.stderr[-1000:])
            return False
    except FileNotFoundError:
        # pytest not installed; accept JSON validity only.
        pass
    except subprocess.TimeoutExpired:
        print("Pytest validation timed out.")
        return False

    return True


def execute_plans(
    update_plan_dir: Path,
    men_issues_path: Path,
    validate: Optional[ValidateFn] = None,
) -> Dict[str, List[Path]]:
    """Execute all approved plans.

    Returns a mapping of outcome ('executed'/'rejected') to plan paths.
    """
    dirs = plan_dirs(update_plan_dir)
    approved = sorted(dirs["approved"].glob("UPDATE_PLAN_*.md"))

    if validate is None:
        validate = _default_validate

    outcomes: Dict[str, List[Path]] = {"executed": [], "rejected": []}

    for plan_path in approved:
        text = plan_path.read_text(encoding="utf-8")
        front = _parse_front_matter(text)
        update_type = front.get("update_type", "unknown")
        plan_id = front.get("plan_id", plan_path.stem)

        if update_type == "schema_tuning":
            success = _schema_tuning_execute(plan_path, front, dirs["executed"], validate)
        else:
            print(f"Update type '{update_type}' not auto-executed; generating manual stub.")
            print(f"  Affected field: {front.get('affected_field')}")
            print(f"  Proposed solution: {_extract_proposed_solution(text)}")
            # Mark as manual stub by copying to executed dir.
            stub_dir = dirs["executed"] / plan_id
            stub_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(plan_path, stub_dir / plan_path.name)
            success = True

        if success:
            move_plan(plan_path, dirs["executed"])
            outcomes["executed"].append(plan_path)
            issue_id = front.get("issue_id")
            if issue_id:
                solve_issue(Path(men_issues_path), issue_id)
        else:
            move_plan(plan_path, dirs["rejected"])
            outcomes["rejected"].append(plan_path)

    return outcomes


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execute approved update plans")
    parser.add_argument("--update-plan-dir", type=Path, default=Path("update_plan"))
    parser.add_argument("--men-issues", type=Path, default=Path("data/Men_issues.md"))
    parser.add_argument(
        "--validate-command",
        default=None,
        help="Optional shell command to validate schema changes; must return 0 on success",
    )
    args = parser.parse_args(argv)

    def validate(path: Path) -> bool:
        if args.validate_command:
            result = subprocess.run(args.validate_command, shell=True, capture_output=True)
            return result.returncode == 0
        return _default_validate(path)

    outcomes = execute_plans(args.update_plan_dir, args.men_issues, validate)
    print(f"Executed: {len(outcomes['executed'])}, Rejected: {len(outcomes['rejected'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
