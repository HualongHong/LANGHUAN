"""Tests for the schema multiLabel/modeN optional keys (feedback capsule C7)."""

from __future__ import annotations

import json
from pathlib import Path

from lit_scout_harvester.schemas import (
    ALLOWED_FIELD_KEYS,
    OPTIONAL_FIELD_KEYS,
    validate_schema,
)

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "lit_scout_harvester" / "schemas"


def _load(name):
    return json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def test_new_keys_are_registered_in_allow_list():
    assert "multiLabel" in OPTIONAL_FIELD_KEYS
    assert "modeN" in OPTIONAL_FIELD_KEYS
    assert "multiLabel" in ALLOWED_FIELD_KEYS
    assert "modeN" in ALLOWED_FIELD_KEYS


def test_sample_rst_with_new_keys_validates():
    schema = _load("sample_rst.json")
    sci_question = next(f for f in schema["fields"] if f["name"] == "sciQuestion")
    assert sci_question["multiLabel"] is True
    assert sci_question["modeN"] == 2
    assert validate_schema(schema) == []


def test_legacy_schemas_still_validate():
    assert validate_schema(_load("paleoclimate.json")) == []
    assert validate_schema(_load("sci_edu.json")) == []


def test_unknown_field_key_is_reported():
    schema = {
        "name": "x",
        "fields": [
            {
                "name": "f",
                "type": "str",
                "description": "d",
                "surprise_key": 1,
            }
        ],
    }
    problems = validate_schema(schema)
    assert any("surprise_key" in p for p in problems)


def test_required_key_and_type_checks():
    schema = {"name": "x", "fields": [{"name": "f", "description": "d"}]}
    problems = validate_schema(schema)
    assert any("'type'" in p for p in problems)

    bad = {
        "name": "x",
        "fields": [
            {
                "name": "f",
                "type": "str",
                "description": "d",
                "multiLabel": "yes",
                "modeN": 0,
            }
        ],
    }
    problems = validate_schema(bad)
    assert any("multiLabel must be a boolean" in p for p in problems)
    assert any("modeN must be a positive integer" in p for p in problems)


def test_term_extractor_still_loads_schema_with_new_keys():
    from lit_scout_harvester.modules.term_extractor import TermExtractorModule

    module = TermExtractorModule(llm_client=None, cache=None)
    schema = module._load_schema(str(SCHEMAS_DIR / "sample_rst.json"))
    prompt = module._build_prompt(schema, [{"TI": "t", "AB": "a"}])
    assert "sciQuestion" in prompt
