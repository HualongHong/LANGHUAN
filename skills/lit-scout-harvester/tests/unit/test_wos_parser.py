"""Tests for src.parsers.wos_parser."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from lit_scout_harvester.parsers.wos_parser import (
    WosParseError,
    count_records,
    parse_wos_plaintext,
    parse_wos_plaintext_to_list,
)


SAMPLE_HEADER = """FN Clarivate Analytics Web of Science
VR 1.0
"""

MINIMAL_RECORD = """PT J
AU Smith, JA
   Doe, RB
AF Smith, John A.
   Doe, Robert B.
TI A study of something important
SO Journal of Examples
LA English
DT Article
DE keyword one; keyword two
ID term1; term2
AB This is the abstract.
   It has two lines.
C1 [Smith, John A.] Example Univ, Dept Sci, City, Country.
PY 2023
UT WOS:000000000000001
ER
"""


def write_wos_file(directory: Path, filename: str, content: str) -> Path:
    path = directory / filename
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_minimal_record():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_wos_file(Path(tmp), "minimal.wos", MINIMAL_RECORD)
        records = parse_wos_plaintext_to_list(path)

    assert len(records) == 1
    r = records[0]
    assert r["PT"] == "J"
    assert r["TI"] == "A study of something important"
    assert r["SO"] == "Journal of Examples"
    assert r["AU"] == ["Smith, JA", "Doe, RB"]
    assert r["AF"] == ["Smith, John A.", "Doe, Robert B."]
    assert r["AB"] == "This is the abstract. It has two lines."
    assert r["DE"] == ["keyword one", "keyword two"]
    assert r["PY"] == "2023"
    assert r["UT"] == "WOS:000000000000001"


def test_header_lines_are_ignored():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_wos_file(Path(tmp), "with_header.wos", SAMPLE_HEADER + MINIMAL_RECORD)
        records = parse_wos_plaintext_to_list(path)
    assert len(records) == 1
    assert records[0]["PT"] == "J"


def test_field_filtering():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_wos_file(Path(tmp), "minimal.wos", MINIMAL_RECORD)
        records = parse_wos_plaintext_to_list(path, fields=["TI", "AU", "PY"])
    assert records[0] == {"TI": "A study of something important", "AU": ["Smith, JA", "Doe, RB"], "PY": "2023"}


def test_multiple_records():
    content = MINIMAL_RECORD + "\n" + MINIMAL_RECORD.replace("WOS:000000000000001", "WOS:000000000000002")
    with tempfile.TemporaryDirectory() as tmp:
        path = write_wos_file(Path(tmp), "two.wos", content)
        records = parse_wos_plaintext_to_list(path)
    assert len(records) == 2
    assert records[0]["UT"] == "WOS:000000000000001"
    assert records[1]["UT"] == "WOS:000000000000002"


def test_count_records():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_wos_file(Path(tmp), "minimal.wos", MINIMAL_RECORD * 3)
        assert count_records(path) == 3


def test_missing_file_raises():
    with pytest.raises(WosParseError):
        next(parse_wos_plaintext("/nonexistent/file.wos"))


def test_streaming_does_not_load_all():
    """The iterator should be lazy; requesting one record should not parse the rest eagerly."""
    with tempfile.TemporaryDirectory() as tmp:
        path = write_wos_file(Path(tmp), "minimal.wos", MINIMAL_RECORD * 10)
        iterator = parse_wos_plaintext(path)
        first = next(iterator)
        assert first["UT"] == "WOS:000000000000001"
        iterator.close()  # ensure the file handle is closed before temp cleanup


def test_sample_wos_basic_integrity():
    """Sanity check on the provided real-world sample."""
    path = Path("tests/fixtures/sample.wos")
    if not path.exists():
        pytest.skip("Real sample file not available")

    records = parse_wos_plaintext_to_list(path, limit=10)
    assert len(records) == 10
    for r in records:
        assert r.get("PT") == "J"
        assert "TI" in r
        assert isinstance(r.get("AU"), list) and r["AU"]
        assert "PY" in r
        assert "UT" in r
