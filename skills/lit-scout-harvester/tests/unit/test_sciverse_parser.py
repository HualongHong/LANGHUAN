"""Tests for lit_scout_harvester.parsers.sciverse — 数据源 + 题录补全，全程 fake client。"""

from __future__ import annotations

import json
from pathlib import Path

from lit_scout_harvester.parsers.data_source import detect_datasource
from lit_scout_harvester.parsers.sciverse import (
    EnrichStats,
    enrich_records,
    load_sciverse_records,
    maybe_enrich_records,
    record_from_paper,
)


class FakeSciverseClient:
    """按 filters/query 返回脚本化命中的最小 client（ duck-type SciverseClient ）。"""

    def __init__(self, by_doi: dict | None = None, by_title: dict | None = None,
                 fail: bool = False, configured: bool = True):
        self.by_doi = by_doi or {}
        self.by_title = by_title or {}
        self.fail = fail
        self.configured = configured
        self.calls = 0

    def search_metadata(self, query=None, filters=None, page=1, page_size=25, **kwargs):
        self.calls += 1
        if self.fail:
            return None
        for f in filters or []:
            if f.get("field") == "doi":
                hit = self.by_doi.get(f.get("value"))
                return {"results": [hit] if hit else [], "total_count": 1 if hit else 0,
                        "page": 1, "page_size": page_size, "total_pages": 1}
            if f.get("field") == "title":
                hits = self.by_title.get(f.get("value"), [])
                return {"results": hits[:page_size], "total_count": len(hits),
                        "page": page, "page_size": page_size,
                        "total_pages": (len(hits) + page_size - 1) // page_size}
        return {"results": [], "total_count": 0, "page": page, "page_size": page_size, "total_pages": 1}


PAPER = {
    "title": "Transformer Attention Survey",
    "abstract": "We survey attention mechanisms.",
    "author": [{"name": "Alice A", "orcid": ""}, {"name": "Bob B"}],
    "publication_venue_name_unified": "JMLR",
    "publication_published_year": 2026,
    "doi": "10.1/abc",
}


class TestRecordMapping:
    def test_wos_field_codes(self):
        rec = record_from_paper(PAPER)
        assert rec["TI"] == "Transformer Attention Survey"
        assert rec["AB"].startswith("We survey")
        assert rec["AU"] == ["Alice A", "Bob B"]
        assert rec["SO"] == "JMLR"
        assert rec["PY"] == 2026
        assert rec["DI"] == "10.1/abc"

    def test_partial_hit(self):
        rec = record_from_paper({"title": "Only Title"})
        assert rec == {"TI": "Only Title"}


class TestDatasourceModes:
    def test_detect_sciverse_job(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"mode": "doi_list", "dois": ["10.1/abc"]}))
        assert detect_datasource(str(job)) == "sciverse"
        job.write_text(json.dumps({"mode": "search", "query": "attention"}))
        assert detect_datasource(str(job)) == "sciverse"

    def test_doi_list_mode(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"mode": "doi_list", "dois": ["10.1/abc", "10.1/miss", "10.1/xyz"]}))
        client = FakeSciverseClient(by_doi={"10.1/abc": PAPER})
        records = load_sciverse_records(str(job), client=client)
        assert len(records) == 1
        assert records[0]["DI"] == "10.1/abc"

    def test_doi_list_fail_soft(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"mode": "doi_list", "dois": ["10.1/abc"]}))
        client = FakeSciverseClient(fail=True)
        assert load_sciverse_records(str(job), client=client) == []

    def test_search_mode_paginates(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"mode": "search", "query": "attention", "max_records": 3, "page_size": 2}))
        many = [{**PAPER, "title": f"P{i}", "doi": f"10.1/{i}"} for i in range(5)]
        client = FakeSciverseClient()
        client.search_metadata = lambda **kw: {
            "results": many[(kw["page"] - 1) * kw["page_size"]: kw["page"] * kw["page_size"]],
            "total_count": 5, "page": kw["page"], "page_size": kw["page_size"], "total_pages": 3,
        }
        records = load_sciverse_records(str(job), client=client)
        assert len(records) == 3  # max_records 截断
        assert [r["DI"] for r in records] == ["10.1/0", "10.1/1", "10.1/2"]


