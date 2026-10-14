"""Tests for src.core.output_writer."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.context import ContextFactory, PipelineContext
from lit_scout_harvester.core.output_writer import OutputWriter


def test_write_csv_and_citation(tmp_path):
    cfg = load_config("config/default.yaml")
    cfg.global_.output_dir = str(tmp_path)
    cfg.output.format = "csv"

    cache = object()  # not used in this test
    ctx = PipelineContext("r1", {"TI": "Title", "AU": ["Smith, J"]}, cache)
    ctx.set_processed("detected_language", "en")

    writer = OutputWriter(cfg)
    paths = writer.write([ctx], run_name="test")

    assert "csv" in paths
    assert "citation" in paths
    assert paths["csv"].exists()
    assert paths["citation"].exists()

    df = pd.read_csv(paths["csv"])
    assert "record_id" in df.columns
    assert "detected_language" in df.columns


def test_write_json(tmp_path):
    cfg = load_config("config/default.yaml")
    cfg.global_.output_dir = str(tmp_path)
    cfg.output.format = "json"

    cache = object()
    ctx = PipelineContext("r1", {"TI": "Title"}, cache)
    writer = OutputWriter(cfg)
    paths = writer.write([ctx], run_name="test")

    assert "json" in paths
    assert paths["json"].exists()
