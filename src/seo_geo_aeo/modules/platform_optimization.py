"""Platform-specific GEO scoring, ported from geo-platform-optimizer SKILL.md.

Honest scope limitation: the source rubric's biggest point values are
search-ranking position, third-party backlink authority, IndexNow
submission status, and community discussion volume — none of which a
single-page fetch can measure. This module scores only the sub-criteria
that ARE derivable from the parsed page, its schema, and its own fetch
timing, and documents what's skipped in `raw["unmeasured"]` per platform
rather than guessing.

Each of the five platforms gets a component score (0-100) from whatever
subset of its checklist is locally verifiable; the module-level score is
the mean of the five. This deliberately is NOT the source rubric's full
0-100 scale per platform — it's a fraction of it, scoped to what's real.
"""

from __future__ import annotations

import re

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

_QUESTION_HEADING = re.compile(
    r"^(what|how|why|when|where|who|which|can|does|is|are)\b.*\?\s*$|.*\?\s*$", re.IGNORECASE
)
_STAT_WITH_SOURCE = re.compile(
    r"(according to|source:|study by|research (?:from|by))[^.]{0,80}(\$\d[\d,]*|\d{1,3}%|\d[\d,]{2,})",
    re.IGNORECASE,
)
_TABLE_MARKER = re.compile(r"<table|(\|.+\|.+\|)")
_LIST_MARKER = re.compile(r"<ul|<ol|^\s*[-*]\s|^\s*\d+\.\s", re.MULTILINE)


def _has_faq_section(page: ParsedPage) -> bool:
    question_headings = sum(
        1 for h in page.headings if h.level in (2, 3) and h.text.strip().endswith("?")
    )
    return question_headings >= 5


def _score_google_aio(page: ParsedPage) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 0.0

    question_headings = [h for h in page.headings if h.level in (2, 3) and h.text.strip().endswith("?")]
    score += min(len(question_headings) * 2.0, 10.0)
    if not question_headings:
        findings.append(
            Finding(Severity.MEDIUM, "No question-phrased headings",
                     "Google AI Overviews strongly favors H2/H3 headings phrased as questions "
                     "matching real search queries.", page.url)
        )

    direct_answers = sum(
        1 for h in question_headings if h.following_text and len(h.following_text.split()) >= 15
    )
    score += min(direct_answers * 3.0, 15.0)

    body_text = " ".join(h.following_text for h in page.headings)
    if _TABLE_MARKER.search(page.raw_html):
        score += 10.0
    if _LIST_MARKER.search(page.raw_html):
        score += 10.0
    if _has_faq_section(page):
        score += 10.0
    else:
        findings.append(
            Finding(Severity.LOW, "No FAQ section (5+ questions)",
                     "A dedicated FAQ section with 5+ real questions helps AIO extraction even "
                     "without FAQPage rich-result eligibility.", page.url)
        )

    score += min(len(_STAT_WITH_SOURCE.findall(body_text)) * 2.0, 10.0)
    if page.published_date or page.modified_date:
        score += 5.0
    if page.author_byline_present:
        score += 5.0
    if page.h1_count == 1:
        score += 5.0

    return min(score, 80.0), findings  # 20 of 100 pts are ranking-position based; not measurable here


def _score_chatgpt(page: ParsedPage, same_as: dict[str, str]) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 0.0

    if "wikipedia" in same_as:
        score += 20.0
    else:
        findings.append(
            Finding(Severity.MEDIUM, "No Wikipedia entity linked",
                     "ChatGPT's top citation source by domain share is Wikipedia (47.9%) per "
                     "Ahrefs Dec 2025 data. See the brand_authority dimension for the full check.")
        )

    if page.word_count >= 2000:
        score += 25.0
    elif page.word_count >= 1000:
        score += 12.0
    else:
        findings.append(
            Finding(Severity.LOW, "Below ChatGPT's comprehensiveness threshold",
                     f"{page.word_count} words; ChatGPT tends to cite single authoritative "
                     "pages of 2000+ words over combining thin pages.", page.url)
        )

    if page.author_byline_present:
        score += 10.0
    if "linkedin" in same_as or "github" in same_as:
        score += 10.0

    return score, findings  # unmeasured: Bing index coverage, backlink authority, entity consistency


