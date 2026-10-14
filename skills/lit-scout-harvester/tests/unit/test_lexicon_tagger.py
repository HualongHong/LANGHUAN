"""Tests for modules.lexicon_tagger (deterministic lexicon tagging, 3B)."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.config_loader import CaliberConfig
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.base import ExtractionModule
from lit_scout_harvester.modules.lexicon_tagger import (
    LexiconTaggerModule,
    compile_term_pattern,
)


LEXICON = {
    "sampleType": {
        "terms": {
            "lake sediments": ["lake sediment", "lacustrine"],
            "ice cores": ["ice core"],
            "tephra": ["ash"],
        }
    }
}


def make_ctx(**raw):
    return PipelineContext(str(raw.get("UT", "r1")), raw, object())


def test_modulebase_interface_compliance():
    module = LexiconTaggerModule(LEXICON)
    assert isinstance(module, ExtractionModule)
    assert module.name == "lexicon_tagger"
    assert module.version


@pytest.mark.asyncio
async def test_hit_terms_and_positions_recorded():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(TI="Lake sediment records from Nam Co")

    result = await module.process(ctx, {})

    field = result["sampleType"]
    assert field["value"] == ["lake sediments"]
    assert field["confidence"] == 1.0
    assert result["field_method"] == {"sampleType": "lexicon"}
    hits = result["sampleType_lexicon_hits"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["term"] == "lake sediment"
    assert hit["label"] == "lake sediments"
    assert hit["source"] == "TI"
    assert ctx.raw["TI"][hit["start"] : hit["end"]] == "Lake sediment"


@pytest.mark.asyncio
async def test_case_insensitive_and_multiword():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(AB="We study ICE CORE isotopes and Lake Sediment DNA.")

    result = await module.process(ctx, {})

    labels = result["sampleType"]["value"]
    assert set(labels) == {"ice cores", "lake sediments"}
    terms = {h["term"] for h in result["sampleType_lexicon_hits"]}
    assert terms == {"ice core", "lake sediment"}


@pytest.mark.asyncio
async def test_word_boundary_prevents_substring_match():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(TI="A flashy dashboard for data science")  # 'ash' inside 'flashy'

    result = await module.process(ctx, {})

    assert "sampleType" not in result
    assert "field_method" not in result


@pytest.mark.asyncio
async def test_boundary_allows_real_match():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(TI="Basaltic ash layers in Greenland")

    result = await module.process(ctx, {})

    assert result["sampleType"]["value"] == ["tephra"]
    assert result["sampleType_lexicon_hits"][0]["term"] == "ash"


@pytest.mark.asyncio
async def test_miss_produces_no_label():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(TI="Completely unrelated title", AB="Nothing here.")

    result = await module.process(ctx, {})

    # 未命中 = 不产标签（不算错误），交给下游 LLM
    assert result == {}


@pytest.mark.asyncio
async def test_two_runs_byte_identical():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(TI="Lake sediment and ice core study", DE="paleoclimate; lacustrine")

    first = await module.process(ctx, {})
    second = await module.process(make_ctx(TI="Lake sediment and ice core study", DE="paleoclimate; lacustrine"), {})

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )


@pytest.mark.asyncio
async def test_multiple_hits_and_label_ordering():
    module = LexiconTaggerModule(LEXICON)
    ctx = make_ctx(TI="Ice core ash record")  # ice core first, ash second

    result = await module.process(ctx, {})

    assert result["sampleType"]["value"] == ["ice cores", "tephra"]
    starts = [h["start"] for h in result["sampleType_lexicon_hits"]]
    assert starts == sorted(starts)


@pytest.mark.asyncio
async def test_lexicon_path_and_source_fields(tmp_path):
    lex_file = tmp_path / "lex.json"
    lex_file.write_text(
        json.dumps({"speleothems": ["stalagmite", "speleothem"]}), encoding="utf-8"
    )
    module = LexiconTaggerModule(
        {"sampleType": {"path": str(lex_file), "source_fields": ["DE"]}}
    )
    ctx = make_ctx(TI="stalagmite in title must not match", DE="cave; stalagmite")

    result = await module.process(ctx, {})

    assert result["sampleType"]["value"] == ["speleothems"]
    assert result["sampleType_lexicon_hits"][0]["source"] == "DE"


@pytest.mark.asyncio
async def test_caliber_strategy_fallback(monkeypatch):
    caliber = CaliberConfig(
        strategy={
            "fields": {
                "sampleType": {
                    "order": ["lexicon", "decision"],
                    "lexicon": {"terms": {"ice cores": ["ice core"]}},
                }
            }
        }
    )
    monkeypatch.setattr(
        "lit_scout_harvester.modules.lexicon_tagger.load_caliber", lambda: caliber
    )
    module = LexiconTaggerModule()
    ctx = make_ctx(TI="An ice core record")

    result = await module.process(ctx, {})

    assert result["sampleType"]["value"] == ["ice cores"]
    assert result["field_method"] == {"sampleType": "lexicon"}


@pytest.mark.asyncio
async def test_empty_lexicon_raises():
    module = LexiconTaggerModule({"sampleType": {"terms": {}}})
    with pytest.raises(ValueError):
        await module.process(make_ctx(TI="x"), {})


def test_compile_term_pattern_escapes_regex():
    pattern = compile_term_pattern("U-Th (238)").pattern
    assert "\\" in pattern  # parentheses escaped
