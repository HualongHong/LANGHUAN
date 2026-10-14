"""Quota governance primitives for online data sources (provider-agnostic).

Extracted from a real bibliometrics project post-mortem: HTTP fetching is the
number-one failure surface of literature skills, and online-source quotas are
window/daily budgets, not rate limits. The primitives here enforce three
disciplines — they are **constants**, not options:

- :data:`DISCIPLINE_NO_FAKE_UA` — never forge a browser User-Agent. An honest
  client identity (library default) is sent instead; politeness is achieved
  via ``mailto`` parameters and backoff, not disguise.
- :data:`DISCIPLINE_NO_CONCURRENCY` — requests are strictly sequential. One
  in-flight request per source at a time; no client-side parallelism against
  a quota window.
- :data:`DISCIPLINE_NO_PROXY_BYPASS` — no proxy rotation / VPN hopping to
  circumvent 403 wind-control. A 403 means back off (600 s) and retry later.

Quota model (OpenAlex-style, generalised): a *single-record* endpoint is free;
a *filter/list* endpoint costs one credit per request; a *search* endpoint
costs ten. Anonymous pools typically get 1,000 credits/day resetting at
midnight UTC. Because the budget is daily, the workflow is: estimate demand →
convert to credits → convert to days (:class:`QuotaBudget`) → fetch while
feeding every response's ``X-RateLimit-*`` headers to :class:`QuotaTracker`
and stop *before* the window is exhausted → persist the cursor so an
interrupted multi-day run resumes without re-requesting already-received IDs
(:class:`CursorStore`).

Pure stdlib.
"""

from __future__ import annotations

import json
import math
import os
import random
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Set, Union

# --- disciplines (constants, not options) -----------------------------------
DISCIPLINE_NO_FAKE_UA = True
"""Never forge a browser User-Agent; send an honest client identity only."""

DISCIPLINE_NO_CONCURRENCY = True
"""One in-flight request per source; no client-side parallelism."""

DISCIPLINE_NO_PROXY_BYPASS = True
"""No proxy rotation to circumvent 403 wind-control; back off instead."""

# --- rate-limit headers ------------------------------------------------------
HEADER_REMAINING = "X-RateLimit-Remaining"
HEADER_LIMIT = "X-RateLimit-Limit"
HEADER_RESET = "X-RateLimit-Reset"

DEFAULT_DAILY_CREDITS = 1000
"""Anonymous-pool default (OpenAlex); keyed pools may be higher."""

DEFAULT_STOP_RATIO = 0.05
"""Stop when remaining credits fall below this fraction of the limit."""

# --- backoff table (seconds) -------------------------------------------------
BACKOFF_TOO_MANY_REQUESTS = 900.0
"""429: quota windows are daily, so the backoff must be long (>= 15 min)."""

BACKOFF_FORBIDDEN = 600.0
"""403: wind-control backoff."""

BACKOFF_SERVER_ERROR = 30.0
"""5xx: short backoff; give the upstream a moment to recover."""

BACKOFF_JITTER_RATIO = 0.1
"""Positive jitter (uniform in [0, ratio*base]) applied on top of the base."""

_NO_RETRY = None

_BACKOFF_BASE: Dict[int, float] = {
    429: BACKOFF_TOO_MANY_REQUESTS,
    403: BACKOFF_FORBIDDEN,
}
_BACKOFF_5XX_BASE = BACKOFF_SERVER_ERROR


def backoff_seconds(status_code: int, rng: Optional[random.Random] = None) -> Optional[float]:
    """Return how many seconds to wait before retrying ``status_code``.

    Implements the backoff half of the disciplines:

    - 429 → at least :data:`BACKOFF_TOO_MANY_REQUESTS` s (plus positive jitter);
    - 403 → :data:`BACKOFF_FORBIDDEN` s (plus jitter);
    - other 5xx → short :data:`BACKOFF_SERVER_ERROR` s backoff;
    - any other status → ``None`` (do not retry).

    Args:
        status_code: HTTP status of the rejected response.
        rng: Optional :class:`random.Random` for deterministic jitter in tests.
            Defaults to the module-level generator.

    Returns:
        Delay in seconds (never below the base), or ``None`` when the status
        must not be retried.
    """
    if 500 <= status_code <= 599:
        base = _BACKOFF_5XX_BASE
    else:
        base = _BACKOFF_BASE.get(status_code)
        if base is None:
            return _NO_RETRY
    jitter_source = rng if rng is not None else random
    return base * (1.0 + jitter_source.uniform(0.0, BACKOFF_JITTER_RATIO))


