"""Tests for lit_scout_harvester.parsers.dianshi — 数据源 + 题录补全，全程 fake client。"""

from __future__ import annotations

import json

from lit_scout_harvester.parsers.data_source import detect_datasource
from lit_scout_harvester.parsers.dianshi import (
    JOB_PLATFORM,
    EnrichStats,
    enrich_records,
    load_dianshi_records,
    maybe_enrich_records,
    record_from_reference,
)


class FakeDianshiClient:
    """按 query/reference_id 返回脚本化命中的最小 client（duck-type DianshiClient）。"""

    def __init__(self, by_title: dict | None = None, by_id: dict | None = None,
                 fail: bool = False, configured: bool = True):
        self.by_title = by_title or {}
        self.by_id = by_id or {}
        self.fail = fail
        self.configured = configured
        self.search_calls = 0

    def reference_search(self, query, limit=10):
        self.search_calls += 1
        if self.fail:
            return None
        hits = self.by_title.get(query, [])
        return {"references": hits[:limit], "total_count": len(hits)}

    def reference_get_by_id(self, reference_id, use_cache=True):
        if self.fail:
            return None
        return self.by_id.get(reference_id)


REFERENCE = {
    "reference_id": 42,
    "title": "Catalytic Transformations Survey",
    "abstract": "We survey catalytic transformations.",
    "authors": [{"name": "Alice A"}, {"name": "Bob B"}],
    "venue": "JACS",
    "year": 2026,
    "doi": "10.1/abc",
}


class TestRecordMapping:
    def test_wos_field_codes(self):
        rec = record_from_reference(REFERENCE)
        assert rec["TI"] == "Catalytic Transformations Survey"
        assert rec["AB"].startswith("We survey")
        assert rec["AU"] == ["Alice A", "Bob B"]
        assert rec["SO"] == "JACS"
        assert rec["PY"] == 2026
        assert rec["DI"] == "10.1/abc"

    def test_partial_hit(self):
        rec = record_from_reference({"title": "Only Title"})
        assert rec == {"TI": "Only Title"}

    def test_candidate_key_fallback(self):
        rec = record_from_reference({"reference_title": "T", "summary": "S", "pub_year": 2020})
        assert rec == {"TI": "T", "AB": "S", "PY": 2020}

    def test_string_authors_list(self):
        rec = record_from_reference({"title": "T", "authors": ["Alice A", "Bob B"]})
        assert rec["AU"] == ["Alice A", "Bob B"]


class TestDatasourceModes:
    def test_detect_dianshi_job(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"platform": "dianshi", "mode": "search", "query": "catalysis"}))
        assert detect_datasource(str(job)) == "dianshi"
        job.write_text(json.dumps({"platform": "dianshi", "mode": "id_list", "ids": [1, 2]}))
        assert detect_datasource(str(job)) == "dianshi"

    def test_sciverse_job_not_claimed(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"mode": "search", "query": "attention"}))
        assert detect_datasource(str(job)) == "sciverse"

    def test_search_mode(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({
            "platform": JOB_PLATFORM, "mode": "search", "query": "catalysis", "max_records": 10,
        }))
        client = FakeDianshiClient(by_title={"catalysis": [REFERENCE]})
        records = load_dianshi_records(str(job), client=client)
        assert len(records) == 1
        assert records[0]["DI"] == "10.1/abc"

    def test_search_mode_fail_soft(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"platform": JOB_PLATFORM, "mode": "search", "query": "x"}))
        client = FakeDianshiClient(fail=True)
        assert load_dianshi_records(str(job), client=client) == []

    def test_search_mode_unexpected_envelope(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"platform": JOB_PLATFORM, "mode": "search", "query": "x"}))

        class OddClient(FakeDianshiClient):
            def reference_search(self, query, limit=10):
                return {"data": {"hits": []}}  # 无任何候选列表键

        assert load_dianshi_records(str(job), client=OddClient()) == []

    def test_id_list_mode(self, tmp_path):
        job = tmp_path / "job.json"
        job.write_text(json.dumps({"platform": JOB_PLATFORM, "mode": "id_list", "ids": [42, 99]}))
        client = FakeDianshiClient(by_id={42: REFERENCE})
        records = load_dianshi_records(str(job), client=client)
        assert len(records) == 1
        assert records[0]["TI"] == "Catalytic Transformations Survey"


