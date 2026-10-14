"""Tests for agentkit.quota — budget arithmetic, stop threshold, cursor, backoff."""

from __future__ import annotations

import json
import random

import pytest

from agentkit.quota import (
    BACKOFF_FORBIDDEN,
    BACKOFF_SERVER_ERROR,
    BACKOFF_TOO_MANY_REQUESTS,
    CursorStore,
    QuotaBudget,
    QuotaTracker,
    backoff_seconds,
)


class TestQuotaBudget:
    def test_empirical_case_62712_items_search(self):
        """62,712 records @200/page, search=10 credits -> 314 req = 3,140 credits
        -> ~3.1 anonymous day windows (1,000 credits/day)."""
        plan = QuotaBudget(daily_credits=1000).plan(
            n_items=62_712, per_page=200, credits_per_request=10
        )
        assert plan.requests == 314
        assert plan.total_credits == 3_140
        assert plan.daily_credits == 1000
        assert round(plan.days_float, 1) == 3.1
        assert plan.days_float == pytest.approx(3.14)
        assert plan.days_required == 4
        assert not plan.fits_in_one_day

    def test_filter_endpoint_one_credit_fits_day(self):
        plan = QuotaBudget(daily_credits=1000).plan(
            n_items=62_712, per_page=200, credits_per_request=1
        )
        assert plan.requests == 314
        assert plan.total_credits == 314
        assert plan.days_float == pytest.approx(0.314)
        assert plan.days_required == 1
        assert plan.fits_in_one_day

    def test_keyed_pool_bigger_window(self):
        plan = QuotaBudget(daily_credits=100_000).plan(
            n_items=62_712, per_page=200, credits_per_request=10
        )
        assert plan.days_required == 1

    def test_zero_items_is_free(self):
        plan = QuotaBudget().plan(0)
        assert plan.requests == 0
        assert plan.total_credits == 0
        assert plan.days_required == 0

    def test_validation(self):
        budget = QuotaBudget()
        with pytest.raises(ValueError):
            budget.plan(-1)
        with pytest.raises(ValueError):
            budget.plan(10, per_page=0)
        with pytest.raises(ValueError):
            budget.plan(10, credits_per_request=-1)
        with pytest.raises(ValueError):
            QuotaBudget(daily_credits=0)

    def test_plan_report_round_trips(self):
        plan = QuotaBudget().plan(1000, per_page=200, credits_per_request=1)
        report = plan.to_dict()
        assert report["requests"] == 5
        assert report["total_credits"] == 5
        assert "3.1" not in str(report)  # small job; sanity that fields exist
        assert set(report) >= {
            "n_items",
            "per_page",
            "requests",
            "credits_per_request",
            "total_credits",
            "daily_credits",
            "days_float",
            "days_required",
            "fits_in_one_day",
        }


class TestQuotaTracker:
    def _headers(self, remaining: int, limit: int = 1000, reset: int = 1_700_000_000):
        return {
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Reset": str(reset),
        }

    def test_records_headers_case_insensitively(self):
        tracker = QuotaTracker()
        entry = tracker.record(
            credits_used=1,
            headers={"x-ratelimit-remaining": "999", "X-RateLimit-Limit": "1000"},
        )
        assert tracker.credits_used == 1
        assert tracker.remaining == 999
        assert tracker.limit == 1000
        assert entry.remaining == 999

    def test_free_single_record_endpoints_cost_zero(self):
        tracker = QuotaTracker()
        for _ in range(25):
            tracker.record(credits_used=0, headers=self._headers(1000))
        assert tracker.credits_used == 0
        assert len(tracker.ledger) == 25
        assert all(e.credits_used == 0 for e in tracker.ledger)

    def test_cumulative_ledger(self):
        tracker = QuotaTracker()
        tracker.record(credits_used=10, headers=self._headers(990))
        tracker.record(credits_used=10, headers=self._headers(980))
        assert [e.cumulative_credits for e in tracker.ledger] == [10, 20]

    def test_should_stop_below_default_five_percent(self):
        tracker = QuotaTracker(stop_ratio=0.05)
        tracker.record(credits_used=1, headers=self._headers(remaining=60, limit=1000))
        assert not tracker.should_stop()  # 60 >= 50
        tracker.record(credits_used=1, headers=self._headers(remaining=49, limit=1000))
        assert tracker.should_stop()  # 49 < 50

    def test_should_stop_never_triggers_without_headers(self):
        tracker = QuotaTracker()
        tracker.record(credits_used=999)
        assert not tracker.should_stop()

    def test_custom_threshold(self):
        tracker = QuotaTracker(stop_ratio=0.5)
        tracker.record(credits_used=0, headers=self._headers(remaining=499, limit=1000))
        assert tracker.should_stop()
        tracker2 = QuotaTracker(stop_ratio=0.01)
        tracker2.record(credits_used=0, headers=self._headers(remaining=10, limit=1000))
        assert not tracker2.should_stop()  # 10 is not < 10
        tracker2.record(credits_used=0, headers=self._headers(remaining=9, limit=1000))
        assert tracker2.should_stop()  # 9 < 10

    def test_invalid_stop_ratio(self):
        with pytest.raises(ValueError):
            QuotaTracker(stop_ratio=1.0)
        with pytest.raises(ValueError):
            QuotaTracker(stop_ratio=-0.1)

    def test_state_dict_snapshot(self):
        tracker = QuotaTracker()
        tracker.record(credits_used=3, headers=self._headers(997))
        state = tracker.state_dict()
        assert state["credits_used"] == 3
        assert state["remaining"] == 997
        assert len(state["ledger"]) == 1


