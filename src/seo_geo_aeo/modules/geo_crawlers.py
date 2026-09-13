"""AI crawler access scoring.

Ports the Tier 1/2/3 crawler matrix from the source `geo-crawlers` SKILL.md
into code instead of leaving it as a checklist Claude re-reads every audit.
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin

from seo_geo_aeo.core.fetcher import FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

# (user_agent, tier, operator, product) — tier 1 = highest-impact for AI search visibility.
AI_CRAWLERS: list[tuple[str, int, str, str]] = [
    ("GPTBot", 1, "OpenAI", "ChatGPT web browsing / training"),
    ("OAI-SearchBot", 1, "OpenAI", "ChatGPT Search (no training use)"),
    ("ChatGPT-User", 1, "OpenAI", "User-initiated ChatGPT browsing"),
    ("ClaudeBot", 1, "Anthropic", "Claude web search and analysis"),
    ("PerplexityBot", 1, "Perplexity", "Perplexity AI search"),
    ("Google-Extended", 2, "Google", "Gemini / AI Overviews training"),
    ("GoogleOther", 2, "Google", "Google AI research"),
    ("Applebot-Extended", 2, "Apple", "Apple Intelligence"),
    ("Amazonbot", 2, "Amazon", "Alexa / Amazon AI"),
    ("FacebookBot", 2, "Meta", "Meta AI"),
    ("CCBot", 3, "Common Crawl", "Training-data corpus (no live search use)"),
    ("anthropic-ai", 3, "Anthropic", "Claude model training (not live features)"),
    ("Bytespider", 3, "ByteDance", "TikTok / Doubao training"),
    ("cohere-ai", 3, "Cohere", "Model training"),
]

TIER_1_AGENTS = {ua for ua, tier, _, _ in AI_CRAWLERS if tier == 1}
TIER_2_AGENTS = {ua for ua, tier, _, _ in AI_CRAWLERS if tier == 2}


@dataclass
class CrawlerStatus:
    user_agent: str
    tier: int
    operator: str
    product: str
    allowed: bool


def analyze_crawler_access(domain_url: str, fetcher: SafeFetcher) -> DimensionScore:
    """Fetch robots.txt for `domain_url`'s origin and score AI crawler access.

    Score = 50% Tier-1 coverage + 25% Tier-2 coverage + 15% "no blanket AI
    block" + 10% presence of an llms.txt file, matching the source rubric's
    weighting.
    """
    robots_url = urljoin(domain_url, "/robots.txt")
    findings: list[Finding] = []

    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    robots_fetched = False
    try:
        result = fetcher.fetch(robots_url, respect_robots_override=False)
        parser.parse(result.text.splitlines())
        robots_fetched = True
    except (FetchError, UnsafeURLError):
        parser.parse([])  # No robots.txt => everything implicitly allowed.

    statuses: list[CrawlerStatus] = []
    for user_agent, tier, operator, product in AI_CRAWLERS:
        allowed = parser.can_fetch(user_agent, domain_url)
        statuses.append(CrawlerStatus(user_agent, tier, operator, product, allowed))
        if tier == 1 and not allowed:
            findings.append(
                Finding(
                    severity=Severity.CRITICAL,
                    title=f"{user_agent} blocked in robots.txt",
                    detail=(
                        f"{operator}'s {user_agent} powers {product}. Blocking it removes this "
                        "site from that platform's citations entirely."
                    ),
                )
            )
        elif tier == 2 and not allowed:
            findings.append(
                Finding(
                    severity=Severity.MEDIUM,
                    title=f"{user_agent} blocked in robots.txt",
                    detail=f"{operator}'s {user_agent} powers {product}.",
                )
            )

    tier1_allowed = sum(1 for s in statuses if s.tier == 1 and s.allowed)
    tier2_allowed = sum(1 for s in statuses if s.tier == 2 and s.allowed)
    tier1_component = 50.0 * (tier1_allowed / len(TIER_1_AGENTS))
    tier2_component = 25.0 * (tier2_allowed / len(TIER_2_AGENTS))

    blanket_block = not parser.can_fetch("*", domain_url)
    no_blanket_component = 0.0 if blanket_block else 15.0
    if blanket_block:
        findings.append(
            Finding(
                severity=Severity.CRITICAL,
                title="Blanket wildcard block detected",
                detail="robots.txt disallows '*' for this path, which blocks every AI crawler "
                "along with everything else.",
            )
        )

    llms_txt_component = 0.0
    try:
        llms_result = fetcher.fetch(urljoin(domain_url, "/llms.txt"), respect_robots_override=False)
        if llms_result.status_code == 200:
            llms_txt_component = 10.0
    except (FetchError, UnsafeURLError):
        pass
    if llms_txt_component == 0.0:
        findings.append(
            Finding(
                severity=Severity.LOW,
                title="No llms.txt found",
                detail="An llms.txt at the domain root helps AI systems understand site "
                "structure without crawling every page.",
            )
        )

    score = tier1_component + tier2_component + no_blanket_component + llms_txt_component

    return DimensionScore(
        dimension="technical_geo",
        score=round(min(score, 100.0), 1),
        findings=findings,
        raw={
            "robots_txt_fetched": robots_fetched,
            "crawler_statuses": [s.__dict__ for s in statuses],
            "tier1_allowed": tier1_allowed,
            "tier1_total": len(TIER_1_AGENTS),
            "tier2_allowed": tier2_allowed,
            "tier2_total": len(TIER_2_AGENTS),
            "blanket_block": blanket_block,
            "llms_txt_present": llms_txt_component > 0.0,
        },
    )