class TestEnrichRecords:
    def _records(self):
        return [
            {"TI": "Catalytic Transformations Survey", "AB": "", "DI": "10.1/abc", "SO": "", "PY": ""},
            {"TI": "Local Only Paper", "AB": "has abstract", "DI": ""},
            {"TI": "Catalytic Transformations Survey", "AB": "existing", "DI": "10.1/abc"},
        ]

    def test_fills_only_missing_never_overwrites(self):
        by_title = {"Catalytic Transformations Survey": [REFERENCE]}
        client = FakeDianshiClient(by_title=by_title)
        records = self._records()
        stats = enrich_records(records, client=client, fields=["AB", "DI", "SO", "PY"])
        r0, _r1, r2 = records
        assert r0["AB"] == "We survey catalytic transformations."
        assert r0["SO"] == "JACS" and r0["PY"] == 2026
        assert r2["AB"] == "existing"  # 已有 → 不覆盖
        assert stats.fields_filled.get("AB", 0) == 1

    def test_title_guard_requires_exact_match(self):
        by_title = {"Catalytic Transformations Survey": [REFERENCE]}
        client = FakeDianshiClient(by_title=by_title)
        records = [{"TI": "Catalytic Transformations Survey", "AB": ""}]
        enrich_records(records, client=client, fields=["AB"])
        assert records[0]["AB"] == "We survey catalytic transformations."

        bad = {"Unique Title X": [REFERENCE]}  # 题名不同 → 归一化不等 → 拒补
        client_bad = FakeDianshiClient(by_title=bad)
        records_bad = [{"TI": "Unique Title X", "AB": ""}]
        stats_bad = enrich_records(records_bad, client=client_bad, fields=["AB"])
        assert records_bad[0]["AB"] == ""
        assert stats_bad.enriched == 0 and stats_bad.failed == 1

    def test_client_failure_is_noop(self):
        client = FakeDianshiClient(fail=True)
        records = [{"TI": "T", "AB": ""}]
        stats = enrich_records(records, client=client, fields=["AB"])
        assert records[0]["AB"] == ""
        assert stats.failed == 1

    def test_limit_caps_queries(self):
        by_title = {"T": [REFERENCE]}
        client = FakeDianshiClient(by_title=by_title)
        records = [{"TI": f"T{i}", "AB": ""} for i in range(5)]
        stats = enrich_records(records, client=client, fields=["AB"], limit=2)
        assert client.search_calls == 2
        assert stats.candidates == 2


class TestMaybeEnrichGate:
    def test_disabled_by_default(self, monkeypatch):
        from lit_scout_harvester.core.config_loader import load_caliber

        monkeypatch.setattr(load_caliber, "__defaults__", (None,))
        caliber = load_caliber()
        assert caliber.dianshi.enabled is False

        client = FakeDianshiClient()
        monkeypatch.setattr(
            "lit_scout_harvester.parsers.dianshi.DianshiClient", lambda *a, **k: client
        )
        records = [{"TI": "T", "AB": ""}]
        maybe_enrich_records(records)
        assert client.search_calls == 0, "默认关：零请求"

    def test_enabled_requires_token(self, monkeypatch):
        from lit_scout_harvester.core import config_loader

        monkeypatch.setattr(
            config_loader, "load_caliber",
            lambda *a, **k: config_loader.CaliberConfig(dianshi={"enabled": True}),
        )
        client = FakeDianshiClient(configured=False)
        monkeypatch.setattr(
            "lit_scout_harvester.parsers.dianshi.DianshiClient", lambda *a, **k: client
        )
        records = [{"TI": "T", "AB": ""}]
        maybe_enrich_records(records)
        assert records[0]["AB"] == ""
        assert client.search_calls == 0

    def test_enabled_enriches(self, monkeypatch):
        from lit_scout_harvester.core import config_loader

        monkeypatch.setattr(
            config_loader, "load_caliber",
            lambda *a, **k: config_loader.CaliberConfig(dianshi={"enabled": True}),
        )
        client = FakeDianshiClient(by_title={"Catalytic Transformations Survey": [REFERENCE]})
        monkeypatch.setattr(
            "lit_scout_harvester.parsers.dianshi.DianshiClient", lambda *a, **k: client
        )
        records = [{"TI": "Catalytic Transformations Survey", "AB": ""}]
        maybe_enrich_records(records)
        assert records[0]["AB"] == "We survey catalytic transformations."
