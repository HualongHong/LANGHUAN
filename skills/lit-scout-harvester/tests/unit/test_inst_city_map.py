"""Tests for src.external.inst_city_map."""

from __future__ import annotations

from lit_scout_harvester.external.inst_city_map import InstitutionCityMap


def test_get_and_set(tmp_path):
    mapping = InstitutionCityMap(str(tmp_path / "inst.db"))
    mapping.set("Example University", "Beijing", "China", "manual")
    entry = mapping.get("Example University")
    assert entry["city"] == "Beijing"
    assert entry["country"] == "China"


def test_batch_update(tmp_path):
    mapping = InstitutionCityMap(str(tmp_path / "inst.db"))
    entries = [
        {"institution": "A Univ", "city": "City A", "country": "Country A"},
        {"institution": "B Univ", "city": "City B", "country": "Country B"},
    ]
    assert mapping.batch_update(entries) == 2
    assert mapping.stats()["entries"] == 2
