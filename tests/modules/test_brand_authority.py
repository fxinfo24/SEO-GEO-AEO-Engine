"""
Tests for the brand_authority scoring module (sameAs reachability +
Wikipedia/Wikidata entity checks). Wikipedia/Wikidata HTTP calls are mocked
via pytest-httpx so these tests never touch the network.
"""
from __future__ import annotations

import httpx

from seo_geo_aeo.core.fetcher import FetchError, FetchResult
from seo_geo_aeo.core.parser import ParsedPage
from seo_geo_aeo.modules.brand_authority import extract_same_as_links, score_brand_authority

_WIKI_URL = (
    "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=Acme&format=json"
)
_WIKIDATA_URL = (
    "https://www.wikidata.org/w/api.php?action=wbsearchentities&search=Acme"
    "&language=en&format=json"
)


class _StubFetcher:
    """Duck-typed SafeFetcher: resolves reachability per-platform by URL
    substring, so tests never depend on real network availability."""

    def __init__(self, reachable: set[str] | None = None, unreachable: set[str] | None = None) -> None:
        self._reachable = reachable or set()
        self._unreachable = unreachable or set()

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        if any(host in url for host in self._unreachable):
            raise FetchError(f"unreachable: {url}")
        status = 200 if any(host in url for host in self._reachable) else 404
        return FetchResult(
            url=url, final_url=url, status_code=status, headers={}, text="", elapsed_seconds=0.01
        )


def _make_page(**overrides) -> ParsedPage:
    defaults = {
        "url": "https://example.com",
        "title": "Test",
        "meta_description": None,
        "canonical": None,
        "robots_meta": None,
        "h1_count": 0,
        "headings": [],
        "word_count": 10,
        "schema_blocks": [],
        "images_total": 0,
        "images_missing_alt": 0,
        "internal_links": [],
        "external_links": [],
        "open_graph": {},
        "has_viewport_meta": False,
        "author_byline_present": False,
        "published_date": None,
        "modified_date": None,
        "raw_html": "<html><body><p>Test</p></body></html>",
    }
    defaults.update(overrides)
    return ParsedPage(**defaults)


def test_no_same_as_no_brand_name_scores_low_with_missing_findings():
    """Weak/missing input: no sameAs schema links at all and no brand_name
    (Wikipedia check skipped entirely) -- every declared-profile finding
    should fire and the score should be near the floor."""
    page = _make_page(schema_blocks=[])

    result = score_brand_authority(page, fetcher=_StubFetcher())

    assert result.dimension == "brand_authority"
    assert 0.0 <= result.score <= 100.0
    titles = {f.title for f in result.findings}
    assert "No youtube profile declared" in titles
    assert "No reddit profile declared" in titles
    assert "No linkedin profile declared" in titles
    assert "wikipedia" not in result.raw["component_scores"]


def test_extract_same_as_links_reads_organization_schema():
    page = _make_page(
        schema_blocks=[
            {
                "@type": "Organization",
                "sameAs": [
                    "https://youtube.com/@acme",
                    "https://reddit.com/r/acme",
                    "not-a-list-of-strings-entry",
                ],
            }
        ]
    )

    same_as = extract_same_as_links(page)

    assert same_as["youtube"] == "https://youtube.com/@acme"
    assert same_as["reddit"] == "https://reddit.com/r/acme"


def test_reachable_declared_profiles_score_well_no_unreachable_findings():
    """Strong input: youtube/reddit/linkedin all declared via sameAs and all
    reachable -- component scores should hit the reachable ceiling (60) and
    no 'unreachable' findings should appear."""
    page = _make_page(
        schema_blocks=[
            {
                "@type": "Organization",
                "sameAs": [
                    "https://youtube.com/@acme",
                    "https://reddit.com/r/acme",
                    "https://linkedin.com/company/acme",
                ],
            }
        ]
    )
    fetcher = _StubFetcher(reachable={"youtube.com", "reddit.com", "linkedin.com"})

    result = score_brand_authority(page, fetcher=fetcher)

    assert result.raw["component_scores"]["youtube"] == 60.0
    assert result.raw["component_scores"]["reddit"] == 60.0
    assert result.raw["component_scores"]["linkedin"] == 60.0
    assert not any("unreachable" in f.title for f in result.findings)


