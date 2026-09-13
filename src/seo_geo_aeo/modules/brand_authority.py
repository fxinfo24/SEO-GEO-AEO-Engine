"""Brand authority scoring, ported from geo-brand-mentions SKILL.md.

Honest scope limitation: the source rubric weighs YouTube subscriber counts,
Reddit mention volume/sentiment, and LinkedIn follower counts — none of
which are obtainable without paid APIs or a live web-search pass. This
module automates the two things that ARE reliably checkable with free,
keyless, deterministic calls:

1. Wikipedia + Wikidata entity existence (real API calls, per the source
   skill's own recommended method — this is the single most reliable free
   signal in the whole rubric).
2. Reachability of the platform profiles the SITE ITSELF claims via
   schema.org `sameAs` links (Organization/Person JSON-LD) — confirms the
   declared YouTube/Reddit/LinkedIn/GitHub profile actually resolves,
   rather than being a dead or placeholder link.

What this module does NOT measure (flagged in `raw["unmeasured"]` and as
a standing finding): third-party mention volume, sentiment, subscriber/
follower counts, competitive context. Those require a web-search pass —
run one manually and feed the results back into the score if you need the
full rubric.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from seo_geo_aeo.core.fetcher import FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

_HTTP_TIMEOUT = 15.0
_USER_AGENT = "SEOGeoAeoEngine/1.0 (brand-authority-check)"

_PLATFORM_PATTERNS = {
    "youtube": re.compile(r"youtube\.com|youtu\.be", re.I),
    "reddit": re.compile(r"reddit\.com", re.I),
    "linkedin": re.compile(r"linkedin\.com", re.I),
    "github": re.compile(r"github\.com", re.I),
    "wikipedia": re.compile(r"wikipedia\.org", re.I),
    "wikidata": re.compile(r"wikidata\.org", re.I),
    "twitter_x": re.compile(r"twitter\.com|x\.com", re.I),
}

# Weights per geo-brand-mentions SKILL.md's composite formula.
_WEIGHTS = {
    "youtube": 0.25,
    "reddit": 0.25,
    "wikipedia": 0.20,
    "linkedin": 0.15,
    "other": 0.15,
}


def extract_same_as_links(page: ParsedPage) -> dict[str, str]:
    """Return {platform: url} for sameAs links found in Organization/Person schema."""
    found: dict[str, str] = {}
    for block in page.schema_blocks:
        same_as = block.get("sameAs")
        urls: list[str] = []
        if isinstance(same_as, str):
            urls = [same_as]
        elif isinstance(same_as, list):
            urls = [u for u in same_as if isinstance(u, str)]
        for url in urls:
            host = urlparse(url).netloc.lower()
            for platform, pattern in _PLATFORM_PATTERNS.items():
                if pattern.search(host) and platform not in found:
                    found[platform] = url
    return found


def _check_wikipedia_wikidata(brand_name: str) -> tuple[float, list[Finding], dict]:
    """Real, keyless API check against Wikipedia + Wikidata, per the source skill's
    own recommended method (API check first, never rely on search alone)."""
    findings: list[Finding] = []
    raw: dict = {"wikipedia_title": None, "wikidata_id": None}
    score = 0.0

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT, headers={"User-Agent": _USER_AGENT}) as client:
            wiki_resp = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={"action": "query", "list": "search", "srsearch": brand_name, "format": "json"},
            )
            wiki_resp.raise_for_status()
            results = wiki_resp.json().get("query", {}).get("search", [])
            if results and brand_name.lower() in results[0].get("title", "").lower():
                title = results[0]["title"]
                raw["wikipedia_title"] = title
                score += 60.0
            else:
                findings.append(
                    Finding(
                        severity=Severity.LOW,
                        title="No Wikipedia article found",
                        detail=f"Wikipedia search API found no direct match for '{brand_name}'. "
                        "This may be a genuine notability gap, or the brand name used here "
                        "doesn't match the article title exactly.",
                    )
                )

            wd_resp = client.get(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbsearchentities",
                    "search": brand_name,
                    "language": "en",
                    "format": "json",
                },
            )
            wd_resp.raise_for_status()
            entities = wd_resp.json().get("search", [])
            if entities:
                raw["wikidata_id"] = entities[0].get("id")
                score += 40.0
            else:
                findings.append(
                    Finding(
                        severity=Severity.LOW,
                        title="No Wikidata entity found",
                        detail=f"Wikidata search API found no entity for '{brand_name}'. A "
                        "Wikidata item is free to create and doesn't require notability the "
                        "way a Wikipedia article does.",
                    )
                )
    except httpx.HTTPError as exc:
        findings.append(
            Finding(
                severity=Severity.LOW,
                title="Wikipedia/Wikidata check failed",
                detail=f"Could not reach Wikipedia/Wikidata APIs: {exc}. Score for this "
                "dimension is based on sameAs links only.",
            )
        )

    return score, findings, raw


def _check_reachable(url: str, fetcher: SafeFetcher) -> bool:
    try:
        result = fetcher.fetch(url, respect_robots_override=False)
        return result.status_code < 400
    except (FetchError, UnsafeURLError):
        return False


def score_brand_authority(
    page: ParsedPage,
    *,
    brand_name: str | None = None,
    fetcher: SafeFetcher | None = None,
) -> DimensionScore:
    """Score brand authority from sameAs-declared profiles + Wikipedia/Wikidata.

    `brand_name` enables the Wikipedia/Wikidata API check. Without it, that
    component is skipped and the remaining weight is renormalized over the
    platforms that were checked — the score returned is always based only
    on what was actually verified, never a guessed default.
    """
    fetcher = fetcher or SafeFetcher()
    findings: list[Finding] = []
    same_as = extract_same_as_links(page)

    component_scores: dict[str, float] = {}

    for platform in ("youtube", "reddit", "linkedin"):
        if platform in same_as:
            reachable = _check_reachable(same_as[platform], fetcher)
            component_scores[platform] = 60.0 if reachable else 20.0
            if not reachable:
                findings.append(
                    Finding(
                        severity=Severity.MEDIUM,
                        title=f"Declared {platform} profile is unreachable",
                        detail=f"{same_as[platform]} is linked in schema but did not return a "
                        "successful response — likely dead, renamed, or placeholder.",
                        page_url=page.url,
                    )
                )
        else:
            component_scores[platform] = 0.0
            findings.append(
                Finding(
                    severity=Severity.MEDIUM if platform in ("youtube", "reddit") else Severity.LOW,
                    title=f"No {platform} profile declared",
                    detail=f"No sameAs link to {platform} found in this page's Organization/"
                    "Person schema. Note: this only checks what the site self-declares — "
                    f"third-party {platform} mention volume requires a manual web-search pass.",
                    page_url=page.url,
                )
            )

    other_platforms_found = sum(1 for p in ("github", "twitter_x") if p in same_as)
    component_scores["other"] = min(other_platforms_found * 50.0, 100.0)

    wiki_findings: list[Finding] = []
    wiki_raw: dict = {}
    if brand_name:
        wiki_score, wiki_findings, wiki_raw = _check_wikipedia_wikidata(brand_name)
        component_scores["wikipedia"] = wiki_score
    elif "wikipedia" in same_as or "wikidata" in same_as:
        component_scores["wikipedia"] = 50.0  # declared but not independently verified
    findings.extend(wiki_findings)

    applicable_weights = {k: v for k, v in _WEIGHTS.items() if k in component_scores}
    weight_sum = sum(applicable_weights.values())
    if weight_sum == 0:
        overall = 0.0
    else:
        overall = sum(
            component_scores[k] * (w / weight_sum) for k, w in applicable_weights.items()
        )

    return DimensionScore(
        dimension="brand_authority",
        score=round(overall, 1),
        findings=findings,
        raw={
            "component_scores": component_scores,
            "same_as_declared": same_as,
            "wikipedia_wikidata": wiki_raw,
            "unmeasured": [
                "third-party mention volume (YouTube/Reddit/LinkedIn)",
                "sentiment of mentions",
                "subscriber/follower counts",
                "competitive context",
            ],
        },
    )
