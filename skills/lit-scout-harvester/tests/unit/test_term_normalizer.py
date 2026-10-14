"""Tests for term normalization (R3)."""

from __future__ import annotations

from lit_scout_harvester.utils.term_normalizer import normalize_result, normalize_term


def test_normalize_term_basic():
    assert normalize_term("  The pollen record  ") == "pollen record"


def test_normalize_term_truncate():
    assert normalize_term("erosion and sedimentation processes in lakes", max_words=4) == "erosion and sedimentation processes"


def test_normalize_term_strip_fillers():
    assert normalize_term("a study of climate change") == "study of climate change"


def test_normalize_result_list():
    result = {"sampleType": ["  a marine sediments  ", "lake sediments", "lake sediments"]}
    schema = {
        "fields": [
            {"name": "sampleType", "type": "list[str]", "max_words_per_term": 3},
        ]
    }
    out = normalize_result(result, schema=schema)
    assert out["sampleType"] == ["marine sediments", "lake sediments"]


def test_normalize_result_string():
    result = {"contribution": "the scientific value of this study"}
    schema = {"fields": [{"name": "contribution", "type": "str", "max_words_per_term": 3}]}
    out = normalize_result(result, schema=schema)
    assert out["contribution"] == "scientific value of"


def test_normalize_result_no_schema():
    result = {"indicator": [" the diatoms ", "a grain size"]}
    out = normalize_result(result, global_max_words=2)
    assert out["indicator"] == ["diatoms", "grain size"]
