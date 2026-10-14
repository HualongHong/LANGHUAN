"""Tests for output/run_counts.json written alongside the main output (C12)."""

from __future__ import annotations

import json

from lit_scout_harvester.core.config_loader import load_config
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.output_writer import OutputWriter


def _make_writer(tmp_path, fmt="csv"):
    cfg = load_config("config/default.yaml")
    cfg.global_.output_dir = str(tmp_path)
    cfg.output.format = fmt
    return OutputWriter(cfg)


def test_run_counts_written_with_correct_counts(tmp_path):
    cache = object()
    ctx1 = PipelineContext("r1", {"TI": "Title"}, cache)
    ctx1.set_processed("detected_language", "en")
    ctx2 = PipelineContext("r2", {"TI": "Other"}, cache)

    writer = _make_writer(tmp_path)
    stage_counts = {
        "pipeline_input": {"rows_in": 2, "distinct_ids_in": 2, "rows_out": 2},
        "pipeline_output": {"rows_in": 2, "distinct_ids_in": 2, "rows_out": 2},
    }
    paths = writer.write([ctx1, ctx2], run_name="rc", stage_counts=stage_counts)

    counts_path = tmp_path / "run_counts.json"
    assert paths["run_counts"] == counts_path
    assert counts_path.exists()

    doc = json.loads(counts_path.read_text(encoding="utf-8"))
    assert doc["run_name"] == "rc"
    # Scheduler stages are carried through, writer stage appended.
    assert doc["stages"]["pipeline_input"] == {
        "rows_in": 2,
        "distinct_ids_in": 2,
        "rows_out": 2,
    }
    assert doc["stages"]["output_writer"] == {
        "rows_in": 2,
        "distinct_ids_in": 2,
        "rows_out": 2,
    }
    # Non-null rates: record_id always present, detected_language only on r1.
    assert doc["field_nonnull_rate"]["record_id"] == 1.0
    assert doc["field_nonnull_rate"]["detected_language"] == 0.5


def test_run_counts_without_stage_counts(tmp_path):
    cache = object()
    ctx = PipelineContext("r1", {"TI": "Title"}, cache)
    ctx.set_processed("detected_language", "zh")

    writer = _make_writer(tmp_path)
    paths = writer.write([ctx], run_name="solo")

    doc = json.loads(paths["run_counts"].read_text(encoding="utf-8"))
    assert list(doc["stages"]) == ["output_writer"]
    assert doc["stages"]["output_writer"]["rows_out"] == 1
    assert doc["field_nonnull_rate"]["detected_language"] == 1.0


def test_run_counts_written_for_json_format(tmp_path):
    cache = object()
    ctx = PipelineContext("r1", {"TI": "Title"}, cache)

    writer = _make_writer(tmp_path, fmt="json")
    paths = writer.write([ctx], run_name="json_run")

    assert paths["json"].exists()
    assert paths["run_counts"].exists()
    doc = json.loads(paths["run_counts"].read_text(encoding="utf-8"))
    assert doc["stages"]["output_writer"]["distinct_ids_in"] == 1
