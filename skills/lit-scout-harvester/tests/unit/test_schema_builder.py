"""Tests for scripts/schema_builder.py helper functions."""

from __future__ import annotations

from scripts.schema_builder import build_schema, parse_list_input, save_schema


def test_parse_list_input_comma():
    assert parse_list_input("a; b; c") == ["a", "b", "c"]


def test_parse_list_input_newline():
    assert parse_list_input("a\nb\nc") == ["a", "b", "c"]


def test_parse_list_input_mixed():
    assert parse_list_input("a; b\nc") == ["a", "b", "c"]


def test_parse_list_input_empty():
    assert parse_list_input("") == []


def test_build_schema_default_hints():
    schema = build_schema("x", "desc", [{"name": "f", "type": "str", "description": "d"}])
    assert schema["name"] == "x"
    assert schema["description"] == "desc"
    assert len(schema["fields"]) == 1
    assert len(schema["prompt_hints"]) >= 1


def test_build_schema_custom_hints():
    schema = build_schema("x", "desc", [{"name": "f", "type": "str", "description": "d"}], ["hint1"])
    assert schema["prompt_hints"] == ["hint1"]


def test_save_schema(tmp_path):
    schema = build_schema("x", "desc", [{"name": "f", "type": "str", "description": "d"}])
    path = tmp_path / "sub" / "schema.json"
    save_schema(schema, path)
    assert path.exists()
    import json
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "x"
