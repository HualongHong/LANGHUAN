"""OpenAlex online datasource parser (endpoint-disciplined, resumable).

Endpoint discipline (C2 — free endpoints first):

- **single-record** endpoints (``works/doi:{doi}`` / ``works/{id}``) are free
  and not subject to list rate limits: any *known* DOI/ID goes through them;
- **filter** lists cost 1 credit per request and paginate with ``cursor=*``;
- **search** costs 10 credits per request and is **disabled by default** —
  callers must opt in explicitly with ``allow_search=True``.

Quota handling (see :mod:`agentkit.quota`): every response feeds a
:class:`~agentkit.quota.QuotaTracker` (0/1/10 credits by endpoint class);
when ``X-RateLimit-Remaining`` drops below the stop threshold, pagination
stops before the daily window is exhausted. Multi-day jobs persist their
cursor through :class:`~agentkit.quota.CursorStore` under a state directory
(agent_run ``--resume`` uses ``<workdir>/quota_state/``) and replay a staging
file, so an interrupted run resumes at the exact page and never re-requests
already-received IDs.

Records are mapped to the same WOS-field-code dicts that ``wos_parser``
produces (TI/AB/AU/SO/PY/DI/...), so the rest of the pipeline cannot tell
the datasource apart. Abstracts are reconstructed from OpenAlex's
``abstract_inverted_index``. The ``mailto`` parameter comes from the
``AGENTKIT_OPENALEX_MAILTO`` environment variable and defaults to a
placeholder address — set it to a real email to enter the polite pool.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Union

from agentkit.quota import CursorState, CursorStore, QuotaTracker

logger = logging.getLogger(__name__)

BASE_URL = "https://api.openalex.org"
DEFAULT_PER_PAGE = 200

# Endpoint credit classes (OpenAlex window/daily quota).
COST_SINGLE = 0
COST_FILTER_PAGE = 1
COST_SEARCH_PAGE = 10

ENV_MAILTO = "AGENTKIT_OPENALEX_MAILTO"
DEFAULT_MAILTO = "langhuan-openalex-polite-pool@example.invalid"
"""Placeholder mailto (RFC 2606 .invalid); override with the env var."""

# Job-file modes for load_openalex_records().
JOB_MODE_FILTER = "filter"
JOB_MODE_DOI_LIST = "doi_list"
JOB_MODE_SEARCH = "search"

STATE_CURSOR_FILE = "openalex_cursor.json"
STATE_STAGING_FILE = "openalex_works.jsonl"
STATE_DONE_FILE = "openalex_done.json"

_LANGUAGE_NAMES = {
    "en": "English",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "ja": "Japanese",
    "ko": "Korean",
    "ru": "Russian",
    "pt": "Portuguese",
    "it": "Italian",
}


class OpenAlexError(Exception):
    """Base error for OpenAlex parsing/fetching."""


class SearchDisabledError(OpenAlexError):
    """Raised when the 10-credit search endpoint is used without opt-in."""


class OpenAlexFetchError(OpenAlexError):
    """A non-OK response from the OpenAlex API."""

    def __init__(self, status: int, url: str):
        super().__init__(f"OpenAlex request failed with HTTP {status}: {url}")
        self.status = status
        self.url = url


@dataclass(frozen=True)
class FetchedPayload:
    """One fetcher response: parsed JSON plus the raw response headers."""

    data: Any
    headers: Mapping[str, str] = field(default_factory=dict)


Fetcher = Callable[[str], FetchedPayload]
"""Injectable fetch backend: full URL in, :class:`FetchedPayload` out.

