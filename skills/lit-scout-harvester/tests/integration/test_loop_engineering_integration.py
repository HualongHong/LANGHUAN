"""Integration test for the full Loop Engineering chain."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.loop_engineering.collect_feedback import collect_feedback
from scripts.loop_engineering.common import load_men_issues
from scripts.loop_engineering.evaluate_update import evaluate_update
from scripts.loop_engineering.execute_update import execute_plans
from scripts.loop_engineering.issue_manager import confirm_issue, propose_issues, solve_issue
from scripts.loop_engineering.plan_update import generate_plans
from scripts.loop_engineering.review_update_plan import list_pending_plans, review_plan


def _make_review_queue(db_path: Path, rows):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL,
                raw_record TEXT NOT NULL,
                processed TEXT,
                extracted TEXT,
                flags TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewer_note TEXT,
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            )
            """
        )
        for row in rows:
            conn.execute(
                "INSERT INTO review_queue (record_id, raw_record, processed, extracted, flags, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z')",
                (row["record_id"], "{}", "{}", json.dumps({}), "; ".join(row["flags"])),
            )
        conn.commit()
    finally:
        conn.close()


def _make_schema(path: Path):
    path.write_text(
        json.dumps(
            {"name": "test", "fields": [{"name": "indicator", "type": "list[str]"}], "prompt_hints": []}
        ),
        encoding="utf-8",
    )


def test_full_loop_engineering_chain(tmp_path: Path):
    review_queue = tmp_path / "review_queue.db"
    feedback = tmp_path / "feedback" / "feedback_digest.json"
    men_issues = tmp_path / "Men_issues.md"
    update_plan_dir = tmp_path / "update_plan"
    schema = tmp_path / "test_schema.json"

    rows = [{"record_id": f"r{i}", "flags": ["low_confidence:indicator"]} for i in range(6)]
    _make_review_queue(review_queue, rows)
    _make_schema(schema)

    # 1. Collect feedback.
    digest = collect_feedback(review_queue, feedback)
    assert digest["summary"]["general_issues"] == 1

    # 2. Propose pending issue.
    new = propose_issues(men_issues, feedback)
    assert len(new) == 1
    issue_id = new[0]["issue_id"]

    # 3. Human confirms.
    assert confirm_issue(men_issues, issue_id)

    # 4. Generate plan.
    plans = generate_plans(men_issues, update_plan_dir, feedback)
    assert len(plans) == 1

    # Patch schema path in plan to temp schema.
    plan_text = plans[0].read_text(encoding="utf-8")
    plan_text = plan_text.replace("lit_scout_harvester/schemas/sample_rst.json", str(schema).replace("\\", "/"))
    plans[0].write_text(plan_text, encoding="utf-8")

    # 5. Approve plan.
    pending = list_pending_plans(update_plan_dir)
    assert len(pending) == 1
    assert review_plan(update_plan_dir, pending[0].name, "approve")

    # 6. Execute plan.
    outcomes = execute_plans(update_plan_dir, men_issues, validate=lambda _path: True)
    assert len(outcomes["executed"]) == 1

    # 7. Verify schema updated and issue solved.
    updated = json.loads(schema.read_text(encoding="utf-8"))
    assert len(updated["prompt_hints"]) > 0
    assert load_men_issues(men_issues) == []

    # 8. Evaluate update.
    after_digest = tmp_path / "feedback" / "feedback_digest_after.json"
    after_digest.write_text(json.dumps({"metrics": {"low_confidence_rate": 0.0}}), encoding="utf-8")
    report = evaluate_update(feedback, after_digest, "PLAN_TEST", update_plan_dir / "executed")
    assert report.exists()
    assert "符合预期" in report.read_text(encoding="utf-8")
