"""Sciverse accessible full-text pagination and term_extractor integration."""

from __future__ import annotations

import json

import pytest

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.sciverse_context import SciverseContextProvider
from lit_scout_harvester.modules.term_extractor import TermExtractorModule


class FakeFulltextSciverse:
    def __init__(self, hits=None, pages=None):
        self.hits = hits if hits is not None else []
        self.pages = pages or {}
        self.search_calls = []
        self.read_calls = []

    def search_metadata(self, **kwargs):
        self.search_calls.append(kwargs)
        return {"results": self.hits}

    def read_content(self, doc_id, offset=0, limit=4096, collection="papers"):
        self.read_calls.append((doc_id, offset, limit, collection))
        return self.pages.get(offset)


def _accessible_hit(**overrides):
    return {
        "doc_id": "doc-a",
        "doi": "10.1/x",
        "title": "Paper A",
        "is_content_accessible": True,
        **overrides,
    }


def _rules(**overrides):
    return {"page_chars": 3, "max_pages": 3, "max_context_chars": 12000, **overrides}


def test_accessible_doi_fulltext_follows_next_offset():
    client = FakeFulltextSciverse(
        hits=[_accessible_hit()],
        pages={
            0: {"text": "abc", "next_offset": 3, "more": True},
            3: {"text": "def", "next_offset": 6, "more": False},
        },
    )
    text = SciverseContextProvider(client).fulltext_for({"DI": "doi:10.1/X"}, _rules())
    assert text == "abc\ndef"
    assert client.search_calls[0]["filters"][0]["value"] == "10.1/x"
    assert [call[:2] for call in client.read_calls] == [("doc-a", 0), ("doc-a", 3)]


def test_inaccessible_document_does_not_read_content():
    client = FakeFulltextSciverse(hits=[_accessible_hit(is_content_accessible=False)])
    text = SciverseContextProvider(client).fulltext_for({"DI": "10.1/x"}, _rules())
    assert text == ""
    assert client.read_calls == []


def test_title_lookup_accepts_only_exact_title_match():
    client = FakeFulltextSciverse(
        hits=[
            _accessible_hit(doc_id="other", doi="", title="Paper A extended"),
            _accessible_hit(doc_id="match", doi="", title="Paper A"),
        ],
        pages={0: {"text": "abc", "more": False}},
    )
    text = SciverseContextProvider(client).fulltext_for({"TI": "Paper A"}, _rules())
    assert text == "abc"
    assert client.read_calls[0][0] == "match"


def test_non_advancing_next_offset_stops_without_loop():
    client = FakeFulltextSciverse(
        hits=[_accessible_hit()],
        pages={0: {"text": "abc", "next_offset": 0, "more": True}},
    )
    assert SciverseContextProvider(client).fulltext_for({"DI": "10.1/x"}, _rules()) == "abc"
    assert len(client.read_calls) == 1


def test_later_page_failure_keeps_already_read_text():
    client = FakeFulltextSciverse(
        hits=[_accessible_hit()],
        pages={0: {"text": "abc", "next_offset": 3, "more": True}},
    )
    text = SciverseContextProvider(client).fulltext_for(
        {"DI": "10.1/x"}, _rules(max_context_chars=2)
    )
    assert text == "ab"
    assert len(client.read_calls) == 2


@pytest.mark.asyncio
async def test_fulltext_enters_prompt_and_cache_key(tmp_path):
    client = FakeFulltextSciverse(
        hits=[_accessible_hit()],
        pages={0: {"text": "full text candidate", "more": False}},
    )

    class FakeLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            return json.dumps({"results": [{"sampleType": ["lake sediments"]}]})

    llm = FakeLLM()
    module = TermExtractorModule(llm, CacheManager(tmp_path / "cache.db"), semantic_client=client)
    raw = {"TI": "Paper A", "DI": "10.1/x"}
    ctx = PipelineContext("r1", raw, object())
    config = {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "sciverse_fulltext": {"enabled": True, **_rules()},
    }
    await module.process(ctx, config)
    await module.process(ctx, {key: value for key, value in config.items() if key != "sciverse_fulltext"})
    assert len(llm.calls) == 2
    assert "full text candidate" in llm.calls[0]["messages"][0]["content"]
    assert "full text candidate" not in llm.calls[1]["messages"][0]["content"]
    assert "_sciverse_fulltext" not in raw
