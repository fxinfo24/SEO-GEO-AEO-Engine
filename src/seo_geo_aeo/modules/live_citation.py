"""
Live Citation scoring module.
Checks for likelihood that a page would be cited by AI engines using OpenRouter API.
"""
from __future__ import annotations

import os
import re

import httpx

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity


def _extract_page_content_for_prompt(page: ParsedPage, max_chars: int = 1500) -> str:
    """Extract relevant content from page for AI evaluation."""
    content_parts = []

    # Add title
    if page.title:
        content_parts.append(f"Title: {page.title}")

    # Add meta description if available
    if page.meta_description:
        content_parts.append(f"Description: {page.meta_description}")

    # Add headings with their following text (limited)
    for heading in page.headings[:3]:  # Limit to first 3 headings
        if heading.text:
            content_parts.append(f"Heading: {heading.text}")
            if heading.following_text:
                # Limit following text to avoid too much content
                following = heading.following_text[:200]
                content_parts.append(f"Content: {following}")

    # Join and limit total length
    full_content = "\n".join(content_parts)
    if len(full_content) > max_chars:
        full_content = full_content[:max_chars] + "..."

    return full_content or "No content available"


def _call_openrouter_api(prompt: str, api_key: str) -> float | None:
    """
    Call OpenRouter API to get citation likelihood assessment.
    Returns a score between 0.0 and 100.0, or None if failed.
    """
    if not api_key:
        return None

    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Prompt designed to elicit explanatory response that includes the score
    system_prompt = """You are an expert evaluator of web content for AI citation suitability.
    When providing your assessment, explain your reasoning clearly and naturally include your
    numerical score (0-100) in your explanation."""

    payload = {
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"""Please evaluate this web content for its likelihood of being cited by AI language models.

In your explanation, please include your numerical assessment where:
- 0 = Very unlikely to be cited (poor quality, inaccurate, or irrelevant)
- 50 = Moderately likely to be cited (decent quality but not exceptional)
- 100 = Very likely to be cited (high-quality, authoritative, comprehensive)

Content to evaluate:
{prompt}"""}
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

            # Combine content and reasoning for number extraction
            full_text = f"{content} {reasoning}"

            # Extract all numbers
            numbers = re.findall(r"\b(\d+(?:\.\d+)?)\b", full_text)

            # Filter for plausible scores (0-100 range)
            plausible_scores = []
            for num_str in numbers:
                try:
                    score = float(num_str)
                    # Additionally, filter out obvious false positives like years, counts, etc.
                    # by being somewhat selective about what looks like a rating
                    if 0 <= score <= 100:
                        plausible_scores.append(score)
                except ValueError:
                    continue

            if plausible_scores:
                # Return the average of all plausible scores found
                # This smooths out any outliers and gives a consensus value
                avg_score = sum(plausible_scores) / len(plausible_scores)
                return avg_score
            else:
                # Fallback: if no clear score found, look for qualitative assessments
                full_text_lower = full_text.lower()
                if any(phrase in full_text_lower for phrase in [
                    "very unlikely", "extremely low", "minimal chance", "nearly impossible"
                ]):
                    return 5.0
                elif any(phrase in full_text_lower for phrase in [
                    "unlikely", "low chance", "poor", "below average"
                ]):
                    return 25.0
                elif any(phrase in full_text_lower for phrase in [
                    "moderate", "average", "fair", "decent"
                ]):
                    return 50.0
                elif any(phrase in full_text_lower for phrase in [
                    "likely", "good chance", "above average", "promising"
                ]):
                    return 75.0
                elif any(phrase in full_text_lower for phrase in [
                    "very likely", "high chance", "excellent", "outstanding"
                ]):
                    return 85.0
                elif any(phrase in full_text_lower for phrase in [
                    "extremely likely", "nearly certain", "almost guaranteed"
                ]):
                    return 95.0

    except httpx.HTTPError as e:
        # Log error but don't fail the entire audit
        print(f"Warning: OpenRouter API call failed: {e}")
        return None
    except Exception as e:  # noqa: BLE001 - catch-all for unexpected errors
        # Log error but don't fail the entire audit
        print(f"Warning: OpenRouter API call failed: {e}")
        return None

    return None


def score_live_citation(page: ParsedPage) -> DimensionScore:
    """
    Score the likelihood that the page would be cited by AI engines.
    Uses OpenRouter API with nvidia/nemotron-3-super-120b-a12b:free model.
    """
    findings: list[Finding] = []

    # Get API key from environment
    api_key = os.getenv("OPENROUTER_API_KEY")

    if not api_key:
        findings.append(Finding(
            severity=Severity.MEDIUM,
            title="OpenRouter API key not configured",
            detail="Set OPENROUTER_API_KEY environment variable to enable live citation checking.",
            page_url=page.url,
        ))

        # Fallback to proof-of-concept behavior
        return DimensionScore(
            dimension="live_citation",
            score=0.0,
            findings=findings,
            raw={
                "note": "Proof-of-concept: live citation checking not implemented (no API key)",
            }
        )

    # Extract content for evaluation
    content_for_prompt = _extract_page_content_for_prompt(page)

    # Call OpenRouter API
    score = _call_openrouter_api(content_for_prompt, api_key)

    if score is None:
        findings.append(Finding(
            severity=Severity.MEDIUM,
            title="Live citation check failed",
            detail="Unable to connect to OpenRouter API for live citation assessment. Check API key and network connectivity.",
            page_url=page.url,
        ))

        # Return a neutral score with warning
        return DimensionScore(
            dimension="live_citation",
            score=50.0,  # Neutral score when assessment fails
            findings=findings,
            raw={
                "error": "API call failed",
                "content_evaluated_chars": len(content_for_prompt),
            }
        )

    # Determine severity based on score
    if score >= 80:
        severity = Severity.LOW
        title = "High AI citation likelihood"
        detail = f"Content assessed as highly likely to be cited by AI engines (score: {score:.1f}/100)"
    elif score >= 60:
        severity = Severity.LOW
        title = "Good AI citation likelihood"
        detail = f"Content assessed as likely to be cited by AI engines (score: {score:.1f}/100)"
    elif score >= 40:
        severity = Severity.MEDIUM
        title = "Moderate AI citation likelihood"
        detail = f"Content assessed as moderately likely to be cited by AI engines (score: {score:.1f}/100)"
    elif score >= 20:
        severity = Severity.MEDIUM
        title = "Low AI citation likelihood"
        detail = f"Content assessed as unlikely to be cited by AI engines (score: {score:.1f}/100)"
    else:
        severity = Severity.HIGH
        title = "Very low AI citation likelihood"
        detail = f"Content assessed as very unlikely to be cited by AI engines (score: {score:.1f}/100)"

    findings.append(Finding(
        severity=severity,
        title=title,
        detail=detail,
        page_url=page.url,
    ))

    return DimensionScore(
        dimension="live_citation",
        score=round(score, 1),
        findings=findings,
        raw={
            "ai_model": "nvidia/nemotron-3-super-120b-a12b:free",
            "content_evaluated_chars": len(content_for_prompt),
            "api_provider": "openrouter",
            "assessment_method": "explanatory_with_score_extraction",
            "raw_response_length": len(content_for_prompt),
        }
    )