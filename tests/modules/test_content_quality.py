"""
Tests for the content quality scoring module.
"""
from __future__ import annotations

from seo_geo_aeo.core.parser import HeadingBlock, ParsedPage
from seo_geo_aeo.modules.content_quality import score_content_quality


def test_score_content_quality_thin_content():
    """Test scoring on thin content."""
    heading = HeadingBlock(
        level=2,
        text="Test",
        following_text="Short content."
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
        raw_html="<html><body><h2>Test</h2><p>Short content.</p></body></html>"
    )
    
    result = score_content_quality(page)
    
    assert result.dimension == "content_quality"
    # Should be low score for thin content (adjusted threshold)
    assert result.score < 50.0
    assert len(result.findings) > 0


def test_score_content_quality_good_content():
    """Test scoring on reasonably good content."""
    heading1 = HeadingBlock(
        level=2,
        text="What is SEO?",
        following_text="SEO (Search Engine Optimization) is the practice of increasing the quantity and quality of traffic to your website through organic search results. According to a 2023 study by industry experts, 67% of marketers say SEO is effective for generating leads and improving online visibility. In our experience working with clients over the past 5 years, we've seen significant improvements when implementing comprehensive SEO strategies."
    )
    
    heading2 = HeadingBlock(
        level=2,
        text="How does SEO work?",
        following_text="Search engines use complex algorithms to determine which pages to show for any given query. These algorithms consider hundreds of factors including keywords, content quality, website speed, mobile-friendliness, and backlink profiles. The key components include on-page optimization, technical SEO, and off-page factors like link building and social signals."
    )
    
    page = ParsedPage(
        url="https://example.com",
        title="Understanding SEO: A Comprehensive Guide",
        meta_description="Learn what SEO is, how it works, and why it's essential for online success. This comprehensive guide covers everything from basics to advanced strategies.",
        canonical=None,
        robots_meta=None,
        h1_count=1,
        headings=[heading1, heading2],
        word_count=150,
        schema_blocks=[],
        images_total=1,
        images_missing_alt=0,
        internal_links=[],
        external_links=[],
        open_graph={},
        has_viewport_meta=False,
        author_byline_present=True,  # This should help with expertise score
        published_date=None,
        modified_date=None,
        raw_html="""<html>
<head>
    <title>Understanding SEO: A Comprehensive Guide</title>
    <meta name="description" content="Learn what SEO is, how it works, and why it's essential for online success. This comprehensive guide covers everything from basics to advanced strategies.">
</head>
<body>
    <h1>Understanding SEO: A Comprehensive Guide</h1>
    <h2>What is SEO?</h2>
    <p>SEO (Search Engine Optimization) is the practice of increasing the quantity and quality of traffic to your website through organic search results. According to a 2023 study by industry experts, 67% of marketers say SEO is effective for generating leads and improving online visibility. In our experience working with clients over the past 5 years, we've seen significant improvements when implementing comprehensive SEO strategies.</p>
    <h2>How does SEO work?</h2>
    <p>Search engines use complex algorithms to determine which pages to show for any given query. These algorithms consider hundreds of factors including keywords, content quality, website speed, mobile-friendliness, and backlink profiles. The key components include on-page optimization, technical SEO, and off-page factors like link building and social signals.</p>
    <img src="seo-diagram.jpg" alt="SEO diagram showing how search engines work">
</body>
</html>"""
    )
    
    result = score_content_quality(page)
    
    assert result.dimension == "content_quality"
    # Should have a decent score with good content elements
    assert result.score > 40.0
    assert isinstance(result.findings, list)


def test_no_first_person_language_produces_finding():
    """Regression test: this branch used to be dead code (`if matches == 0`
    nested inside `if matches > 0`), so the finding could never fire no
    matter how third-person the content was."""
    heading = HeadingBlock(
        level=2,
        text="What is SEO?",
        following_text=(
            "Search engine optimization is a marketing discipline focused on growing "
            "visibility in organic search results. The process involves technical and "
            "creative elements required to improve rankings, drive traffic, and increase "
            "awareness in search engines."
        ),
    )
    page = ParsedPage(
        url="https://example.com",
        title="SEO Overview",
        meta_description=None,
        canonical=None,
        robots_meta=None,
        h1_count=1,
        headings=[heading],
        word_count=60,
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
        raw_html="<html><body><h2>What is SEO?</h2><p>Search engine optimization is a marketing discipline.</p></body></html>",
    )

    result = score_content_quality(page)

    assert any(
        f.title == "Limited first-person or experiential language" for f in result.findings
    ), "Expected the no-first-person finding to fire for entirely third-person content"