"""Tests for v0.2.0 output format serialization (R2)."""

from __future__ import annotations

import json

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.core.cache_manager import CacheManager


def make_context() -> PipelineContext:
    cache = CacheManager(":memory:")
    return PipelineContext("r1", {"TI": "T", "AB": "A"}, cache)


def test_list_field_joined():
    ctx = make_context()
    ctx.set_extracted("sampleType", ["marine sediments", "lake sediments"])
    row = ctx.to_flat_dict()
    assert row["sampleType"] == "marine sediments; lake sediments"


def test_list_separator_custom():
    ctx = make_context()
    ctx.set_extracted("indicator", ["a", "b"])
    row = ctx.to_flat_dict(list_separator="|")
    assert row["indicator"] == "a|b"


def test_author_addresses_flattened():
    ctx = make_context()
    ctx.set_processed(
        "author_addresses",
        [
            {"author": "Kemp, AES", "country": "England", "city": "London", "institution": "UCL"},
            {"author": "Wang, W", "country": "China", "city": "Beijing", "institution": "PKU"},
        ],
    )
    row = ctx.to_flat_dict()
    assert row["authors"] == "Kemp, AES; Wang, W"
    assert row["countries"] == "England; China"
    assert row["first_author_country"] == "England"
    assert row["first_author_institution"] == "UCL"


def test_author_addresses_not_duplicated_countries():
    ctx = make_context()
    ctx.set_processed(
        "author_addresses",
        [
            {"author": "A", "country": "China"},
            {"author": "B", "country": "China"},
            {"author": "C", "country": "USA"},
        ],
    )
    row = ctx.to_flat_dict()
    assert row["countries"] == "China; USA"


def test_legacy_mode_keeps_lists():
    ctx = make_context()
    ctx.set_extracted("sampleType", ["a", "b"])
    row = ctx.to_flat_dict(legacy_mode=True)
    assert row["sampleType"] == ["a", "b"]


def test_dict_serialization_json():
    ctx = make_context()
    ctx.set_processed("meta", {"a": 1, "b": [1, 2]})
    row = ctx.to_flat_dict(dict_serialization="json")
    assert json.loads(row["meta"]) == {"a": 1, "b": [1, 2]}


def test_dict_serialization_flatten():
    ctx = make_context()
    ctx.set_processed("meta", {"a": 1, "b": {"c": 2}})
    row = ctx.to_flat_dict(dict_serialization="flatten")
    assert row["meta.a"] == 1
    assert row["meta.b.c"] == 2


def test_dict_serialization_omit():
    ctx = make_context()
    ctx.set_processed("meta", {"a": 1})
    row = ctx.to_flat_dict(dict_serialization="omit")
    assert "meta" not in row
