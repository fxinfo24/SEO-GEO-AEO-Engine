"""
Content Quality scoring module.
Evaluates content depth, originality, relevance, and user value.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity


def score_content_quality(
    page: ParsedPage,
    *,
    page_type_hint: str = "default",
) -> DimensionScore:
    """
    Score content quality factors including depth, originality, and user value.
    
    This module evaluates how well content serves user needs and demonstrates
    expertise, which are key factors in modern search ranking algorithms.
    """
    findings: list[Finding] = []
    score = 0.0
    
    # Content Depth and Comprehensiveness (30 points)
    depth_score = _score_content_depth(page, page_type_hint, findings)
    score += depth_score * 0.30
    
    # Originality and Value Addition (25 points)
    originality_score = _score_originality(page, findings)
    score += originality_score * 0.25
    
    # Expertise and Authority Signals (20 points)
    expertise_score = _score_expertise_signals(page, findings)
    score += expertise_score * 0.20
    
    # Content Freshness and Relevance (15 points)
    freshness_score = _score_content_freshness(page, findings)
    score += freshness_score * 0.15
    
    # User Engagement Potential (10 points)
    engagement_score = _score_engagement_potential(page, findings)
    score += engagement_score * 0.10
    
    return DimensionScore(
        dimension="content_quality",
        score=round(score * 100.0, 1),
        findings=findings,
        raw={
            "depth": round(depth_score * 100.0, 1),
            "originality": round(originality_score * 100.0, 1),
            "expertise": round(expertise_score * 100.0, 1),
            "freshness": round(freshness_score * 100.0, 1),
            "engagement": round(engagement_score * 100.0, 1),
        }
    )


def _score_content_depth(page: ParsedPage, page_type_hint: str, findings: list[Finding]) -> float:
    """Score content depth and comprehensiveness (0-1 scale)."""
    # Minimum word count by page type (similar to EEAT but with different thresholds)
    MIN_WORD_COUNT_BY_TYPE = {
        "homepage": 300,
        "blog": 800,
        "article": 1000,
        "guide": 1500,
        "product": 200,
        "service": 400,
        "landing": 350,
        "default": 500,
    }
    
    min_words = MIN_WORD_COUNT_BY_TYPE.get(page_type_hint, MIN_WORD_COUNT_BY_TYPE["default"])
    word_ratio = min(page.word_count / min_words, 1.0) if min_words > 0 else 0.0
    
    if page.word_count < min_words:
        findings.append(Finding(
            severity=Severity.LOW if page.word_count >= min_words * 0.5 else Severity.MEDIUM,
            title=f"Content may be too thin for {page_type_hint} page type",
            detail=f"{page.word_count} words vs. {min_words}-word minimum for comprehensive {page_type_hint} content.",
            page_url=page.url,
        ))
    
    # Check for comprehensive coverage signals
    # Look for subtopics, FAQ sections, etc.
    has_faq_indicators = bool(re.search(r'<h[2-3][^>]*[^>]*\?[^<]*</h[2-3]>', page.raw_html, re.IGNORECASE))
    if not has_faq_indicators and page.word_count > 800:
        # Longer content might benefit from FAQ structure
        pass  # Not penalizing, just noting opportunity
    
    return word_ratio


def _score_originality(page: ParsedPage, findings: list[Finding]) -> float:
    """Score originality and value addition (0-1 scale)."""
    score = 0.7  # Start with baseline assumption of some originality
    
    # Check for first-person language (indicates original experience)
    first_person_pattern = re.compile(r'\b(I|we|our|my|I\'ve|we\'ve)\b', re.IGNORECASE)
    first_person_matches = len(first_person_pattern.findall(" ".join(h.following_text for h in page.headings)))
    if first_person_matches > 0:
        score += min(first_person_matches * 0.05, 0.2)  # Up to 0.2 bonus
        if first_person_matches == 0:
            findings.append(Finding(
                severity=Severity.LOW,
                title="Limited first-person or experiential language",
                detail="Content may lack original insights or personal experience that adds unique value.",
                page_url=page.url,
            ))
    
    # Check for specific data, examples, case studies
    # Look for statistics, specific numbers, dates, measurements
    stat_pattern = re.compile(r'(\$\d[\d,]*(\.\d+)?|\b\d{1,3}(\.\d+)?%|\b\d{4}\b(?!\s?(?:AM|PM))|\b\d[\d,]{2,}\b)')
    stat_matches = len(stat_pattern.findall(" ".join(h.following_text for h in page.headings)))
    if stat_matches > 0:
        score += min(stat_matches * 0.03, 0.15)  # Up to 0.15 bonus
    
    # Check for overly generic language that might indicate low originality
    generic_phrases = [
        r'it is known that',
        r'studies show',
        r'experts say',
        r'it is believed',
        r'it is thought that',
    ]
    generic_count = sum(
        len(re.findall(pattern, " ".join(h.following_text for h in page.headings), re.IGNORECASE))
        for pattern in generic_phrases
    )
    if generic_count > 3:
        score -= min(generic_count * 0.02, 0.15)  # Penalty for overly generic language
        findings.append(Finding(
            severity=Severity.LOW,
            title="Content relies heavily on generic attributions",
            detail="Consider adding more specific sources, data, or original analysis to increase content value.",
            page_url=page.url,
        ))
    
    return max(0.0, min(score, 1.0))


def _score_expertise_signals(page: ParsedPage, findings: list[Finding]) -> float:
    """Score expertise and authority signals (0-1 scale)."""
    score = 0.5  # Baseline
    
    # Author byline (also in EEAT, but important here too)
    if page.author_byline_present:
        score += 0.2
    else:
        findings.append(Finding(
            severity=Severity.MEDIUM,
            title="No author byline detected",
            detail="Content lacks clear authorship, which can impact perceived expertise and authority.",
            page_url=page.url,
        ))
    
    # Author credentials or expertise indicators in content
    expertise_indicators = [
        r'years? of experience',
        r'certified',
        r'license',
        r'degree in',
        r'Ph\.?D',
        r'MBA',
        r'expert in',
        r'specialist in',
    ]
    content_text = " ".join(h.following_text for h in page.headings)
    expertise_matches = sum(
                len(re.findall(pattern, content_text, re.IGNORECASE))
                for pattern in expertise_indicators
            )
    if expertise_matches > 0:
        score += min(expertise_matches * 0.05, 0.2)  # Up to 0.2 bonus
    
    # Citations and references to authoritative sources
    citation_patterns = [
        r'according to',
        r'source:',
        r'study by',
        r'research (?:from|by)',
        r'\[\d+\]',  # [1], [2] style citations
        r'\( et al\.\)',
        r'journal of',
        r'proceedings of',
    ]
    citation_matches = sum(
        len(re.findall(pattern, content_text, re.IGNORECASE))
        for pattern in citation_patterns
    )
    if citation_matches > 0:
        score += min(citation_matches * 0.04, 0.15)  # Up to 0.15 bonus
    
    # Schema.org Author/Person markup (helps search engines understand expertise)
    has_author_schema = any(
        "Person" in str(block.get("@type", "")) or "author" in str(block).lower()
        for block in page.schema_blocks
    )
    if has_author_schema:
        score += 0.1
    
    return max(0.0, min(score, 1.0))


def _score_content_freshness(page: ParsedPage, findings: list[Finding]) -> float:
    """Score content freshness and relevance (0-1 scale)."""
    score = 0.7  # Baseline assumption
    
    # Check for dates
    now = datetime.now(UTC)
    
    published = None
    modified = None
    
    # Try to get dates from schema first
    for block in page.schema_blocks:
        if "datePublished" in block and not published:
            try:
                published = datetime.fromisoformat(block["datePublished"])
            except ValueError:
                pass
        if "dateModified" in block and not modified:
            try:
                modified = datetime.fromisoformat(block["dateModified"])
            except ValueError:
                pass

    # Fallback to meta tags
    if not published and page.published_date:
        try:
            published = datetime.fromisoformat(page.published_date)
        except ValueError:
            pass
    if not modified and page.modified_date:
        try:
            modified = datetime.fromisoformat(page.modified_date)
        except ValueError:
            pass

    # Score based on recency
    reference_date = modified or published
    if reference_date:
        days_old = (now - reference_date).days
        if days_old <= 30:
            score = 1.0  # Very fresh
        elif days_old <= 90:
            score = 0.9  # Fresh
        elif days_old <= 180:
            score = 0.7  # Moderately fresh
        elif days_old <= 365:
            score = 0.5  # Getting stale
        else:
            score = 0.3  # Stale
            
        if days_old > 365:
            findings.append(Finding(
                severity=Severity.MEDIUM,
                title="Content may be outdated",
                detail=f"Content was last modified over {days_old // 30} months ago. Consider updating for current accuracy and relevance.",
                page_url=page.url,
            ))
    else:
        findings.append(Finding(
            severity=Severity.LOW,
            title="No publication or modification date found",
            detail="Content lacks date signals, making it difficult to assess freshness and relevance.",
            page_url=page.url,
        ))
        score = 0.5  # No date info = moderate baseline
    
    # Check for time-sensitive language that might indicate need for updates
    time_sensitive = [
        r'latest',
        r'newest',
        r'current',
        r'this year',
        r'202[0-9]',  # Recent years
        r'recently',
        r'just released',
    ]
    time_matches = sum(
        len(re.findall(pattern, content_text, re.IGNORECASE))
        for pattern in time_sensitive
        for content_text in [" ".join(h.following_text for h in page.headings)]
    )
    if time_matches > 2 and (not reference_date or (now - reference_date).days > 180):
        findings.append(Finding(
            severity=Severity.LOW,
            title="Time-sensitive language detected in potentially outdated content",
            detail="Contains references to 'latest' or 'recent' but content may be stale. Consider verifying current accuracy.",
            page_url=page.url,
        ))
    
    return max(0.0, min(score, 1.0))


def _score_engagement_potential(page: ParsedPage, findings: list[Finding]) -> float:
    """Score user engagement potential (0-1 scale)."""
    score = 0.5  # Baseline
    
    content_text = " ".join(h.following_text for h in page.headings)
    
    # Check for engaging elements
    # Questions that encourage thought
    question_count = content_text.count('?')
    if question_count > 0:
        score += min(question_count * 0.05, 0.2)  # Up to 0.2 bonus
    
    # Calls to action
    cta_patterns = [
        r'learn more',
        r'discover',
        r'find out',
        r'get started',
        r'try now',
        r'click here',
        r'read more',
        r'sign up',
        r'download',
    ]
    cta_count = sum(
        len(re.findall(pattern, content_text, re.IGNORECASE))
        for pattern in cta_patterns
    )
    if cta_count > 0:
        score += min(cta_count * 0.03, 0.15)  # Up to 0.15 bonus
    
    # Lists and steps (scannable, actionable content)
    list_indicators = ['<ul>', '<ol>', '<li>', '•', '-', '*']
    list_score = sum(content_text.count(indicator) for indicator in list_indicators[:3])  # HTML tags
    if list_score > 0:
        score += min(list_score * 0.02, 0.1)  # Up to 0.1 bonus
    
    # Tables and comparison data
    if '<table>' in page.raw_html or '|' in page.raw_html:
        score += 0.1
    
    # Multimedia mentions (even if not present, intent to include helps)
    multimedia = [
        r'image',
        r'video',
        r'diagram',
        r'chart',
        r'infographic',
        r'screenshot',
    ]
    multimedia_count = sum(
        len(re.findall(pattern, content_text, re.IGNORECASE))
        for pattern in multimedia
    )
    if multimedia_count > 0:
        score += min(multimedia_count * 0.02, 0.1)  # Up to 0.1 bonus
    
    return max(0.0, min(score, 1.0))