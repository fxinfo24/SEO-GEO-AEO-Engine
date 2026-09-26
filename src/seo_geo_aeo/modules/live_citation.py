"""
Live Citation scoring module.

Honest naming caveat: despite the dimension name "live_citation", this does
NOT test whether ChatGPT, Perplexity, Gemini, or any other AI system has
actually cited this URL. It sends page content to an LLM via OpenRouter and
asks it to *guess* a 0-100 citation-likelihood score. That is a model
opinion, not an observation — treat it accordingly. A genuine live-citation
test would need a fixed query set, real answer-engine API calls, URL
extraction from real responses, and repeated runs to account for
nondeterminism; none of that exists here.

Privacy note: page content (title, meta description, and up to ~1500 chars
of heading/body text) is sent to a third-party API (OpenRouter, currently
routed to a specific free model) for every audit that runs this module.
There is no opt-out flag yet beyond unsetting OPENROUTER_API_KEY, and no
redaction — do not run this against pages containing anything sensitive.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

logger = logging.getLogger(__name__)

_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
_STRICT_SCORE_PATTERN = re.compile(r"FINAL_SCORE:\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_LOOSE_NUMBER_PATTERN = re.compile(r"\b(\d+(?:\.\d+)?)\b")

_QUALITATIVE_FALLBACKS: list[tuple[tuple[str, ...], float]] = [
    (("very unlikely", "extremely low", "minimal chance", "nearly impossible"), 5.0),
    (("unlikely", "low chance", "poor", "below average"), 25.0),
    (("moderate", "average", "fair", "decent"), 50.0),
    (("likely", "good chance", "above average", "promising"), 75.0),
    (("very likely", "high chance", "excellent", "outstanding"), 85.0),
    (("extremely likely", "nearly certain", "almost guaranteed"), 95.0),
]


@dataclass
class _ApiCallResult:
    score: float | None
    response_text_length: int
    extraction_method: str  # "strict_marker" | "loose_number_average" | "qualitative_phrase" | "none"


def _extract_page_content_for_prompt(page: ParsedPage, max_chars: int = 1500) -> str:
    """Extract relevant content from page for AI evaluation."""
    content_parts = []

    if page.title:
        content_parts.append(f"Title: {page.title}")

    if page.meta_description:
        content_parts.append(f"Description: {page.meta_description}")

    for heading in page.headings[:3]:  # Limit to first 3 headings
        if heading.text:
            content_parts.append(f"Heading: {heading.text}")
            if heading.following_text:
                following = heading.following_text[:200]
                content_parts.append(f"Content: {following}")

    full_content = "\n".join(content_parts)
    if len(full_content) > max_chars:
        full_content = full_content[:max_chars] + "..."

    return full_content or "No content available"


def _extract_score(full_text: str) -> tuple[float | None, str]:
    """Try strict marker first, then loose number averaging, then phrase heuristics.

    Returns (score, method) so the caller can record which extraction path
    actually produced the number — the loose/qualitative paths are known to
    be fragile and this makes that visible in the result's raw data instead
    of hiding it behind a single opaque score.
    """
    strict_match = _STRICT_SCORE_PATTERN.search(full_text)
    if strict_match:
        score = float(strict_match.group(1))
        if 0 <= score <= 100:
            return score, "strict_marker"

    numbers = _LOOSE_NUMBER_PATTERN.findall(full_text)
    plausible_scores = [float(n) for n in numbers if 0 <= float(n) <= 100]
    if plausible_scores:
        return sum(plausible_scores) / len(plausible_scores), "loose_number_average"

    full_text_lower = full_text.lower()
    for phrases, fallback_score in _QUALITATIVE_FALLBACKS:
        if any(phrase in full_text_lower for phrase in phrases):
            return fallback_score, "qualitative_phrase"

    return None, "none"


def _call_openrouter_api(prompt: str, api_key: str) -> _ApiCallResult:
    """Call OpenRouter API and extract a citation-likelihood score from the response."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    system_prompt = (
        "You are an expert evaluator of web content for AI citation suitability. "
        "Explain your reasoning clearly, then end your response with exactly one line "
        "in the format 'FINAL_SCORE: <number>' where <number> is 0-100."
    )
    user_prompt = f"""Please evaluate this web content for its likelihood of being cited by AI language models.

Scoring guide:
- 0 = Very unlikely to be cited (poor quality, inaccurate, or irrelevant)
- 50 = Moderately likely to be cited (decent quality but not exceptional)
- 100 = Very likely to be cited (high-quality, authoritative, comprehensive)

End your response with a line reading exactly: FINAL_SCORE: <number>

Content to evaluate:
{prompt}"""

    payload = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 200,
        "temperature": 0.1,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            reasoning = result.get("choices", [{}])[0].get("message", {}).get("reasoning", "") or ""
            full_text = f"{content} {reasoning}"
            score, method = _extract_score(full_text)
            return _ApiCallResult(
                score=score, response_text_length=len(full_text), extraction_method=method
            )
    except httpx.HTTPError as exc:
        logger.warning("OpenRouter API call failed for live_citation: %s", exc)
        return _ApiCallResult(score=None, response_text_length=0, extraction_method="none")
    except Exception as exc:  # noqa: BLE001 - never let a third-party call crash the audit
        logger.warning("Unexpected error calling OpenRouter for live_citation: %s", exc)
        return _ApiCallResult(score=None, response_text_length=0, extraction_method="none")