Tests inject a fake; production builds one on top of :func:`agentkit.fetch.fetch`
with its 429/403 long-backoff handling.
"""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def resolve_mailto(explicit: Optional[str] = None) -> str:
    """Resolve the OpenAlex ``mailto`` (polite pool) parameter.

    Priority: explicit argument > ``AGENTKIT_OPENALEX_MAILTO`` env var >
    :data:`DEFAULT_MAILTO` placeholder (never a real email).
    """
    value = (explicit or os.environ.get(ENV_MAILTO) or "").strip()
    return value or DEFAULT_MAILTO


def _mailto_query(mailto: Optional[str]) -> str:
    return f"&mailto={urllib.parse.quote(resolve_mailto(mailto), safe='')}"


def _default_fetcher(tracker: Optional[QuotaTracker]) -> Fetcher:
    """Build a production fetcher on :func:`agentkit.fetch.fetch`.

    Imported late so tests can monkeypatch ``agentkit.fetch.fetch`` and so
    the module stays importable without network dependencies.
    """

    def _fetcher(url: str) -> FetchedPayload:
        from agentkit.fetch import fetch

        result = fetch(url, accept="application/json", tracker=tracker)
        if result.status != 200:
            raise OpenAlexFetchError(result.status, url)
        try:
            data = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenAlexError(f"invalid JSON from {url}: {exc}") from exc
        return FetchedPayload(data=data, headers=result.headers)

    return _fetcher


def _get_json(url: str, fetcher: Fetcher, tracker: Optional[QuotaTracker], credits: int) -> Any:
    payload = fetcher(url)
    if tracker is not None:
        tracker.record(credits_used=credits, headers=payload.headers)
    return payload.data


# ---------------------------------------------------------------------------
# record mapping
# ---------------------------------------------------------------------------

def reconstruct_abstract(inverted_index: Optional[Mapping[str, Sequence[int]]]) -> str:
    """Rebuild the abstract text from OpenAlex's ``abstract_inverted_index``."""
    if not inverted_index:
        return ""
    positions: Dict[int, str] = {}
    max_pos = -1
    for word, indices in inverted_index.items():
        for idx in indices:
            if idx >= 0:
                positions[idx] = word
                max_pos = max(max_pos, idx)
    if max_pos < 0:
        return ""
    return " ".join(positions[i] for i in range(max_pos + 1) if i in positions)


def _short_openalex_id(work_id: Optional[str]) -> str:
    """``https://openalex.org/W2982345678`` -> ``W2982345678``."""
    if not work_id:
        return ""
    return str(work_id).rsplit("/", 1)[-1]


def _clean_doi(doi: Optional[str]) -> str:
    """``https://doi.org/10.1/abc`` / ``doi:10.1/abc`` -> ``10.1/abc``."""
    value = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
            break
    return value


def work_to_record(work: Mapping[str, Any]) -> Dict[str, Any]:
    """Map one OpenAlex work JSON to the internal WOS-field-code record.

    Produces the same key convention as :func:`wos_parser.parse_wos_plaintext`
    (TI/AB/AU/AF/SO/DT/DE/ID/C1/PY/DI/LA/UT/TC/NR), so downstream modules
    work unchanged.
    """
    authorships = work.get("authorships") or []
    authors = [a.get("author", {}).get("display_name", "") for a in authorships]
    authors = [a for a in authors if a]

    affiliations: List[str] = []
    for authorship in authorships:
        raw = (authorship.get("raw_affiliation_string") or "").strip()
        if raw and raw not in affiliations:
            affiliations.append(raw)

    source = (work.get("primary_location") or {}).get("source") or {}
    if not source:
        source = work.get("host_venue") or {}

    keywords = [k.get("display_name", "") for k in (work.get("keywords") or [])]
    keywords = [k for k in keywords if k]
    topics = [t.get("display_name", "") for t in (work.get("topics") or [])]
    topics = [t for t in topics if t]

    work_type = str(work.get("type") or "")
    language = str(work.get("language") or "")

    return {
        "PT": "B" if work_type in ("book", "book-chapter") else "J",
        "AU": authors,
        "AF": list(authors),
        "TI": work.get("display_name") or work.get("title") or "",
        "AB": reconstruct_abstract(work.get("abstract_inverted_index")),
        "SO": source.get("display_name") or "",
        "DT": work_type.replace("-", " ").title() or "Article",
        "DE": keywords or topics,
        "ID": topics,
        "C1": "; ".join(affiliations),
        "PY": work.get("publication_year"),
        "DI": _clean_doi(work.get("doi")),
        "LA": _LANGUAGE_NAMES.get(language.lower(), language.title() if language else ""),
        "UT": _short_openalex_id(work.get("id")),
        "TC": work.get("cited_by_count", 0),
        "NR": len(work.get("referenced_works") or []),
    }


