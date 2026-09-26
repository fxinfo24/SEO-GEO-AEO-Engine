"""
Tests for the technical SEO scoring module.
"""
from __future__ import annotations

from seo_geo_aeo.core.fetcher import FetchError
from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.technical_seo import score_technical_seo


class _StubFetcher:
    """Minimal duck-typed stand-in for SafeFetcher's is_allowed() — keeps
    these tests offline/deterministic instead of hitting real robots.txt."""

    def __init__(self, allowed: bool = True, raises: Exception | None = None) -> None:
        self._allowed = allowed
        self._raises = raises

    def is_allowed(self, url: str) -> bool:
        if self._raises is not None:
            raise self._raises
        return self._allowed


def _make_page(**overrides) -> ParsedPage:
    defaults = dict(
        url="https://example.com",
        title="Test",
        meta_description=None,
        canonical=None,
        robots_meta=None,
        h1_count=0,
        headings=[],
        word_count=10,
        schema_blocks=[],
        images_total=0,
        images_missing_alt=0,
        internal_links=[],
        external_links=[],
        open_graph={},
        has_viewport_meta=False,
        author_byline_present=False,
        published_date=None,
        modified_date=None,
        raw_html="<html><body><p>Test</p></body></html>",
    )
    defaults.update(overrides)
    return ParsedPage(**defaults)


def test_score_technical_seo_minimal_page():
    """A minimal page with no technical SEO signals should score low, not
    a false-inflated ~60-80 from a crawlability check that never ran."""
    page = _make_page()

    result = score_technical_seo(page, fetcher=_StubFetcher(allowed=True))

    assert result.dimension == "technical_seo"
    assert 0.0 <= result.score <= 100.0
    assert len(result.findings) > 0


def test_score_technical_seo_with_basic_elements():
    """Test scoring with some basic technical SEO elements present."""
    page = _make_page(
        title="This is a test page with proper length for SEO",
        meta_description="This is a meta description that is long enough to be good for SEO purposes",
        canonical="https://example.com/",
        robots_meta="index, follow",
        h1_count=1,
        word_count=100,
        images_total=2,
        images_missing_alt=0,
        internal_links=["https://example.com/about", "https://example.com/contact"],
        open_graph={
            "og:title": "Test Page",
            "og:description": "A test page for SEO",
            "og:image": "https://example.com/image.jpg",
        },
        has_viewport_meta=True,
        raw_html="""<html>
<head>
    <title>This is a test page with proper length for SEO</title>
    <meta name="description" content="This is a meta description that is long enough to be good for SEO purposes">
    <link rel="canonical" href="https://example.com/">
    <meta name="robots" content="index, follow">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta property="og:title" content="Test Page">
    <meta property="og:description" content="A test page for SEO">
    <meta property="og:image" content="https://example.com/image.jpg">
</head>
<body>
    <h1>Main Heading</h1>
    <p>This is some content.</p>
    <a href="/about">About</a>
    <a href="/contact">Contact</a>
    <img src="image1.jpg" alt="Image 1">
    <img src="image2.jpg" alt="Image 2">
</body>
</html>""",
    )

    result = score_technical_seo(page, fetcher=_StubFetcher(allowed=True))

    assert result.dimension == "technical_seo"
    assert result.score > 30.0
    assert isinstance(result.findings, list)


def test_crawlability_actually_checks_robots_txt_disallow():
    """Regression test: crawlability used to be a no-op that always returned
    ~1.0 regardless of robots.txt. A disallowed page must now score 0 on
    the crawlability component and raise a critical finding."""
    page = _make_page()

    result = score_technical_seo(page, fetcher=_StubFetcher(allowed=False))

    assert result.raw["crawlability"] == 0.0
    assert any(
        f.title == "Page disallowed by robots.txt" for f in result.findings
    ), "Expected a robots.txt-disallow finding when is_allowed() returns False"


def test_crawlability_handles_robots_txt_fetch_failure_gracefully():
    """A fetcher that raises FetchError while checking robots.txt should
    degrade the crawlability score and surface a finding, not crash."""
    page = _make_page()

    result = score_technical_seo(
        page, fetcher=_StubFetcher(raises=FetchError("robots.txt unreachable"))
    )

    assert result.raw["crawlability"] == 80.0  # 1.0 - 0.2, as a fraction * 100
    assert any(f.title == "Could not fetch robots.txt" for f in result.findings)
