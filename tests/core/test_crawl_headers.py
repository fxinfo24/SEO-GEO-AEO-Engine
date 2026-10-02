"""Response headers must survive the multi-page crawl.

Regression test for a real false finding in the PathosBay report: the
site-wide audit reported "Missing security headers: strict-transport-
security, x-content-type-options, x-frame-options" on all 20 pages of a
site that serves all three.

Root cause: `crawl_site()` fetched `result.headers`, used them only for the
content-type check, then discarded them — `CrawlResult` carried
`list[ParsedPage]` with no header storage. `run_site_audit()` therefore
called `_score_page_dimensions()` without `fetch_headers`, which defaults
to `None` and is coerced to `{}` in `score_on_page_seo()`. Every crawled
page scored as if the server sent no security headers at all.

The single-page path (`run_audit`) passed `fetch_result.headers` correctly,
which is why the bug only ever appeared in site-wide runs.
"""

from __future__ import annotations

from seo_geo_aeo.core.crawler import crawl_site
from seo_geo_aeo.core.fetcher import FetchResult
from seo_geo_aeo.core.orchestrator import _score_page_dimensions

_HTML = """<html>
<head>
    <title>A perfectly adequate title for this test fixture page here</title>
    <meta name="description" content="A description long enough to satisfy the minimum length check in the on-page module.">
    <link rel="canonical" href="https://example.com/">
</head>
<body>
    <h1>Main Heading</h1>
    <p>Some reasonably long body copy so word count checks don't bottom out at zero.</p>
    <a href="/about">About</a>
</body>
</html>"""

_HEADERS = {
    "content-type": "text/html; charset=UTF-8",
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "content-security-policy": "default-src 'self'",
    "referrer-policy": "strict-origin-when-cross-origin",
}


class _HeaderFetcher:
    user_agent = "test-agent"
    respect_robots = True

    def __init__(self) -> None:
        self.calls: list[str] = []

    def is_allowed(self, url: str) -> bool:
        return True

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        self.calls.append(url)
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            headers=dict(_HEADERS),
            text=_HTML,
            elapsed_seconds=0.05,
        )


def test_crawl_retains_response_headers_per_page() -> None:
    """CrawlResult must expose the headers the origin actually sent."""
    result = crawl_site("https://example.com/", fetcher=_HeaderFetcher(), max_pages=2)

    assert result.pages, "crawl returned no pages"
    for page in result.pages:
        assert page.response_headers, f"{page.url} lost its response headers"
        assert "strict-transport-security" in page.response_headers


def test_crawled_page_scores_as_if_headers_present() -> None:
    """A page crawled from a server that sends all required headers must not
    be reported as missing any. This is the exact finding the PathosBay report
    got wrong."""
    result = crawl_site("https://example.com/", fetcher=_HeaderFetcher(), max_pages=2)
    page = result.pages[0]

    # Mirror how run_site_audit() scores a crawled page: from the page's
    # own headers, not from an empty dict.
    scores = _score_page_dimensions(
        page,
        "seo",
        fetch_headers=page.response_headers,
    )

    titles = {f.title for ds in scores.values() for f in ds.findings}
    assert "Missing security headers" not in titles


def test_partially_headered_page_names_only_the_truly_missing_header() -> None:
    """The PathosBay case exactly: HSTS and x-content-type-options are sent,
    x-frame-options is not. The finding must name one header, not three.

    Before the fix the crawler dropped headers entirely and this produced
    "Missing: strict-transport-security, x-content-type-options,
    x-frame-options" on a server sending two of the three.
    """

    class _NoFrameOptions(_HeaderFetcher):
        def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
            headers = {k: v for k, v in _HEADERS.items() if k != "x-frame-options"}
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                headers=headers,
                text=_HTML,
                elapsed_seconds=0.05,
            )

    result = crawl_site("https://example.com/", fetcher=_NoFrameOptions(), max_pages=2)
    page = result.pages[0]
    scores = _score_page_dimensions(page, "seo", fetch_headers=page.response_headers)

    details = [
        f.detail
        for ds in scores.values()
        for f in ds.findings
        if f.title == "Missing security headers"
    ]
    assert details, "expected a finding — x-frame-options genuinely is absent"
    assert "x-frame-options" in details[0]
    assert "strict-transport-security" not in details[0], (
        "HSTS was sent by the server; reporting it missing means headers "
        "were dropped somewhere between fetch and score"
    )


def test_site_audit_passes_headers_for_every_crawled_page() -> None:
    """Guard the orchestrator itself: the site-wide loop must forward each
    page's headers. Regressing here re-breaks the whole report."""
    import inspect

    from seo_geo_aeo.core import orchestrator

    src = inspect.getsource(orchestrator.run_site_audit)
    assert "fetch_headers=page.response_headers" in src, (
        "run_site_audit() no longer forwards crawled response headers; "
        "every site-wide report will claim all security headers are missing"
    )

def test_crawl_does_not_score_error_pages() -> None:
    """A 404 is not a page to audit.

    SafeFetcher returns a FetchResult with status_code set rather than
    raising, and the crawler filtered only on content-type -- so every 404
    the BFS reached got parsed and scored. That is where the PathosBay
    report's "No canonical tag" and "No structured data found" findings
    came from: /wp-json/, /shop/ and other non-pages, not real articles.
    """

    class _StatusFetcher:
        user_agent = "test-agent"
        respect_robots = True

        def is_allowed(self, url: str) -> bool:
            return True

        def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
            code = 404 if "missing" in url else 200
            text = _HTML.replace("/about", "/missing-page") if url.endswith(".com/") else _HTML
            return FetchResult(
                url=url,
                final_url=url,
                status_code=code,
                headers={"content-type": "text/html"},
                text=text,
                elapsed_seconds=0.01,
            )

    result = crawl_site(
        "https://example.com/", fetcher=_StatusFetcher(), max_pages=10
    )
    # Guard against a vacuous pass: the 404 must actually have been reached.
    assert "https://example.com/missing-page" in result.failed_urls
    scored = [p.url for p in result.pages]
    assert "https://example.com/missing-page" not in scored
    assert all("missing" not in u for u in scored), (
        f"error pages were scored as if they were content: {scored}"
    )


def test_crawl_records_non_200_urls() -> None:
    """Skipped-by-status URLs must be reported, not silently dropped."""

    class _StatusFetcher:
        user_agent = "test-agent"
        respect_robots = True

        def is_allowed(self, url: str) -> bool:
            return True

        def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
            code = 404 if "missing" in url else 200
            text = _HTML.replace("/about", "/missing-page") if url.endswith(".com/") else _HTML
            return FetchResult(
                url=url,
                final_url=url,
                status_code=code,
                headers={"content-type": "text/html"},
                text=text,
                elapsed_seconds=0.01,
            )

    result = crawl_site("https://example.com/", fetcher=_StatusFetcher(), max_pages=10)
    assert "https://example.com/missing-page" in result.failed_urls
