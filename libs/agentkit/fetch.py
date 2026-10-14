"""Generic synchronous HTTP fetch layer (stdlib only).

Motivation (empirical): HTTP fetching is the top failure surface of
literature skills. Publisher domains redirect across origins (to IDP /
WebVPN login pages), reject unexpected content types, and wind-control
aggressive clients with 403/429. This module therefore:

- follows redirects **explicitly**, recording the full redirect chain and
  capping the count so cross-domain loops (e.g. publisher → IDP → publisher)
  terminate instead of hanging;
- sends an explicit ``Accept`` header for content negotiation, and **never
  rejects binary payloads** (``application/pdf``, ``application/gzip``, ...)
  — the body bytes and content-type are returned as-is for the caller to
  handle;
- backs off per :func:`agentkit.quota.backoff_seconds`: 429 ≥ 900 s, 403
  600 s, other 5xx short, anything else not retried.

Discipline (see :mod:`agentkit.quota`): no forged User-Agent, no
concurrency, no proxy bypass. Requests are strictly sequential and the
client identity is honest.

``transport`` is injectable so tests can run fully offline behind a fake::

    def fake_transport(url, headers, timeout):
        return RawResponse(status=200, headers={"Content-Type": "text/plain"},
                           body=b"hello")

    result = fetch("https://example.org/x", transport=fake_transport)

Pure stdlib (urllib under the hood).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional

from agentkit.quota import QuotaTracker, backoff_seconds

__all__ = [
    "FetchResult",
    "RawResponse",
    "RedirectStep",
    "FetchError",
    "default_transport",
    "fetch",
]

DEFAULT_ACCEPT = "*/*"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RETRIES = 3

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """Raised when a fetch cannot be completed (redirect loop/cap exceeded)."""

    def __init__(self, message: str, redirect_chain: Optional[List["RedirectStep"]] = None):
        super().__init__(message)
        self.redirect_chain = redirect_chain or []


@dataclass(frozen=True)
class RedirectStep:
    """One hop of an explicit redirect follow."""

    url: str  # the URL that issued the redirect
    status: int  # 301/302/303/307/308
    location: str  # absolute resolved target


@dataclass(frozen=True)
class RawResponse:
    """Transport-level response: status, headers, body bytes."""

    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class FetchResult:
    """Outcome of :func:`fetch`."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    redirect_chain: List[RedirectStep] = field(default_factory=list)
    url: str = ""  # final URL after any redirects

    @property
    def content_type(self) -> str:
        for key, value in self.headers.items():
            if key.lower() == "content-type":
                return str(value)
        return ""

    @property
    def text(self) -> str:
        """Body decoded as UTF-8 (lossy). Convenience for text payloads."""
        return self.body.decode("utf-8", errors="replace")


# Transport: (url, headers, timeout) -> RawResponse
Transport = Callable[[str, Mapping[str, str], float], RawResponse]
# Sleeper: seconds -> None (injectable in tests)
Sleeper = Callable[[float], None]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirect handler that never follows: fetch() follows explicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def default_transport(url: str, headers: Mapping[str, str], timeout: float) -> RawResponse:
    """Stdlib urllib transport that surfaces 3xx as responses, not follows.

    No User-Agent is forged (discipline); urllib sends its honest
    ``Python-urllib/x.y`` default.
    """
    request = urllib.request.Request(url, headers=dict(headers))
    try:
        with _OPENER.open(request, timeout=timeout) as resp:
            return RawResponse(status=resp.status, headers=dict(resp.headers), body=resp.read())
    except urllib.error.HTTPError as exc:
        # With redirects disabled, 3xx arrives here carrying Location.
        return RawResponse(status=exc.code, headers=dict(exc.headers or {}), body=exc.read() or b"")
    except urllib.error.URLError as exc:
        raise FetchError(f"request failed: {exc}") from exc


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return None


def fetch(
    url: str,
    accept: Optional[str] = DEFAULT_ACCEPT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    timeout: float = DEFAULT_TIMEOUT,
    transport: Optional[Transport] = None,
    tracker: Optional[QuotaTracker] = None,
    sleeper: Sleeper = time.sleep,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> FetchResult:
    """Fetch ``url`` sequentially with explicit redirects and quota backoff.

    Args:
        url: Request URL.
        accept: Value for the ``Accept`` header (content negotiation).
        max_redirects: Maximum redirect hops; exceeded hops and URL loops
            raise :class:`FetchError` with the chain recorded so far.
        timeout: Per-request timeout in seconds.
        transport: Injectable ``(url, headers, timeout) -> RawResponse``.
            Defaults to :func:`default_transport` (urllib).
        tracker: Optional :class:`~agentkit.quota.QuotaTracker`; response
            headers are fed into it on every attempt (including backoffs).
        sleeper: Injectable sleep function (tests inject a recorder).
        max_retries: Retries for backoff-able statuses (429/403/5xx) before
            the last response is returned as-is.

    Returns:
        :class:`FetchResult` with the final status, headers, **raw body
        bytes** (binaries such as PDF/gzip are never rejected), the redirect
        chain, and the final URL.
    """
    do_request: Transport = transport or default_transport
    headers: Dict[str, str] = {"Accept": accept} if accept else {}

    current = url
    chain: List[RedirectStep] = []
    seen = {url}
    retries = 0

    while True:
        raw = do_request(current, headers, timeout)
        status = raw.status

        if status in _REDIRECT_STATUSES:
            location = _header(raw.headers, "Location")
            if not location:
                return FetchResult(status, raw.headers, raw.body, chain, current)
            target = urllib.parse.urljoin(current, location)
            if len(chain) >= max_redirects:
                raise FetchError(
                    f"redirect cap exceeded ({max_redirects} hops): {url} -> ... -> {target}",
                    chain,
                )
            if target in seen:
                raise FetchError(f"redirect loop detected: {target}", chain)
            chain.append(RedirectStep(url=current, status=status, location=target))
            seen.add(target)
            current = target
            continue

        if tracker is not None:
            tracker.record(credits_used=0, headers=raw.headers)

        if status == 429 or status == 403 or 500 <= status <= 599:
            delay = backoff_seconds(status)
            if delay is not None and retries < max_retries:
                retries += 1
                sleeper(delay)
                continue
        return FetchResult(status, raw.headers, raw.body, chain, current)
