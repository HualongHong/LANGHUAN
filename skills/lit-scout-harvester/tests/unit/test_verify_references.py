"""Tests for scripts/verify_references.py — all offline behind a fake fetcher."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from lit_scout_harvester.parsers.openalex import OpenAlexFetchError, FetchedPayload
from scripts import verify_references as vr

N_TOTAL = 40
UNRESOLVED = {"10.1000/ref5", "10.1000/ref17"}
MISMATCHED = "10.1000/ref23"


def make_work(n: int, doi: str, title: str | None = None) -> dict:
    return {
        "id": f"https://openalex.org/W{n:012d}",
        "doi": f"https://doi.org/{doi}",
        "display_name": title or f"Ref {n} study: findings on example topic",
        "publication_year": 2021,
        "type": "article",
        "authorships": [
            {"author": {"display_name": "Jane Doe"}, "raw_affiliation_string": "Example Univ"}
        ],
        "primary_location": {"source": {"display_name": "Journal of Examples"}},
    }


class FakeFetcher:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url: str) -> FetchedPayload:
        self.urls.append(url)
        if "works/doi:" not in url:
            raise AssertionError(f"verify must use the single-record endpoint: {url}")
        for doi in UNRESOLVED:
            if doi.replace("/", "%2F") in url:
                raise OpenAlexFetchError(404, url)
        if MISMATCHED.replace("/", "%2F") in url:
            return FetchedPayload(
                make_work(99, MISMATCHED, title="A totally unrelated crystallography paper"),
                {},
            )
        for n in range(N_TOTAL):
            doi = f"10.1000/ref{n}"
            if doi.replace("/", "%2F") in url:
                return FetchedPayload(make_work(n, doi), {})
        raise AssertionError(f"unexpected DOI in URL: {url}")


def make_ris(path: Path, n: int) -> Path:
    lines = []
    for i in range(n):
        doi = f"10.1000/ref{i}"
        title = (
            "A totally different input title about ocean circulation"
            if doi == MISMATCHED
            else f"Ref {i} study: findings on example topic"
        )
        lines += [
            "TY  - JOUR",
            f"AU  - Doe, Jane",
            f"TI  - {title}",
            "JO  - Journal of Examples",
            "PY  - 2021//",
            f"DO  - {doi}",
            "ER  - ",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestParsing:
    def test_parse_ris(self, tmp_path):
        path = make_ris(tmp_path / "refs.ris", 3)
        entries = vr.parse_ris(path)
        assert len(entries) == 3
        assert entries[0]["doi"] == "10.1000/ref0"
        assert entries[0]["title"].startswith("Ref 0 study")
        assert entries[0]["authors"] == ["Doe, Jane"]
        assert entries[0]["journal"] == "Journal of Examples"
        assert entries[0]["year"] == "2021"

    def test_parse_csv(self, tmp_path):
        path = tmp_path / "refs.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["DOI", "Title", "Authors", "Journal", "Year"])
            writer.writerow(["10.1000/ref0", "Ref 0 study: findings on example topic", "Doe, Jane", "Journal of Examples", "2021"])
            writer.writerow(["10.1000/ref1", "Ref 1 study: findings on example topic", "Doe, Jane", "Journal of Examples", "2021"])
        entries = vr.load_references(path)
        assert len(entries) == 2
        assert entries[1]["doi"] == "10.1000/ref1"
        assert entries[0]["authors"] == ["Doe, Jane"]

    def test_csv_without_doi_column_rejected(self, tmp_path):
        path = tmp_path / "refs.csv"
        path.write_text("Title,Year\nFoo,2020\n", encoding="utf-8")
        with pytest.raises(ValueError):
            vr.load_references(path)


class TestMatching:
    def test_title_lenient(self):
        assert vr.titles_match("Ref 1 study: findings", "Ref 1 study: findings on example topic")
        assert vr.titles_match("FINDINGS ON EXAMPLE TOPIC", "findings on example topic")
        assert not vr.titles_match("Ocean circulation paper", "Crystallography today")

    def test_author_family(self):
        assert vr.author_family("Doe, Jane") == "doe"
        assert vr.author_family("Jane Doe") == "doe"


class TestVerifyForty:
    def test_forty_references_two_unresolved_one_mismatch(self, tmp_path):
        ris = make_ris(tmp_path / "refs.ris", N_TOTAL)
        entries = vr.load_references(ris)
        fake = FakeFetcher()
        rows, summary = vr.verify_references(entries, fetcher=fake)

        assert summary["total"] == 40
        assert summary["status_counts"].get("doi_unresolved", 0) == 2
        assert summary["status_counts"].get("title_mismatch", 0) == 1
        assert summary["ok"] == 37
        assert summary["problems"] == 3

        by_doi = {r["doi"]: r for r in rows}
        assert by_doi["10.1000/ref5"]["status"] == "doi_unresolved"
        assert by_doi["10.1000/ref17"]["status"] == "doi_unresolved"
        assert by_doi[MISMATCHED]["status"] == "title_mismatch"
        assert by_doi[MISMATCHED]["openalex_title"] == "A totally unrelated crystallography paper"
        assert by_doi["10.1000/ref0"]["status"] == "ok"
        # every check went through the free single-record endpoint, once per DOI
        assert len(fake.urls) == 40
        assert len(fake.urls) == len(set(fake.urls))

    def test_report_files_and_citation(self, tmp_path):
        ris = make_ris(tmp_path / "refs.ris", N_TOTAL)
        entries = vr.load_references(ris)
        rows, summary = vr.verify_references(entries, fetcher=FakeFetcher())
        out = tmp_path / "report"
        paths = vr.write_report(rows, summary, out)

        with open(paths["csv"], encoding="utf-8-sig") as fh:
            reader = list(csv.DictReader(fh))
        assert len(reader) == 40
        assert {r["status"] for r in reader} >= {"ok", "doi_unresolved", "title_mismatch"}

        saved = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
        assert saved["total"] == 40 and saved["ok"] == 37

        citation = Path(paths["citation"]).read_text(encoding="utf-8")
        assert "OpenAlex single-record API" in citation
        assert "40 DOIs checked" in citation

    def test_cli_end_to_end(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(vr.ENV_MAILTO, raising=False)
        # keep the CLI offline: its default fetcher would hit the network
        import lit_scout_harvester.parsers.openalex as openalex_module

        fake = FakeFetcher()
        monkeypatch.setattr(openalex_module, "_default_fetcher", lambda tracker=None: fake)
        ris = make_ris(tmp_path / "refs.ris", N_TOTAL)
        out = tmp_path / "report"
        code = vr.main(["--input", str(ris), "--output-dir", str(out)])
        assert code == 0
        summary = json.loads(capsys.readouterr().out)
        assert summary["total"] == 40
        assert summary["ok"] == 37
        assert len(fake.urls) == 40  # all checks went through the fake
        assert Path(summary["outputs"]["citation"]).exists()

    def test_cli_missing_input(self, capsys):
        assert vr.main(["--input", "nope.ris"]) == 2
