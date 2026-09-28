"""Multi-page site crawl: BFS from a seed URL, same-origin only, page-budgeted."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from seo_geo_aeo.core.fetcher import Fetcher, FetchError, SafeFetcher, UnsafeURLError
from seo_geo_aeo.core.parser import ParsedPage, parse_page

# Hard ceiling on frontier size so a page with thousands of internal links
# can't blow up memory before max_pages is even reached.
_MAX_FRONTIER_MULTIPLIER = 5


@dataclass
class CrawlResult:
    pages: list[ParsedPage]
    failed_urls: dict[str, str] = field(default_factory=dict)
    skipped_offsite: int = 0


def crawl_site(seed_url: str, *, fetcher: Fetcher | None = None, max_pages: int = 20) -> CrawlResult:
    """BFS-crawl same-origin HTML pages starting at `seed_url`, up to `max_pages`.

    Respects robots.txt via SafeFetcher (same as single-page audits) and the
    fetcher's built-in per-host rate limiting. Non-HTML responses (by
    Content-Type) are parsed for links only if empty, otherwise skipped from
    scoring — a PDF or image linked internally won't be treated as a page.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be >= 1")

    fetcher = fetcher or SafeFetcher()
    seed_origin = urlparse(seed_url).netloc

    visited: set[str] = set()
    queue: list[str] = [seed_url]
    pages: list[ParsedPage] = []
    failed: dict[str, str] = {}
    skipped_offsite = 0
    frontier_cap = max_pages * _MAX_FRONTIER_MULTIPLIER

    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        normalized = url.split("#")[0]
        if normalized in visited:
            continue
        visited.add(normalized)

        try:
            result = fetcher.fetch(normalized)
        except (FetchError, UnsafeURLError) as exc:
            failed[normalized] = str(exc)
            continue

        content_type = result.headers.get("content-type", "")
        if content_type and "text/html" not in content_type:
            continue

        page = parse_page(result.final_url, result.text)
        pages.append(page)

        if len(visited) + len(queue) >= frontier_cap:
            continue

        for link in page.internal_links:
            link_clean = link.split("#")[0]
            if urlparse(link_clean).netloc != seed_origin:
                skipped_offsite += 1
                continue
            if link_clean not in visited and link_clean not in queue:
                queue.append(link_clean)

    return CrawlResult(pages=pages, failed_urls=failed, skipped_offsite=skipped_offsite)