def test_unreachable_declared_profile_flags_medium_finding():
    """External failure: a declared youtube sameAs link that the fetcher
    cannot reach must drop to the dead-link floor (20) and raise a finding
    naming the platform, not silently score as if it were fine."""
    page = _make_page(
        schema_blocks=[{"@type": "Organization", "sameAs": ["https://youtube.com/@acme"]}]
    )
    fetcher = _StubFetcher(unreachable={"youtube.com"})

    result = score_brand_authority(page, fetcher=fetcher)

    assert result.raw["component_scores"]["youtube"] == 20.0
    assert any(f.title == "Declared youtube profile is unreachable" for f in result.findings)


def test_brand_name_with_wikipedia_and_wikidata_match(httpx_mock):
    """Strong input: brand_name supplied and both Wikipedia and Wikidata
    return a match -- wikipedia component should hit the full 100."""
    httpx_mock.add_response(url=_WIKI_URL, json={"query": {"search": [{"title": "Acme"}]}})
    httpx_mock.add_response(url=_WIKIDATA_URL, json={"search": [{"id": "Q12345"}]})
    page = _make_page(schema_blocks=[])

    result = score_brand_authority(page, brand_name="Acme", fetcher=_StubFetcher())

    assert result.raw["component_scores"]["wikipedia"] == 100.0
    assert result.raw["wikipedia_wikidata"]["wikipedia_title"] == "Acme"
    assert result.raw["wikipedia_wikidata"]["wikidata_id"] == "Q12345"
    assert not any("No Wikipedia article" in f.title for f in result.findings)


def test_brand_name_with_no_matches_flags_both_low_findings(httpx_mock):
    """Weak input: brand_name supplied but neither API finds a match --
    wikipedia component should be 0 and both LOW findings should fire."""
    httpx_mock.add_response(url=_WIKI_URL, json={"query": {"search": []}})
    httpx_mock.add_response(url=_WIKIDATA_URL, json={"search": []})
    page = _make_page(schema_blocks=[])

    result = score_brand_authority(page, brand_name="Acme", fetcher=_StubFetcher())

    assert result.raw["component_scores"]["wikipedia"] == 0.0
    titles = {f.title for f in result.findings}
    assert "No Wikipedia article found" in titles
    assert "No Wikidata entity found" in titles


def test_wikipedia_api_failure_is_reported_not_raised(httpx_mock):
    """External failure: the Wikipedia/Wikidata HTTP call itself fails.
    Must degrade to a specific finding, never propagate the exception."""
    httpx_mock.add_exception(httpx.ConnectError("connection refused"), url=_WIKI_URL)
    page = _make_page(schema_blocks=[])

    result = score_brand_authority(page, brand_name="Acme", fetcher=_StubFetcher())

    assert any(f.title == "Wikipedia/Wikidata check failed" for f in result.findings)
    assert result.raw["component_scores"]["wikipedia"] == 0.0


def test_declared_but_unverified_wikipedia_gets_partial_credit():
    """A sameAs wikipedia link with no brand_name to independently verify it
    should get partial (50) credit rather than 0 or full marks."""
    page = _make_page(
        schema_blocks=[
            {"@type": "Organization", "sameAs": ["https://en.wikipedia.org/wiki/Acme"]}
        ]
    )

    result = score_brand_authority(page, fetcher=_StubFetcher())

    assert result.raw["component_scores"]["wikipedia"] == 50.0


def test_raw_lists_unmeasured_signals():
    page = _make_page(schema_blocks=[])

    result = score_brand_authority(page, fetcher=_StubFetcher())

    assert "third-party mention volume (YouTube/Reddit/LinkedIn)" in result.raw["unmeasured"]


def test_score_never_exceeds_100():
    page = _make_page(
        schema_blocks=[
            {
                "@type": "Organization",
                "sameAs": [
                    "https://youtube.com/@acme",
                    "https://reddit.com/r/acme",
                    "https://linkedin.com/company/acme",
                    "https://github.com/acme",
                    "https://x.com/acme",
                ],
            }
        ]
    )
    fetcher = _StubFetcher(reachable={"youtube.com", "reddit.com", "linkedin.com"})

    result = score_brand_authority(page, fetcher=fetcher)

    assert result.score <= 100.0
