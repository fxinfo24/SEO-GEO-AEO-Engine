"""SSRF-guarded, robots.txt-aware HTTP fetcher for the SEO/GEO/AEO engine.

Every external fetch in this engine goes through `SafeFetcher`. It exists to
close the gap every skill in the source masterlist left open: none of them
validate that a user-supplied URL doesn't resolve to internal infrastructure
before fetching it.
"""

from __future__ import annotations

import ipaddress
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

DEFAULT_USER_AGENT = "SEOGeoAeoEngine/1.0 (+https://example.invalid/bot)"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RATE_LIMIT_SECONDS = 1.0


class UnsafeURLError(ValueError):
    """Raised when a target URL resolves to a disallowed network range."""


class FetchError(RuntimeError):
    """Raised for any non-SSRF fetch failure (timeout, DNS, HTTP error)."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    text: str
    elapsed_seconds: float


@dataclass
class SafeFetcher:
    """Fetches pages while enforcing SSRF protection, robots.txt, and rate limits.

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
    """

    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS
    respect_robots: bool = True
    _last_request_at: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _robots_cache: dict[str, urllib.robotparser.RobotFileParser] = field(
        default_factory=dict, init=False, repr=False
    )

    def _assert_public_host(self, hostname: str) -> None:
        """Resolve `hostname` and reject loopback/link-local/private ranges."""
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Could not resolve host: {hostname}") from exc

        for info in infos:
            ip_str = info[4][0]
            ip = ipaddress.ip_address(ip_str)
            if (
                ip.is_loopback
                or ip.is_link_local
                or ip.is_private
                or ip.is_reserved
                or ip.is_multicast
                or ip.is_unspecified
            ):
                raise UnsafeURLError(
                    f"Refusing to fetch {hostname}: resolves to non-public address {ip_str}"
                )

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

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        """Fetch `url`, enforcing SSRF checks, robots.txt, and per-host rate limiting.

        Raises
        ------
        UnsafeURLError
            If the URL is malformed or resolves to a non-public address.
        FetchError
            If robots.txt disallows the fetch, or the HTTP request itself fails.
        """
        hostname = self._validate_url(url)

        should_check_robots = (
            self.respect_robots if respect_robots_override is None else respect_robots_override
        )
        if should_check_robots and not self.is_allowed(url):
            raise FetchError(f"Disallowed by robots.txt: {url}")

        self._throttle(hostname)

        start = time.monotonic()
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=self.timeout_seconds,
                headers={"User-Agent": self.user_agent},
            ) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"HTTP error fetching {url}: {exc}") from exc
        elapsed = time.monotonic() - start

        # Re-validate the final URL in case a redirect pointed at internal infra.
        self._validate_url(str(response.url))

        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
            elapsed_seconds=elapsed,
        )
