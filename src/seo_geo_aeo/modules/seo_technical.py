"""On-page SEO + security-header scoring (dimension: "on_page").

Merges seo-technical, seo-page, and the "Technical On-Page" section of the
standalone SEO-GEO-AEO-Skill.md into one deterministic scorer, plus the
security checks (headers, exposed-secret paths) that skill defined.

Despite this file's name, this is NOT the `technical_seo` dimension —
that's `modules.technical_seo.score_technical_seo()` (crawlability,
indexability, site architecture, mobile-friendliness; RoadMap.md Phase
3.2). This module covers title/meta/heading/canonical/OG/security-header
signals scoped to a single page, hence `score_on_page_seo()`. The two
modules previously both exported a function named `score_technical_seo`,
which the orchestrator had to alias around (`as score_on_page_factors`) —
renamed per Phase 3.2 to remove that ambiguity at the source.
"""

from __future__ import annotations

from seo_geo_aeo.core.fetcher import FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.core.scoring import DimensionScore, Finding, Severity

_REQUIRED_SECURITY_HEADERS = (
    "strict-transport-security",
    "x-content-type-options",
    "x-frame-options",
)

# Confirm existence only per SEO-GEO-AEO-Skill.md's rule: never read/download contents.
_SENSITIVE_PATHS = ("/.env", "/.git/config")


def _check_exposed_paths(base_url: str, fetcher: SafeFetcher) -> list[Finding]:
    from urllib.parse import urljoin

    findings: list[Finding] = []
    for path in _SENSITIVE_PATHS:
        url = urljoin(base_url, path)
        try:
            result = fetcher.fetch(url, respect_robots_override=False)
        except (FetchError, UnsafeURLError):
            continue
        if result.status_code == 200:
            findings.append(
                Finding(
                    severity=Severity.CRITICAL,
                    title=f"Exposed sensitive path: {path}",
                    detail=f"{url} returned HTTP 200. This path should return 403/404. Do not "
                    "access its contents — flag for the site owner to lock it down immediately.",
                    page_url=base_url,
                )
            )
    return findings


def score_on_page_seo(
    page: ParsedPage,
    response_headers: dict[str, str],
    *,
    fetcher: SafeFetcher | None = None,
    check_exposed_paths: bool = True,
) -> DimensionScore:
    findings: list[Finding] = []
    score = 0.0
    headers_lower = {k.lower(): v for k, v in response_headers.items()}

    # Title tag (10)
    if page.title:
        score += 5.0
        if 30 <= len(page.title) <= 65:
            score += 5.0
        else:
            findings.append(
                Finding(
                    severity=Severity.LOW,
                    title="Title tag length outside 50-60 char sweet spot",
                    detail=f"Title is {len(page.title)} characters: {page.title!r}",
                    page_url=page.url,
                )
            )
    else:
        findings.append(
            Finding(Severity.HIGH, "Missing <title> tag", "No <title> element found.", page.url)
        )

    # Meta description (10)
    if page.meta_description:
        score += 5.0
        if 120 <= len(page.meta_description) <= 165:
            score += 5.0
    else:
        findings.append(
            Finding(
                Severity.MEDIUM,
                "Missing meta description",
                "No <meta name=description> found.",
                page.url,
            )
        )

    # Heading hierarchy (10)
    if page.h1_count == 1:
        score += 10.0
    elif page.h1_count == 0:
        findings.append(Finding(Severity.HIGH, "No H1 found", "Page has zero H1 tags.", page.url))
    else:
        findings.append(
            Finding(
                Severity.MEDIUM,
                f"Multiple H1 tags ({page.h1_count})",
                "Multiple H1s dilute topical signal for both search and AI parsing.",
                page.url,
            )
        )

    # Canonical (10)
    if page.canonical:
        score += 10.0
    else:
        findings.append(
            Finding(Severity.MEDIUM, "No canonical tag", "No rel=canonical link found.", page.url)
        )

    # Robots meta sanity (5)
    if not page.robots_meta or "noindex" not in page.robots_meta.lower():
        score += 5.0

    # Viewport / mobile (10)
    if page.has_viewport_meta:
        score += 10.0
    else:
        findings.append(
            Finding(
                Severity.HIGH,
                "No viewport meta tag",
                "Missing <meta name=viewport>; page will not render responsively on mobile.",
                page.url,
            )
        )

    # Images / alt text (10)
    if page.images_total == 0:
        score += 10.0
    else:
        alt_ratio = 1 - (page.images_missing_alt / page.images_total)
        score += 10.0 * alt_ratio
        if page.images_missing_alt:
            findings.append(
                Finding(
                    Severity.LOW,
                    f"{page.images_missing_alt}/{page.images_total} images missing alt text",
                    "Add descriptive alt text for accessibility and image SEO.",
                    page.url,
                )
            )

    # Open Graph (5)
    if {"og:title", "og:description", "og:image"} <= set(page.open_graph):
        score += 5.0
    else:
        findings.append(
            Finding(
                Severity.LOW,
                "Incomplete Open Graph tags",
                f"Found: {sorted(page.open_graph)}. Missing core og:title/og:description/og:image.",
                page.url,
            )
        )

    # Security headers (20)
    present_headers = [h for h in _REQUIRED_SECURITY_HEADERS if h in headers_lower]
    score += (len(present_headers) / len(_REQUIRED_SECURITY_HEADERS)) * 15.0
    if len(present_headers) < len(_REQUIRED_SECURITY_HEADERS):
        missing = sorted(set(_REQUIRED_SECURITY_HEADERS) - set(present_headers))
        findings.append(
            Finding(
                Severity.MEDIUM,
                "Missing security headers",
                f"Missing: {', '.join(missing)}. These are a trust-signal gap for both users "
                "and AI systems evaluating site trustworthiness.",
                page.url,
            )
        )
    if page.url.startswith("https://"):
        score += 5.0
    else:
        findings.append(
            Finding(Severity.CRITICAL, "Page served over HTTP", "Site is not on HTTPS.", page.url)
        )

    # Mixed content (5) — rough heuristic on raw HTML.
    if page.url.startswith("https://") and "http://" not in page.raw_html:
        score += 5.0
    elif page.url.startswith("https://"):
        findings.append(
            Finding(
                Severity.LOW,
                "Possible mixed content",
                "Found http:// references on an https:// page; verify none are active resource loads.",
                page.url,
            )
        )

    if check_exposed_paths and fetcher is not None:
        exposed_findings = _check_exposed_paths(page.url, fetcher)
        findings.extend(exposed_findings)
        if exposed_findings:
            score = max(score - 20.0, 0.0)

    return DimensionScore(
        dimension="on_page",
        score=round(min(score, 100.0), 1),
        findings=findings,
        raw={"security_headers_present": present_headers},
    )
