"""Content quality / E-E-A-T scoring.

Merges eeat-audit, seo-content, and geo-content into one deterministic
scorer covering the signals that can be checked from parsed HTML alone
(author presence, dates, first-person language, sourcing, depth). Signals
that require external verification (backlink authority, press mentions) are
intentionally out of scope here — see modules/brand_authority.py.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

_FIRST_PERSON = re.compile(r"\b(I|we|our|my|I've|we've)\b", re.IGNORECASE)
_HEDGING = re.compile(
    r"\b(reportedly|supposedly|some say|it is often said|generally speaking|"
    r"in most cases|it depends on various factors)\b",
    re.IGNORECASE,
)
_SOURCE_CITATION = re.compile(
    r"\b(according to|source:|study by|research (?:from|by)|\[\d+\])\b", re.IGNORECASE
)
_STAT_PATTERN = re.compile(r"(\$\d[\d,]*(\.\d+)?|\b\d{1,3}(\.\d+)?%|\b\d[\d,]{2,}\b)")

MINIMUM_WORD_COUNT_BY_HINT = {
    "homepage": 500,
    "blog": 1500,
    "pillar": 2000,
    "product": 300,
    "service": 500,
    "about": 300,
    "default": 500,
}


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def score_eeat(page: ParsedPage, *, page_type_hint: str = "default") -> DimensionScore:
    findings: list[Finding] = []
    body_text = " ".join(h.following_text for h in page.headings) or ""

    # Experience (25 pts): first-person language, low hedging.
    first_person_hits = len(_FIRST_PERSON.findall(body_text))
    hedging_hits = len(_HEDGING.findall(body_text))
    experience = min(first_person_hits * 3.0, 20.0) + (5.0 if hedging_hits == 0 else 0.0)
    if first_person_hits == 0:
        findings.append(
            Finding(
                severity=Severity.MEDIUM,
                title="No first-person experience language detected",
                detail="Content reads as third-person research summary. Add first-hand "
                "observations (\"when we tested...\", \"in our experience...\").",
                page_url=page.url,
            )
        )

    # Expertise (25 pts): author byline + sourced statistics.
    expertise = 0.0
    if page.author_byline_present:
        expertise += 12.0
    else:
        findings.append(
            Finding(
                severity=Severity.HIGH,
                title="No author byline detected",
                detail="No author name, rel=author, or itemprop=author markup found on the page.",
                page_url=page.url,
            )
        )
    source_hits = len(_SOURCE_CITATION.findall(body_text))
    stat_hits = len(_STAT_PATTERN.findall(body_text))
    expertise += min(source_hits * 4.0, 8.0)
    expertise += min(stat_hits * 1.0, 5.0)

    # Authoritativeness (25 pts): internal topical linking as a rough proxy.
    authoritativeness = min(len(page.internal_links) * 1.0, 20.0)
    if page.external_links:
        authoritativeness += min(len(page.external_links) * 1.0, 5.0)
    if len(page.internal_links) < 3:
        findings.append(
            Finding(
                severity=Severity.LOW,
                title="Thin internal linking",
                detail=f"Only {len(page.internal_links)} internal link(s) found. Pages isolated "
                "from a topical cluster read as lower-authority to both search engines and AI "
                "systems.",
                page_url=page.url,
            )
        )

    # Trustworthiness (25 pts): dates, canonical, robots sanity.
    trustworthiness = 0.0
    published = _parse_date(page.published_date)
    modified = _parse_date(page.modified_date)
    if published or modified:
        trustworthiness += 10.0
        reference_date = modified or published
        if reference_date and (datetime.now(timezone.utc) - reference_date).days > 730:
            findings.append(
                Finding(
                    severity=Severity.MEDIUM,
                    title="Content not updated in 24+ months",
                    detail="No visible update within the last 2 years. AI platforms weight "
                    "recency heavily for time-sensitive topics.",
                    page_url=page.url,
                )
            )
    else:
        findings.append(
            Finding(
                severity=Severity.MEDIUM,
                title="No publish or modified date found",
                detail="Undated content is treated as less trustworthy by AI citation systems.",
                page_url=page.url,
            )
        )
    if page.canonical:
        trustworthiness += 5.0
    if page.robots_meta and "noindex" not in page.robots_meta.lower():
        trustworthiness += 5.0
    elif page.robots_meta and "noindex" in page.robots_meta.lower():
        findings.append(
            Finding(
                severity=Severity.CRITICAL,
                title="Page is noindexed",
                detail="meta robots contains noindex — this page is excluded from search and "
                "AI citation entirely.",
                page_url=page.url,
            )
        )
    trustworthiness += 5.0 if page.meta_description else 0.0

    # Word count floor check.
    min_words = MINIMUM_WORD_COUNT_BY_HINT.get(page_type_hint, MINIMUM_WORD_COUNT_BY_HINT["default"])
    if page.word_count < min_words:
        findings.append(
            Finding(
                severity=Severity.LOW,
                title="Below the word-count floor for this page type",
                detail=f"{page.word_count} words vs. a {min_words}-word floor for "
                f"'{page_type_hint}' pages. More words isn't automatically better, but this is "
                "thin for the topic to be fully covered.",
                page_url=page.url,
            )
        )

    total = min(experience, 25.0) + min(expertise, 25.0) + min(authoritativeness, 25.0) + min(
        trustworthiness, 25.0
    )

    return DimensionScore(
        dimension="content_eeat",
        score=round(total, 1),
        findings=findings,
        raw={
            "experience": round(min(experience, 25.0), 1),
            "expertise": round(min(expertise, 25.0), 1),
            "authoritativeness": round(min(authoritativeness, 25.0), 1),
            "trustworthiness": round(min(trustworthiness, 25.0), 1),
            "word_count": page.word_count,
        },
    )
