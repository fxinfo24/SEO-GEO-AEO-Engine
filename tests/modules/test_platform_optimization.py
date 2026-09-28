"""
Tests for the platform_optimization scoring module (AIO/ChatGPT/Perplexity/
Gemini/Bing Copilot per-platform proxy scores).
"""
from __future__ import annotations

from seo_geo_aeo.core.parser import HeadingBlock, ParsedPage
from seo_geo_aeo.modules.platform_optimization import score_platform_optimization


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


def test_minimal_page_scores_low_across_every_platform():
    """Weak input: no FAQ, no dates, no same_as links, no schema. Every
    per-platform component should be near its floor and the corresponding
    findings should fire for each platform's top signal."""
    page = _make_page()

    result = score_platform_optimization(page)

    assert result.dimension == "platform_optimization"
    assert 0.0 <= result.score <= 100.0
    assert result.score < 30.0
    titles = {f.title for f in result.findings}
    assert "No question-phrased headings" in titles
    assert "No Wikipedia entity linked" in titles
    assert "No Reddit presence declared" in titles
    assert "No YouTube presence declared" in titles
    assert "No meta description" in titles


def test_rich_page_scores_well_across_platforms():
    """Strong input: FAQ-style headings, dated content, an author byline,
    a full same_as set, high schema score, and a fast fetch -- every
    platform component should pick up meaningful points."""
    faq_headings = [
        HeadingBlock(level=2, text=f"What is thing {i}?", following_text="A" * 100)
        for i in range(6)
    ]
    page = _make_page(
        headings=faq_headings,
        word_count=2500,
        meta_description="A sufficiently descriptive meta description.",
        author_byline_present=True,
        published_date="2026-01-01",
        images_total=4,
        images_missing_alt=0,
        raw_html="<html><body><table></table><ul><li>x</li></ul></body></html>",
    )
    same_as = {
        "wikipedia": "https://en.wikipedia.org/wiki/Example",
        "reddit": "https://reddit.com/r/example",
        "youtube": "https://youtube.com/example",
        "linkedin": "https://linkedin.com/company/example",
        "github": "https://github.com/example",
    }

    result = score_platform_optimization(
        page, schema_score=90.0, same_as_links=same_as, fetch_elapsed_seconds=0.5
    )

    assert result.score > 50.0
    assert result.raw["per_platform"]["gemini"] > 40.0
    assert result.raw["per_platform"]["bing_copilot"] > 30.0


def test_slow_fetch_flags_bing_copilot_finding():
    page = _make_page(meta_description="desc")

    result = score_platform_optimization(page, fetch_elapsed_seconds=5.0)

    assert any(f.title == "Slow response time" for f in result.findings)


def test_no_fetch_timing_skips_copilot_speed_component_without_crashing():
    """Missing field: fetch_elapsed_seconds=None (e.g. a rendered fetch that
    didn't record timing) must not raise and must not produce a spurious
    slow-response finding."""
    page = _make_page(meta_description="desc")

    result = score_platform_optimization(page, fetch_elapsed_seconds=None)

    assert not any(f.title == "Slow response time" for f in result.findings)


def test_score_is_mean_of_five_platform_components():
    page = _make_page()

    result = score_platform_optimization(page)

    per_platform = result.raw["per_platform"]
    assert len(per_platform) == 5
    expected_mean = round(sum(per_platform.values()) / 5, 1)
    assert result.score == expected_mean


def test_raw_lists_unmeasured_signals():
    """The module must be explicit about what it cannot measure (ranking
    position, backlinks, IndexNow, etc.) rather than implying full coverage."""
    page = _make_page()

    result = score_platform_optimization(page)

    assert "search ranking position (all platforms)" in result.raw["unmeasured"]
    assert "backlink authority / Domain Rating" in result.raw["unmeasured"]


def test_score_never_exceeds_100():
    faq_headings = [
        HeadingBlock(level=2, text=f"What is thing {i}?", following_text="stat " * 30)
        for i in range(10)
    ]
    page = _make_page(
        headings=faq_headings,
        word_count=5000,
        meta_description="x" * 150,
        author_byline_present=True,
        published_date="2026-01-01",
        modified_date="2026-06-01",
        images_total=10,
        images_missing_alt=0,
        h1_count=1,
        raw_html="<table></table><ul></ul>",
    )
    same_as = {p: f"https://{p}.com/x" for p in ("wikipedia", "reddit", "youtube", "linkedin", "github")}

    result = score_platform_optimization(
        page, schema_score=100.0, same_as_links=same_as, fetch_elapsed_seconds=0.1
    )

    assert result.score <= 100.0
    for component in result.raw["per_platform"].values():
        assert component <= 100.0
