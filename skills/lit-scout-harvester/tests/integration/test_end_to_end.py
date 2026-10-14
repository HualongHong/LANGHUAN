"""End-to-end integration tests using real DeepSeek API when available."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pandas as pd
import pytest
import yaml
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")


def _temp_config(base_config: Path, output_dir: Path, overrides: Dict[str, Any] = None) -> Path:
    data: Dict[str, Any] = {"extends": str(base_config)}
    data["global"] = {"output_dir": str(output_dir)}
    if overrides:
        data = {**data, **overrides}
    path = output_dir / "test_config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _run_pipeline(config_path: Path, input_path: Path, run_name: str, limit: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "scripts/run_pipeline.py",
            str(input_path),
            "--config",
            str(config_path),
            "--run-name",
            run_name,
            "--limit",
            str(limit),
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture
def tmp_output(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def test_default_skeleton_pipeline(tmp_output):
    """Default config has no LLM modules; should run offline."""
    cfg = _temp_config(PROJECT_ROOT / "config/default.yaml", tmp_output)
    result = _run_pipeline(
        cfg,
        PROJECT_ROOT / "tests/fixtures/sample.wos",
        "integ_default",
        10,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_output / "integ_default.csv"
    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    assert "record_id" in df.columns
    assert len(df) == 10


@pytest.mark.skipif(not DEEPSEEK_API_KEY, reason="DEEPSEEK_API_KEY not set")
def test_sample_rst_extraction(tmp_output):
    cfg = _temp_config(PROJECT_ROOT / "config/sample_rst.yaml", tmp_output)
    result = _run_pipeline(
        cfg,
        PROJECT_ROOT / "tests/fixtures/sample.wos",
        "integ_sample_rst",
        3,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_output / "integ_sample_rst.csv"
    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    assert len(df) == 3
    # Schema fields should be present.
    for col in ["sampleType", "indicator", "sampleSource", "timeScale", "sciQuestion", "contribution"]:
        assert col in df.columns, f"Missing schema column: {col}"
    # Author addresses flattened.
    for col in ["authors", "countries", "first_author_country"]:
        assert col in df.columns, f"Missing flattened author column: {col}"


@pytest.mark.skipif(not DEEPSEEK_API_KEY, reason="DEEPSEEK_API_KEY not set")
def test_cnki_extraction(tmp_output):
    cfg = _temp_config(PROJECT_ROOT / "config/cnki_sci_edu_test.yaml", tmp_output)
    result = _run_pipeline(
        cfg,
        PROJECT_ROOT / "tests/fixtures/sample2.cnki.refworks",
        "integ_cnki",
        3,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_output / "integ_cnki.csv"
    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    assert len(df) == 3
    # CNKI-specific raw fields are preserved when present in source.
    assert "raw_cn" in df.columns


def test_policy_analysis_offline_modules(tmp_output):
    """Run GDP weighter + network builder without geocoder microservice."""
    cfg = _temp_config(
        PROJECT_ROOT / "config/policy_analysis.yaml",
        tmp_output,
        {"modules": {"geocoder": {"enabled": False}}},
    )
    result = _run_pipeline(
        cfg,
        PROJECT_ROOT / "tests/fixtures/sample.wos",
        "integ_policy",
        10,
    )
    assert result.returncode == 0, result.stderr

    csv_path = tmp_output / "integ_policy.csv"
    assert csv_path.exists()
    df = pd.read_csv(csv_path)
    assert len(df) == 10
    assert "gdp_weights" in df.columns
