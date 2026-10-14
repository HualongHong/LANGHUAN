"""Tests for src.external.gdp_cache."""

from __future__ import annotations

from lit_scout_harvester.external.gdp_cache import GDPCache


def test_set_and_get(tmp_path):
    cache = GDPCache(str(tmp_path / "gdp.db"))
    cache.set("USA", 2020, gdp_current=21_000_000.0, gdp_constant=19_000_000.0)
    entry = cache.get("USA", 2020)
    assert entry["gdp_current"] == 21_000_000.0
    assert entry["gdp_constant"] == 19_000_000.0


def test_get_nearest(tmp_path):
    cache = GDPCache(str(tmp_path / "gdp.db"))
    cache.set("USA", 2019, gdp_current=20.0, gdp_constant=19.0)
    nearest = cache.get_nearest("USA", 2020, max_gap=2)
    assert nearest["year"] == 2019
    assert nearest["year_gap"] == 1


def test_get_nearest_out_of_range(tmp_path):
    cache = GDPCache(str(tmp_path / "gdp.db"))
    cache.set("USA", 2010, gdp_current=10.0, gdp_constant=9.0)
    assert cache.get_nearest("USA", 2020, max_gap=5) is None
