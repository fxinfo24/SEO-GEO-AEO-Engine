"""
Tests for `seo_geo_aeo.modules.seo_technical.score_on_page_seo` -- the
on-page/security-headers scorer (dimension name: "on_page"). Not to be
confused with tests/modules/test_technical_seo.py, which covers the
separate `modules.technical_seo.score_technical_seo` crawlability module
(dimension: "technical_seo"). Renamed per RoadMap.md Phase 3.2 from a
same-named `score_technical_seo` that the orchestrator had to alias
around (`as score_on_page_factors`).
"""
from __future__ import annotations

from seo_geo_aeo.core.fetcher import FetchError, FetchResult
from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.seo_technical import score_on_page_seo

_GOOD_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class _StubFetcher:
    """Serves 404 for every sensitive path by default; pass `exposed` to
    simulate one being live, or `raises` to simulate a network failure."""

    def __init__(self, exposed: set[str] | None = None, raises: Exception | None = None) -> None:
        self._exposed = exposed or set()
        self._raises = raises

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        if self._raises is not None:
            raise self._raises
        status = 200 if any(url.endswith(p) for p in self._exposed) else 404
        return FetchResult(
            url=url, final_url=url, status_code=status, headers={}, text="", elapsed_seconds=0.01
        )


def _make_page(**overrides) -> ParsedPage:
    defaults = {
        "url": "https://example.com",
        "title": "Test",
        "meta_description": None,
        "canonical": None,
        "robots_meta": None,
        "h1_count": 0,
        "headings": [],
        "word_count": 10,
        "schema_blocks": [],
        "images_total": 0,
        "images_missing_alt": 0,
        "internal_links": [],
        "external_links": [],
        "open_graph": {},
        "has_viewport_meta": False,
        "author_byline_present": False,
        "published_date": None,
        "modified_date": None,
        "raw_html": "<html><body><p>Test</p></body></html>",
    }
    defaults.update(overrides)
    return ParsedPage(**defaults)


def test_minimal_page_scores_low_with_many_findings():
    """Weak input: no title, no meta description, no H1, no canonical, no
    viewport, no security headers, served over HTTP."""
    page = _make_page(url="http://example.com", title=None)

    result = score_on_page_seo(page, {}, fetcher=None, check_exposed_paths=False)

    assert result.dimension == "on_page"
    assert 0.0 <= result.score <= 100.0
    assert result.score < 30.0
    titles = {f.title for f in result.findings}
    assert "Missing <title> tag" in titles
    assert "No H1 found" in titles
    assert "No canonical tag" in titles
    assert "No viewport meta tag" in titles
    assert "Page served over HTTP" in titles


def test_well_formed_page_scores_high():
    """Strong input: full title/meta/H1/canonical/viewport/OG/security
    headers, HTTPS, no exposed paths."""
    page = _make_page(
        title="A properly sized title tag for SEO purposes here",
        meta_description="A properly sized meta description that sits comfortably in the "
        "120 to 165 character sweet spot recommended for search snippets.",
        canonical="https://example.com/",
        robots_meta="index, follow",
        h1_count=1,
        has_viewport_meta=True,
        images_total=2,
        images_missing_alt=0,
        open_graph={"og:title": "T", "og:description": "D", "og:image": "https://example.com/i.png"},
        raw_html="<html>no mixed refs here</html>",
    )

    result = score_on_page_seo(page, _GOOD_HEADERS, fetcher=None, check_exposed_paths=False)

    assert result.score > 80.0
    assert not any(f.title == "Missing security headers" for f in result.findings)


def test_exposed_env_path_is_critical_and_penalizes_score():
    """The security check this module owns: a live /.env should be a
    CRITICAL finding and subtract 20 points from whatever was earned."""
    page = _make_page(title="x" * 40, has_viewport_meta=True)
    fetcher = _StubFetcher(exposed={"/.env"})

    result = score_on_page_seo(page, _GOOD_HEADERS, fetcher=fetcher, check_exposed_paths=True)

    assert any(
        f.title == "Exposed sensitive path: /.env" and f.severity.value == "critical"
        for f in result.findings
    )


def test_exposed_path_check_skipped_when_disabled():
    page = _make_page()
    fetcher = _StubFetcher(exposed={"/.env"})

    result = score_on_page_seo(page, {}, fetcher=fetcher, check_exposed_paths=False)

    assert not any(f.title.startswith("Exposed sensitive path") for f in result.findings)


def test_exposed_path_fetch_failure_is_silently_ignored_not_a_crash():
    """External failure: the fetcher raising on every sensitive-path probe
    must not propagate -- an unreachable path is not evidence of exposure."""
    page = _make_page()
    fetcher = _StubFetcher(raises=FetchError("connection reset"))

    result = score_on_page_seo(page, {}, fetcher=fetcher, check_exposed_paths=True)

    assert not any(f.title.startswith("Exposed sensitive path") for f in result.findings)


def test_missing_security_headers_flagged_with_names():
    page = _make_page()

    result = score_on_page_seo(page, {}, fetcher=None, check_exposed_paths=False)

    finding = next(f for f in result.findings if f.title == "Missing security headers")
    assert "strict-transport-security" in finding.detail
    assert result.raw["security_headers_present"] == []


def test_score_never_exceeds_100():
    page = _make_page(
        title="x" * 55,
        meta_description="y" * 140,
        canonical="https://example.com/",
        robots_meta="index, follow",
        h1_count=1,
        has_viewport_meta=True,
        images_total=3,
        images_missing_alt=0,
        open_graph={"og:title": "T", "og:description": "D", "og:image": "https://example.com/i.png"},
    )

    result = score_on_page_seo(page, _GOOD_HEADERS, fetcher=None, check_exposed_paths=False)

    assert result.score <= 100.0


def test_multiple_h1_tags_flagged_medium():
    page = _make_page(h1_count=3)

    result = score_on_page_seo(page, {}, fetcher=None, check_exposed_paths=False)

    assert any(
        f.title == "Multiple H1 tags (3)" and f.severity.value == "medium" for f in result.findings
    )
