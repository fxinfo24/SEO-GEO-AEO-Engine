"""
Tests for the live citation scoring module.
"""
from __future__ import annotations

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.live_citation import score_live_citation


def test_score_live_citation():
    """Test that the live citation module returns a valid score."""
    page = ParsedPage(
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
        raw_html="<html><body><p>Test</p></body></html>"
    )
    
    result = score_live_citation(page)
    
    assert result.dimension == "live_citation"
    # Score should be a valid float between 0 and 100
    assert isinstance(result.score, (int, float))
    assert 0.0 <= result.score <= 100.0
    assert len(result.findings) >= 1
    # Should have a finding - either API key not configured or AI citation likelihood
    finding_title = result.findings[0].title
    assert "AI citation likelihood" in finding_title or "OpenRouter API key" in finding_title
    # If API was used, should indicate openrouter provider
    if "OpenRouter API key" not in finding_title:
        assert result.raw.get("api_provider") == "openrouter"