@dataclass(frozen=True)
class BudgetPlan:
    """Pre-computed quota budget for one fetch job (see :meth:`QuotaBudget.plan`)."""

    n_items: int
    per_page: int
    requests: int
    credits_per_request: int
    total_credits: int
    daily_credits: int
    days_float: float
    days_required: int
    fits_in_one_day: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "n_items": self.n_items,
            "per_page": self.per_page,
            "requests": self.requests,
            "credits_per_request": self.credits_per_request,
            "total_credits": self.total_credits,
            "daily_credits": self.daily_credits,
            "days_float": round(self.days_float, 2),
            "days_required": self.days_required,
            "fits_in_one_day": self.fits_in_one_day,
        }

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"{self.n_items} items @ {self.per_page}/page = {self.requests} requests "
            f"x {self.credits_per_request} credits = {self.total_credits} credits; "
            f"at {self.daily_credits} credits/day that is ~{self.days_float:.1f} day(s) "
            f"({self.days_required} full day window(s) required)."
        )


class QuotaBudget:
    """Turn a fetch demand into a day-window plan before any request is sent.

    Args:
        daily_credits: Size of the daily credit window. Defaults to
            :data:`DEFAULT_DAILY_CREDITS` (anonymous OpenAlex pool).
    """

    def __init__(self, daily_credits: int = DEFAULT_DAILY_CREDITS):
        if daily_credits <= 0:
            raise ValueError("daily_credits must be positive")
        self.daily_credits = int(daily_credits)

    def plan(
        self,
        n_items: int,
        per_page: int = 200,
        credits_per_request: int = 1,
    ) -> BudgetPlan:
        """Compute the budget for fetching ``n_items`` through a list endpoint.

        Example (the empirical case that motivated this module): 62,712 records
        at 200/page through the OpenAlex *search* endpoint (10 credits/request)
        → 314 requests → 3,140 credits → ~3.1 anonymous day windows. When
        ``days_required`` exceeds what the deadline allows, apply for an API
        key (bigger window) or narrow the filter before fetching.

        Args:
            n_items: Total records demanded.
            per_page: Page size (OpenAlex maximum is 200).
            credits_per_request: 1 for ``filter`` lists, 10 for ``search``.

        Returns:
            A frozen :class:`BudgetPlan`; raises ``ValueError`` on
            non-positive inputs.
        """
        if n_items < 0:
            raise ValueError("n_items must be >= 0")
        if per_page <= 0:
            raise ValueError("per_page must be positive")
        if credits_per_request < 0:
            raise ValueError("credits_per_request must be >= 0")
        requests = math.ceil(n_items / per_page) if n_items else 0
        total_credits = requests * credits_per_request
        days_float = total_credits / self.daily_credits if total_credits else 0.0
        days_required = math.ceil(days_float)
        return BudgetPlan(
            n_items=n_items,
            per_page=per_page,
            requests=requests,
            credits_per_request=credits_per_request,
            total_credits=total_credits,
            daily_credits=self.daily_credits,
            days_float=days_float,
            days_required=days_required,
            fits_in_one_day=days_required <= 1,
        )


@dataclass
class LedgerEntry:
    """One observed quota event (a consumed request and/or rate-limit headers)."""

    ts: str
    credits_used: int
    cumulative_credits: int
    remaining: Optional[int] = None
    limit: Optional[int] = None
    reset: Optional[int] = None


