"""Tests for the Loop Engineering framework."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest
import yaml

from scripts.loop_engineering.collect_feedback import collect_feedback
from scripts.loop_engineering.common import load_feedback_digest, load_men_issues, save_men_issues
from scripts.loop_engineering.evaluate_update import evaluate_update
from scripts.loop_engineering.execute_update import execute_plans
from scripts.loop_engineering.issue_manager import (
    confirm_issue,
    delete_issue,
    propose_issues,
    solve_issue,
)
from scripts.loop_engineering.plan_update import generate_plans
from scripts.loop_engineering.review_update_plan import (
    list_pending_plans,
    review_plan,
    show_plan,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_loop(tmp_path: Path):
    """Provide temporary paths for a loop-engineering test run."""
    return {
        "review_queue": tmp_path / "review_queue.db",
        "feedback": tmp_path / "feedback" / "feedback_digest.json",
        "men_issues": tmp_path / "Men_issues.md",
        "update_plan_dir": tmp_path / "update_plan",
        "schema": tmp_path / "test_schema.json",
    }


def _make_review_queue(db_path: Path, rows: List[Dict[str, Any]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_queue (
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
                """
                INSERT INTO review_queue (record_id, raw_record, processed, extracted, flags, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', '2026-01-01T00:00:00Z')
                """,
                (
                    row["record_id"],
                    json.dumps({}),
                    json.dumps({}),
                    json.dumps(row.get("extracted", {})),
                    "; ".join(row["flags"]),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _make_schema(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "name": "test",
                "fields": [{"name": "indicator", "type": "list[str]"}],
                "prompt_hints": ["Be concise."],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# collect_feedback
# ---------------------------------------------------------------------------


def test_collect_feedback_creates_digest_with_general_issues(tmp_loop):
    rows = [
        {"record_id": f"r{i}", "flags": ["low_confidence:indicator"], "extracted": {}}
        for i in range(6)
    ]
    _make_review_queue(tmp_loop["review_queue"], rows)

    digest = collect_feedback(tmp_loop["review_queue"], tmp_loop["feedback"])

    assert digest["summary"]["total_reviewed"] == 6
    assert digest["summary"]["general_issues"] == 1
    assert digest["general_issues"][0]["issue_type"] == "low_confidence"
    assert digest["general_issues"][0]["field"] == "indicator"
    assert tmp_loop["feedback"].exists()


def test_collect_feedback_specific_issues_not_general(tmp_loop):
    rows = [
        {"record_id": "r1", "flags": ["low_confidence:indicator"], "extracted": {}},
        {"record_id": "r2", "flags": ["module_error:geocoder"], "extracted": {}},
    ]
    _make_review_queue(tmp_loop["review_queue"], rows)

    digest = collect_feedback(tmp_loop["review_queue"], tmp_loop["feedback"])

    assert digest["summary"]["general_issues"] == 0
    assert digest["summary"]["specific_issues"] == 2


# ---------------------------------------------------------------------------
# issue_manager
# ---------------------------------------------------------------------------


def test_propose_issues_adds_pending_entries(tmp_loop):
    digest = {
        "general_issues": [
            {
                "issue_id": "LOW_CONFIDENCE_INDICATOR",
                "issue_type": "low_confidence",
                "field": "indicator",
                "count": 10,
                "rate": 0.2,
                "example_record_ids": ["r1", "r2"],
                "suggested_update_type": "schema_tuning",
                "proposed_solution": "Add guidelines.",
            }
        ]
    }
    tmp_loop["feedback"].parent.mkdir(parents=True, exist_ok=True)
    tmp_loop["feedback"].write_text(json.dumps(digest), encoding="utf-8")

    new = propose_issues(tmp_loop["men_issues"], tmp_loop["feedback"])

    assert len(new) == 1
    assert new[0]["status"] == "pending"
    assert new[0]["source"] == "auto"
    issues = load_men_issues(tmp_loop["men_issues"])
    assert len(issues) == 1


def test_confirm_and_delete_issue(tmp_loop):
    save_men_issues(
        tmp_loop["men_issues"],
        [
            {
                "issue_id": "ISSUE_001",
                "issue_description": "desc",
                "proposed_solution": "sol",
                "source": "auto",
                "status": "pending",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ],
    )

    assert confirm_issue(tmp_loop["men_issues"], "ISSUE_001", "new desc", "new sol")
    issues = load_men_issues(tmp_loop["men_issues"])
    assert issues[0]["status"] == "confirmed"
    assert issues[0]["issue_description"] == "new desc"

    assert delete_issue(tmp_loop["men_issues"], "ISSUE_001")
    assert load_men_issues(tmp_loop["men_issues"]) == []


def test_solve_issue_removes_from_active_ledger(tmp_loop):
    save_men_issues(
        tmp_loop["men_issues"],
        [
            {
                "issue_id": "ISSUE_001",
                "issue_description": "desc",
                "proposed_solution": "sol",
                "source": "human",
                "status": "confirmed",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ],
    )

    archive_dir = tmp_loop["men_issues"].parent / "archive"
    assert solve_issue(tmp_loop["men_issues"], "ISSUE_001", archive_dir)
    assert load_men_issues(tmp_loop["men_issues"]) == []
    assert (archive_dir / "ISSUE_001.yaml").exists()


# ---------------------------------------------------------------------------
# plan_update
# ---------------------------------------------------------------------------


def test_generate_plans_only_uses_confirmed_issues(tmp_loop):
    save_men_issues(
        tmp_loop["men_issues"],
        [
            {
                "issue_id": "ISSUE_001",
                "issue_description": "Low confidence on indicator",
                "proposed_solution": "Add guidelines",
                "source": "auto",
                "status": "pending",
                "affected_field": "indicator",
                "suggested_update_type": "schema_tuning",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            },
            {
                "issue_id": "ISSUE_002",
                "issue_description": "Parser fails on CNKI fund",
                "proposed_solution": "Fix parser",
                "source": "human",
                "status": "confirmed",
                "affected_field": "raw_fund",
                "suggested_update_type": "parser_fix",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            },
        ],
    )

    created = generate_plans(tmp_loop["men_issues"], tmp_loop["update_plan_dir"])

    assert len(created) == 1
    text = created[0].read_text(encoding="utf-8")
    assert "ISSUE_002" in text
    assert "parser_fix" in text
    assert "ISSUE_001" not in text


# ---------------------------------------------------------------------------
# review_update_plan
# ---------------------------------------------------------------------------


def test_review_approve_and_reject_plan(tmp_loop):
    save_men_issues(
        tmp_loop["men_issues"],
        [
            {
                "issue_id": "ISSUE_001",
                "issue_description": "desc",
                "proposed_solution": "sol",
                "source": "human",
                "status": "confirmed",
                "affected_field": "indicator",
                "suggested_update_type": "schema_tuning",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ],
    )
    generate_plans(tmp_loop["men_issues"], tmp_loop["update_plan_dir"])

    pending = list_pending_plans(tmp_loop["update_plan_dir"])
    assert len(pending) == 1

    plan_name = pending[0].name
    assert review_plan(tmp_loop["update_plan_dir"], plan_name, "approve")
    assert len(list_pending_plans(tmp_loop["update_plan_dir"])) == 0
    approved_dir = tmp_loop["update_plan_dir"] / "approved"
    assert len(list(approved_dir.glob("UPDATE_PLAN_*.md"))) == 1

    # Move back to pending for reject test.
    plan_path = next(approved_dir.glob("UPDATE_PLAN_*.md"))
    (tmp_loop["update_plan_dir"] / "pending").mkdir(parents=True, exist_ok=True)
    plan_path.rename(tmp_loop["update_plan_dir"] / "pending" / plan_path.name)
    assert review_plan(tmp_loop["update_plan_dir"], plan_name, "reject")
    rejected_dir = tmp_loop["update_plan_dir"] / "rejected"
    assert len(list(rejected_dir.glob("UPDATE_PLAN_*.md"))) == 1


# ---------------------------------------------------------------------------
# execute_update
# ---------------------------------------------------------------------------


def test_execute_schema_tuning_updates_schema_and_solves_issue(tmp_loop):
    _make_schema(tmp_loop["schema"])
    save_men_issues(
        tmp_loop["men_issues"],
        [
            {
                "issue_id": "ISSUE_001",
                "issue_description": "Low confidence on indicator",
                "proposed_solution": "Add more specific guidelines.",
                "source": "human",
                "status": "confirmed",
                "affected_field": "indicator",
                "suggested_update_type": "schema_tuning",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ],
    )
    generate_plans(tmp_loop["men_issues"], tmp_loop["update_plan_dir"])
    pending = list_pending_plans(tmp_loop["update_plan_dir"])
    # Approve plan.
    review_plan(tmp_loop["update_plan_dir"], pending[0].name, "approve")

    # Patch schema path in plan front matter to point to temp schema.
    approved_dir = tmp_loop["update_plan_dir"] / "approved"
    plan_path = next(approved_dir.glob("UPDATE_PLAN_*.md"))
    text = plan_path.read_text(encoding="utf-8")
    text = text.replace("lit_scout_harvester/schemas/sample_rst.json", str(tmp_loop["schema"]).replace("\\", "/"))
    plan_path.write_text(text, encoding="utf-8")

    outcomes = execute_plans(
        tmp_loop["update_plan_dir"],
        tmp_loop["men_issues"],
        validate=lambda _path: True,
    )

    assert len(outcomes["executed"]) == 1
    updated_schema = json.loads(tmp_loop["schema"].read_text(encoding="utf-8"))
    assert any("indicator" in hint for hint in updated_schema["prompt_hints"])
    assert load_men_issues(tmp_loop["men_issues"]) == []


def test_execute_schema_tuning_restores_backup_on_validation_failure(tmp_loop):
    _make_schema(tmp_loop["schema"])
    save_men_issues(
        tmp_loop["men_issues"],
        [
            {
                "issue_id": "ISSUE_001",
                "issue_description": "Bad hint",
                "proposed_solution": "Should fail validation.",
                "source": "human",
                "status": "confirmed",
                "affected_field": "indicator",
                "suggested_update_type": "schema_tuning",
                "created_at": "2026-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ],
    )
    generate_plans(tmp_loop["men_issues"], tmp_loop["update_plan_dir"])
    pending = list_pending_plans(tmp_loop["update_plan_dir"])
    review_plan(tmp_loop["update_plan_dir"], pending[0].name, "approve")

    approved_dir = tmp_loop["update_plan_dir"] / "approved"
    plan_path = next(approved_dir.glob("UPDATE_PLAN_*.md"))
    text = plan_path.read_text(encoding="utf-8")
    text = text.replace("lit_scout_harvester/schemas/sample_rst.json", str(tmp_loop["schema"]).replace("\\", "/"))
    plan_path.write_text(text, encoding="utf-8")

    original = tmp_loop["schema"].read_text(encoding="utf-8")
    outcomes = execute_plans(
        tmp_loop["update_plan_dir"],
        tmp_loop["men_issues"],
        validate=lambda _path: False,
    )

    assert len(outcomes["rejected"]) == 1
    assert tmp_loop["schema"].read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# evaluate_update
# ---------------------------------------------------------------------------


def test_evaluate_update_detects_degradation(tmp_loop):
    before = {"metrics": {"low_confidence_rate": 0.1, "module_error_rate": 0.0}}
    after = {"metrics": {"low_confidence_rate": 0.2, "module_error_rate": 0.0}}
    tmp_loop["feedback"].parent.mkdir(parents=True, exist_ok=True)
    before_path = tmp_loop["feedback"].parent / "before.json"
    after_path = tmp_loop["feedback"].parent / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    report_path = evaluate_update(before_path, after_path, "PLAN_001", tmp_loop["feedback"].parent)
    text = report_path.read_text(encoding="utf-8")
    assert "建议回滚" in text
    assert "0.1000" in text or "0.1" in text


def test_evaluate_update_reports_improvement(tmp_loop):
    before = {"metrics": {"low_confidence_rate": 0.2, "module_error_rate": 0.05}}
    after = {"metrics": {"low_confidence_rate": 0.1, "module_error_rate": 0.0}}
    tmp_loop["feedback"].parent.mkdir(parents=True, exist_ok=True)
    before_path = tmp_loop["feedback"].parent / "before.json"
    after_path = tmp_loop["feedback"].parent / "after.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")

    report_path = evaluate_update(before_path, after_path, "PLAN_002", tmp_loop["feedback"].parent)
    text = report_path.read_text(encoding="utf-8")
    assert "符合预期" in text
