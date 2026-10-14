"""Tests for modules.label_scheme (feedback capsule C7)."""

from __future__ import annotations

import pytest

from lit_scout_harvester.modules.label_scheme import LabelSchemeAnalyzer


def make_records(field, labels_per_record):
    """Build records whose ``field`` holds the given label lists."""
    return [{field: labels} for labels in labels_per_record]


def test_mode_tier_when_mode_share_at_least_60_percent():
    # 7 of 10 labeled records carry exactly 2 labels -> mode share 0.7
    records = make_records(
        "sdg", [["a", "b"]] * 7 + [["a"]] * 2 + [["a", "b", "c"]] * 1
    ) + [{"sdg": []}, {"sdg": None}]  # empties are counted, not distributed
    report = LabelSchemeAnalyzer().analyze(records, "sdg")

    assert report["mode_n"] == 2
    assert report["mode_count"] == 7
    assert report["mode_share"] == pytest.approx(0.7)
    assert report["tier"] == "mode"
    assert report["records_empty"] == 2
    assert report["distribution"] == {1: 2, 2: 7, 3: 1}
    assert "N*=2" in report["recommendation"]


def test_degraded_tier_between_45_and_60_percent():
    # 5 of 10 labeled records carry exactly 2 labels -> mode share 0.5
    records = make_records("sdg", [["a", "b"]] * 5 + [["a"]] * 4 + [["a", "b", "c"]] * 1)
    report = LabelSchemeAnalyzer().analyze(records, "sdg")

    assert report["mode_n"] == 2
    assert report["mode_share"] == pytest.approx(0.5)
    assert report["tier"] == "degraded"


def test_unfit_tier_below_45_percent():
    records = make_records(
        "indicator", [["a"]] * 4 + [["a", "b"]] * 3 + [["a", "b", "c"]] * 3
    )
    report = LabelSchemeAnalyzer().analyze(records, "indicator")

    assert report["mode_n"] == 1
    assert report["mode_share"] == pytest.approx(0.4)
    assert report["tier"] == "unfit"


def test_thresholds_are_configurable():
    records = make_records("sdg", [["a", "b"]] * 5 + [["a"]] * 5)  # share 0.5

    # stricter mode threshold pushes the same corpus into the degraded tier
    assert LabelSchemeAnalyzer(mode_threshold=0.8).analyze(records, "sdg")["tier"] == "degraded"
    # raising the degraded threshold pushes it into unfit
    assert (
        LabelSchemeAnalyzer(degraded_threshold=0.6).analyze(records, "sdg")["tier"]
        == "unfit"
    )
    # boundary values: 0.6 share with default thresholds is exactly "mode"
    boundary = make_records("f", [["a", "b"]] * 6 + [["a"]] * 4)
    assert LabelSchemeAnalyzer().analyze(boundary, "f")["tier"] == "mode"


def test_invalid_thresholds_rejected():
    with pytest.raises(ValueError):
        LabelSchemeAnalyzer(mode_threshold=0.4, degraded_threshold=0.5)


def test_mode_tie_breaks_toward_smaller_n():
    records = make_records("f", [["a"]] * 2 + [["a", "b"]] * 2)
    report = LabelSchemeAnalyzer().analyze(records, "f")
    assert report["mode_n"] == 1
    assert report["mode_share"] == pytest.approx(0.5)


def test_string_values_are_split_on_separator():
    records = [
        {"topics": "paleoclimate reconstruction; human activity impacts"},
        {"topics": "paleoclimate reconstruction; human activity impacts"},
        {"topics": "paleoclimate reconstruction"},
        {"topics": "  "},
    ]
    report = LabelSchemeAnalyzer().analyze(records, "topics")
    assert report["mode_n"] == 2
    assert report["mode_share"] == pytest.approx(2 / 3)
    assert report["tier"] == "mode"
    assert report["records_empty"] == 1


def test_analyze_schema_only_targets_multilabel_fields():
    schema = {
        "name": "demo",
        "fields": [
            {"name": "sdg", "type": "list[str]", "description": "d", "multiLabel": True},
            {"name": "title", "type": "str", "description": "d"},
        ],
    }
    records = make_records("sdg", [["a", "b"]] * 8 + [["a"]] * 2)
    analyzer = LabelSchemeAnalyzer()
    reports = analyzer.analyze_schema(schema, records)
    assert list(reports) == ["sdg"]
    assert reports["sdg"]["tier"] == "mode"


def test_fill_mode_n_writes_back_into_schema():
    schema = {
        "name": "demo",
        "fields": [
            {
                "name": "sdg",
                "type": "list[str]",
                "description": "d",
                "multiLabel": True,
            },
            {"name": "title", "type": "str", "description": "d"},
        ],
    }
    records = make_records("sdg", [["a", "b"]] * 8 + [["a"]] * 2)
    filled = LabelSchemeAnalyzer().fill_mode_n(schema, records)

    sdg = next(f for f in filled["fields"] if f["name"] == "sdg")
    title = next(f for f in filled["fields"] if f["name"] == "title")
    assert sdg["modeN"] == 2
    assert "modeN" not in title