class QuotaTracker:
    """Track credits consumed and the remote quota window in real time.

    Feed **every** response (list pages and free single-record endpoints
    alike, the latter with ``credits_used=0``) so the running ledger is
    complete. The tracker recalibrates from ``X-RateLimit-Remaining`` on each
    response, so it is safe across restarts even though the ledger itself is
    in-memory.

    Args:
        daily_credits: Expected daily window size (informational).
        stop_ratio: Stop when ``remaining < limit * stop_ratio``. Defaults to
            :data:`DEFAULT_STOP_RATIO` (5 %).
    """

    def __init__(
        self,
        daily_credits: int = DEFAULT_DAILY_CREDITS,
        stop_ratio: float = DEFAULT_STOP_RATIO,
    ):
        if not 0.0 <= stop_ratio < 1.0:
            raise ValueError("stop_ratio must be in [0, 1)")
        self.daily_credits = int(daily_credits)
        self.stop_ratio = float(stop_ratio)
        self.credits_used = 0
        self.remaining: Optional[int] = None
        self.limit: Optional[int] = None
        self.reset: Optional[int] = None
        self.ledger: List[LedgerEntry] = []

    @staticmethod
    def _header(headers: Optional[Mapping[str, str]], name: str) -> Optional[int]:
        if not headers:
            return None
        for key, value in headers.items():
            if key.lower() == name.lower():
                try:
                    return int(str(value).strip())
                except (TypeError, ValueError):
                    return None
        return None

    def record(
        self,
        credits_used: int = 0,
        headers: Optional[Mapping[str, str]] = None,
        now: Optional[datetime] = None,
    ) -> LedgerEntry:
        """Record one request/response against the quota.

        Args:
            credits_used: Credits this request cost (0 for free single-record
                endpoints, 1 for filter lists, 10 for searches).
            headers: Response headers; ``X-RateLimit-Remaining/Limit/Reset``
                are read case-insensitively when present.
            now: Timestamp override (tests).

        Returns:
            The appended :class:`LedgerEntry`.
        """
        self.credits_used += int(credits_used)
        remaining = self._header(headers, HEADER_REMAINING)
        limit = self._header(headers, HEADER_LIMIT)
        reset = self._header(headers, HEADER_RESET)
        if remaining is not None:
            self.remaining = remaining
        if limit is not None:
            self.limit = limit
        if reset is not None:
            self.reset = reset
        entry = LedgerEntry(
            ts=(now or datetime.now(timezone.utc)).isoformat(),
            credits_used=int(credits_used),
            cumulative_credits=self.credits_used,
            remaining=self.remaining,
            limit=self.limit,
            reset=self.reset,
        )
        self.ledger.append(entry)
        return entry

    def stop_threshold(self) -> Optional[float]:
        """The remaining-credits line below which fetching must stop."""
        if self.limit is None:
            return None
        return self.limit * self.stop_ratio

    def should_stop(self) -> bool:
        """Return ``True`` when the remote window is nearly exhausted.

        Only meaningful once a response carrying ``X-RateLimit-Remaining`` has
        been recorded; before that, returns ``False`` (nothing observed yet).
        """
        if self.remaining is None or self.limit is None:
            return False
        return self.remaining < self.stop_threshold()

    def state_dict(self) -> Dict[str, object]:
        """Serializable snapshot of the running quota state."""
        return {
            "daily_credits": self.daily_credits,
            "stop_ratio": self.stop_ratio,
            "credits_used": self.credits_used,
            "remaining": self.remaining,
            "limit": self.limit,
            "reset": self.reset,
            "ledger": [entry.__dict__.copy() for entry in self.ledger],
        }


@dataclass
class CursorState:
    """Resume point of a paginated fetch: the next cursor plus received IDs."""

    cursor: Optional[str] = None
    ids: Set[str] = field(default_factory=set)


class CursorStore:
    """Persist a pagination cursor and the set of already-received IDs.

    The JSON file looks like::

        {"cursor": "abc...", "ids": ["W1", "W2", ...], "updated_at": "..."}

    - ``cursor`` is the *next* cursor to send (``None``/absent before the first
      page and once the last page has been consumed);
    - ``ids`` supports dedup-on-resume: records already received are never
      re-requested even if a page is replayed.

    Writes are atomic (temp file + replace) so an interrupted run never sees a
    torn file.

    Args:
        path: JSON file path. Parent directories are created on save.
    """

    VERSION = 1

    def __init__(self, path: Union[str, Path]):
        self.path = Path(path)

    def save(self, cursor: Optional[str], ids: Iterable[str]) -> None:
        """Persist ``cursor`` and the full set of received ``ids``."""
        payload = {
            "version": self.VERSION,
            "cursor": cursor,
            "ids": sorted(set(ids)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            Path(tmp_name).replace(self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def load(self) -> CursorState:
        """Load the resume point; empty state when the file is missing/corrupt."""
        if not self.path.exists():
            return CursorState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return CursorState()
        if not isinstance(data, dict):
            return CursorState()
        cursor = data.get("cursor")
        raw_ids = data.get("ids") or []
        ids = {str(i) for i in raw_ids} if isinstance(raw_ids, list) else set()
        return CursorState(cursor=cursor if isinstance(cursor, str) else None, ids=ids)

    def add_id(self, state: Optional[CursorState], new_id: str) -> CursorState:
        """Return ``state`` (or a fresh one) with ``new_id`` folded in."""
        state = state or self.load()
        state.ids.add(str(new_id))
        return state
