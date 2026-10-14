"""Tests for C11 counting modes in utils.country."""

from __future__ import annotations

import pytest

from lit_scout_harvester.utils.country import (
    aggregate_country_counts,
    canonical_counting_mode,
    check_fractional_conservation,
    count_record_countries,
    locatable_record_count,
)


THREE_AUTHORS_TWO_COUNTRIES = [
    {"author": "A", "country": "China"},
    {"author": "B", "country": "United States"},
    {"author": "C", "country": None},
]


def test_fractional_three_authors_two_countries():
    weights = count_record_countries(THREE_AUTHORS_TWO_COUNTRIES, "fractional")
    # 3 位作者分属 2 国：n=2 位可定位作者，每国得 1/n = 0.5。
    assert weights == {"CHN": 0.5, "USA": 0.5}


def test_whole_count_three_authors_two_countries():
    weights = count_record_countries(THREE_AUTHORS_TWO_COUNTRIES, "whole_count")
    assert weights == {"CHN": 1.0, "USA": 1.0}


def test_fractional_weighted_by_author_count():
    addresses = [
        {"country": "China"},
        {"country": "China"},
        {"country": "USA"},
    ]
    weights = count_record_countries(addresses, "fractional")
    assert weights["CHN"] == pytest.approx(2 / 3)
    assert weights["USA"] == pytest.approx(1 / 3)


def test_first_author_mode():
    weights = count_record_countries(THREE_AUTHORS_TWO_COUNTRIES, "first_author")
    assert weights == {"CHN": 1.0}


def test_first_author_skips_unlocatable_lead():
    addresses = [{"country": None}, {"country": "Brazil"}, {"country": "China"}]
    weights = count_record_countries(addresses, "first_author")
    assert weights == {"BRA": 1.0}


def test_unlocatable_record_returns_empty():
    assert count_record_countries([{"country": None}], "fractional") == {}
    assert count_record_countries([], "whole_count") == {}
    assert count_record_countries([{"country": "Atlantis"}], "fractional") == {}


def test_country_aliases_merge_under_same_iso():
    addresses = [{"country": "Peoples R China"}, {"country": "China"}]
    weights = count_record_countries(addresses, "fractional")
    assert weights == {"CHN": 1.0}


def test_mode_alias_canonicalization():
    assert canonical_counting_mode("whole") == "whole_count"
    assert canonical_counting_mode("first-author") == "first_author"
    with pytest.raises(ValueError):
        canonical_counting_mode("bogus")


def test_aggregate_uses_single_mode():
    records = [
        [{"country": "China"}, {"country": "USA"}],
        [{"country": "China"}],
    ]
    frac = aggregate_country_counts(records, "fractional")
    assert frac["CHN"] == pytest.approx(1.5)
    assert frac["USA"] == pytest.approx(0.5)
    whole = aggregate_country_counts(records, "whole_count")
    assert whole == {"CHN": 2.0, "USA": 1.0}


def test_fractional_conservation_invariant():
    """守恒不变量：fractional 列各记录之和 == 可定位记录数（C11/C12）。"""
    records = [
        [{"country": "China"}, {"country": "USA"}, {"country": None}],
        [{"country": "China"}, {"country": "China"}],
        [{"country": None}],                      # 完全不可定位：贡献 0
        [{"country": "Atlantis"}],                # 归一化失败：贡献 0
        [{"country": "USA"}],
    ]
    report = check_fractional_conservation(records)
    assert report["locatable_records"] == 3
    assert report["frac_sum"] == pytest.approx(3.0)
    assert report["balanced"] is True
    assert "分母" in report["denominator_note"]


def test_locatable_record_count():
    records = [
        [{"country": "China"}],
        [{"country": None}],
        [],
        [{"country": "USA"}, {"country": "Canada"}],
    ]
    assert locatable_record_count(records) == 2


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        count_record_countries([{"country": "China"}], "nonsense")
