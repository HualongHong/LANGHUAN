"""Tests for src.parsers.author_address."""

from __future__ import annotations

from lit_scout_harvester.parsers.author_address import restore_author_address


def test_bracketed_multiple_authors_per_block():
    c1 = (
        "[Jordaan, L. J.] Council Geosci, Private Bag X112, ZA-0001 Pretoria, South Africa.; "
        "[Wepener, V.; Huizenga, J. M.] North West Univ, Unit Environm Sci, Private Bag X6001, ZA-2520 Potchefstroom, South Africa."
    )
    au = ["Jordaan, LJ", "Wepener, V", "Huizenga, JM"]
    af = ["Jordaan, L. J.", "Wepener, V.", "Huizenga, J. M."]
    addrs = restore_author_address(c1, au, af)

    assert len(addrs) == 3
    assert addrs[0]["author"] == "Jordaan, LJ"
    assert addrs[0]["country"] == "South Africa"
    assert addrs[0]["city"] == "Pretoria"
    assert addrs[0]["institution"] == "Council Geosci"
    assert addrs[0]["postal_code"] == "ZA-0001"
    assert addrs[0]["confidence"] == 1.0

    assert addrs[1]["country"] == "South Africa"
    assert addrs[1]["city"] == "Potchefstroom"
    assert addrs[1]["institution"] == "North West Univ"
    assert addrs[1]["confidence"] == 1.0

    assert addrs[2]["country"] == "South Africa"
    assert addrs[2]["city"] == "Potchefstroom"
    assert addrs[2]["confidence"] == 1.0


def test_unbracketed_shared_address():
    c1 = "Univ Southampton, Southampton Oceanog Ctr, Sch Ocean & Earth Sci, Southampton SO14 3ZH, Hants, England."
    au = ["Kemp, AES"]
    addrs = restore_author_address(c1, au)
    assert len(addrs) == 1
    assert addrs[0]["country"] == "England"
    assert addrs[0]["institution"] == "Univ Southampton"
    assert addrs[0]["confidence"] == 0.6


def test_author_with_multiple_addresses_gets_first():
    c1 = (
        "[Huizenga, J. M.] North West Univ, Potchefstroom, South Africa.; "
        "[Huizenga, J. M.] James Cook Univ, Townsville, Qld 4811, Australia."
    )
    au = ["Huizenga, JM"]
    addrs = restore_author_address(c1, au)
    assert addrs[0]["country"] == "South Africa"
    assert addrs[0]["confidence"] == 1.0


def test_missing_c1_returns_zero_confidence():
    au = ["Smith, JA", "Doe, RB"]
    addrs = restore_author_address("", au)
    assert len(addrs) == 2
    for a in addrs:
        assert a["address"] is None
        assert a["confidence"] == 0.0


def test_full_name_fallback():
    # Short name differs slightly from full name; full name should still match.
    c1 = "[Chen, Taili] Xichang Coll, Xichang 615000, Peoples R China."
    au = ["Chen, TL"]
    af = ["Chen, Taili"]
    addrs = restore_author_address(c1, au, af)
    assert addrs[0]["country"] == "Peoples R China"
    assert addrs[0]["confidence"] == 1.0


def test_options_can_disable_extraction():
    c1 = "[Smith, J.] Example Univ, City 12345, Country."
    au = ["Smith, J"]
    addrs = restore_author_address(
        c1, au, options={"extract_city": False, "extract_postal_code": False}
    )
    assert addrs[0]["country"] == "Country"
    assert addrs[0]["city"] is None
    assert addrs[0]["postal_code"] is None
    assert addrs[0]["institution"] == "Example Univ"
