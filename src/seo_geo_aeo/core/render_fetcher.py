"""Headless-browser fetcher for JavaScript-rendered pages (React/Vue/SPA sites).

`SafeFetcher` sees only the server-delivered HTML shell — for a client-rendered
site that's often just a `<div id="root">` and a script tag, which silently
produces near-empty scores rather than an error (see the Vigil finding: a
GEO audit of a Vite/React marketing site scored 1 word of body content
because nothing executes the JS that renders the real page).

`RenderFetcher` launches headless Chromium via Playwright, executes the
page's JS, and returns the fully rendered DOM as `FetchResult.text` — a
drop-in swap for `SafeFetcher` at the single call site that fetches the
page being audited.

SECURITY: SSRF protection is NOT weakened just because a browser is
involved. Every navigation and every sub-resource request the page makes
(images, XHR/fetch calls, redirects) is intercepted via Playwright's request
routing and validated through the exact same `SafeFetcher.validate_url`
check used by the plain HTTP path — a rendered page cannot be used to pivot
into internal infrastructure via a redirect or a JS-initiated fetch() to a
private IP. Robots.txt is still checked via SafeFetcher before rendering.

Requires: `pip install playwright && playwright install chromium` (the
browser binary is a separate ~150MB download the pip package does not
include).
"""

from __future__ import annotations

import time
from typing import Self

from seo_geo_aeo.core.fetcher import (
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    FetchError,
    FetchResult,
    SafeFetcher,
    UnsafeURLError,
)


class RenderFetcher:
    """Fetches a JS-rendered page's final DOM via headless Chromium.

    Reuses a `SafeFetcher` internally for URL validation and robots.txt —
    this class only replaces the network-fetch step, not the safety checks.
    Launches the browser lazily on first `fetch()` call and keeps it open
    across calls; call `close()` (or use as a context manager) when done.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        respect_robots: bool = True,
        wait_until: str = "load",
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.respect_robots = respect_robots
        self.wait_until = wait_until
        self._safe_fetcher = SafeFetcher(user_agent=user_agent, respect_robots=respect_robots)
        self._playwright = None
        self._browser = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(
                "playwright is not installed. Run: pip install playwright && "
                "playwright install chromium"
            ) from exc

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            self._playwright.stop()
            self._playwright = None
            raise FetchError(
                f"Failed to launch headless Chromium: {exc}. If this is a fresh install, run: "
                "playwright install chromium"
            ) from exc

    def _validate_request_url(self, url: str) -> bool:
        """Return True if `url` passes the SSRF guard; used as the route filter."""
        try:
            self._safe_fetcher.validate_url(url)
            return True
        except UnsafeURLError:
            return False

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        """Render `url` in headless Chromium and return the final DOM as text.

        Raises the same UnsafeURLError / FetchError types as SafeFetcher.fetch,
        so callers can handle both fetchers identically.
        """
        # Initial validation — identical rules to the plain HTTP path.
        self._safe_fetcher.validate_url(url)

        should_check_robots = (
            self.respect_robots if respect_robots_override is None else respect_robots_override
        )
        if should_check_robots and not self._safe_fetcher.is_allowed(url):
            raise FetchError(f"Disallowed by robots.txt: {url}")

        self._ensure_browser()
        assert self._browser is not None

        start = time.monotonic()
        context = self._browser.new_context(user_agent=self.user_agent)
        page = context.new_page()

        blocked_urls: list[str] = []

        def _route_handler(route, request):  # type: ignore[no-untyped-def]
            if not self._validate_request_url(request.url):
                blocked_urls.append(request.url)
                route.abort()
            else:
                route.continue_()

        page.route("**/*", _route_handler)

        try:
            response = page.goto(
                url, wait_until=self.wait_until, timeout=self.timeout_seconds * 1000
            )
            final_url = page.url
            # Re-validate post-redirect/post-JS-navigation destination.
            self._safe_fetcher.validate_url(final_url)

            content = page.content()
            status_code = response.status if response else 0
            headers = dict(response.headers) if response else {}
        except UnsafeURLError:
            raise
        except Exception as exc:
            raise FetchError(f"Render fetch failed for {url}: {exc}") from exc
        finally:
            context.close()

        elapsed = time.monotonic() - start

        if blocked_urls:
            # Not fatal — the page may still have rendered useful content —
            # but this is worth knowing about for anyone debugging a score.
            import logging

            logging.getLogger(__name__).warning(
                "RenderFetcher blocked %d sub-resource request(s) to non-public hosts on %s",
                len(blocked_urls),
                url,
            )

        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=status_code,
            headers=headers,
            text=content,
            elapsed_seconds=elapsed,
        )
