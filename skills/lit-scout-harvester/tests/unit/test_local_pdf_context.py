"""Local PDF MinerU context: containment, cache, and term_extractor integration."""

from __future__ import annotations

import json
import os

import pytest

from agentkit.mineru import MinerUDoc, MinerUError
from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.local_pdf_context import LocalPDFContextProvider
from lit_scout_harvester.modules.term_extractor import TermExtractorModule


class FakeMinerU:
    def __init__(self, markdown="# Full text\nlake sediment evidence", error=None):
        self.markdown = markdown
        self.error = error
        self.calls = []

    def parse_file(self, path, mode="auto"):
        self.calls.append((path, mode))
        if self.error:
            raise self.error
        return MinerUDoc(mode="local", markdown=self.markdown)


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps({"results": [{"sampleType": ["lake sediments"]}]})


def _rules(root, **overrides):
    return {
        "enabled": True,
        "root": str(root),
        "path_field": "PDF",
        "filename_pattern": "{safe_id}.pdf",
        "mode": "local",
        "max_context_chars": 12000,
        **overrides,
    }


def test_pdf_markdown_cached_by_file_fingerprint_and_truncated(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"%PDF-fake")
    cache = CacheManager(tmp_path / "cache.db")
    client = FakeMinerU()
    provider = LocalPDFContextProvider(client, cache)
    rules = _rules(root)

    first = provider.context_for({"PDF": "paper.pdf"}, rules)
    second = provider.context_for({"PDF": "paper.pdf"}, rules)
    assert first == second == client.markdown
    assert len(client.calls) == 1

    stat = pdf.stat()
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    provider.context_for({"PDF": "paper.pdf"}, rules)
    assert len(client.calls) == 2
    assert provider.context_for({"PDF": "paper.pdf"}, _rules(root, max_context_chars=5)) == "# Ful"
    assert len(client.calls) == 2


def test_doi_filename_fallback_and_root_containment(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    (root / "10.1000_example.pdf").write_bytes(b"%PDF-fake")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-outside")
    client = FakeMinerU()
    provider = LocalPDFContextProvider(client, CacheManager(tmp_path / "cache.db"))

    assert provider.context_for({"DI": "10.1000/example"}, _rules(root)) == client.markdown
    assert provider.context_for({"PDF": "../outside.pdf"}, _rules(root)) == ""
    assert provider.context_for({"PDF": str(outside)}, _rules(root)) == ""
    assert provider.context_for({"PDF": "missing.txt"}, _rules(root)) == ""
    assert len(client.calls) == 1


def test_corrupt_pdf_cache_entry_triggers_reparse(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    pdf = root / "paper.pdf"
    pdf.write_bytes(b"%PDF-fake")
    cache = CacheManager(tmp_path / "cache.db")
    client = FakeMinerU()
    provider = LocalPDFContextProvider(client, cache)
    stat = pdf.stat()
    key = provider._cache_key(pdf.resolve(), stat.st_size, stat.st_mtime_ns, "local")
    cache.set(key, "not-json")
    assert provider.context_for({"PDF": "paper.pdf"}, _rules(root)) == client.markdown
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_term_extractor_pdf_enters_prompt_and_cache_key(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF-fake")
    mineru = FakeMinerU()
    llm = FakeLLM()
    module = TermExtractorModule(llm, CacheManager(tmp_path / "cache.db"), pdf_client=mineru)
    raw = {"TI": "Paper A", "AB": "Abstract", "PDF": "paper.pdf"}
    ctx = PipelineContext("r1", raw, object())
    config = {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "local_pdf": _rules(root),
    }
    await module.process(ctx, config)
    await module.process(ctx, {key: value for key, value in config.items() if key != "local_pdf"})
    assert len(llm.calls) == 2
    assert "lake sediment evidence" in llm.calls[0]["messages"][0]["content"]
    assert "lake sediment evidence" not in llm.calls[1]["messages"][0]["content"]
    assert "_local_pdf_context" not in raw


@pytest.mark.asyncio
async def test_pdf_failure_preserves_baseline_extraction(tmp_path):
    (tmp_path / "paper.pdf").write_bytes(b"%PDF-fake")
    llm = FakeLLM()
    mineru = FakeMinerU(error=MinerUError("offline"))
    module = TermExtractorModule(
        llm,
        CacheManager(tmp_path / "cache.db"),
        pdf_client=mineru,
    )
    ctx = PipelineContext("r1", {"TI": "Paper A", "PDF": "paper.pdf"}, object())
    result = await module.process(ctx, {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "local_pdf": _rules(tmp_path),
    })
    assert result["sampleType"]["value"] == ["lake sediments"]
    assert len(mineru.calls) == 1
    assert "Local full-text PDF" not in llm.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_sciverse_failure_does_not_discard_pdf_context(tmp_path):
    root = tmp_path / "pdfs"
    root.mkdir()
    (root / "paper.pdf").write_bytes(b"%PDF-fake")

    class FailingSciverse:
        def semantic_search(self, **kwargs):
            raise ConnectionError("offline")

    llm = FakeLLM()
    module = TermExtractorModule(
        llm,
        CacheManager(tmp_path / "cache.db"),
        semantic_client=FailingSciverse(),
        pdf_client=FakeMinerU(),
    )
    ctx = PipelineContext("r1", {"TI": "Paper A", "PDF": "paper.pdf"}, object())
    await module.process(ctx, {
        "schema_path": "lit_scout_harvester/schemas/sample_rst.json",
        "local_pdf": _rules(root),
        "sciverse_semantic": {"enabled": True},
    })
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "lake sediment evidence" in prompt
    assert "Local full-text PDF" in prompt
