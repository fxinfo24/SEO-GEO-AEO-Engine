"""
Runs audits: fetch -> parse -> score -> compose, single-page or site-wide.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from seo_geo_aeo.core.crawler import crawl_site
from seo_geo_aeo.core.fetcher import FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.parser import ParsedPage, parse_page
from seo_geo_aeo.core.scoring import (
    CompositeResult,
    CompositeScorer,
    DimensionScore,
    aggregate_dimension_scores,
)
from seo_geo_aeo.modules.ai_citation_likelihood import score_ai_citation_likelihood
from seo_geo_aeo.modules.brand_authority import extract_same_as_links, score_brand_authority
from seo_geo_aeo.modules.content_quality import score_content_quality
from seo_geo_aeo.modules.eeat import score_eeat
from seo_geo_aeo.modules.geo_citability import score_citability
from seo_geo_aeo.modules.geo_crawlers import analyze_crawler_access
from seo_geo_aeo.modules.geo_schema import score_schema
from seo_geo_aeo.modules.platform_optimization import score_platform_optimization
from seo_geo_aeo.modules.seo_technical import score_on_page_seo
from seo_geo_aeo.modules.technical_seo import score_technical_seo

logger = logging.getLogger(__name__)

# Dimensions each profile scores. technical_geo is a domain-level check (one
# robots.txt fetch) so it's handled once per audit, never per-page.
_PAGE_DIMENSIONS = {
    "geo": ("schema", "ai_citability", "content_eeat", "brand_authority", "platform_optimization"),
    "seo": ("technical_seo", "on_page", "content_quality", "schema"),
    # platform_optimization deliberately excluded here: it is not in
    # PROFILE_WEIGHTS["aeo"], so computing it for AEO ran real network calls
    # (schema/sameAs-derived checks) whose result CompositeScorer.combine()
    # then discarded every time. Removed rather than "fixed" by adding it to
    # the weight table, since its 10% GEO weight was never validated for AEO.
    "aeo": ("schema", "ai_citability", "content_eeat", "brand_authority", "ai_citation_likelihood"),
}
_DOMAIN_DIMENSIONS = {
    "geo": ("technical_geo",),
    "seo": (),
    "aeo": ("technical_geo",),
}


def _score_page_dimensions(
    page: ParsedPage,
    profile: str,
    *,
    fetch_headers: dict[str, str] | None = None,
    fetch_elapsed_seconds: float | None = None,
    fetcher: SafeFetcher | None = None,
    page_type_hint: str = "default",
    brand_name: str | None = None,
) -> dict[str, DimensionScore]:
    wanted = _PAGE_DIMENSIONS[profile]
    scores: dict[str, DimensionScore] = {}

    schema_ds = score_schema(page) if "schema" in wanted else None
    if schema_ds is not None:
        scores["schema"] = schema_ds

    if "ai_citability" in wanted:
        scores["ai_citability"] = score_citability(page)

    if "content_eeat" in wanted:
        scores["content_eeat"] = score_eeat(page, page_type_hint=page_type_hint)

    same_as: dict[str, str] = {}
    if "brand_authority" in wanted or "platform_optimization" in wanted:
        same_as = extract_same_as_links(page)

    if "brand_authority" in wanted:
        scores["brand_authority"] = score_brand_authority(page, brand_name=brand_name, fetcher=fetcher)

    if "platform_optimization" in wanted:
        scores["platform_optimization"] = score_platform_optimization(
            page,
            schema_score=schema_ds.score if schema_ds else 0.0,
            same_as_links=same_as,
            fetch_elapsed_seconds=fetch_elapsed_seconds,
        )

    if "technical_seo" in wanted:
        scores["technical_seo"] = score_technical_seo(page, fetcher=fetcher)

    if "on_page" in wanted:
        scores["on_page"] = score_on_page_seo(page, fetch_headers or {}, fetcher=fetcher)

    if "content_quality" in wanted:
        scores["content_quality"] = score_content_quality(page, page_type_hint=page_type_hint)

    if "ai_citation_likelihood" in wanted:
        scores["ai_citation_likelihood"] = score_ai_citation_likelihood(page)

    return scores


def run_audit(
    url: str,
    *,
    profile: str = "geo",
    fetcher: SafeFetcher | None = None,
    render: bool = False,
    render_wait_until: str = "load",
    render_timeout_seconds: float = 30.0,
    page_type_hint: str = "default",
    brand_name: str | None = None,
) -> CompositeResult:
    if profile not in _PAGE_DIMENSIONS:
        raise ValueError(f"Unknown profile {profile!r}; choose from {sorted(_PAGE_DIMENSIONS)}")

    aux_fetcher = fetcher or SafeFetcher()

    if render:
        from seo_geo_aeo.core.render_fetcher import RenderFetcher

        with RenderFetcher(
            user_agent=aux_fetcher.user_agent,
            respect_robots=aux_fetcher.respect_robots,
            wait_until=render_wait_until,
            timeout_seconds=render_timeout_seconds,
            deadline=aux_fetcher.deadline,
        ) as render_fetcher:
            fetch_result = render_fetcher.fetch(url)
    else:
        fetch_result = aux_fetcher.fetch(url)

    fetcher = aux_fetcher  # everything below this point always uses the plain fetcher
    page = parse_page(fetch_result.final_url, fetch_result.text)

    dimension_scores = _score_page_dimensions(
        page,
        profile,
        fetch_headers=fetch_result.headers,
        fetch_elapsed_seconds=fetch_result.elapsed_seconds,
        fetcher=fetcher,
        page_type_hint=page_type_hint,
        brand_name=brand_name,
    )

    for domain_dim in _DOMAIN_DIMENSIONS[profile]:
        if domain_dim == "technical_geo":
            try:
                dimension_scores["technical_geo"] = analyze_crawler_access(fetch_result.final_url, fetcher)
            except (FetchError, UnsafeURLError) as exc:
                logger.warning("Crawler access check failed for %s: %s", url, exc)

    scorer = CompositeScorer(profile)
    return scorer.combine(dimension_scores)


def run_profiles(
    url: str,
    *,
    profiles: Sequence[str],
    fetcher: SafeFetcher | None = None,
    render: bool = False,
    render_wait_until: str = "load",
    render_timeout_seconds: float = 30.0,
    page_type_hint: str = "default",
    brand_name: str | None = None,
) -> dict[str, CompositeResult]:
    """Fetch and parse `url` once, then score every profile in `profiles`
    from that shared page data.

    Per RoadMap.md Phase 7: `cmd_report()` used to call `run_audit()` once
    per profile, which fetched, parsed, and (for `render=True`) spun up a
    fresh headless-browser session 2-3x for what is, from the target site's
    perspective, one visit. This is the shared-fetch replacement for that
    loop. Domain-level checks (currently just `technical_geo`'s robots.txt
    fetch) are also deduplicated across profiles that both declare them
    (aeo + geo), rather than fetched once per profile.

    Per-profile dimension modules (brand_authority's Wikipedia/Wikidata
    call, platform_optimization's same_as extraction, etc.) are NOT shared
    here — those are cheap, page-local computations or external checks that
    legitimately vary by what each profile needs (Phase 7.4: "fetch once"
    means one fetch of the audited page, not that every external check must
    be collapsed).
    """
    unknown = [p for p in profiles if p not in _PAGE_DIMENSIONS]
    if unknown:
        raise ValueError(f"Unknown profile(s) {unknown}; choose from {sorted(_PAGE_DIMENSIONS)}")

    aux_fetcher = fetcher or SafeFetcher()

    if render:
        from seo_geo_aeo.core.render_fetcher import RenderFetcher

        with RenderFetcher(
            user_agent=aux_fetcher.user_agent,
            respect_robots=aux_fetcher.respect_robots,
            wait_until=render_wait_until,
            timeout_seconds=render_timeout_seconds,
            deadline=aux_fetcher.deadline,
        ) as render_fetcher:
            fetch_result = render_fetcher.fetch(url)
    else:
        fetch_result = aux_fetcher.fetch(url)

    page = parse_page(fetch_result.final_url, fetch_result.text)

    domain_dims_needed: set[str] = set()
    for profile in profiles:
        domain_dims_needed.update(_DOMAIN_DIMENSIONS[profile])

    domain_scores: dict[str, DimensionScore] = {}
    if "technical_geo" in domain_dims_needed:
        try:
            domain_scores["technical_geo"] = analyze_crawler_access(fetch_result.final_url, aux_fetcher)
        except (FetchError, UnsafeURLError) as exc:
            logger.warning("Crawler access check failed for %s: %s", url, exc)

    results: dict[str, CompositeResult] = {}
    for profile in profiles:
        dimension_scores = _score_page_dimensions(
            page,
            profile,
            fetch_headers=fetch_result.headers,
            fetch_elapsed_seconds=fetch_result.elapsed_seconds,
            fetcher=aux_fetcher,
            page_type_hint=page_type_hint,
            brand_name=brand_name,
        )
        for domain_dim in _DOMAIN_DIMENSIONS[profile]:
            if domain_dim in domain_scores:
                dimension_scores[domain_dim] = domain_scores[domain_dim]

        scorer = CompositeScorer(profile)
        results[profile] = scorer.combine(dimension_scores)

    return results


def run_site_audit(
    seed_url: str,
    *,
    profile: str = "geo",
    max_pages: int = 20,
    fetcher: SafeFetcher | None = None,
    render: bool = False,
    render_wait_until: str = "load",
    render_timeout_seconds: float = 30.0,
    page_type_hint: str = "default",
    brand_name: str | None = None,
) -> tuple[CompositeResult, dict]:
    if profile not in _PAGE_DIMENSIONS:
        raise ValueError(f"Unknown profile {profile!r}; choose from {sorted(_PAGE_DIMENSIONS)}")

    aux_fetcher = fetcher or SafeFetcher()

    if render:
        from seo_geo_aeo.core.render_fetcher import RenderFetcher

        with RenderFetcher(
            user_agent=aux_fetcher.user_agent,
            respect_robots=aux_fetcher.respect_robots,
            wait_until=render_wait_until,
            timeout_seconds=render_timeout_seconds,
            deadline=aux_fetcher.deadline,
        ) as render_fetcher:
            crawl_result = crawl_site(seed_url, fetcher=render_fetcher, max_pages=max_pages)
    else:
        crawl_result = crawl_site(seed_url, fetcher=aux_fetcher, max_pages=max_pages)

    if not crawl_result.pages:
        raise FetchError(
            f"Crawl of {seed_url} returned zero pages "
            f"({len(crawl_result.failed_urls)} failed fetches). Nothing to score."
        )

    per_page_scores: list[dict[str, DimensionScore]] = []
    for page in crawl_result.pages:
        per_page_scores.append(
            _score_page_dimensions(
                page,
                profile,
                fetcher=aux_fetcher,
                page_type_hint=page_type_hint,
                brand_name=brand_name,
            )
        )

    aggregated: dict[str, DimensionScore] = {}
    for dim in _PAGE_DIMENSIONS[profile]:
        dim_scores = [scores[dim] for scores in per_page_scores if dim in scores]
        if dim_scores:
            aggregated[dim] = aggregate_dimension_scores(dim, dim_scores)

    for domain_dim in _DOMAIN_DIMENSIONS[profile]:
        if domain_dim == "technical_geo":
            try:
                aggregated["technical_geo"] = analyze_crawler_access(seed_url, aux_fetcher)
            except (FetchError, UnsafeURLError) as exc:
                logger.warning("Crawler access check failed for %s: %s", seed_url, exc)

    scorer = CompositeScorer(profile)
    result = scorer.combine(aggregated)

    crawl_meta = {
        "pages_crawled": len(crawl_result.pages),
        "urls_crawled": [p.url for p in crawl_result.pages],
        "failed_urls": crawl_result.failed_urls,
        "skipped_offsite_links": crawl_result.skipped_offsite,
    }
    return result, crawl_meta