def score_live_citation(page: ParsedPage) -> DimensionScore:
    """Ask an LLM (via OpenRouter) to estimate this page's AI-citation likelihood.

    Returns measured=False (excluded from the composite score entirely) when
    no API key is configured or the API call fails — never a placeholder
    number standing in for a real measurement.
    """
    findings: list[Finding] = []
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        return DimensionScore(
            dimension="live_citation",
            score=0.0,
            measured=False,
            unmeasured_reason="OPENROUTER_API_KEY not set — live citation check did not run.",
            findings=[
                Finding(
                    severity=Severity.LOW,
                    title="Live citation check skipped: no API key",
                    detail="Set OPENROUTER_API_KEY to enable this check. Excluded from the "
                    "composite score rather than scored as a default value.",
                    page_url=page.url,
                )
            ],
            raw={"reason": "no_api_key"},
        )

    content_for_prompt = _extract_page_content_for_prompt(page)
    api_result = _call_openrouter_api(content_for_prompt, api_key)

    if api_result.score is None:
        return DimensionScore(
            dimension="live_citation",
            score=0.0,
            measured=False,
            unmeasured_reason="OpenRouter API call failed or returned no extractable score.",
            findings=[
                Finding(
                    severity=Severity.LOW,
                    title="Live citation check failed",
                    detail="Could not reach OpenRouter or extract a score from its response. "
                    "Excluded from the composite score rather than defaulted to neutral.",
                    page_url=page.url,
                )
            ],
            raw={
                "reason": "api_call_or_extraction_failed",
                "content_evaluated_chars": len(content_for_prompt),
            },
        )

    score = api_result.score
    if score >= 80:
        severity, title = Severity.LOW, "High AI citation likelihood"
    elif score >= 60:
        severity, title = Severity.LOW, "Good AI citation likelihood"
    elif score >= 40:
        severity, title = Severity.MEDIUM, "Moderate AI citation likelihood"
    elif score >= 20:
        severity, title = Severity.MEDIUM, "Low AI citation likelihood"
    else:
        severity, title = Severity.HIGH, "Very low AI citation likelihood"

    findings.append(
        Finding(
            severity=severity,
            title=title,
            detail=f"Model-estimated citation likelihood: {score:.1f}/100 "
            f"(extraction method: {api_result.extraction_method}). This is an LLM's opinion, "
            f"not an observed citation.",
            page_url=page.url,
        )
    )

    return DimensionScore(
        dimension="live_citation",
        score=round(score, 1),
        findings=findings,
        raw={
            "ai_model": _MODEL,
            "content_evaluated_chars": len(content_for_prompt),
            "api_provider": "openrouter",
            "extraction_method": api_result.extraction_method,
            "raw_response_length": api_result.response_text_length,
        },
    )
