"""Tests for institution-city-map fallback in author_address."""

from __future__ import annotations

from lit_scout_harvester.external.inst_city_map import InstitutionCityMap
from lit_scout_harvester.parsers.author_address import restore_author_address


def test_inst_city_map_fallback(tmp_path):
    mapping = InstitutionCityMap(str(tmp_path / "inst.db"))
    mapping.set("Mystery Univ", "Mystery City", "Mysteryland", "manual")

    # Address has a country but no city; the map should fill in the city.
    c1 = "[Smith, J.] Mystery Univ, Mysteryland."
    au = ["Smith, J"]
    addrs = restore_author_address(
        c1, au, options={"use_inst_city_map": True}, inst_city_map=mapping
    )
    assert addrs[0]["city"] == "Mystery City"
    assert addrs[0]["country"] == "Mysteryland"
    assert addrs[0]["confidence"] == 0.7
