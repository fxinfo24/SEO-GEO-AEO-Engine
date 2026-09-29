"""SSRF-guarded, robots.txt-aware, resource-bounded HTTP fetcher.

Every external fetch in this engine goes through `SafeFetcher`. It exists to
close the gap every skill in the source masterlist left open: none of them
validate that a user-supplied URL doesn't resolve to internal infrastructure
before fetching it, cap how much of a response they'll read, or bound how
long an audit as a whole is allowed to run.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

DEFAULT_USER_AGENT = "SEOGeoAeoEngine/1.0 (+https://example.invalid/bot)"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RATE_LIMIT_SECONDS = 1.0
# RoadMap.md Phase 10 "maximum response size". 20 MB comfortably covers any
# real HTML/robots.txt/llms.txt page; a response that large is either not a
# page this engine should be parsing, or is an attempt to exhaust memory.
DEFAULT_MAX_RESPONSE_BYTES = 20_000_000


class UnsafeURLError(ValueError):
    """Raised when a target URL resolves to a disallowed network range."""


class FetchError(RuntimeError):
    """Raised for any non-SSRF fetch failure (timeout, DNS, HTTP error, size cap)."""


class AuditTimeoutError(FetchError):
    """Raised when a shared `Deadline`'s total wall-clock budget is exceeded.

    A subclass of FetchError so every existing `except FetchError` call site
    (cli.py, crawler.py, orchestrator.py) already handles it correctly
    without change — an exceeded audit timeout is a fetch failure from their
    point of view, just one with a specific, actionable cause.
    """


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    text: str
    elapsed_seconds: float


class Fetcher(Protocol):
    """The minimal fetch interface crawlers and orchestrators depend on.

    Satisfied by both `SafeFetcher` (plain HTTP) and `RenderFetcher` (headless
    Chromium), so a crawl can run over either without a cast.
    """

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult: ...


@dataclass
class Deadline:
    """A wall-clock budget shared across every fetch one audit/crawl makes
    (RoadMap.md Phase 10 "total audit timeout").

    This is independent of `SafeFetcher.timeout_seconds`, which bounds a
    single HTTP request: a crawl of 20 pages at 2s each stays under any
    single request's timeout while still taking 40s in aggregate. Attach one
    Deadline to the fetcher shared across an audit/crawl/report (orchestrator
    already shares one fetcher instance for exactly this kind of aggregate
    concern) and every fetch made with it is checked against the same clock.

    `seconds=None` means unbounded — the default, so existing callers that
    don't pass a deadline are unaffected.
    """

    seconds: float | None
    _start: float = field(default_factory=time.monotonic, init=False, repr=False)

    def remaining(self) -> float | None:
        if self.seconds is None:
            return None
        return self.seconds - (time.monotonic() - self._start)

    def check(self, context: str = "audit") -> None:
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise AuditTimeoutError(
                f"{context} exceeded its total timeout of {self.seconds}s"
            )


def _resolve_public_ip(hostname: str) -> str:
    """Resolve `hostname` and return one IP that is globally routable.

    Every resolved address is checked, not just the one that will be used —
    if a hostname resolves to several IPs and even one is non-public, the
    whole hostname is refused, since a future connection (ours or a proxy's)
    might land on that record.

    The deny rule is `not ip.is_global` rather than enumerating
    private/loopback/link-local/reserved/multicast individually: cloud
    metadata ranges (e.g. Alibaba Cloud's 100.100.100.200) and other
    non-RFC-1918 internal ranges are not reliably caught by those specific
    flags, but none of them are ever globally routable either. Deny-by-
    default is the safer default here.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host: {hostname}") from exc

    resolved: list[str] = []
    for info in infos:
        ip_str = str(info[4][0])
        ip = ipaddress.ip_address(ip_str)
        if not ip.is_global:
            raise UnsafeURLError(
                f"Refusing to fetch {hostname}: resolves to non-public address {ip_str}"
            )
        resolved.append(ip_str)
    if not resolved:
        raise UnsafeURLError(f"Could not resolve host: {hostname}")
    return resolved[0]


class _PinnedNetworkBackend(httpcore.SyncBackend):
    """A `httpcore` network backend that resolves each hostname exactly once
    per connection attempt and connects to that literal, already-validated
    IP — it never hands resolution back to the OS a second time.

    Without this, `_validate_url()` and the actual TCP connect are two
    separate DNS lookups, seconds apart in the worst case (robots.txt fetch,
    then the real request). A DNS-rebinding attacker who controls the
    target's DNS can answer the first lookup with a public IP and the
    second with 127.0.0.1 or a cloud metadata address — the validation
    passes, and the real connection goes straight to internal infra. Pinning
    the resolution used for validation to the exact resolution used for the
    connection closes that TOCTOU gap. TLS SNI and certificate hostname
    verification are unaffected: httpcore passes the original hostname for
    those separately (via `server_hostname`), never the literal IP this
    backend substitutes for the socket connect itself.
    """

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.NetworkStream:
        ip = _resolve_public_ip(host)
        return super().connect_tcp(
            ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


def _build_pinned_transport(*, retries: int = 0) -> httpx.HTTPTransport:
    """An `httpx.HTTPTransport` whose connections are only ever opened to
    addresses `_resolve_public_ip` has approved. `httpx.HTTPTransport` does
    not accept a `network_backend` argument, so this builds one the normal
    way (to get its SSL-context handling right) and then swaps in a pool
    that uses `_PinnedNetworkBackend` in its place.
    """
    transport = httpx.HTTPTransport(retries=retries)
    # Accesses HTTPTransport's private `_pool` to swap in a pinned-resolution
    # network backend — see this function's docstring for why.
    transport._pool = httpcore.ConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        retries=retries,
        network_backend=_PinnedNetworkBackend(),
    )
    return transport


@dataclass
class SafeFetcher:
    """Fetches pages while enforcing SSRF protection, robots.txt, rate
    limits, a maximum response size, and an optional shared total-time budget.

    Parameters
    ----------
    user_agent:
        Sent on every request and used to evaluate robots.txt rules.
    timeout_seconds:
        Per-request timeout.
    rate_limit_seconds:
        Minimum delay enforced between requests to the same host.
    respect_robots:
        When True (default), disallowed paths raise FetchError instead of
        being fetched. Set False only for robots.txt/llms.txt/sitemap.xml
        fetches themselves, which must always be reachable to be audited.
    max_response_bytes:
        Hard cap on a single response body. Checked against a declared
        Content-Length up front when present, and against actual bytes
        received while streaming regardless (a dishonest or missing
        Content-Length cannot be used to bypass the cap).
    deadline:
        Optional `Deadline` shared across every fetch this instance makes
        (e.g. for one whole audit or crawl). None (default) is unbounded.
    """

    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    respect_robots: bool = True
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    deadline: Deadline | None = None
    _last_request_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _robots_cache: dict[str, urllib.robotparser.RobotFileParser] = field(
        default_factory=dict, init=False, repr=False
    )
    _transport: httpx.HTTPTransport | None = field(default=None, init=False, repr=False)

    def _get_transport(self) -> httpx.HTTPTransport:
        # Built lazily and reused across requests on this instance: the SSL
        # context and connection pool are safe to share and expensive to
        # rebuild on every single fetch, which matters for a multi-page crawl.
        if self._transport is None:
            self._transport = _build_pinned_transport()
        return self._transport

    def _assert_public_host(self, hostname: str) -> None:
        """Validate `hostname` resolves to a public address. Raises UnsafeURLError.

        This is a fast, early check for a clear error message before any
        request is attempted — it is not itself what makes the connection
        safe. `_PinnedNetworkBackend` performs the authoritative,
        TOCTOU-safe check at actual connect time; this may resolve slightly
        earlier and separately.
        """
        _resolve_public_ip(hostname)

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise UnsafeURLError(f"Refusing non-http(s) scheme: {parsed.scheme!r}")
        if not parsed.hostname:
            raise UnsafeURLError(f"URL has no hostname: {url!r}")
        if parsed.hostname.lower() in ("localhost",):
            raise UnsafeURLError("Refusing to fetch localhost")
        self._assert_public_host(parsed.hostname)
        return parsed.hostname

    def _get_robots_parser(self, base_url: str) -> urllib.robotparser.RobotFileParser:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots_cache:
            return self._robots_cache[origin]

        robots_url = urljoin(origin, "/robots.txt")
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            result = self.fetch(robots_url, respect_robots_override=False)
            parser.parse(result.text.splitlines())
        except (FetchError, UnsafeURLError):
            # No robots.txt / unreachable => treat as "allow all" per RFC 9309 default.
            parser.parse([])
        self._robots_cache[origin] = parser
        return parser

    def _throttle(self, hostname: str) -> None:
        last = self._last_request_at.get(hostname)
        if last is not None:
            elapsed = time.monotonic() - last
            remaining = self.rate_limit_seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[hostname] = time.monotonic()

    def is_allowed(self, url: str) -> bool:
        """Check robots.txt permission for `url` under this fetcher's user agent."""
        parser = self._get_robots_parser(url)
        return parser.can_fetch(self.user_agent, url)

    def validate_url(self, url: str) -> str:
        """Public wrapper around the SSRF/scheme checks — reused by RenderFetcher
        so the headless-browser path enforces the identical safety rules."""
        return self._validate_url(url)

    def _read_capped(self, response: httpx.Response, url: str) -> str:
        """Read a streamed response body, enforcing `max_response_bytes`.

        Content-Length is checked first so an honest oversized response is
        rejected before downloading anything; the running total is checked
        as bytes actually arrive so a missing or understated Content-Length
        can't be used to smuggle an unbounded body past the header check.
        """
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > self.max_response_bytes:
                raise FetchError(
                    f"Response for {url} declares {declared} bytes, exceeding the "
                    f"{self.max_response_bytes}-byte limit"
                )

        total = 0
        chunks: list[bytes] = []
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > self.max_response_bytes:
                raise FetchError(
                    f"Response for {url} exceeded the {self.max_response_bytes}-byte "
                    "limit while streaming (Content-Length was absent or understated)"
                )
            chunks.append(chunk)

        # Header-declared charset only (e.g. "text/html; charset=..."): body
        # content isn't sniffed for an encoding, since that needs the fully
        # buffered content httpx's own `.text` property assumes is present.
        # errors="replace" over a hard failure: a wrong guess should degrade
        # the parsed text, not abort an otherwise-successful fetch.
        encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        """Fetch `url`, enforcing SSRF checks, robots.txt, rate limiting,
        a maximum response size, and this fetcher's shared deadline (if any).

        Redirects are followed manually, one hop at a time, validating each
        `Location` target BEFORE it is requested. `follow_redirects=True`
        would let httpx send the request to a redirect's destination before
        any final-URL check ran, so a malicious 302 to a cloud metadata IP
        (169.254.169.254) or other internal host would already have been
        contacted by the time that check raised. Pre-hop validation closes
        that gap: an unsafe redirect target is never requested at all. Each
        hop's actual connection is additionally re-validated at connect
        time by `_PinnedNetworkBackend`, closing the DNS-rebinding TOCTOU a
        pre-hop-only check would still leave open.

        Raises
        ------
        UnsafeURLError
            If the URL is malformed, resolves to a non-public address, or a
            redirect hop in the chain points at one.
        FetchError
            If robots.txt disallows the fetch, the redirect chain exceeds
            `max_redirects`, the response exceeds `max_response_bytes`, or
            the HTTP request itself fails.
        AuditTimeoutError
            If this fetcher has a `deadline` and it has already elapsed.
            (A FetchError subclass — see its docstring.)
        """
        if self.deadline is not None:
            self.deadline.check(context="fetch")

        hostname = self._validate_url(url)

        should_check_robots = (
            self.respect_robots if respect_robots_override is None else respect_robots_override
        )
        if should_check_robots and not self.is_allowed(url):
            raise FetchError(f"Disallowed by robots.txt: {url}")

        self._throttle(hostname)

        start = time.monotonic()
        current_url = url
        max_redirects = 10
        response: httpx.Response | None = None
        try:
            with httpx.Client(
                transport=self._get_transport(),
                timeout=self.timeout_seconds,
                headers={"User-Agent": self.user_agent},
            ) as client:
                for _ in range(max_redirects + 1):
                    request = client.build_request("GET", current_url)
                    response = client.send(request, stream=True)
                    if not response.is_redirect:
                        break
                    location = response.headers.get("location")
                    response.close()
                    if not location:
                        break
                    next_url = urljoin(current_url, location)
                    self._validate_url(next_url)
                    current_url = next_url
                else:
                    raise FetchError(f"Too many redirects ({max_redirects}) fetching {url}")

                assert response is not None  # loop always assigns before break/raise
                try:
                    text = self._read_capped(response, current_url)
                finally:
                    response.close()
        except httpx.HTTPError as exc:
            raise FetchError(f"HTTP error fetching {url}: {exc}") from exc
        elapsed = time.monotonic() - start

        return FetchResult(
            url=url,
            final_url=current_url,
            status_code=response.status_code,
            headers=dict(response.headers),
            text=text,
            elapsed_seconds=elapsed,
        )
