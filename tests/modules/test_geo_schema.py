"""
Tests for the structured-data (schema.org JSON-LD) scoring module.
"""
from __future__ import annotations

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.geo_schema import score_schema


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


def test_no_schema_blocks_scores_zero_with_high_finding():
    """No JSON-LD at all is the weakest possible input: score must be 0 and
    the missing-structured-data finding must be HIGH, not buried as LOW."""
    page = _make_page(schema_blocks=[])

    result = score_schema(page)

    assert result.dimension == "schema"
    assert result.score == 0.0
    assert any(f.title == "No structured data found" for f in result.findings)


def test_parse_error_only_scores_ten_not_zero():
    """A block that failed to parse is a different, slightly-less-bad case
    than no schema at all -- the module should distinguish them (10 vs 0)."""
    page = _make_page(schema_blocks=[{"@parse_error": True, "raw": "{not json"}])

    result = score_schema(page)

    assert result.score == 10.0
    assert any(f.title == "Invalid JSON-LD block" for f in result.findings)


def test_rich_organization_schema_scores_high():
    """Strong input: Organization with 3+ sameAs links, an Article with an
    author, WebSite and BreadcrumbList -- should score well above the
    no-schema floor and raise no 'missing Organization' finding."""
    page = _make_page(
        schema_blocks=[
            {
                "@type": "Organization",
                "sameAs": [
                    "https://en.wikipedia.org/wiki/Example",
                    "https://www.linkedin.com/company/example",
                    "https://github.com/example",
                ],
            },
            {"@type": "WebSite"},
            {"@type": "BreadcrumbList"},
            {"@type": "BlogPosting", "author": {"@type": "Person", "name": "Jane Doe"}},
        ]
    )

    result = score_schema(page)

    assert result.dimension == "schema"
    assert result.score > 60.0
    assert result.raw["has_organization_or_person"] is True
    assert result.raw["has_article_with_author"] is True
    assert not any(f.title == "No Organization/LocalBusiness/Person schema" for f in result.findings)


def test_score_never_exceeds_100():
    """Boundary: stacking every high-value type plus max sameAs links must
    still clamp to 100, not overflow past it."""
    many_types = [
        "Organization", "LocalBusiness", "Article", "BlogPosting", "NewsArticle",
        "Product", "FAQPage", "HowTo", "SoftwareApplication", "WebSite",
        "BreadcrumbList", "Person",
    ]
    page = _make_page(
        schema_blocks=[
            {
                "@type": t,
                "sameAs": [f"https://example.com/{i}" for i in range(5)],
                "author": {"name": "A"},
            }
            for t in many_types
        ]
    )

    result = score_schema(page)

    assert result.score <= 100.0


def test_deprecated_schema_type_flagged():
    """A deprecated type (e.g. SpecialAnnouncement) must produce a specific
    LOW finding naming it, independent of overall score."""
    page = _make_page(schema_blocks=[{"@type": "SpecialAnnouncement"}])

    result = score_schema(page)

    assert any("SpecialAnnouncement" in f.title for f in result.findings)


def test_organization_with_few_same_as_links_flags_thin_entity_graph():
    """Missing fields / weak input: an Organization block present but with
    fewer than 3 sameAs links should still raise the 'few sameAs' finding."""
    page = _make_page(schema_blocks=[{"@type": "Organization", "sameAs": ["https://x.com/a"]}])

    result = score_schema(page)

    assert any(f.title == "Few or no sameAs links" for f in result.findings)


def test_raw_metadata_reports_types_found():
    page = _make_page(schema_blocks=[{"@type": "Product"}, {"@type": ["Article", "BlogPosting"]}])

    result = score_schema(page)

    assert result.raw["types_found"] == ["Article", "BlogPosting", "Product"]