class TestCursorStore:
    def test_round_trip(self, tmp_path):
        store = CursorStore(tmp_path / "state" / "cursor.json")
        store.save(cursor="next-page-cursor", ids={"W1", "W2", "W1"})
        state = store.load()
        assert state.cursor == "next-page-cursor"
        assert state.ids == {"W1", "W2"}

    def test_missing_file_is_empty_state(self, tmp_path):
        store = CursorStore(tmp_path / "nope.json")
        state = store.load()
        assert state.cursor is None
        assert state.ids == set()

    def test_corrupt_file_is_empty_state(self, tmp_path):
        path = tmp_path / "cursor.json"
        path.write_text("{not json", encoding="utf-8")
        state = CursorStore(path).load()
        assert state.cursor is None
        assert state.ids == set()

    def test_resume_dedup(self, tmp_path):
        """IDs persisted before the interruption are not re-requested."""
        store = CursorStore(tmp_path / "cursor.json")
        store.save(cursor="cursor-B", ids={f"W{i}" for i in range(200)})
        state = store.load()
        replayed = [f"W{i}" for i in range(250)]  # next page overlaps first 200
        new_ids = [i for i in replayed if i not in state.ids]
        assert new_ids == [f"W{i}" for i in range(200, 250)]
        store.save(cursor=None, ids=state.ids | set(new_ids))
        final = store.load()
        assert final.cursor is None
        assert len(final.ids) == 250

    def test_save_is_atomic_enough_for_resume(self, tmp_path):
        store = CursorStore(tmp_path / "cursor.json")
        store.save(cursor="c1", ids={"a"})
        data = json.loads((tmp_path / "cursor.json").read_text(encoding="utf-8"))
        assert data["cursor"] == "c1"
        assert data["ids"] == ["a"]
        assert not list(tmp_path.glob("*.tmp"))


class TestBackoffTable:
    def test_429_at_least_900_seconds(self):
        for _ in range(20):
            delay = backoff_seconds(429)
            assert BACKOFF_TOO_MANY_REQUESTS <= delay < BACKOFF_TOO_MANY_REQUESTS * 1.2

    def test_403_at_600_seconds(self):
        delay = backoff_seconds(403, rng=random.Random(0))
        assert BACKOFF_FORBIDDEN <= delay < BACKOFF_FORBIDDEN * 1.2

    def test_5xx_short_backoff(self):
        for status in (500, 502, 503):
            delay = backoff_seconds(status, rng=random.Random(1))
            assert BACKOFF_SERVER_ERROR <= delay < BACKOFF_SERVER_ERROR * 1.2

    def test_other_4xx_not_retried(self):
        for status in (400, 401, 404, 422):
            assert backoff_seconds(status) is None

    def test_2xx_3xx_not_retried(self):
        assert backoff_seconds(200) is None
        assert backoff_seconds(302) is None

    def test_jitter_is_deterministic_with_rng(self):
        rng = random.Random(42)
        a = backoff_seconds(429, rng=random.Random(42))
        b = backoff_seconds(429, rng=random.Random(42))
        assert a == b
