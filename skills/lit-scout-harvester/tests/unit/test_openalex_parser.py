"""Tests for lit_scout_harvester.parsers.openalex — all offline (fake fetcher)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentkit.quota import CursorStore, QuotaTracker
from lit_scout_harvester.core.config_loader import PipelineConfig
from lit_scout_harvester.parsers.data_source import detect_datasource, load_records
from lit_scout_harvester.parsers.openalex import (
    BASE_URL,
    DEFAULT_MAILTO,
    DEFAULT_PER_PAGE,
    ENV_MAILTO,
    STATE_CURSOR_FILE,
    STATE_DONE_FILE,
    STATE_STAGING_FILE,
    FetchedPayload,
    OpenAlexFetchError,
    SearchDisabledError,
    fetch_work_by_doi,
    fetch_work_by_id,
    iter_filter_works,
    load_openalex_records,
    parse_openalex_payload,
    reconstruct_abstract,
    resolve_mailto,
    search_works,
    work_to_record,
)
from lit_scout_harvester.parsers.wos_parser import parse_wos_plaintext_to_list


def make_work(n: int, title: str | None = None, doi: str | None = None) -> dict:
    """Build a minimal but realistic OpenAlex work JSON."""
    return {
        "id": f"https://openalex.org/W{n:012d}",
        "doi": doi or f"https://doi.org/10.1000/test{n}",
        "display_name": title or f"Study number {n}",
        "publication_year": 2020 + (n % 5),
        "type": "article",
        "language": "en",
        "cited_by_count": n,
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
        "authorships": [
            {
                "author": {"display_name": "Smith, John"},
                "raw_affiliation_string": "Example University, Springfield",
            },
            {
                "author": {"display_name": "Doe, Jane"},
                "raw_affiliation_string": "Example University, Springfield",
            },
        ],
        "primary_location": {"source": {"display_name": "Journal of Examples"}},
        "abstract_inverted_index": {
            "Study": [0],
            "number": [1],
            f"{n}": [2],
            "abstract.": [3],
        },
        "keywords": [{"display_name": "example keyword"}],
        "topics": [{"display_name": "Example Topic"}],
    }


class FakeFetcher:
    """Scripted fetcher: handler(url) -> FetchedPayload | Exception; records URLs."""

    def __init__(self, handler):
        self.handler = handler
        self.urls: list[str] = []

    def __call__(self, url: str) -> FetchedPayload:
        self.urls.append(url)
        result = self.handler(url)
        if isinstance(result, Exception):
            raise result
        return result


RATE_HEADERS = {"X-RateLimit-Remaining": "900", "X-RateLimit-Limit": "1000"}


def filter_page(works: list, next_cursor: str | None) -> FetchedPayload:
    return FetchedPayload(
        data={"results": works, "meta": {"next_cursor": next_cursor}},
        headers=dict(RATE_HEADERS),
    )


# ---------------------------------------------------------------------------
# abstract reconstruction & record mapping
# ---------------------------------------------------------------------------

class TestReconstructAbstract:
    def test_round_trip(self):
        inverted = {"the": [0, 1], "quick": [2], "brown": [3], "fox": [4]}
        assert reconstruct_abstract(inverted) == "the the quick brown fox"

    def test_empty(self):
        assert reconstruct_abstract(None) == ""
        assert reconstruct_abstract({}) == ""


class TestWorkToRecord:
    def test_maps_core_fields(self):
        record = work_to_record(make_work(7))
        assert record["TI"] == "Study number 7"
        assert record["AB"] == "Study number 7 abstract."
        assert record["AU"] == ["Smith, John", "Doe, Jane"]
        assert record["AF"] == ["Smith, John", "Doe, Jane"]
        assert record["SO"] == "Journal of Examples"
        assert record["PY"] == 2022  # 2020 + 7 % 5
        assert record["DI"] == "10.1000/test7"  # https://doi.org/ prefix stripped
        assert record["LA"] == "English"
        assert record["UT"] == "W000000000007"
        assert record["PT"] == "J"
        assert record["DT"] == "Article"
        assert record["TC"] == 7
        assert record["NR"] == 2
        assert "Example University, Springfield" in record["C1"]

    def test_wos_isomorphism(self):
        """OpenAlex records expose the same field codes as wos_parser output."""
        wos_text = (
            "FN Web of Science\nVR 1.0\nPT J\nAU Smith, JA\nTI A study of something\n"
            "SO Journal of Examples\nAB The abstract here.\nPY 2023\n"
            "DI 10.1000/xyz\nLA English\nUT WOS:0001\nER\nEF\n"
        )
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".wos", delete=False, encoding="utf-8") as fh:
            fh.write(wos_text)
            wos_path = fh.name
        wos_records = parse_wos_plaintext_to_list(wos_path)
        oa_record = work_to_record(make_work(1))

        core_keys = {"TI", "AB", "AU", "SO", "PY", "DI", "LA", "UT"}
        assert core_keys <= set(wos_records[0]) | {"PT"}  # sanity on the fixture
        assert core_keys <= set(oa_record)
        for key in ("TI", "AB", "SO", "DI", "LA", "UT"):  # text fields match type
            assert isinstance(oa_record[key], str), key
        assert isinstance(oa_record["AU"], list)
        assert isinstance(wos_records[0]["AU"], list)


# ---------------------------------------------------------------------------
# single-record endpoints (free)
# ---------------------------------------------------------------------------

class TestSingleRecordEndpoints:
    def test_fetch_work_by_doi_is_free_and_uses_single_endpoint(self):
        work = make_work(1)
        fake = FakeFetcher(lambda url: FetchedPayload(work, dict(RATE_HEADERS)))
        tracker = QuotaTracker()
        result = fetch_work_by_doi("10.1000/test1", fetcher=fake, tracker=tracker)
        assert result == work
        assert fake.urls[0].startswith(f"{BASE_URL}/works/doi:")
        assert "10.1000%2Ftest1" in fake.urls[0]
        assert tracker.credits_used == 0  # single-record endpoints are free
        assert len(tracker.ledger) == 1

    def test_fetch_work_by_doi_strips_url_prefix(self):
        work = make_work(2)
        fake = FakeFetcher(lambda url: FetchedPayload(work, {}))
        result = fetch_work_by_doi("https://doi.org/10.1000/test2", fetcher=fake)
        assert result == work
        assert "doi:10.1000%2Ftest2" in fake.urls[0]

    def test_fetch_work_by_doi_404_returns_none(self):
        def handler(url):
            raise OpenAlexFetchError(404, url)

        fake = FakeFetcher(handler)
        assert fetch_work_by_doi("10.1000/ghost", fetcher=fake) is None

    def test_fetch_work_by_id_uses_single_endpoint(self):
        work = make_work(3)
        fake = FakeFetcher(lambda url: FetchedPayload(work, {}))
        tracker = QuotaTracker()
        result = fetch_work_by_id("https://openalex.org/W000000000003", fetcher=fake, tracker=tracker)
        assert result == work
        assert f"{BASE_URL}/works/W000000000003" in fake.urls[0]
        assert tracker.credits_used == 0


# ---------------------------------------------------------------------------
# list endpoints & credits
# ---------------------------------------------------------------------------

def two_page_handler(pages: dict, fail_cursors: dict | None = None, served: list | None = None):
    """handler factory: pages maps cursor -> (works, next_cursor).

    ``fail_cursors`` maps cursor -> remaining failure count (each request for
    that cursor raises once per count). Successfully served URLs are appended
    to ``served`` when given."""

    fail_cursors = fail_cursors or {}

    def handler(url: str):
        for cursor, (works, next_cursor) in pages.items():
            encoded = cursor.replace("*", "%2A")
            if f"cursor={encoded}" in url:
                if cursor in fail_cursors and fail_cursors[cursor] > 0:
                    fail_cursors[cursor] -= 1
                    raise RuntimeError(f"interrupted while fetching cursor={cursor}")
                if served is not None:
                    served.append(url)
                return filter_page(works, next_cursor)
        raise AssertionError(f"unexpected URL: {url}")

    return handler


class TestFilterPagination:
    def test_two_pages_stitched(self):
        w1, w2, w3 = make_work(1), make_work(2), make_work(3)
        fake = FakeFetcher(
            two_page_handler({"*": ([w1, w2], "C2"), "C2": ([w3], None)})
        )
        tracker = QuotaTracker()
        works = list(iter_filter_works("title.search:study", fetcher=fake, tracker=tracker))
        assert works == [w1, w2, w3]
        assert tracker.credits_used == 2  # 1 credit per filter page
        assert len(fake.urls) == 2
        assert f"per_page={DEFAULT_PER_PAGE}" in fake.urls[0]
        assert "filter=title.search%3Astudy" in fake.urls[0] or "filter=title.search:study" in fake.urls[0]

    def test_overlapping_pages_deduped(self):
        w1, w2, w3 = make_work(1), make_work(2), make_work(3)
        fake = FakeFetcher(
            two_page_handler({"*": ([w1, w2], "C2"), "C2": ([w1, w3], None)})
        )
        works = list(iter_filter_works("title.search:study", fetcher=fake))
        assert works == [w1, w2, w3]  # w1 replayed on page 2 is skipped

    def test_quota_stop_halts_before_next_page(self):
        tracker = QuotaTracker(stop_ratio=0.05)
        tracker.record(credits_used=0, headers={"X-RateLimit-Remaining": "40", "X-RateLimit-Limit": "1000"})
        fake = FakeFetcher(lambda url: pytest.fail(f"should not fetch: {url}"))
        works = list(iter_filter_works("title.search:study", fetcher=fake, tracker=tracker))
        assert works == []


class TestSearchEndpoint:
    def test_search_requires_explicit_opt_in(self):
        fake = FakeFetcher(lambda url: pytest.fail("must not fetch without opt-in"))
        with pytest.raises(SearchDisabledError):
            list(search_works("study", fetcher=fake))
        assert fake.urls == []

    def test_search_costs_ten_credits_per_page(self):
        w1 = make_work(1)
        fake = FakeFetcher(lambda url: filter_page([w1], None))
        tracker = QuotaTracker()
        works = list(search_works("study", allow_search=True, fetcher=fake, tracker=tracker))
        assert works == [w1]
        assert tracker.credits_used == 10
        assert "search=study" in fake.urls[0]


# ---------------------------------------------------------------------------
# mailto resolution
# ---------------------------------------------------------------------------

class TestMailto:
    def test_default_placeholder_is_not_a_real_email(self):
        fake = FakeFetcher(lambda url: FetchedPayload(make_work(1), {}))
        fetch_work_by_doi("10.1000/test1", fetcher=fake, mailto=None)
        encoded_mailto = DEFAULT_MAILTO.replace("@", "%40")
        assert encoded_mailto in fake.urls[0]
        assert DEFAULT_MAILTO.endswith(".invalid")

    def test_env_var_wins_over_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MAILTO, "researcher@example.org")
        assert resolve_mailto() == "researcher@example.org"
        assert resolve_mailto("explicit@example.org") == "explicit@example.org"


# ---------------------------------------------------------------------------
# local payload parsing
# ---------------------------------------------------------------------------

class TestLocalPayload:
    def test_list_response_envelope(self, tmp_path):
        payload = {"results": [make_work(1), make_work(2)], "meta": {"next_cursor": None}}
        path = tmp_path / "works.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        records = load_openalex_records(str(path))
        assert len(records) == 2
        assert records[0]["TI"] == "Study number 1"

    def test_plain_work_list(self):
        records = parse_openalex_payload([make_work(1), make_work(2)])
        assert [r["UT"] for r in records] == ["W000000000001", "W000000000002"]

    def test_single_work(self):
        records = parse_openalex_payload(make_work(5))
        assert len(records) == 1

    def test_detect_datasource_openalex_json(self, tmp_path):
        path = tmp_path / "export.json"
        path.write_text(json.dumps({"results": [make_work(1)]}), encoding="utf-8")
        assert detect_datasource(str(path)) == "openalex"


# ---------------------------------------------------------------------------
# online job files + resume
# ---------------------------------------------------------------------------

def write_job(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "job.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestDoiListJob:
    def test_doi_list_fetches_free_single_endpoints(self, tmp_path):
        dois = ["10.1000/a", "10.1000/b", "10.1000/c"]
        works = {doi: make_work(i + 1, doi=f"https://doi.org/{doi}") for i, doi in enumerate(dois)}

        def handler(url: str):
            for doi, work in works.items():
                if f"doi:{doi.replace('/', '%2F')}" in url:
                    return FetchedPayload(work, dict(RATE_HEADERS))
            raise AssertionError(f"unexpected URL: {url}")

        fake = FakeFetcher(handler)
        job = write_job(tmp_path, {"mode": "doi_list", "dois": dois})
        records = load_openalex_records(str(job), fetcher=fake)
        assert len(records) == 3
        assert all("/works/doi:" in url for url in fake.urls)

    def test_unresolvable_doi_is_skipped_and_marked(self, tmp_path):
        dois = ["10.1000/ok", "10.1000/ghost"]

        def handler(url: str):
            if "ghost" in url:
                raise OpenAlexFetchError(404, url)
            return FetchedPayload(make_work(1, doi="https://doi.org/10.1000/ok"), {})

        job = write_job(tmp_path, {"mode": "doi_list", "dois": dois})
        state_dir = tmp_path / "quota_state"
        records = load_openalex_records(str(job), fetcher=FakeFetcher(handler), state_dir=state_dir)
        assert len(records) == 1
        store = CursorStore(state_dir / STATE_CURSOR_FILE)
        assert set(dois) <= store.load().ids  # misses are marked too

    def test_resume_does_not_re_request_received_dois(self, tmp_path):
        dois = ["10.1000/a", "10.1000/b"]
        job = write_job(tmp_path, {"mode": "doi_list", "dois": dois})
        state_dir = tmp_path / "quota_state"

        def handler(url: str):
            for i, doi in enumerate(dois):
                if f"doi:{doi.replace('/', '%2F')}" in url:
                    return FetchedPayload(make_work(i + 1, doi=f"https://doi.org/{doi}"), {})
            raise AssertionError(f"unexpected URL: {url}")

        run1 = FakeFetcher(handler)
        records1 = load_openalex_records(str(job), fetcher=run1, state_dir=state_dir)
        assert len(records1) == 2
        run2 = FakeFetcher(handler)
        records2 = load_openalex_records(str(job), fetcher=run2, state_dir=state_dir)
        assert run2.urls == []  # everything already received
        assert len(records2) == 2  # replayed from staging


class TestFilterJobResume:
    def _job(self, tmp_path):
        return write_job(tmp_path, {"mode": "filter", "filter": "title.search:study", "per_page": 2})

    def test_interrupt_then_resume_no_duplicate_requests(self, tmp_path):
        w1, w2, w3 = make_work(1), make_work(2), make_work(3)
        job = self._job(tmp_path)
        state_dir = tmp_path / "quota_state"
        served: list[str] = []
        handler = two_page_handler(
            {"*": ([w1, w2], "C2"), "C2": ([w3], None)},
            fail_cursors={"C2": 1},  # die once while fetching page 2
            served=served,
        )

        run1 = FakeFetcher(handler)
        with pytest.raises(RuntimeError):
            load_openalex_records(str(job), fetcher=run1, state_dir=state_dir)
        # State landed on disk: cursor points at page 2, page-1 IDs recorded.
        store = CursorStore(state_dir / STATE_CURSOR_FILE)
        assert store.load().cursor == "C2"
        assert store.load().ids == {w1["id"], w2["id"]}
        staged = (state_dir / STATE_STAGING_FILE).read_text(encoding="utf-8").strip().splitlines()
        assert len(staged) == 2

        run2 = FakeFetcher(handler)
        records = load_openalex_records(str(job), fetcher=run2, state_dir=state_dir)
        assert len(records) == 3
        # Page 1 was fully received in run 1 and must not be re-requested;
        # page 2 never arrived (the request died), so retrying it is required.
        assert len(served) == 2
        assert served[0] in run1.urls and served[1] in run2.urls
        assert not set(run2.urls) & set(served[:1]), "resume re-requested a received page"
        assert (state_dir / STATE_DONE_FILE).exists()

        run3 = FakeFetcher(lambda url: pytest.fail(f"completed job must not refetch: {url}"))
        records3 = load_openalex_records(str(job), fetcher=run3, state_dir=state_dir)
        assert run3.urls == []
        assert len(records3) == 3  # replayed from staging

    def test_resume_dedups_replayed_page(self, tmp_path):
        w1, w2, w3 = make_work(1), make_work(2), make_work(3)
        job = self._job(tmp_path)
        state_dir = tmp_path / "quota_state"
        handler = two_page_handler(
            {"*": ([w1, w2], "C2"), "C2": ([w1, w3], None)},  # page 2 replays w1
            fail_cursors={"C2": 1},
        )
        with pytest.raises(RuntimeError):
            load_openalex_records(str(job), fetcher=FakeFetcher(handler), state_dir=state_dir)
        records = load_openalex_records(str(job), fetcher=FakeFetcher(handler), state_dir=state_dir)
        assert len(records) == 3  # w1 not duplicated from the replayed page

    def test_quota_stop_is_pause_not_done_and_resumes(self, tmp_path):
        w1, w2, w3 = make_work(1), make_work(2), make_work(3)
        job = self._job(tmp_path)
        state_dir = tmp_path / "quota_state"
        pages = {"*": ([w1, w2], "C2"), "C2": ([w3], None)}
        low_headers = {"X-RateLimit-Remaining": "40", "X-RateLimit-Limit": "1000"}

        def handler(url: str):
            for cursor, (works, next_cursor) in pages.items():
                if f"cursor={cursor.replace('*', '%2A')}" in url:
                    return FetchedPayload(
                        {"results": works, "meta": {"next_cursor": next_cursor}}, dict(low_headers)
                    )
            raise AssertionError(f"unexpected URL: {url}")

        # Run 1: the window trips the stop threshold after page 1.
        tracker = QuotaTracker(stop_ratio=0.05)
        records1 = load_openalex_records(
            str(job), fetcher=FakeFetcher(handler), state_dir=state_dir, tracker=tracker
        )
        assert len(records1) == 2
        assert not (state_dir / STATE_DONE_FILE).exists(), "quota stop must not mark the job done"
        assert CursorStore(state_dir / STATE_CURSOR_FILE).load().cursor == "C2"

        # Run 2 (fresh window): resumes at page 2 and finishes.
        records2 = load_openalex_records(
            str(job),
            fetcher=FakeFetcher(handler),
            state_dir=state_dir,
            tracker=QuotaTracker(stop_ratio=0.05),
        )
        assert len(records2) == 3
        assert (state_dir / STATE_DONE_FILE).exists()


# ---------------------------------------------------------------------------
# data_source dispatch
# ---------------------------------------------------------------------------

def _config(datasource="openalex"):
    return PipelineConfig(
        llm={"api_key": "sk-test"},
        skeleton={"datasource": datasource, "base_data_source": "native_parser"},
        modules={},
    )


class TestDataSourceDispatch:
    def test_load_records_openalex_local_json(self, tmp_path):
        path = tmp_path / "works.json"
        path.write_text(json.dumps({"results": [make_work(1), make_work(2)]}), encoding="utf-8")
        records = load_records(_config(), str(path))
        assert len(records) == 2
        assert records[0]["TI"] == "Study number 1"

    def test_load_records_openalex_online_with_fetcher_and_state(self, tmp_path):
        job = write_job(tmp_path, {"mode": "filter", "filter": "title.search:study", "per_page": 2})
        w1, w2 = make_work(1), make_work(2)
        fake = FakeFetcher(two_page_handler({"*": ([w1, w2], None)}))
        state_dir = tmp_path / "quota_state"
        records = load_records(_config(), str(job), fetcher=fake, quota_state=str(state_dir))
        assert len(records) == 2
        assert (state_dir / STATE_CURSOR_FILE).exists()

    def test_load_records_auto_detect_openalex(self, tmp_path):
        path = tmp_path / "works.json"
        path.write_text(json.dumps([make_work(1)]), encoding="utf-8")
        records = load_records(_config("auto_detect"), str(path))
        assert len(records) == 1

    def test_search_job_requires_opt_in_through_loader(self, tmp_path):
        job = write_job(tmp_path, {"mode": "search", "search": "study"})
        with pytest.raises(SearchDisabledError):
            load_records(_config(), str(job), fetcher=FakeFetcher(lambda url: pytest.fail("no fetch")))