class TestEnrichRecords:
    def _records(self):
        return [
            {"TI": "Transformer Attention Survey", "AB": "", "DI": "10.1/abc", "SO": "", "PY": ""},
            {"TI": "Local Only Paper", "AB": "has abstract", "DI": "", "SO": "", "PY": ""},
            {"TI": "Transformer Attention Survey", "AB": "existing", "DI": "10.1/abc"},
        ]

    def test_fills_only_missing_never_overwrites(self):
        client = FakeSciverseClient(by_doi={"10.1/abc": PAPER})
        records = self._records()
        stats = enrich_records(records, client=client, fields=["AB", "DI", "SO", "PY"])
        r0, r1, r2 = records
        assert r0["AB"] == "We survey attention mechanisms."  # 缺 → 补
        assert r0["SO"] == "JMLR" and r0["PY"] == 2026
        assert r2["AB"] == "existing"  # 已有 → 不覆盖
        assert stats.enriched >= 1
        assert stats.fields_filled.get("AB", 0) == 1

    def test_title_guard_requires_exact_match(self):
        by_title = {"Transformer Attention Survey": [{**PAPER, "title": "Transformer attention survey"}]}
        client = FakeSciverseClient(by_title=by_title)
        records = [{"TI": "Transformer Attention Survey", "AB": ""}]
        stats = enrich_records(records, client=client, fields=["AB"])
        assert records[0]["AB"] == "We survey attention mechanisms."  # 归一化相等 → 命中

        by_title_bad = {"Unique Title X": [{**PAPER, "title": "Completely Different"}]}
        client_bad = FakeSciverseClient(by_title=by_title_bad)
        records_bad = [{"TI": "Unique Title X", "AB": ""}]
        stats_bad = enrich_records(records_bad, client=client_bad, fields=["AB"])
        assert records_bad[0]["AB"] == ""
        assert stats_bad.enriched == 0 and stats_bad.failed == 1

    def test_client_failure_is_noop(self):
        client = FakeSciverseClient(fail=True)
        records = [{"TI": "T", "AB": "", "DI": "10.1/x"}]
        stats = enrich_records(records, client=client, fields=["AB"])
        assert records[0]["AB"] == ""
        assert stats.failed == 1

    def test_limit_caps_queries(self):
        client = FakeSciverseClient(by_doi={"10.1/a": PAPER})
        records = [{"TI": f"T{i}", "AB": "", "DI": f"10.1/a"} for i in range(5)]
        stats = enrich_records(records, client=client, fields=["AB"], limit=2)
        assert client.calls == 2
        assert stats.candidates == 2


class TestMaybeEnrichGate:
    def test_disabled_by_default(self, tmp_path, monkeypatch):
        from lit_scout_harvester.core.config_loader import load_caliber

        monkeypatch.setattr(load_caliber, "__defaults__", (None,))
        caliber = load_caliber()
        assert caliber.sciverse.enabled is False

        client = FakeSciverseClient()
        monkeypatch.setattr(
            "lit_scout_harvester.parsers.sciverse.SciverseClient", lambda *a, **k: client
        )
        records = [{"TI": "T", "AB": ""}]
        maybe_enrich_records(records)
        assert client.calls == 0, "默认关：零请求"

    def test_enabled_requires_key(self, tmp_path, monkeypatch):
        from lit_scout_harvester.core import config_loader

        monkeypatch.setattr(
            config_loader, "load_caliber",
            lambda *a, **k: config_loader.CaliberConfig(sciverse={"enabled": True}),
        )
        client = FakeSciverseClient(configured=False)
        monkeypatch.setattr(
            "lit_scout_harvester.parsers.sciverse.SciverseClient", lambda *a, **k: client
        )
        records = [{"TI": "T", "AB": ""}]
        maybe_enrich_records(records)
        assert records[0]["AB"] == ""

    def test_enabled_enriches(self, tmp_path, monkeypatch):
        from lit_scout_harvester.core import config_loader

        monkeypatch.setattr(
            config_loader, "load_caliber",
            lambda *a, **k: config_loader.CaliberConfig(sciverse={"enabled": True}),
        )
        client = FakeSciverseClient(by_doi={"10.1/abc": PAPER})
        monkeypatch.setattr(
            "lit_scout_harvester.parsers.sciverse.SciverseClient", lambda *a, **k: client
        )
        records = [{"TI": "Transformer Attention Survey", "AB": "", "DI": "10.1/abc"}]
        maybe_enrich_records(records)
        assert records[0]["AB"] == "We survey attention mechanisms."
