"""Structured data (schema.org JSON-LD) scoring.

Merges the three overlapping source skills (schema-markup, seo-schema,
geo-schema) into one scorer. Focuses on JSON-LD only — Microdata/RDFa are
flagged as a migration recommendation per the source rubric, not parsed.
"""

from __future__ import annotations

from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

# Deprecated/changed schemas worth flagging (per geo-schema SKILL.md, Step 4).
_DEPRECATED_TYPES = {
    "SpecialAnnouncement": "Deprecated in 2023 (was for COVID-era announcements); remove if present.",
    "CourseInfo": "Replaced by updated Course schema properties in 2024.",
}

_HIGH_VALUE_TYPES = {
    "Organization",
    "LocalBusiness",
    "Article",
    "BlogPosting",
    "NewsArticle",
    "Product",
    "FAQPage",
    "HowTo",
    "SoftwareApplication",
    "WebSite",
    "BreadcrumbList",
    "Person",
}


def _type_names(block: dict) -> list[str]:
    raw_type = block.get("@type")
    if raw_type is None:
        return []
    if isinstance(raw_type, list):
        return [str(t) for t in raw_type]
    return [str(raw_type)]


def score_schema(page: ParsedPage) -> DimensionScore:
    findings: list[Finding] = []
    blocks = [b for b in page.schema_blocks if "@parse_error" not in b]
    parse_errors = [b for b in page.schema_blocks if "@parse_error" in b]

    for err in parse_errors:
        findings.append(
            Finding(
                severity=Severity.MEDIUM,
                title="Invalid JSON-LD block",
                detail=f"A <script type=application/ld+json> block failed to parse: "
                f"{err.get('raw', '')[:120]}",
                page_url=page.url,
            )
        )

    if not blocks:
        findings.append(
            Finding(
                severity=Severity.HIGH,
                title="No structured data found",
                detail="No valid JSON-LD schema.org markup detected on this page.",
                page_url=page.url,
            )
        )
        return DimensionScore(
            dimension="schema",
            score=0.0 if not parse_errors else 10.0,
            findings=findings,
            raw={"types_found": [], "same_as_count": 0},
        )

    score = 0.0
    types_found: set[str] = set()
    same_as_count = 0
    has_organization_or_person = False
    has_article_with_author = False

    for block in blocks:
        types = _type_names(block)
        types_found.update(types)

        for dep_type, note in _DEPRECATED_TYPES.items():
            if dep_type in types:
                findings.append(
                    Finding(
                        severity=Severity.LOW,
                        title=f"Deprecated schema type: {dep_type}",
                        detail=note,
                        page_url=page.url,
                    )
                )

        if {"Organization", "LocalBusiness", "Person"} & set(types):
            has_organization_or_person = True
            same_as = block.get("sameAs")
            if isinstance(same_as, list):
                same_as_count += len(same_as)
            elif isinstance(same_as, str):
                same_as_count += 1

        if {"Article", "BlogPosting", "NewsArticle"} & set(types):
            author = block.get("author")
            if author:
                has_article_with_author = True

    # Weighting adapted from geo-schema SKILL.md's 0-100 rubric.
    score += 15.0 if has_organization_or_person else 0.0
    score += min(same_as_count * 3.0, 15.0)
    score += 10.0 if has_article_with_author else 0.0
    score += 10.0 if types_found & _HIGH_VALUE_TYPES else 0.0
    score += 5.0 if "WebSite" in types_found else 0.0
    score += 5.0 if "BreadcrumbList" in types_found else 0.0
    score += 5.0  # JSON-LD format used at all (vs. Microdata/RDFa)
    score += 0.0 if parse_errors else 10.0
    score += 5.0 if not any(t in _DEPRECATED_TYPES for t in types_found) else 0.0
    # Remaining points (up to 20) for schema.org type coverage breadth.
    score += min(len(types_found) * 2.5, 20.0)

    if not has_organization_or_person:
        findings.append(
            Finding(
                severity=Severity.HIGH,
                title="No Organization/LocalBusiness/Person schema",
                detail="This is the foundational entity-recognition schema every AI platform "
                "checks for. Add an Organization block with sameAs links.",
                page_url=page.url,
            )
        )
    if has_organization_or_person and same_as_count < 3:
        findings.append(
            Finding(
                severity=Severity.MEDIUM,
                title="Few or no sameAs links",
                detail=f"Only {same_as_count} sameAs link(s) found. Add Wikipedia, Wikidata, "
                "LinkedIn, YouTube, and other profile URLs to build the entity graph AI "
                "systems use for trust.",
                page_url=page.url,
            )
        )

    return DimensionScore(
        dimension="schema",
        score=round(min(score, 100.0), 1),
        findings=findings,
        raw={
            "types_found": sorted(types_found),
            "same_as_count": same_as_count,
            "has_organization_or_person": has_organization_or_person,
            "has_article_with_author": has_article_with_author,
        },
    )
