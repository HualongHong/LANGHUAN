"""Shared utilities for Loop Engineering scripts."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Valid update types mapped from issue patterns.
UPDATE_TYPES = {
    "low_confidence": "schema_tuning",
    "module_error": "parser_fix",
    "parser_error": "parser_fix",
    "pipeline_error": "parser_fix",
    "output_format": "output_format",
    "performance": "performance",
    "schema_extension": "schema_extension",
    "model_quality": "model_switch",
}

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_SOLVED = "solved"

SOURCE_AUTO = "auto"
SOURCE_HUMAN = "human"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_men_issues(path: Path) -> List[Dict[str, Any]]:
    """Load issues from a YAML-front-matter markdown file."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    # Require YAML front matter delimiters.
    if text.strip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            front = yaml.safe_load(parts[1]) or {}
            return front.get("issues") or []
    # Fallback: try parsing the whole file as YAML.
    data = yaml.safe_load(text) or {}
    return data.get("issues") or []


def save_men_issues(path: Path, issues: List[Dict[str, Any]]) -> None:
    """Write issues as YAML-front-matter markdown."""
    ensure_dir(path.parent)
    front = {"issues": issues}
    yaml_text = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=False)
    path.write_text(f"---\n{yaml_text}---\n", encoding="utf-8")


def next_issue_id(existing: List[Dict[str, Any]]) -> str:
    """Generate the next ISSUE_### id."""
    nums = []
    for issue in existing:
        mid = issue.get("issue_id", "")
        match = re.search(r"(\d+)", mid)
        if match:
            nums.append(int(match.group(1)))
    next_num = max(nums, default=0) + 1
    return f"ISSUE_{next_num:03d}"


def load_feedback_digest(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"general_issues": [], "specific_issues": [], "metrics": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_feedback_digest(path: Path, digest: Dict[str, Any]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")


def plan_dirs(base: Path) -> Dict[str, Path]:
    return {
        "pending": ensure_dir(base / "pending"),
        "approved": ensure_dir(base / "approved"),
        "executed": ensure_dir(base / "executed"),
        "rejected": ensure_dir(base / "rejected"),
    }


def move_plan(plan_path: Path, dest_dir: Path) -> Path:
    ensure_dir(dest_dir)
    dest = dest_dir / plan_path.name
    shutil.move(str(plan_path), str(dest))
    return dest


def parse_plan_front_matter(text: str) -> Dict[str, Any]:
    """Parse YAML front matter from a plan markdown file."""
    if text.strip().startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return yaml.safe_load(parts[1]) or {}
    return {}


def render_template(template_path: Path, variables: Dict[str, Any]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for key, value in variables.items():
        placeholder = "{{" + key + "}}"
        text = text.replace(placeholder, str(value))
    return text
