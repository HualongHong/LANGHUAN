"""Tests for src.utils.country."""

from __future__ import annotations

from lit_scout_harvester.utils.country import normalize_country


def test_normalize_common_names():
    assert normalize_country("South Africa") == "ZAF"
    assert normalize_country("United States") == "USA"
    assert normalize_country("Peoples R China") == "CHN"
    assert normalize_country("England") == "GBR"


def test_normalize_unknown():
    assert normalize_country("Atlantis") is None
    assert normalize_country("") is None
