"""Runs audits: fetch -> parse -> score -> compose, single-page or site-wide."""

from __future__ import annotations

import logging

from seo_geo_aeo.core.crawler import crawl_site
from seo_geo_aeo.core.fetcher import FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.parser import ParsedPage, parse_page
from seo_geo_aeo.core.scoring import (
    CompositeResult,
    CompositeScorer,
    DimensionScore,
    aggregate_dimension_scores,
)
from seo_geo_aeo.modules.brand_authority import extract_same_as_links, score_brand_authority
from seo_geo_aeo.modules.eeat import score_eeat
from seo_geo_aeo.modules.geo_citability import score_citability
from seo_geo_aeo.modules.geo_crawlers import analyze_crawler_access
from seo_geo_aeo.modules.geo_schema import score_schema
from seo_geo_aeo.modules.platform_optimization import score_platform_optimization
from seo_geo_aeo.modules.seo_technical import score_technical_seo

logger = logging.getLogger(__name__)

# Dimensions each profile scores. technical_geo is a domain-level check (one
# robots.txt fetch) so it's handled once per audit, never per-page.
_PAGE_DIMENSIONS = {
    "geo": ("schema", "ai_citability", "content_eeat", "brand_authority", "platform_optimization"),
    "seo": ("on_page", "schema"),
    "aeo": ("schema", "ai_citability", "content_eeat", "brand_authority", "platform_optimization"),
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

    if "on_page" in wanted:
        scores["on_page"] = score_technical_seo(page, fetch_headers or {}, fetcher=fetcher)

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
    """Single-page audit: fetch `url`, score it, compose the result.

    `render=True` fetches the page content through headless Chromium
    (see core.render_fetcher) instead of a plain HTTP GET — use this for
    React/Vue/SPA sites where the server-delivered HTML is just an empty
    shell. All auxiliary checks (robots.txt, exposed-path probes, sameAs
    reachability) still use a plain SafeFetcher regardless of `render`,
    since rendering those is unnecessary overhead.

    `render_wait_until` defaults to "load" rather than Playwright's
    "networkidle" — ad-heavy or analytics-heavy sites often never go fully
    idle, which makes networkidle time out on pages that actually loaded
    fine. Use "networkidle" for SPAs that fetch data asynchronously after
    the load event, or "domcontentloaded" for a faster, earlier snapshot.

    `render_timeout_seconds` defaults to 30 (Playwright's own default) —
    raise it for pages with slow third-party ad/analytics loads, which can
    legitimately vary run-to-run on the same URL.
    """
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
    """Site-wide audit: crawl up to `max_pages` same-origin pages, score each,
    aggregate per-dimension, and compose one site-level result.

    `render=True` crawls and fetches every page through headless Chromium —
    necessary for SPA sites where both content AND internal links only exist
    after JS runs. This is significantly slower (a browser launch is not
    free) and NOT recommended for large `max_pages` on a server-rendered
    site that doesn't need it. See `run_audit` for `render_wait_until` and
    `render_timeout_seconds`.

    Returns (result, crawl_meta) where crawl_meta reports pages crawled,
    failed URLs, and offsite links skipped — useful for sanity-checking the
    crawl itself before trusting the score.
    """
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
