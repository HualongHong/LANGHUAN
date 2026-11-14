"""Tests for the agent-entry stdout and process-exit contract."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

from agentkit.agent_entry import run_entry


def test_domain_exit_code_controls_summary_and_process_status():
    args = SimpleNamespace(quiet=True, mode="lint")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = run_entry(args, lambda _args: {"exit_code": 1, "errors": 2})
    summary = json.loads(stdout.getvalue())
    assert rc == 1
    assert summary["ok"] is False
    assert summary["errors"] == 2


def test_success_without_domain_exit_code_remains_compatible():
    args = SimpleNamespace(quiet=True, mode="direct")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = run_entry(args, lambda _args: {"records": 3})
    summary = json.loads(stdout.getvalue())
    assert rc == 0
    assert summary["ok"] is True
    assert summary["records"] == 3
