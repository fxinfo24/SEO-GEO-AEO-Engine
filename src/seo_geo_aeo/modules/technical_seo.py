"""
Technical SEO scoring module.
Covers crawlability, indexability, site architecture, and technical performance factors.
"""
from __future__ import annotations

from seo_geo_aeo.core.fetcher import FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity


def score_technical_seo(
    page: ParsedPage,
    *,
    fetcher: SafeFetcher | None = None,
) -> DimensionScore:
    """
    Score technical SEO factors including crawlability, indexability, and site architecture.
    
    This module focuses on technical aspects that affect how search engines crawl,
    index, and understand a website.
    """
    findings: list[Finding] = []
    score = 0.0
    fetcher = fetcher or SafeFetcher()
    
    # Crawlability (25 points)
    crawlability_score = _score_crawlability(page, fetcher, findings)
    score += crawlability_score * 0.25
    
    # Indexability (25 points)
    indexability_score = _score_indexability(page, findings)
    score += indexability_score * 0.25
    
    # Site Architecture (20 points)
    architecture_score = _score_site_architecture(page, findings)
    score += architecture_score * 0.20
    
    # Technical Performance (15 points) 
    performance_score = _score_technical_performance(page, findings)
    score += performance_score * 0.15
    
    # Mobile Friendliness (15 points)
    mobile_score = _score_mobile_friendliness(page, findings)
    score += mobile_score * 0.15
    
    return DimensionScore(
        dimension="technical_seo",
        score=round(score * 100.0, 1),
        findings=findings,
        raw={
            "crawlability": round(crawlability_score * 100.0, 1),
            "indexability": round(indexability_score * 100.0, 1),
            "architecture": round(architecture_score * 100.0, 1),
            "performance": round(performance_score * 100.0, 1),
            "mobile": round(mobile_score * 100.0, 1),
        }
    )


def _score_crawlability(page: ParsedPage, fetcher: SafeFetcher, findings: list[Finding]) -> float:
    """Score crawlability factors (0-1 scale).

    Actually checks robots.txt via the passed fetcher — this used to be a
    no-op (`try: pass except: ...`) that accepted `fetcher` and never called
    it, silently returning a near-perfect score for a check that never ran.
    """
    score = 1.0  # Start with perfect score, deduct for issues

    try:
        allowed = fetcher.is_allowed(page.url)
    except (FetchError, UnsafeURLError) as exc:
        findings.append(Finding(
            severity=Severity.MEDIUM,
            title="Could not fetch robots.txt",
            detail=f"Unable to retrieve robots.txt to check for crawl directives: {exc}",
            page_url=page.url,
        ))
        return max(0.0, score - 0.2)

    if not allowed:
        findings.append(Finding(
            severity=Severity.CRITICAL,
            title="Page disallowed by robots.txt",
            detail="robots.txt blocks this URL for this fetcher's user agent — the page "
            "cannot be crawled at all under current directives.",
            page_url=page.url,
        ))
        score = 0.0

    return max(0.0, score)


def _score_indexability(page: ParsedPage, findings: list[Finding]) -> float:
    """Score indexability factors (0-1 scale)."""
    score = 1.0
    
    # Check for indexation-blocking meta tags
    if page.robots_meta and "noindex" in page.robots_meta.lower():
        findings.append(Finding(
            severity=Severity.CRITICAL,
            title="Page is noindexed",
            detail="meta robots contains noindex — this page is excluded from search entirely.",
            page_url=page.url,
        ))
        score = 0.0  # Noindex means 0 for indexability
    
    # Check for canonicalization issues
    if not page.canonical:
        findings.append(Finding(
            severity=Severity.MEDIUM,
            title="No canonical tag",
            detail="No rel=canonical link found. This can lead to duplicate content issues.",
            page_url=page.url,
        ))
        score -= 0.2
    
    # Check for pagination handling (simplified)
    # In a full implementation, we'd check for rel=next/prev etc.
    
    return max(0.0, score)


def _score_site_architecture(page: ParsedPage, findings: list[Finding]) -> float:
    """Score site architecture factors (0-1 scale)."""
    score = 1.0
    
    # Check URL depth and structure
    # This would require analyzing the site structure across multiple pages
    # For single-page analysis, we'll use basic signals
    
    # Check for breadcrumb schema (helps with architecture understanding)
    has_breadcrumb = any(
        "BreadcrumbList" in str(block.get("@type", "")) 
        for block in page.schema_blocks
    )
    if not has_breadcrumb:
        findings.append(Finding(
            severity=Severity.LOW,
            title="No breadcrumb schema found",
            detail="BreadcrumbList schema helps search engines understand site structure.",
            page_url=page.url,
        ))
        score -= 0.1
    
    # Check internal linking depth (simplified)
    if len(page.internal_links) < 3:
        findings.append(Finding(
            severity=Severity.MEDIUM,
            title="Limited internal linking",
            detail=f"Only {len(page.internal_links)} internal link(s) found. Good site architecture requires adequate internal linking for crawl distribution.",
            page_url=page.url,
        ))
        score -= 0.2
    
    return max(0.0, score)


def _score_technical_performance(page: ParsedPage, findings: list[Finding]) -> float:
    """Score technical performance factors (0-1 scale)."""
    score = 1.0
    
    # Page size considerations (from raw HTML)
    html_size = len(page.raw_html.encode('utf-8'))
    if html_size > 500 * 1024:  # 500KB
        findings.append(Finding(
            severity=Severity.LOW,
            title="Large HTML page size",
            detail=f"HTML size is {html_size // 1024}KB. Large pages can slow crawling and indexing.",
            page_url=page.url,
        ))
        score -= 0.1
    
    # Check for excessive DOM size (heuristic based on tag count)
    # This is a rough approximation
    if page.raw_html.count('<') > 2000:  # Many tags
        findings.append(Finding(
            severity=Severity.LOW,
            title="Potentially complex DOM structure",
            detail="Page appears to have many HTML elements, which may slow rendering.",
            page_url=page.url,
        ))
        score -= 0.1
    
    return max(0.0, score)


def _score_mobile_friendliness(page: ParsedPage, findings: list[Finding]) -> float:
    """Score mobile friendliness factors (0-1 scale)."""
    score = 1.0
    
    # Viewport meta tag (critical for mobile)
    if not page.has_viewport_meta:
        findings.append(Finding(
            severity=Severity.HIGH,
            title="No viewport meta tag",
            detail="Missing <meta name=viewport>; page will not render properly on mobile devices.",
            page_url=page.url,
        ))
        score = 0.0  # No viewport means not mobile-friendly
    
    # Check for mobile-usable font sizes (would need CSS analysis)
    # Check for tap target sizing (would need CSS analysis)
    # Check for legible text sizes (would need CSS analysis)
    
    # For now, rely on viewport as primary signal
    return score