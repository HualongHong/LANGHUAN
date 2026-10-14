"""Tests for external.gazetteer (C10 stage 2: deterministic country mapping)."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.external.gazetteer import (
    BUILTIN_DATA_PATH,
    Gazetteer,
    normalize_place_name,
)


def test_builtin_sample_loads():
    gaz = Gazetteer()
    assert len(gaz) > 50
    assert gaz.data_path == BUILTIN_DATA_PATH
    hit = gaz.lookup("Beijing")
    assert hit is not None
    assert hit.country_code == "CHN"
    assert hit.country_name == "China"


def test_lookup_case_insensitive():
    gaz = Gazetteer()
    assert gaz.lookup("bEIJING").country_code == "CHN"
    assert gaz.lookup("new york").country_code == "USA"
    assert gaz.lookup("TOKYO").country_code == "JPN"


def test_lookup_chinese_aliases():
    gaz = Gazetteer()
    assert gaz.lookup("北京").country_code == "CHN"
    assert gaz.lookup("东京").country_code == "JPN"  # Tokyo 的别名
    assert gaz.lookup("纽约").country_code == "USA"


def test_lookup_punctuation_and_accents():
    gaz = Gazetteer()
    assert gaz.lookup("Beijing,").country_code == "CHN"
    assert gaz.lookup("  New   York  ").country_code == "USA"
    assert gaz.lookup("sao paulo").country_code == "BRA"  # São Paulo 无重音命中
    assert gaz.lookup("u.s.a.").country_code == "USA"


def test_lookup_region_aliases():
    gaz = Gazetteer()
    assert gaz.lookup("Iberian Peninsula").country_code == "ESP"
    assert gaz.lookup("伊比利亚半岛").country_code == "ESP"
    assert gaz.lookup("加州").country_code == "USA"


def test_lookup_miss_returns_none():
    gaz = Gazetteer()
    assert gaz.lookup("Atlantis") is None
    assert gaz.lookup("") is None
    assert gaz.lookup(None) is None
    assert gaz.lookup("   ") is None


def test_lookup_deterministic():
    gaz = Gazetteer()
    first = gaz.lookup("Berlin")
    second = gaz.lookup("Berlin")
    assert first == second
    assert repr(first) == repr(second)


def test_custom_json_path(tmp_path):
    data = {
        "version": "test",
        "places": [
            {
                "name": "Springfield",
                "country_code": "USA",
                "country_name": "United States",
                "type": "city",
                "aliases": ["春田"],
            }
        ],
    }
    path = tmp_path / "mini.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    gaz = Gazetteer(path)
    assert len(gaz) == 1
    assert gaz.lookup("springfield").country_code == "USA"
    assert gaz.lookup("春田").country_code == "USA"
    assert gaz.lookup("Beijing") is None


def test_custom_csv_path(tmp_path):
    path = tmp_path / "mini.csv"
    path.write_text(
        "name,country_code,country_name,type,aliases\n"
        "Osgiliath,GNR,Gondor,city,魔都\n",
        encoding="utf-8",
    )
    gaz = Gazetteer(path)
    assert gaz.lookup("osgiliath").country_code == "GNR"
    assert gaz.lookup("魔都").country_name == "Gondor"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Gazetteer(tmp_path / "nope.json")


def test_normalize_place_name():
    assert normalize_place_name("New York,") == "new york"
    assert normalize_place_name("  São\tPAULO ") == "sao paulo"
    assert normalize_place_name("北京") == "北京"
    assert normalize_place_name("u.s.a.") == "u s a"
