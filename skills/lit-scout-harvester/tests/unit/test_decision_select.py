"""Tests for modules.decision_select (stage B of the two-stage architecture)."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.decision_select import DecisionSelectModule


SAMPLE_TYPE_SET = {
    "artifact": "option_set",
    "field": "sampleType",
    "version": "1.0",
    "fingerprint": "fp-sampletype",
    "multiLabel": True,
    "modeN": 2,
    "options": [
        {"label": "lake sediments", "evidence_count": 40},
        {"label": "stalagmites", "evidence_count": 30},
        {"label": "ice cores", "evidence_count": 20},
    ],
}

REGION_SET = {
    "artifact": "option_set",
    "field": "study_region",
    "version": "1.0",
    "fingerprint": "fp-region",
    "options": [
        {"label": "Tibetan Plateau", "evidence_count": 12},
        {"label": "North Atlantic", "evidence_count": 8},
    ],
}


class FakeDecisionLLM:
    """Fake decision client returning a fixed per-field probability payload."""

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    async def chat(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        if callable(self.payload):
            return json.dumps(self.payload(messages[0]["content"]))
        return json.dumps(self.payload)


def make_ctx(title="A record about lake sediments"):
    return PipelineContext("r1", {"TI": title, "AB": "Abstract"}, object())


def hand_truncate(probs, n):
    ranking = sorted(probs, key=lambda opt: (-probs[opt], opt.lower()))
    return ranking[:n]


@pytest.mark.asyncio
async def test_multi_label_truncates_by_mode_n_byte_identical():
    probs = {"lake sediments": 0.5, "stalagmites": 0.3, "ice cores": 0.2}
    fake = FakeDecisionLLM({"fields": {"sampleType": probs}})
    module = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_TYPE_SET})

    result = await module.process(make_ctx(), {})

    expected = hand_truncate(probs, 2)
    assert result["sampleType"]["value"] == expected
    assert result["sampleType"]["value"] == ["lake sediments", "stalagmites"]
    # full probability distribution is preserved for audit (feedback C9)
    assert result["sampleType"]["probabilities"]["lake sediments"] == 0.5
    assert result["sampleType"]["ranking"] == hand_truncate(probs, 3)


@pytest.mark.asyncio
async def test_single_label_takes_top1():
    probs = {"Tibetan Plateau": 0.7, "North Atlantic": 0.3}
    fake = FakeDecisionLLM({"fields": {"study_region": probs}})
    module = DecisionSelectModule(fake, option_sets={"study_region": REGION_SET})

    result = await module.process(make_ctx(), {})

    assert result["study_region"]["value"] == "Tibetan Plateau"
    assert result["study_region"]["confidence"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_selection_method_marker_is_attached():
    fake = FakeDecisionLLM({"fields": {"study_region": {"Tibetan Plateau": 1.0, "North Atlantic": 0.0}}})
    module = DecisionSelectModule(fake, option_sets={"study_region": REGION_SET})

    result = await module.process(make_ctx(), {})

    assert result["selection_method"] == "decision"


@pytest.mark.asyncio
async def test_unknown_options_are_ignored_and_counted():
    probs = {
        "lake sediments": 0.4,
        "stalagmites": 0.3,
        "ice cores": 0.2,
        "volcanic ash tephra": 0.1,  # not in the frozen option set
    }
    fake = FakeDecisionLLM({"fields": {"sampleType": probs}})
    module = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_TYPE_SET})

    result = await module.process(make_ctx(), {})

    field = result["sampleType"]
    assert "volcanic ash tephra" not in field["probabilities"]
    assert field["unknown_options_ignored"] == ["volcanic ash tephra"]
    # remaining mass renormalized back to 1.0
    assert sum(field["probabilities"].values()) == pytest.approx(1.0)
    assert field["value"] == ["lake sediments", "stalagmites"]


@pytest.mark.asyncio
async def test_unknown_options_map_to_nearest_known_option():
    probs = {
        "lake sediment": 0.6,  # near-miss spelling of a frozen option
        "stalagmites": 0.25,
        "ice cores": 0.15,
    }
    fake = FakeDecisionLLM({"fields": {"sampleType": probs}})
    module = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_TYPE_SET})

    result = await module.process(
        make_ctx(), {"unknown_option_policy": "nearest"}
    )

    field = result["sampleType"]
    assert ("lake sediment", "lake sediments") in field["unknown_options_mapped"]
    # merged mass lands on the canonical option and wins the ranking
    assert field["value"][0] == "lake sediments"
    assert field["probabilities"]["lake sediments"] == pytest.approx(0.6)
    assert "unknown_options_ignored" not in field


@pytest.mark.asyncio
async def test_degenerate_distribution_has_no_zero_exit():
    fake = FakeDecisionLLM(
        {"fields": {"sampleType": {"lake sediments": 0.0, "stalagmites": 0.0, "ice cores": 0.0}}}
    )
    module = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_TYPE_SET})

    result = await module.process(make_ctx(), {})

    field = result["sampleType"]
    # uniform fallback: the field always receives exactly N* options
    assert len(field["value"]) == 2
    assert sum(field["probabilities"].values()) == pytest.approx(1.0)
    assert set(field["probabilities"]) == {
        "lake sediments",
        "stalagmites",
        "ice cores",
    }


@pytest.mark.asyncio
async def test_mode_n_from_config_overrides_artifact():
    probs = {"lake sediments": 0.5, "stalagmites": 0.3, "ice cores": 0.2}
    fake = FakeDecisionLLM({"fields": {"sampleType": probs}})
    module = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_TYPE_SET})

    result = await module.process(make_ctx(), {"mode_n": {"sampleType": 1}})

    assert result["sampleType"]["value"] == ["lake sediments"]


@pytest.mark.asyncio
async def test_one_call_per_record_and_order_preserved():
    probs = {"lake sediments": 0.5, "stalagmites": 0.3, "ice cores": 0.2}
    fake = FakeDecisionLLM({"fields": {"sampleType": probs}})
    module = DecisionSelectModule(fake, option_sets={"sampleType": SAMPLE_TYPE_SET})
    ctxs = [make_ctx("rec A"), make_ctx("rec B")]

    results = await module.process_batch(ctxs, {})

    assert len(results) == 2
    assert [r["sampleType"]["value"] for r in results] == [
        ["lake sediments", "stalagmites"],
        ["lake sediments", "stalagmites"],
    ]
    assert len(fake.prompts) == 2  # one decision call per record


@pytest.mark.asyncio
async def test_cached_result_skips_llm_on_second_run(tmp_path):
    probs = {"lake sediments": 0.5, "stalagmites": 0.3, "ice cores": 0.2}
    fake = FakeDecisionLLM({"fields": {"sampleType": probs}})
    cache = CacheManager(str(tmp_path / "cache.db"))
    module = DecisionSelectModule(
        fake, option_sets={"sampleType": SAMPLE_TYPE_SET}, cache=cache
    )

    first = await module.process(make_ctx(), {})
    calls_after_first = len(fake.prompts)
    second = await module.process(make_ctx(), {})

    assert len(fake.prompts) == calls_after_first  # no new LLM call
    assert second == first
    assert second["selection_method"] == "decision"
