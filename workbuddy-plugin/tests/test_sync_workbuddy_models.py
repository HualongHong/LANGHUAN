"""Regression tests for WorkBuddy configuration preservation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from sync_workbuddy_models import main, read_existing_models  # noqa: E402


def test_corrupt_existing_models_is_fatal_and_preserves_file(tmp_path, capsys):
    providers = tmp_path / "providers.json"
    providers.write_text(json.dumps({"providers": []}), encoding="utf-8")
    env = tmp_path / "langhuan.env"
    env.write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    models = tmp_path / "models.json"
    original = "{not valid json\n"
    models.write_text(original, encoding="utf-8")

    rc = main([
        "sync_workbuddy_models.py",
        "--providers", str(providers),
        "--env", str(env),
        "--models", str(models),
    ])

    assert rc == 1
    assert models.read_text(encoding="utf-8") == original
    assert not (tmp_path / "models.json.bak").exists()
    assert "已中止同步以避免覆盖原配置" in capsys.readouterr().out


def test_missing_existing_models_starts_empty(tmp_path):
    assert read_existing_models(tmp_path / "missing.json") == ([], "missing")
