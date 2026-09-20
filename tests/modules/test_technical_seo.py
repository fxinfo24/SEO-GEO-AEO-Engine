"""
Tests for the technical SEO scoring module.
"""
from __future__ import annotations

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.technical_seo import score_technical_seo


def test_score_technical_seo_minimal_page():
    """Test scoring on a minimal page with almost no technical SEO."""
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
    
    result = score_technical_seo(page)
    
    assert result.dimension == "technical_seo"
    # Should have a moderate score due to good crawlability/performance but poor mobile/architecture
    assert result.score >= 60.0 and result.score <= 80.0
    assert len(result.findings) > 0


def test_score_technical_seo_with_basic_elements():
    """Test scoring with some basic technical SEO elements."""
    page = ParsedPage(
        url="https://example.com",
        title="This is a test page with proper length for SEO",
        meta_description="This is a meta description that is long enough to be good for SEO purposes",
        canonical="https://example.com/",
        robots_meta="index, follow",
        h1_count=1,
        headings=[
            # Note: In a real test, we'd need proper HeadingBlock objects with following_text
            # For simplicity, we'll just test what we can
        ],
        word_count=100,
        schema_blocks=[],
        images_total=2,
        images_missing_alt=0,
        internal_links=["https://example.com/about", "https://example.com/contact"],
        external_links=[],
        open_graph={
            "og:title": "Test Page",
            "og:description": "A test page for SEO",
            "og:image": "https://example.com/image.jpg"
        },
        has_viewport_meta=True,
        author_byline_present=False,
        published_date=None,
        modified_date=None,
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
</html>"""
    )
    
    result = score_technical_seo(page)
    
    assert result.dimension == "technical_seo"
    # Should have a reasonable score with these basic elements
    assert result.score > 30.0
    # Might still have findings for missing elements
    assert isinstance(result.findings, list)