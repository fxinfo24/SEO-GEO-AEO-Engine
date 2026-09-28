"""
Tests for the E-E-A-T (content_eeat) scoring module.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from seo_geo_aeo.core.parser import HeadingBlock, ParsedPage
from seo_geo_aeo.modules.eeat import score_eeat


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


def test_minimal_page_scores_low_and_flags_every_pillar():
    """Weak input: no author, no dates, no first-person language, no
    internal links, thin word count. Every one of the four pillars should
    be near its floor and raise its corresponding finding."""
    page = _make_page()

    result = score_eeat(page)

    assert result.dimension == "content_eeat"
    assert 0.0 <= result.score <= 100.0
    assert result.score < 30.0
    titles = {f.title for f in result.findings}
    assert "No first-person experience language detected" in titles
    assert "No author byline detected" in titles
    assert "Thin internal linking" in titles
    assert "No publish or modified date found" in titles


def test_strong_page_scores_high_across_all_pillars():
    """Strong input: first-person language, byline, sourced stats, dense
    internal linking, a recent date, canonical, and non-noindex robots."""
    headings = [
        HeadingBlock(
            level=2,
            text="Our approach",
            following_text=(
                "In our experience, we tested this ourselves. According to a study by "
                "researchers, 42% of results improved. We found this consistently."
            ),
        )
    ]
    page = _make_page(
        headings=headings,
        author_byline_present=True,
        internal_links=[f"https://example.com/{i}" for i in range(6)],
        external_links=["https://source.example.com/study"],
        canonical="https://example.com/",
        robots_meta="index, follow",
        meta_description="A description.",
        published_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        word_count=2000,
    )

    result = score_eeat(page, page_type_hint="blog")

    assert result.score > 60.0
    assert result.raw["expertise"] > 0.0
    assert result.raw["trustworthiness"] > 15.0


def test_noindex_page_is_critical_finding():
    page = _make_page(robots_meta="noindex, nofollow")

    result = score_eeat(page)

    assert any(f.title == "Page is noindexed" and f.severity.value == "critical" for f in result.findings)


def test_stale_content_flagged_past_24_months():
    """Boundary: a modified_date more than 730 days old must trigger the
    staleness finding even though a date IS present."""
    old_date = (datetime.now(UTC) - timedelta(days=800)).strftime("%Y-%m-%d")
    page = _make_page(modified_date=old_date)

    result = score_eeat(page)

    assert any(f.title == "Content not updated in 24+ months" for f in result.findings)


def test_recent_content_not_flagged_stale():
    recent_date = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
    page = _make_page(modified_date=recent_date)

    result = score_eeat(page)

    assert not any(f.title == "Content not updated in 24+ months" for f in result.findings)


def test_unparseable_date_treated_as_missing_not_crash():
    """Missing/malformed field: an unparseable date string must not raise --
    it should fall through to the 'no date found' branch."""
    page = _make_page(published_date="not-a-real-date")

    result = score_eeat(page)

    assert any(f.title == "No publish or modified date found" for f in result.findings)


def test_below_word_count_floor_flagged_per_page_type_hint():
    page = _make_page(word_count=50)

    result = score_eeat(page, page_type_hint="pillar")

    assert any("word-count floor" in f.title for f in result.findings)
    assert "50 words" in next(f.detail for f in result.findings if "word-count floor" in f.title)


def test_score_components_never_exceed_25_each():
    """Each of the four pillars is capped at 25 points; verify the raw
    breakdown respects that cap even with maximal signal."""
    headings = [
        HeadingBlock(level=2, text=f"H{i}", following_text="we our I've " * 20)
        for i in range(10)
    ]
    page = _make_page(
        headings=headings,
        author_byline_present=True,
        internal_links=[f"https://example.com/{i}" for i in range(50)],
        external_links=[f"https://ext.example.com/{i}" for i in range(50)],
        canonical="https://example.com/",
        robots_meta="index, follow",
        meta_description="A description.",
        published_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        word_count=5000,
    )

    result = score_eeat(page)

    for pillar in ("experience", "expertise", "authoritativeness", "trustworthiness"):
        assert result.raw[pillar] <= 25.0
    assert result.score <= 100.0
