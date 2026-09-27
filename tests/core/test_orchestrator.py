"""
Tests for orchestrator.run_profiles() -- the shared-fetch multi-profile
entry point (RoadMap.md Phase 7 / item 8: "share one fetch across
profiles"). Locks in the specific performance contract the roadmap asks
for: one fetch of the audited page and one fetch of its robots.txt no
matter how many profiles are scored from it.
"""
from __future__ import annotations

from collections import Counter

import pytest

from seo_geo_aeo.core.fetcher import FetchResult
from seo_geo_aeo.core.orchestrator import run_profiles

_PAGE_HTML = """<html>
<head>
    <title>A perfectly adequate title for this test fixture page here</title>
    <meta name="description" content="A meta description long enough to read as reasonable for testing purposes across every dimension module that inspects it.">
    <link rel="canonical" href="https://example.com/">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script type="application/ld+json">
    {"@type": "Organization", "sameAs": ["https://youtube.com/@acme"]}
    </script>
</head>
<body>
    <h1>Main Heading</h1>
    <p>Some reasonably long body copy so word count checks don't all bottom out at zero.</p>
    <a href="/about">About</a>
    <a href="/contact">Contact</a>
</body>
</html>"""


class _CountingFetcher:
    """Duck-typed SafeFetcher stand-in that records every URL fetched so
    tests can assert exact call counts per URL, rather than a single global
    counter that would conflate the main-page fetch with the unrelated
    robots.txt / sensitive-path / llms.txt probes other modules also make."""

    user_agent = "test-agent"
    respect_robots = True

    def __init__(self, page_url: str) -> None:
        self._page_url = page_url
        self.calls: list[str] = []

    def is_allowed(self, url: str) -> bool:
        return True

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        self.calls.append(url)
        if url == self._page_url:
            text = _PAGE_HTML
        else:
            # robots.txt / llms.txt / sensitive-path probes: a harmless 404.
            text = ""
        return FetchResult(
            url=url, final_url=url, status_code=200, headers={}, text=text, elapsed_seconds=0.05
        )

    @property
    def call_counts(self) -> Counter[str]:
        return Counter(self.calls)


_URL = "https://example.com/"


def test_run_profiles_fetches_the_audited_page_exactly_once():
    """The core Phase 7 contract: regardless of how many profiles are
    scored, the audited URL itself is fetched exactly once."""
    fetcher = _CountingFetcher(_URL)

    results = run_profiles(_URL, profiles=["seo", "aeo", "geo"], fetcher=fetcher)

    assert set(results) == {"seo", "aeo", "geo"}
    assert fetcher.call_counts[_URL] == 1


def test_run_profiles_dedupes_shared_domain_level_checks():
    """aeo and geo both declare technical_geo (a robots.txt fetch via
    analyze_crawler_access). Without dedup this would be fetched twice for
    a 3-profile report; run_profiles must fetch it once and reuse the
    result for both profiles."""
    fetcher = _CountingFetcher(_URL)

    results = run_profiles(_URL, profiles=["aeo", "geo"], fetcher=fetcher)

    robots_calls = fetcher.call_counts["https://example.com/robots.txt"]
    assert robots_calls == 1
    assert "technical_geo" in results["aeo"].dimension_scores
    assert "technical_geo" in results["geo"].dimension_scores


def test_run_profiles_seo_only_skips_domain_level_fetch_entirely():
    """seo declares no domain-level dimensions -- analyze_crawler_access
    should never run, so robots.txt is never fetched for a seo-only call."""
    fetcher = _CountingFetcher(_URL)

    run_profiles(_URL, profiles=["seo"], fetcher=fetcher)

    assert fetcher.call_counts["https://example.com/robots.txt"] == 0


def test_run_profiles_returns_a_composite_result_per_profile():
    fetcher = _CountingFetcher(_URL)

    results = run_profiles(_URL, profiles=["seo", "geo"], fetcher=fetcher)

    assert results["seo"].profile == "seo"
    assert results["geo"].profile == "geo"
    assert 0.0 <= results["seo"].overall_score <= 100.0
    assert 0.0 <= results["geo"].overall_score <= 100.0


def test_run_profiles_rejects_unknown_profile():
    fetcher = _CountingFetcher(_URL)

    with pytest.raises(ValueError, match="Unknown profile"):
        run_profiles(_URL, profiles=["seo", "not-a-real-profile"], fetcher=fetcher)


def test_run_profiles_single_profile_matches_run_audit_dimension_set():
    """A single-profile run_profiles() call should produce the same set of
    scored dimensions as the equivalent run_audit() call -- the shared-fetch
    path must not silently drop or add dimensions."""
    from seo_geo_aeo.core.orchestrator import run_audit

    fetcher_a = _CountingFetcher(_URL)
    fetcher_b = _CountingFetcher(_URL)

    single = run_audit(_URL, profile="seo", fetcher=fetcher_a)
    multi = run_profiles(_URL, profiles=["seo"], fetcher=fetcher_b)["seo"]

    assert set(single.dimension_scores) == set(multi.dimension_scores)
