"""Tests for modules.constrained_choice (constrained selection within candidates, 3C)."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.constrained_choice import ConstrainedChoice


FULL_OPTIONS = ["lake sediments", "stalagmites", "ice cores", "tree rings"]


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    async def chat(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        return json.dumps(self.payload)


def make_ctx(title="A record"):
    return PipelineContext("r1", {"TI": title}, object())


def hand_rank(probs):
    return sorted(probs, key=lambda opt: (-probs[opt], opt.lower()))


@pytest.mark.asyncio
async def test_options_outside_candidates_are_excluded():
    # The LLM tries to put mass on a non-candidate; it must be dropped and the
    # candidate mass renormalized to 1.0.
    fake = FakeLLM(
        {"fields": {"sampleType": {"stalagmites": 0.5, "ice cores": 0.3, "tree rings": 0.2}}}
    )
    chooser = ConstrainedChoice(fake)
    candidates = ["stalagmites", "ice cores"]

    result = await chooser.choose(
        make_ctx(), "sampleType", candidates, all_options=FULL_OPTIONS
    )

    field = result["sampleType"]
    assert set(field["probabilities"]) == set(candidates)
    assert "tree rings" not in field["probabilities"]
    assert sum(field["probabilities"].values()) == pytest.approx(1.0)
    assert field["value"] == "stalagmites"
    assert result["excluded_options"] == ["lake sediments", "tree rings"]
    assert result["candidates"] == candidates
    # decision-select semantics preserved
    assert result["selection_method"] == "decision"


@pytest.mark.asyncio
async def test_ranking_matches_decision_select_semantics():
    probs = {"ice cores": 0.2, "stalagmites": 0.5, "lake sediments": 0.3}
    fake = FakeLLM({"fields": {"sampleType": probs}})
    chooser = ConstrainedChoice(fake)

    result = await chooser.choose(
        make_ctx(), "sampleType", list(probs), all_options=FULL_OPTIONS
    )

    field = result["sampleType"]
    assert field["ranking"] == hand_rank(probs)
    assert field["probabilities"]["stalagmites"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_prompt_only_lists_candidates():
    fake = FakeLLM({"fields": {"sampleType": {"ice cores": 1.0}}})
    chooser = ConstrainedChoice(fake)

    await chooser.choose(make_ctx(), "sampleType", ["ice cores", "stalagmites"])

    prompt = fake.prompts[0]
    assert "  - ice cores" in prompt
    assert "  - stalagmites" in prompt
    assert "tree rings" not in prompt
    assert "lake sediments" not in prompt


@pytest.mark.asyncio
async def test_multi_label_mode_n_truncation():
    probs = {"ice cores": 0.5, "stalagmites": 0.3, "lake sediments": 0.2}
    fake = FakeLLM({"fields": {"sampleType": probs}})
    chooser = ConstrainedChoice(fake)

    result = await chooser.choose(
        make_ctx(), "sampleType", list(probs), mode_n=2, all_options=FULL_OPTIONS
    )

    field = result["sampleType"]
    assert field["value"] == hand_rank(probs)[:2]


@pytest.mark.asyncio
async def test_accepts_raw_record_dict():
    fake = FakeLLM({"fields": {"sampleType": {"ice cores": 1.0}}})
    chooser = ConstrainedChoice(fake)

    result = await chooser.choose({"TI": "a record"}, "sampleType", ["ice cores"])

    assert result["sampleType"]["value"] == "ice cores"


@pytest.mark.asyncio
async def test_empty_candidates_rejected():
    chooser = ConstrainedChoice(FakeLLM({}))
    with pytest.raises(ValueError):
        await chooser.choose(make_ctx(), "sampleType", [])


@pytest.mark.asyncio
async def test_degenerate_distribution_uniform_fallback_within_candidates():
    fake = FakeLLM({"fields": {"sampleType": {"ice cores": 0.0, "stalagmites": 0.0}}})
    chooser = ConstrainedChoice(fake)

    result = await chooser.choose(make_ctx(), "sampleType", ["ice cores", "stalagmites"])

    field = result["sampleType"]
    # no zero-exit semantics: uniform over the candidate subset only
    assert set(field["probabilities"]) == {"ice cores", "stalagmites"}
    assert sum(field["probabilities"].values()) == pytest.approx(1.0)
