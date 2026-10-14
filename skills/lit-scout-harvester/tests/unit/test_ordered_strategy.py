"""Tests for the pipeline ordered strategy (3B): lexicon first, LLM only on misses."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.config_loader import (
    CaliberConfig,
    ModuleConfig,
    load_config,
)
from lit_scout_harvester.core.pipeline import PipelineScheduler
from lit_scout_harvester.modules.decision_select import DecisionSelectModule
from lit_scout_harvester.modules.lexicon_tagger import LexiconTaggerModule
from lit_scout_harvester.modules.term_extractor import TermExtractorModule


SAMPLE_SET = {
    "artifact": "option_set",
    "field": "sampleType",
    "version": "1.0",
    "fingerprint": "fp-st",
    "options": [
        {"label": "lake sediments", "evidence_count": 40},
        {"label": "ice cores", "evidence_count": 20},
        {"label": "stalagmites", "evidence_count": 10},
    ],
}

REGION_SET = {
    "artifact": "option_set",
    "field": "study_region",
    "version": "1.0",
    "fingerprint": "fp-rg",
    "options": [
        {"label": "Tibetan Plateau", "evidence_count": 12},
        {"label": "North Atlantic", "evidence_count": 8},
    ],
}

LEXICON = {
    "sampleType": {
        "terms": {
            "lake sediments": ["lake sediment"],
            "ice cores": ["ice core"],
        }
    }
}

STRATEGY = {
    "fields": {
        "sampleType": {"order": ["lexicon", "decision"]},
    }
}


class CountingLLM:
    """Fake LLM counting calls and recording prompts."""

    def __init__(self, payload=None):
        self.payload = payload or {"fields": {}}
        self.calls = 0
        self.prompts = []

    async def chat(self, messages, **kwargs):
        self.calls += 1
        self.prompts.append(messages[0]["content"])
        if callable(self.payload):
            return json.dumps(self.payload(messages[0]["content"]))
        return json.dumps(self.payload)


def base_config(tmp_path):
    cfg = load_config("config/default.yaml")
    cfg.skeleton.translate_to_chinese = False
    cfg.skeleton.segment_abstract = False
    cfg.skeleton.language_detection = False
    cfg.skeleton.restore_author_address = False
    cfg.review.enabled = False
    cfg.modules["lexicon_tagger"] = ModuleConfig(enabled=True, **{"fields": LEXICON})
    cfg.modules["decision_select"] = ModuleConfig(enabled=True)
    return cfg


@pytest.mark.asyncio
async def test_lexicon_hit_never_enters_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber",
        lambda: CaliberConfig(strategy=STRATEGY),
    )
    cfg = base_config(tmp_path)
    fake = CountingLLM({"fields": {"sampleType": {"stalagmites": 1.0}}})
    decision = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_SET})
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache, modules=[LexiconTaggerModule(), decision])

    records = [
        {"UT": "WOS:hit", "TI": "A lake sediment record"},  # lexicon covers it
        {"UT": "WOS:miss", "TI": "Something entirely different"},
    ]
    contexts = await scheduler.run(records)

    assert fake.calls == 1  # only the miss reached the decision LLM
    hit_ctx, miss_ctx = contexts
    assert hit_ctx.extracted["sampleType"] == ["lake sediments"]
    assert hit_ctx.extracted["field_method"] == {"sampleType": "lexicon"}
    assert hit_ctx.extracted["sampleType_lexicon_hits"][0]["term"] == "lake sediment"
    assert miss_ctx.extracted["sampleType"] == "stalagmites"
    assert miss_ctx.extracted["field_method"] == {"sampleType": "decision"}


@pytest.mark.asyncio
async def test_miss_gets_full_option_set_in_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber",
        lambda: CaliberConfig(strategy=STRATEGY),
    )
    cfg = base_config(tmp_path)
    fake = CountingLLM({"fields": {"sampleType": {"ice cores": 1.0}}})
    decision = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_SET})
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache, modules=[LexiconTaggerModule(), decision])

    await scheduler.run([{"UT": "WOS:miss", "TI": "No lexicon words here"}])

    assert fake.calls == 1
    prompt = fake.prompts[0]
    for label in ("lake sediments", "ice cores", "stalagmites"):
        assert label in prompt


@pytest.mark.asyncio
async def test_partial_coverage_groups_by_missed_fields(tmp_path, monkeypatch):
    """One record hits field A only; the LLM must see just field B for it."""
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber",
        lambda: CaliberConfig(
            strategy={
                "fields": {
                    "sampleType": {"order": ["lexicon", "decision"]},
                    "study_region": {"order": ["lexicon", "decision"]},
                }
            }
        ),
    )
    cfg = base_config(tmp_path)
    fake = CountingLLM(
        {
            "fields": {
                "sampleType": {"stalagmites": 1.0},
                "study_region": {"North Atlantic": 1.0},
            }
        }
    )
    decision = DecisionSelectModule(
        fake, option_sets={"sampleType": SAMPLE_SET, "study_region": REGION_SET}
    )
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache, modules=[LexiconTaggerModule(), decision])

    records = [
        # hits sampleType via lexicon, misses study_region
        {"UT": "WOS:a", "TI": "lake sediment study in Greenland"},
        # misses both fields
        {"UT": "WOS:b", "TI": "Mystery record"},
    ]
    contexts = await scheduler.run(records)

    assert fake.calls == 2  # one call per group
    # The partial record's prompt must not offer already-tagged field options.
    first_prompt = fake.prompts[0]
    assert "Field 'study_region'" in first_prompt
    assert "Field 'sampleType'" not in first_prompt
    # The full-miss record was asked about both fields in one call.
    second_prompt = fake.prompts[1]
    assert "Field 'sampleType'" in second_prompt
    assert "Field 'study_region'" in second_prompt

    a, b = contexts
    assert a.extracted["sampleType"] == ["lake sediments"]
    assert a.extracted["study_region"] == "North Atlantic"
    assert a.extracted["field_method"] == {
        "sampleType": "lexicon",
        "study_region": "decision",
    }
    assert b.extracted["field_method"] == {
        "sampleType": "decision",
        "study_region": "decision",
    }


@pytest.mark.asyncio
async def test_lexicon_wins_over_llm_on_conflict(tmp_path, monkeypatch):
    """Even if the LLM would answer differently, the lexicon tag stands."""
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber",
        lambda: CaliberConfig(strategy=STRATEGY),
    )
    cfg = base_config(tmp_path)
    # Deterministic decision-select cache is disabled (no cache passed), so the
    # LLM is only called for the miss; the hit record never calls it at all.
    fake = CountingLLM({"fields": {"sampleType": {"stalagmites": 1.0}}})
    decision = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_SET})
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache, modules=[LexiconTaggerModule(), decision])

    contexts = await scheduler.run([{"UT": "WOS:hit", "TI": "ice core record"}])

    assert contexts[0].extracted["sampleType"] == ["ice cores"]


@pytest.mark.asyncio
async def test_lexicon_to_llm_route_with_term_extractor(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber",
        lambda: CaliberConfig(
            strategy={"fields": {"sampleType": {"order": ["lexicon", "llm"]}}}
        ),
    )
    cfg = base_config(tmp_path)
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "name": "test",
                "fields": [
                    {"name": "sampleType", "type": "list[str]", "description": "sample type"}
                ],
            }
        ),
        encoding="utf-8",
    )
    cfg.modules["term_extractor"] = ModuleConfig(
        enabled=True, **{"schema_path": str(schema_path)}
    )
    fake = CountingLLM({"results": [{"sampleType": ["stalagmites"]}]})
    extractor = TermExtractorModule(fake, CacheManager(str(tmp_path / "tex_cache.db")))
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache, modules=[LexiconTaggerModule(), extractor])

    records = [
        {"UT": "WOS:hit", "TI": "lake sediment record"},
        {"UT": "WOS:miss", "TI": "unrelated record"},
    ]
    contexts = await scheduler.run(records)

    assert fake.calls == 1  # the fully-covered record never reached the LLM
    hit_ctx, miss_ctx = contexts
    assert hit_ctx.extracted["sampleType"] == ["lake sediments"]
    assert hit_ctx.extracted["field_method"] == {"sampleType": "lexicon"}
    assert miss_ctx.extracted["sampleType"] == ["stalagmites"]
    assert miss_ctx.extracted["field_method"] == {"sampleType": "llm"}


@pytest.mark.asyncio
async def test_no_strategy_configured_llm_sees_everything(tmp_path, monkeypatch):
    """Without a caliber strategy the modules run in list order, unfiltered."""
    monkeypatch.setattr(
        "lit_scout_harvester.core.pipeline.load_caliber", lambda: CaliberConfig()
    )
    cfg = base_config(tmp_path)
    fake = CountingLLM({"fields": {"sampleType": {"ice cores": 1.0}}})
    decision = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_SET})
    cache = CacheManager(str(tmp_path / "cache.db"))
    scheduler = PipelineScheduler(cfg, cache, modules=[LexiconTaggerModule(), decision])

    contexts = await scheduler.run([{"UT": "WOS:hit", "TI": "lake sediment record"}])

    assert fake.calls == 1  # decision still runs on the lexicon-tagged record
    # ...but the deterministic lexicon tag stands (lexicon hit is final).
    assert contexts[0].extracted["sampleType"] == ["lake sediments"]
