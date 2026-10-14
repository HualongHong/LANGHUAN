"""Tests for agentkit.fetch — all offline behind fake transports."""

from __future__ import annotations

import pytest

from agentkit.fetch import FetchError, RawResponse, fetch
from agentkit.quota import QuotaTracker


class FakeTransport:
    """Scripted transport: pops queued responses per (url) call, records calls."""

    def __init__(self, responses):
        self.responses = list(responses)  # list of RawResponse or Exception
        self.calls = []  # list of (url, headers) actually requested

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        if not self.responses:
            raise AssertionError(f"unexpected extra request: {url}")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def ok(body=b"ok", content_type="text/html", headers=None):
    merged = {"Content-Type": content_type}
    merged.update(headers or {})
    return RawResponse(status=200, headers=merged, body=body)


class TestFakeTransportInjection:
    def test_injected_transport_is_used(self):
        fake = FakeTransport([ok(b"hello")])
        result = fetch("https://example.org/x", transport=fake)
        assert result.status == 200
        assert result.body == b"hello"
        assert fake.calls[0][0] == "https://example.org/x"

    def test_accept_header_sent(self):
        fake = FakeTransport([ok()])
        fetch("https://api.openalex.org/works", accept="application/json", transport=fake)
        assert fake.calls[0][1]["Accept"] == "application/json"

    def test_default_accept_is_star_star(self):
        fake = FakeTransport([ok()])
        fetch("https://example.org/", transport=fake)
        assert fake.calls[0][1]["Accept"] == "*/*"


class TestRedirectFollowing:
    def test_cross_domain_redirect_followed_and_chain_recorded(self):
        fake = FakeTransport(
            [
                RawResponse(302, {"Location": "https://idp.university.edu/login?back=1"}, b""),
                RawResponse(302, {"Location": "https://vpn.university.edu/pdf/x.pdf"}, b""),
                ok(b"pdf-bytes", content_type="application/pdf"),
            ]
        )
        result = fetch("https://publisher.org/article/10.1/x", transport=fake)
        assert result.status == 200
        assert result.url == "https://vpn.university.edu/pdf/x.pdf"
        assert [c.url for c in result.redirect_chain] == [
            "https://publisher.org/article/10.1/x",
            "https://idp.university.edu/login?back=1",
        ]
        assert [c.location for c in result.redirect_chain] == [
            "https://idp.university.edu/login?back=1",
            "https://vpn.university.edu/pdf/x.pdf",
        ]
        assert result.content_type == "application/pdf"
        assert result.body == b"pdf-bytes"

    def test_relative_redirect_resolved(self):
        fake = FakeTransport(
            [RawResponse(301, {"Location": "/new-path"}, b""), ok(b"done")]
        )
        result = fetch("https://example.org/old", transport=fake)
        assert result.url == "https://example.org/new-path"
        assert result.body == b"done"

    def test_redirect_cap_raises_with_chain(self):
        fake = FakeTransport(
            [RawResponse(302, {"Location": f"https://example.org/{i}"}, b"") for i in range(8)]
        )
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.org/start", max_redirects=3, transport=fake)
        assert "redirect cap" in str(excinfo.value)
        assert len(excinfo.value.redirect_chain) == 3

    def test_redirect_loop_raises(self):
        fake = FakeTransport(
            [
                RawResponse(302, {"Location": "https://example.org/b"}, b""),
                RawResponse(302, {"Location": "https://example.org/a"}, b""),
            ]
        )
        with pytest.raises(FetchError) as excinfo:
            fetch("https://example.org/a", transport=fake)
        assert "loop" in str(excinfo.value)

    def test_302_without_location_returns_as_is(self):
        fake = FakeTransport([RawResponse(302, {}, b"")])
        result = fetch("https://example.org/", transport=fake)
        assert result.status == 302


class TestBinaryContentNotRejected:
    @pytest.mark.parametrize(
        "content_type", ["application/pdf", "application/gzip", "application/octet-stream"]
    )
    def test_binary_returned_verbatim(self, content_type):
        payload = b"\x1f\x8b\x08\x00binary-bytes"
        fake = FakeTransport([ok(payload, content_type=content_type)])
        result = fetch("https://example.org/file", transport=fake)
        assert result.status == 200
        assert result.body == payload
        assert result.content_type == content_type


class TestBackoffRetries:
    def test_429_long_backoff_then_success(self):
        fake = FakeTransport(
            [
                RawResponse(429, {"X-RateLimit-Remaining": "2"}, b"rate limited"),
                ok(b"recovered"),
            ]
        )
        slept = []
        result = fetch(
            "https://api.openalex.org/works",
            transport=fake,
            sleeper=slept.append,
        )
        assert result.status == 200
        assert result.body == b"recovered"
        assert len(slept) == 1
        assert slept[0] >= 900  # 429 backoff is >= 900 s even with jitter

    def test_403_wind_control_backoff(self):
        fake = FakeTransport(
            [RawResponse(403, {}, b"forbidden"), ok(b"later")]
        )
        slept = []
        result = fetch("https://example.org/", transport=fake, sleeper=slept.append)
        assert result.status == 200
        assert slept[0] >= 600

    def test_404_not_retried(self):
        fake = FakeTransport([RawResponse(404, {}, b"missing")])
        slept = []
        result = fetch("https://example.org/missing", transport=fake, sleeper=slept.append)
        assert result.status == 404
        assert slept == []

    def test_5xx_short_backoff_then_gives_up(self):
        fake = FakeTransport(
            [RawResponse(503, {}, b""), RawResponse(503, {}, b"")]
        )
        slept = []
        result = fetch(
            "https://example.org/", transport=fake, sleeper=slept.append, max_retries=1
        )
        assert result.status == 503
        assert len(slept) == 1
        assert slept[0] < 900  # short backoff, not the 429 table

    def test_response_headers_feed_tracker(self):
        fake = FakeTransport(
            [
                RawResponse(429, {"X-RateLimit-Remaining": "4", "X-RateLimit-Limit": "1000"}, b""),
                ok(b"x", headers={"X-RateLimit-Remaining": "3"}),
            ]
        )
        tracker = QuotaTracker(stop_ratio=0.05)
        fetch("https://example.org/", transport=fake, tracker=tracker, sleeper=lambda s: None)
        assert tracker.remaining == 3
        assert len(tracker.ledger) == 2
