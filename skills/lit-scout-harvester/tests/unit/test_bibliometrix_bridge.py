"""Tests for R/Bibliometrix bridge (R1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lit_scout_harvester.parsers.bibliometrix_bridge import (
    BibliometrixError,
    bibliometrix_to_records,
    load_records_with_fallback,
)


def test_bibliometrix_convert_missing_rscript(tmp_path):
    from lit_scout_harvester.parsers.bibliometrix_bridge import bibliometrix_convert

    with pytest.raises(BibliometrixError):
        bibliometrix_convert(
            "dummy.wos",
            str(tmp_path / "out.csv"),
            rscript_path="rscript_that_does_not_exist_12345",
        )


def test_bibliometrix_to_records(tmp_path):
    csv = tmp_path / "biblio.csv"
    csv.write_text("AU,TI,PY\n\"Kemp, AES\",\"A test\",2024\n", encoding="utf-8")
    records = bibliometrix_to_records(str(csv))
    assert len(records) == 1
    assert records[0]["AU"] == "Kemp, AES"
    assert records[0]["TI"] == "A test"
    assert records[0]["PY"] == "2024"


def test_load_records_fallback_when_bibliometrix_unavailable(tmp_path):
    # Create a minimal WOS-like file.
    wos = tmp_path / "sample.wos"
    wos.write_text(
        "PT J\nAU Kemp, AES\nTI A test title\nAB A test abstract.\nPY 2024\nER\n",
        encoding="utf-8",
    )
    config = {
        "skeleton": {"base_data_source": "bibliometrix", "bibliometrix": {"rscript_path": "missing_r"}},
        "global": {"output_dir": str(tmp_path)},
    }
    records = load_records_with_fallback(str(wos), config)
    assert len(records) == 1
    assert records[0]["TI"] == "A test title"