def _score_perplexity(page: ParsedPage, same_as: dict[str, str]) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 0.0

    if "reddit" in same_as:
        score += 20.0
    else:
        findings.append(
            Finding(Severity.MEDIUM, "No Reddit presence declared",
                     "Perplexity's top citation source is Reddit (46.7%) per Ahrefs Dec 2025 "
                     "data — the single highest-leverage platform for this engine.")
        )

    if page.published_date or page.modified_date:
        score += 15.0
    else:
        findings.append(
            Finding(Severity.LOW, "No freshness signal",
                     "Perplexity deprioritizes undated/stale content more aggressively than "
                     "other platforms.", page.url)
        )

    body_text = " ".join(h.following_text for h in page.headings)
    score += min(len(_STAT_WITH_SOURCE.findall(body_text)) * 5.0, 20.0)
    if "youtube" in same_as:
        score += 10.0
    if "wikipedia" in same_as:
        score += 5.0

    return score, findings  # unmeasured: forum/HN/Quora mentions, discussion generation, multi-source validation


def _score_gemini(page: ParsedPage, schema_score: float, same_as: dict[str, str]) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 0.0

    if "youtube" in same_as:
        score += 25.0
    else:
        findings.append(
            Finding(Severity.MEDIUM, "No YouTube presence declared",
                     "Gemini weights YouTube more heavily than standard Google Search.")
        )

    score += (schema_score / 100.0) * 25.0  # Gemini consumes Schema.org directly

    if page.images_total > 0:
        alt_ratio = 1 - (page.images_missing_alt / page.images_total)
        score += alt_ratio * 15.0
    if page.author_byline_present:
        score += 10.0

    return score, findings  # unmeasured: Knowledge Panel, Google Business Profile, Merchant Center


def _score_bing_copilot(page: ParsedPage, elapsed_seconds: float | None, same_as: dict[str, str]) -> tuple[float, list[Finding]]:
    findings: list[Finding] = []
    score = 0.0

    if page.meta_description:
        score += 20.0
    else:
        findings.append(
            Finding(Severity.MEDIUM, "No meta description",
                     "Bing/Copilot weights meta descriptions more heavily than Google does.", page.url)
        )

    if "linkedin" in same_as:
        score += 15.0
    if "github" in same_as:
        score += 10.0

    if elapsed_seconds is not None:
        if elapsed_seconds < 2.0:
            score += 20.0
        elif elapsed_seconds < 4.0:
            score += 10.0
        else:
            findings.append(
                Finding(Severity.MEDIUM, "Slow response time",
                         f"Fetch took {elapsed_seconds:.1f}s; Copilot deprioritizes pages over "
                         "~2s load time. (Server response time, not full page render.)", page.url)
            )

    return score, findings  # unmeasured: IndexNow, Bing WMT verification, Bing index coverage, social signals


def score_platform_optimization(
    page: ParsedPage,
    *,
    schema_score: float = 0.0,
    same_as_links: dict[str, str] | None = None,
    fetch_elapsed_seconds: float | None = None,
) -> DimensionScore:
    """Score GEO platform-readiness across AIO/ChatGPT/Perplexity/Gemini/Copilot.

    `schema_score` should be the 0-100 score from modules.geo_schema, and
    `same_as_links` the dict from modules.brand_authority's sameAs extraction
    — pass both in from the orchestrator to avoid re-deriving them.
    """
    same_as = same_as_links or {}
    findings: list[Finding] = []

    aio_score, aio_findings = _score_google_aio(page)
    chatgpt_score, chatgpt_findings = _score_chatgpt(page, same_as)
    perplexity_score, perplexity_findings = _score_perplexity(page, same_as)
    gemini_score, gemini_findings = _score_gemini(page, schema_score, same_as)
    copilot_score, copilot_findings = _score_bing_copilot(page, fetch_elapsed_seconds, same_as)

    findings.extend(aio_findings)
    findings.extend(chatgpt_findings)
    findings.extend(perplexity_findings)
    findings.extend(gemini_findings)
    findings.extend(copilot_findings)

    per_platform = {
        "google_aio": round(aio_score, 1),
        "chatgpt": round(chatgpt_score, 1),
        "perplexity": round(perplexity_score, 1),
        "gemini": round(gemini_score, 1),
        "bing_copilot": round(copilot_score, 1),
    }
    overall = sum(per_platform.values()) / len(per_platform)

    return DimensionScore(
        dimension="platform_optimization",
        score=round(overall, 1),
        findings=findings,
        raw={
            "per_platform": per_platform,
            "unmeasured": [
                "search ranking position (all platforms)",
                "backlink authority / Domain Rating",
                "IndexNow submission status (Bing Copilot)",
                "Bing/Google index coverage",
                "community discussion volume (Perplexity)",
                "Knowledge Panel / Google Business Profile (Gemini)",
                "actual client-side render speed (only server response time is measured)",
            ],
        },
    )
