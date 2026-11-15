"""Sciverse same-publication semantic context and term_extractor integration."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.sciverse_context import SciverseContextProvider
from lit_scout_harvester.modules.term_extractor import TermExtractorModule


class FakeSciverse:
    def __init__(self, hits=None, expanded=None):
        self.hits = hits if hits is not None else []
        self.expanded = expanded
        self.semantic_calls = []
        self.read_calls = []

    def semantic_search(self, **kwargs):
        self.semantic_calls.append(kwargs)
        return {"hits": self.hits}

    def read_content(self, doc_id, offset=0, limit=4096, collection="papers"):
        self.read_calls.append((doc_id, offset, limit, collection))
        return self.expanded


class FakeLLM:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.responses:
            return self.responses.pop(0)
        return json.dumps({"results": [{"sampleType": ["lake sediments"]}]})


def _rules(**overrides):
    return {
        "enabled": True,
        "expand_context": False,
        **overrides,
    }


def test_context_expands_only_same_title_hit():
    client = FakeSciverse(
        hits=[
            {"doc_id": "other", "title": "Other paper", "offset": 0, "chunk": "foreign"},
            {"doc_id": "doc-a", "title": "Paper A", "offset": 120, "chunk": "original"},
        ],
        expanded={"text": "expanded local evidence"},
    )
    record = {"TI": "Paper A", "AB": "Abstract"}
    context = SciverseContextProvider(client).context_for(
        record, _rules(expand_context=True, overlap_chars=20)
    )
    assert context == "expanded local evidence"
    assert client.read_calls == [("doc-a", 100, 48, "papers")]
    assert client.semantic_calls[0]["query"] == "Paper A\nAbstract"


def test_doi_match_uses_chunk_when_expansion_fails():
    client = FakeSciverse(
        hits=[{"doc_id": "doc-a", "doi": "https://doi.org/10.1/X", "chunk": "chunk text"}],
        expanded=None,
    )
    context = SciverseContextProvider(client).context_for(
        {"DI": "doi:10.1/x", "AB": "Abstract"}, _rules(expand_context=True)
    )
    assert context == "chunk text"
    assert len(client.read_calls) == 1


def test_conflicting_doi_rejects_hit_even_with_same_title():
    client = FakeSciverse(
        hits=[{"doc_id": "doc-a", "doi": "10.1/other", "title": "Paper A", "chunk": "foreign"}]
    )
    context = SciverseContextProvider(client).context_for(
        {"DI": "10.1/x", "TI": "Paper A"}, _rules()
    )
    assert context == ""
    assert client.read_calls == []


def test_unverifiable_record_does_not_search():
    client = FakeSciverse(hits=[])
    assert SciverseContextProvider(client).context_for({"AB": "Abstract"}, _rules()) == ""
    assert client.semantic_calls == []


@pytest.mark.asyncio
async def test_term_extractor_context_enters_prompt_and_cache_key(tmp_path):
    semantic = FakeSciverse(
        hits=[{"doc_id": "doc-a", "title": "Paper A", "chunk": "full-text candidate"}]
    )
    llm = FakeLLM()
    module = TermExtractorModule(llm, CacheManager(tmp_path / "cache.db"), semantic_client=semantic)
    raw = {"TI": "Paper A", "AB": "Abstract"}
    ctx = PipelineContext("r1", raw, object())
    config = {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "sciverse_semantic": _rules(),
    }
    await module.process(ctx, config)
    await module.process(ctx, {key: value for key, value in config.items() if key != "sciverse_semantic"})
    assert len(llm.calls) == 2, "有/无全文证据必须使用不同 LLM 缓存项"
    assert "full-text candidate" in llm.calls[0]["messages"][0]["content"]
    assert "full-text candidate" not in llm.calls[1]["messages"][0]["content"]
    assert "_sciverse_context" not in raw


@pytest.mark.asyncio
async def test_term_extractor_semantic_failure_preserves_baseline(tmp_path):
    class FailingSciverse(FakeSciverse):
        def semantic_search(self, **kwargs):
            raise ConnectionError("offline")

    llm = FakeLLM()
    module = TermExtractorModule(
        llm, CacheManager(tmp_path / "cache.db"), semantic_client=FailingSciverse()
    )
    ctx = PipelineContext("r1", {"TI": "Paper A", "AB": "Abstract"}, object())
    result = await module.process(ctx, {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "sciverse_semantic": _rules(),
    })
    prompt = llm.calls[0]["messages"][0]["content"]
    assert result["sampleType"]["value"] == ["lake sediments"]
    assert "Verified same-publication" not in prompt


@pytest.mark.asyncio
async def test_batch_fallback_reuses_semantic_context(tmp_path):
    semantic = FakeSciverse(
        hits=[{"doc_id": "doc-a", "title": "Paper A", "chunk": "full-text candidate"}]
    )
    llm = FakeLLM(responses=[
        '{"results": []}',
        '{"results": [{"sampleType": ["lake sediments"]}]}',
    ])
    module = TermExtractorModule(llm, CacheManager(tmp_path / "cache.db"), semantic_client=semantic)
    ctx = PipelineContext("r1", {"TI": "Paper A", "AB": "Abstract"}, object())
    results = await module.process_batch([ctx], {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "sciverse_semantic": _rules(),
    })
    assert results[0]["sampleType"]["value"] == ["lake sediments"]
    assert len(semantic.semantic_calls) == 1
    assert len(llm.calls) == 2
