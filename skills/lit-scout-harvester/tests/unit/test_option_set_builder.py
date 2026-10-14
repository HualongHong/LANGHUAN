"""Tests for modules.option_set_builder (stage A of the two-stage architecture)."""

from __future__ import annotations

import inspect
import json
import re

import pytest

from lit_scout_harvester.modules.option_set_builder import OptionSetBuilder


class FakeGenerativeLLM:
    """Fake generative client: answers are looked up by record title."""

    def __init__(self, answers_by_title, default=None, fail=False):
        self.answers_by_title = answers_by_title
        self.default = default if default is not None else []
        self.fail = fail
        self.prompts = []

    async def chat(self, messages, **kwargs):
        if self.fail:
            raise ConnectionError("simulated API failure")
        prompt = messages[0]["content"]
        self.prompts.append(prompt)
        results = []
        for match in re.finditer(r"^\[(\d+)\] Title: (.+)$", prompt, re.MULTILINE):
            title = match.group(2).strip()
            results.append({"answers": list(self.answers_by_title.get(title, self.default))})
        return json.dumps({"results": results})


def make_records(titles):
    return [{"TI": t, "AB": f"Abstract of {t}"} for t in titles]


def titles_seen(fake):
    seen = []
    for prompt in fake.prompts:
        for match in re.finditer(r"^\[(\d+)\] Title: (.+)$", prompt, re.MULTILINE):
            seen.append(match.group(2).strip())
    return seen


@pytest.mark.asyncio
async def test_build_traverses_full_corpus_by_default():
    titles = [f"Record {i}" for i in range(7)]
    fake = FakeGenerativeLLM({t: ["lake sediments"] for t in titles})
    builder = OptionSetBuilder(fake)

    artifact = await builder.build(make_records(titles), field="sampleType", batch_size=3)

    # default of ``sample`` must be None (production forbids sampling)
    sig = inspect.signature(OptionSetBuilder.build)
    assert sig.parameters["sample"].default is None

    assert artifact["records_traversed"] == 7
    assert artifact["records_in_corpus"] == 7
    assert artifact["sampled"] is False
    # 7 records / batch_size 3 -> 3 LLM calls covering every record exactly once
    assert len(fake.prompts) == 3
    assert sorted(titles_seen(fake)) == sorted(titles)


@pytest.mark.asyncio
async def test_aggregation_counts_and_canonicalization():
    answers = {
        "R1": ["Lake Sediments"],
        "R2": ["lake sediments."],
        "R3": ["Lake Sediments", "Stalagmites"],
        "R4": ["stalagmites"],
    }
    fake = FakeGenerativeLLM(answers)
    builder = OptionSetBuilder(fake)
    artifact = await builder.build(make_records(list(answers)), field="sampleType")

    by_label = {o["label"]: o["evidence_count"] for o in artifact["options"]}
    # surface variants merged case-insensitively; first-seen casing kept;
    # record-level evidence: "Lake Sediments" appears in R1, R2 and R3
    assert by_label == {"Lake Sediments": 3, "Stalagmites": 2}
    # duplicate answers within one record count once (record-level evidence)
    r3_first = next(o for o in artifact["options"] if o["label"] == "Stalagmites")
    assert r3_first["first_seen_index"] == 2


@pytest.mark.asyncio
async def test_forbidden_options_are_dropped():
    answers = {
        "R1": ["lake sediments"],
        "R2": ["Other"],
        "R3": ["以上皆非"],
        "R4": ["none of the above"],
        "R5": ["ice cores"],
    }
    fake = FakeGenerativeLLM(answers)
    builder = OptionSetBuilder(fake)
    artifact = await builder.build(make_records(list(answers)), field="sampleType")

    labels = [o["label"].lower() for o in artifact["options"]]
    assert "other" not in labels
    assert "以上皆非" not in labels
    assert "none of the above" not in labels
    assert artifact["forbidden_options_dropped"] == 3
    assert sorted(labels) == ["ice cores", "lake sediments"]


@pytest.mark.asyncio
async def test_persisted_artifact_has_version_and_saturation(tmp_path):
    answers = {f"R{i}": ["lake sediments"] for i in range(6)}
    fake = FakeGenerativeLLM(answers)
    builder = OptionSetBuilder(fake)
    out = tmp_path / "option_set.json"

    artifact = await builder.build(
        make_records(list(answers)), field="sampleType", out_path=str(out), version="2.0"
    )

    assert out.exists()
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk == artifact
    assert on_disk["artifact"] == "option_set"
    assert on_disk["version"] == "2.0"
    assert on_disk["field"] == "sampleType"
    assert on_disk["fingerprint"]
    assert on_disk["options"][0]["evidence_count"] == 6
    saturation = on_disk["saturation"]
    assert saturation["second_half_new_options"] == 0
    assert saturation["converged"] is True
    assert saturation["dilution_curve"][-1]["records_seen"] == 6


@pytest.mark.asyncio
async def test_saturation_detects_second_half_options():
    answers = {
        "R0": ["lake sediments"],
        "R1": ["stalagmites"],
        "R2": ["lake sediments"],
        # corpus of 6 -> second half starts at index 3
        "R3": ["lake sediments"],
        "R4": ["ice cores"],  # new option in second half
        "R5": ["lake sediments"],
    }
    fake = FakeGenerativeLLM(answers)
    builder = OptionSetBuilder(fake)
    artifact = await builder.build(make_records(list(answers)), field="sampleType")

    saturation = artifact["saturation"]
    assert saturation["first_half_new_options"] == 2
    assert saturation["second_half_new_options"] == 1
    assert saturation["second_half_new_labels"] == ["ice cores"]
    assert saturation["converged"] is False


@pytest.mark.asyncio
async def test_sample_flag_is_test_only_and_marks_artifact():
    titles = [f"Record {i}" for i in range(7)]
    fake = FakeGenerativeLLM({t: ["lake sediments"] for t in titles})
    builder = OptionSetBuilder(fake)

    artifact = await builder.build(
        make_records(titles), field="sampleType", sample=2, batch_size=10
    )
    assert artifact["records_traversed"] == 2
    assert artifact["records_in_corpus"] == 7
    assert artifact["sampled"] is True
    assert titles_seen(fake) == titles[:2]


@pytest.mark.asyncio
async def test_llm_failure_raises_instead_of_partial_artifact(tmp_path):
    fake = FakeGenerativeLLM({}, fail=True)
    builder = OptionSetBuilder(fake)
    out = tmp_path / "option_set.json"

    with pytest.raises(RuntimeError, match="recall"):
        await builder.build(
            make_records(["R1", "R2"]), field="sampleType", out_path=str(out)
        )
    assert not out.exists()