def _extract_works(payload: Any) -> List[Mapping[str, Any]]:
    """Accept a single work, a list of works, or a list-response envelope."""
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            return [w for w in payload["results"] if isinstance(w, dict)]
        if payload.get("id") or payload.get("display_name"):
            return [payload]
    if isinstance(payload, list):
        return [w for w in payload if isinstance(w, dict)]
    return []


def parse_openalex_payload(payload: Any) -> List[Dict[str, Any]]:
    """Parse a local OpenAlex JSON payload into internal records (offline)."""
    return [work_to_record(w) for w in _extract_works(payload)]


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------

def fetch_work_by_doi(
    doi: str,
    *,
    fetcher: Optional[Fetcher] = None,
    tracker: Optional[QuotaTracker] = None,
    mailto: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one work by DOI through the **free** single-record endpoint.

    Returns ``None`` when the DOI does not resolve (HTTP 404). Costs 0
    credits and is not subject to list rate limits — always prefer this
    over list/search endpoints for known DOIs (C2).
    """
    fetcher = fetcher or _default_fetcher(tracker)
    url = f"{BASE_URL}/works/doi:{urllib.parse.quote(_clean_doi(doi), safe='')}{_mailto_query(mailto)}"
    try:
        data = _get_json(url, fetcher, tracker, COST_SINGLE)
    except OpenAlexFetchError as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return data


def fetch_work_by_id(
    openalex_id: str,
    *,
    fetcher: Optional[Fetcher] = None,
    tracker: Optional[QuotaTracker] = None,
    mailto: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch one work by OpenAlex ID through the **free** single-record endpoint."""
    fetcher = fetcher or _default_fetcher(tracker)
    short = _short_openalex_id(openalex_id)
    url = f"{BASE_URL}/works/{urllib.parse.quote(short, safe='')}{_mailto_query(mailto)}"
    try:
        data = _get_json(url, fetcher, tracker, COST_SINGLE)
    except OpenAlexFetchError as exc:
        if exc.status == 404:
            return None
        raise
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return data


def _check_stop(tracker: Optional[QuotaTracker]) -> bool:
    if tracker is not None and tracker.should_stop():
        logger.warning(
            "OpenAlex quota window nearly exhausted (remaining=%s); stopping pagination",
            tracker.remaining,
        )
        return True
    return False


def _iter_list_pages(
    params: str,
    fetcher: Fetcher,
    tracker: Optional[QuotaTracker],
    cursor_store: Optional[CursorStore],
    per_page: int,
    credits_per_page: int,
    mailto: Optional[str],
) -> Iterator[Dict[str, Any]]:
    """Cursor-paginate a list endpoint, deduping and persisting as pages flow."""
    state = cursor_store.load() if cursor_store is not None else CursorState()
    seen: Set[str] = set(state.ids)
    cursor = state.cursor or "*"
    while cursor:
        if _check_stop(tracker):
            return
        url = f"{BASE_URL}/works?{params}&per_page={per_page}&cursor={urllib.parse.quote(cursor, safe='')}{_mailto_query(mailto)}"
        data = _get_json(url, fetcher, tracker, credits_per_page)
        if not isinstance(data, dict):
            raise OpenAlexError(f"unexpected list response shape: {url}")
        works = [w for w in (data.get("results") or []) if isinstance(w, dict)]
        for work in works:
            wid = str(work.get("id") or work.get("doi") or "")
            if not wid or wid in seen:
                continue
            seen.add(wid)
            yield work
        next_cursor = (data.get("meta") or {}).get("next_cursor")
        if cursor_store is not None:
            cursor_store.save(cursor=next_cursor, ids=seen)
        if not works or not next_cursor:
            return
        cursor = next_cursor


def iter_filter_works(
    filter_query: str,
    *,
    fetcher: Optional[Fetcher] = None,
    tracker: Optional[QuotaTracker] = None,
    cursor_store: Optional[CursorStore] = None,
    per_page: int = DEFAULT_PER_PAGE,
    mailto: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Iterate a ``filter=`` list (1 credit per request, cursor pagination)."""
    fetcher = fetcher or _default_fetcher(tracker)
    params = f"filter={urllib.parse.quote(filter_query, safe=':|,.')}"
    yield from _iter_list_pages(
        params, fetcher, tracker, cursor_store, per_page, COST_FILTER_PAGE, mailto
    )


def search_works(
    query: str,
    *,
    allow_search: bool = False,
    fetcher: Optional[Fetcher] = None,
    tracker: Optional[QuotaTracker] = None,
    cursor_store: Optional[CursorStore] = None,
    per_page: int = DEFAULT_PER_PAGE,
    mailto: Optional[str] = None,
) -> Iterator[Dict[str, Any]]:
    """Iterate a full-text ``search`` (10 credits per request).

    The search endpoint burns the daily window 10x faster than filter lists,
    so it requires an explicit ``allow_search=True`` opt-in.
    """
    if not allow_search:
        raise SearchDisabledError(
            "OpenAlex search costs 10 credits/request; pass allow_search=True to opt in"
        )
    fetcher = fetcher or _default_fetcher(tracker)
    params = f"search={urllib.parse.quote(query, safe='')}"
    yield from _iter_list_pages(
        params, fetcher, tracker, cursor_store, per_page, COST_SEARCH_PAGE, mailto
    )


def _iter_doi_list(
    dois: Sequence[str],
    fetcher: Fetcher,
    tracker: Optional[QuotaTracker],
    cursor_store: Optional[CursorStore],
    mailto: Optional[str],
) -> Iterator[Dict[str, Any]]:
    """Fetch known DOIs one by one through the free single-record endpoint."""
    state = cursor_store.load() if cursor_store is not None else CursorState()
    seen: Set[str] = set(state.ids)
    for raw_doi in dois:
        doi = _clean_doi(str(raw_doi))
        if not doi:
            continue
        if doi in seen:
            continue  # already received before an interruption
        work = fetch_work_by_doi(doi, fetcher=fetcher, tracker=tracker, mailto=mailto)
        seen.add(doi)  # mark even on 404 so resume does not re-request misses
        if cursor_store is not None:
            cursor_store.save(cursor=None, ids=seen)
        if work is not None:
            yield work


# ---------------------------------------------------------------------------
# job-file loading (agent_run --resume)
# ---------------------------------------------------------------------------

def _looks_like_works_payload(payload: Any) -> bool:
    return bool(_extract_works(payload))


def _read_staging(staging_path: Path) -> List[Dict[str, Any]]:
    if not staging_path.exists():
        return []
    records = []
    for line in staging_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def load_openalex_records(
    input_path: Union[str, Path],
    *,
    fetcher: Optional[Fetcher] = None,
    state_dir: Optional[Union[str, Path]] = None,
    tracker: Optional[QuotaTracker] = None,
    mailto: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Load records from an OpenAlex input: local works JSON or an online job.

    The input file may be:

    - a local OpenAlex payload (single work / list of works / list-response
      envelope with ``results``) — parsed offline, no network;
    - an online *job file* (JSON object) with one of:

      - ``{"mode": "doi_list", "dois": [...]}`` — free single-record fetches;
      - ``{"mode": "filter", "filter": "..."}`` — 1-credit cursor list;
      - ``{"mode": "search", "search": "...", "allow_search": true}`` —
        10-credit list, requires explicit opt-in.

    When ``state_dir`` is given (agent_run ``--resume`` sets it to
    ``<workdir>/quota_state/``), received works are staged to
    ``openalex_works.jsonl``, the cursor lives in ``openalex_cursor.json``
    and ``openalex_done.json`` marks completion: an interrupted run replays
    the staging file, resumes at the saved cursor, and never re-requests
    already-received IDs.
    """
    path = Path(input_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenAlexError(f"cannot read OpenAlex input {path}: {exc}") from exc

    if _looks_like_works_payload(payload):
        return parse_openalex_payload(payload)

    if not isinstance(payload, dict):
        raise OpenAlexError(
            f"OpenAlex input {path} is neither a works payload nor a job object"
        )

    mode = payload.get("mode")
    if mode is None:
        mode = JOB_MODE_DOI_LIST if payload.get("dois") is not None else JOB_MODE_FILTER

    state = Path(state_dir) if state_dir is not None else None
    staging_path = state / STATE_STAGING_FILE if state else None
    done_path = state / STATE_DONE_FILE if state else None
    cursor_store = CursorStore(state / STATE_CURSOR_FILE) if state else None

    if done_path is not None and done_path.exists():
        # Job already completed (possibly across several runs): replay staging.
        return _read_staging(staging_path) if staging_path else []

    records = _read_staging(staging_path) if staging_path else []
    # Crash-window insurance: a record staged just before an interruption
    # whose cursor save never landed would be refetched with the page; skip
    # any work whose key is already staged.
    staged_keys = {str(r.get("UT") or r.get("DI") or "") for r in records}

    if mode == JOB_MODE_DOI_LIST:
        dois = payload.get("dois")
        if not isinstance(dois, list):
            raise OpenAlexError("openalex doi_list job requires a 'dois' list")
        works: Iterable[Dict[str, Any]] = _iter_doi_list(
            dois, fetcher or _default_fetcher(tracker), tracker, cursor_store, mailto
        )
    elif mode == JOB_MODE_FILTER:
        filter_query = payload.get("filter")
        if not isinstance(filter_query, str) or not filter_query.strip():
            raise OpenAlexError("openalex filter job requires a non-empty 'filter' string")
        works = iter_filter_works(
            filter_query,
            fetcher=fetcher,
            tracker=tracker,
            cursor_store=cursor_store,
            per_page=int(payload.get("per_page") or DEFAULT_PER_PAGE),
            mailto=mailto,
        )
    elif mode == JOB_MODE_SEARCH:
        works = search_works(
            str(payload.get("search") or ""),
            allow_search=bool(payload.get("allow_search")),
            fetcher=fetcher,
            tracker=tracker,
            cursor_store=cursor_store,
            per_page=int(payload.get("per_page") or DEFAULT_PER_PAGE),
            mailto=mailto,
        )
    else:
        raise OpenAlexError(f"unknown openalex job mode: {mode!r}")

    staging_file = None
    if staging_path is not None:
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        staging_file = staging_path.open("a", encoding="utf-8")
    try:
        for work in works:
            record = work_to_record(work)
            key = str(record.get("UT") or record.get("DI") or "")
            if key and key in staged_keys:
                continue
            if key:
                staged_keys.add(key)
            records.append(record)
            if staging_file is not None:
                staging_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                staging_file.flush()
    finally:
        if staging_file is not None:
            staging_file.close()

    if done_path is not None:
        # Only a finished pagination may mark the job done. A quota-window
        # stop leaves the next cursor on disk (or, if it fired before the
        # first request, leaves no cursor file at all); both are a pause,
        # not a completion. A completed job always saved cursor=None.
        store_exists = cursor_store is not None and cursor_store.path.exists()
        paused_mid_pagination = (not store_exists) or (cursor_store.load().cursor is not None)
        if not paused_mid_pagination:
            done_path.write_text(
                json.dumps(
                    {
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "records": len(records),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
    return records
