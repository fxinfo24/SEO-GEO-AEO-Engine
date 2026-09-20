"""
Tests for the AI citability scoring module.
"""
from __future__ import annotations

from seo_geo_aeo.core.parser import HeadingBlock, ParsedPage
from seo_geo_aeo.modules.geo_citability import score_citability


def test_score_citability_empty_headings():
    """Test scoring when no headings are present."""
    page = ParsedPage(
        url="https://example.com",
        title="Test",
        meta_description=None,
        canonical=None,
        robots_meta=None,
        h1_count=0,
        headings=[],
        word_count=100,
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
        raw_html="<html><body><p>Some content</p></body></html>"
    )
    
    result = score_citability(page)
    
    assert result.dimension == "ai_citability"
    assert result.score == 20.0  # Default score for no headings
    assert len(result.findings) == 1
    assert result.findings[0].title == "No heading structure found"


def test_score_citability_headings_no_content():
    """Test scoring when headings exist but have no content."""
    heading = HeadingBlock(
        level=2,
        text="Test Heading",
        following_text=""  # No following text
    )
    
    page = ParsedPage(
        url="https://example.com",
        title="Test",
        meta_description=None,
        canonical=None,
        robots_meta=None,
        h1_count=1,
        headings=[heading],
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
        raw_html="<html><body><h2>Test Heading</h2></body></html>"
    )
    
    result = score_citability(page)
    
    assert result.dimension == "ai_citability"
    assert result.score == 25.0  # Default score for headings with no content
    assert len(result.findings) == 1
    assert result.findings[0].title == "Headings present but no scorable content beneath them"


def test_score_citability_with_content():
    """Test scoring with proper content blocks."""
    heading = HeadingBlock(
        level=2,
        text="What is SEO?",
        following_text="SEO (Search Engine Optimization) is the practice of increasing the quantity and quality of traffic to your website through organic search results. According to a 2023 study, 67% of marketers say SEO is effective for generating leads."
    )
    
    page = ParsedPage(
        url="https://example.com",
        title="Test",
        meta_description=None,
        canonical=None,
        robots_meta=None,
        h1_count=1,
        headings=[heading],
        word_count=30,
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
        raw_html="<html><body><h2>What is SEO?</h2><p>SEO (Search Engine Optimization) is the practice of increasing the quantity and quality of traffic to your website through organic search results. According to a 2023 study, 67% of marketers say SEO is effective for generating leads.</p></body></html>"
    )
    
    result = score_citability(page)
    
    assert result.dimension == "ai_citability"
    # Should score reasonably well due to definition pattern, stats, and good length
    assert result.score > 50.0
    # Might have findings for improvement areas
    assert isinstance(result.findings, list)