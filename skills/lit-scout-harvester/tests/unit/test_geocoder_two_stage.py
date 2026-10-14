"""Tests for geocoder two-stage mode (C10 extract-then-map)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.external.gazetteer import Gazetteer
from lit_scout_harvester.modules.geocoder import GeocoderModule


def make_ctx(record_id: str, raw=None, processed=None) -> PipelineContext:
    ctx = PipelineContext(record_id, raw or {}, object())
    for key, value in (processed or {}).items():
        ctx.set_processed(key, value)
    return ctx


@pytest.fixture
def gazetteer():
    return Gazetteer()


@pytest.fixture
def module(gazetteer):
    return GeocoderModule(gazetteer=gazetteer)


def run_two_stage(module, ctx, config=None):
    cfg = {"mode": "two_stage"}
    cfg.update(config or {})
    return module.process(ctx, cfg)


@pytest.mark.asyncio
async def test_two_stage_three_columns_gazetteer_hit(module):
    ctx = make_ctx(
        "r1",
        raw={"AB": "We sampled soils in Beijing and New York during the campaign."},
    )
    result = await run_two_stage(module, ctx)
    assert result["place_raw"] == ["Beijing", "New York"]
    assert result["country_mapped"] == ["CHN", "USA"]
    assert result["mapping_method"] == ["gazetteer_hit", "gazetteer_hit"]
    assert result["geo_unmatched"] == []
    assert result["geo_unmatched_count"] == 0
    assert "geo_unlocated" not in ctx.flags


@pytest.mark.asyncio
async def test_two_stage_deterministic_byte_identical(module):
    ctx = make_ctx(
        "r1",
        raw={
            "AB": "Fieldwork covered Beijing, Bavaria and São Paulo.",
            "C1": "Univ Beijing, Beijing, Peoples R China",
        },
        processed={
            "author_addresses": [
                {"author": "Li, M", "city": "Beijing", "country": "China"}
            ]
        },
    )
    first = json.dumps(await run_two_stage(module, ctx), ensure_ascii=False, sort_keys=True)
    second = json.dumps(await run_two_stage(module, ctx), ensure_ascii=False, sort_keys=True)
    assert first == second


@pytest.mark.asyncio
async def test_two_stage_llm_extracted_places(module):
    ctx = make_ctx("r1", raw={"AB": "We conducted surveys in several regions."})

    def fake_extractor(text):
        return ["Madrid", "北京"]

    result = await run_two_stage(module, ctx, {"llm_extractor": fake_extractor})
    assert result["place_raw"] == ["Madrid", "北京"]
    assert result["country_mapped"] == ["ESP", "CHN"]
    assert result["mapping_method"] == ["llm_extracted", "llm_extracted"]


@pytest.mark.asyncio
async def test_two_stage_llm_places_not_verbatim_in_text(module):
    """LLM 泛化（Iberian Peninsula→Spain 类）：地名不必逐字出现在摘要中。"""
    ctx = make_ctx("r1", raw={"AB": "Samples came from the peninsula's southern edge."})

    async def fake_extractor(text):
        return ["Iberian Peninsula"]

    result = await run_two_stage(module, ctx, {"llm_extractor": fake_extractor})
    assert result["place_raw"] == ["Iberian Peninsula"]
    assert result["country_mapped"] == ["ESP"]
    assert result["mapping_method"] == ["llm_extracted"]


@pytest.mark.asyncio
async def test_two_stage_dictionary_hit_wins_over_llm(module):
    ctx = make_ctx("r1", raw={"AB": "Work was done in Beijing."})

    def fake_extractor(text):
        return ["Beijing", "Tokyo"]

    result = await run_two_stage(module, ctx, {"llm_extractor": fake_extractor})
    # 词典已命中 Beijing（gazetteer_hit），LLM 只贡献新实例 Tokyo。
    assert result["place_raw"] == ["Beijing", "Tokyo"]
    assert result["mapping_method"] == ["gazetteer_hit", "llm_extracted"]


@pytest.mark.asyncio
async def test_two_stage_unmatched_counted_and_listed(module):
    ctx = make_ctx("r1", raw={"AB": "We camped near Nowheresville and Beijing."})

    def fake_extractor(text):
        return ["Nowheresville"]

    result = await run_two_stage(module, ctx, {"llm_extractor": fake_extractor})
    assert result["place_raw"] == ["Beijing", "Nowheresville"]
    assert result["country_mapped"] == ["CHN", ""]
    assert result["mapping_method"] == ["gazetteer_hit", "unmatched"]
    assert result["geo_unmatched"] == ["Nowheresville"]
    assert result["geo_unmatched_count"] == 1
    # 不静默丢弃：进入模块级「无法定位」清单。
    report = module.unmatched_report()
    assert report["unmatched_count"] == 1
    assert report["unmatched_places"] == [
        {"record_id": "r1", "place_raw": "Nowheresville"}
    ]


@pytest.mark.asyncio
async def test_two_stage_unlocated_record_flagged(module):
    ctx = make_ctx("r1", raw={"AB": "A purely theoretical study with no sites."})
    result = await run_two_stage(module, ctx)
    assert result["place_raw"] == []
    assert result["country_mapped"] == []
    assert result["mapping_method"] == []
    assert result["geo_unmatched_count"] == 0
    assert "geo_unlocated" in ctx.flags
    assert module.unmatched_report()["unlocated_records"] == ["r1"]


@pytest.mark.asyncio
async def test_two_stage_chinese_text_and_addresses(module):
    ctx = make_ctx(
        "r1",
        raw={"AB": "样地位于北京和东京附近。"},
        processed={
            "author_addresses": [
                {"author": "Wang, X", "city": "Shanghai", "country": "China"},
                {"author": "Doe, J", "city": None, "country": None},
            ]
        },
    )
    result = await run_two_stage(module, ctx)
    assert "北京" in result["place_raw"]
    assert "东京" in result["place_raw"]
    iso = result["country_mapped"]
    assert iso[result["place_raw"].index("北京")] == "CHN"
    assert iso[result["place_raw"].index("东京")] == "JPN"


@pytest.mark.asyncio
async def test_two_stage_duplicate_places_deduped(module):
    ctx = make_ctx(
        "r1", raw={"AB": "Beijing was revisited; Beijing samples were reanalysed."}
    )
    result = await run_two_stage(module, ctx)
    assert result["place_raw"] == ["Beijing"]


@pytest.mark.asyncio
async def test_legacy_mode_default_unchanged(monkeypatch, tmp_path):
    """旧调用（无 mode 配置）保持 legacy 行为：Nominatim 路径，接口不炸。"""
    from lit_scout_harvester.core.cache_manager import CacheManager

    cache = CacheManager(str(tmp_path / "geo.db"))
    module = GeocoderModule(cache)

    def fake_geocode(query, exactly_one=True, language="en"):
        return SimpleNamespace(
            latitude=1.0,
            longitude=2.0,
            address="Somewhere",
            raw={"type": "city"},
        )

    monkeypatch.setattr(module._get_geolocator(), "geocode", fake_geocode)
    ctx = make_ctx(
        "r1",
        processed={"author_addresses": [{"city": "Beijing", "country": "China"}]},
    )
    result = await module.process(ctx, {"include_coordinates": True})
    loc = result["geocoded_locations"][0]
    assert loc["latitude"] == 1.0
    assert loc["note"] == "ok"


@pytest.mark.asyncio
async def test_unknown_mode_falls_back_to_legacy(module, monkeypatch):
    ctx = make_ctx("r1", processed={"author_addresses": []})
    result = await module.process(ctx, {"mode": "bogus"})
    assert result == {"geocoded_locations": []}


@pytest.mark.asyncio
async def test_two_stage_llm_error_swallowed(module):
    ctx = make_ctx("r1", raw={"AB": "Work was done in Beijing."})

    def broken_extractor(text):
        raise RuntimeError("boom")

    result = await run_two_stage(module, ctx, {"llm_extractor": broken_extractor})
    assert result["place_raw"] == ["Beijing"]
    assert result["mapping_method"] == ["gazetteer_hit"]
