"""
Tests for the AI crawler access scoring module (robots.txt / llms.txt).
"""
from __future__ import annotations

from seo_geo_aeo.core.fetcher import FetchError, FetchResult
from seo_geo_aeo.modules.geo_crawlers import TIER_1_AGENTS, TIER_2_AGENTS, analyze_crawler_access


class _StubFetcher:
    """Duck-typed SafeFetcher stand-in: serves canned robots.txt / llms.txt
    bodies by exact URL so these tests never touch the network."""

    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self._responses = responses

    def fetch(self, url: str, *, respect_robots_override: bool | None = None) -> FetchResult:
        body = self._responses.get(url)
        if body is None:
            raise FetchError(f"no stub configured for {url}")
        if isinstance(body, Exception):
            raise body
        return FetchResult(
            url=url, final_url=url, status_code=200, headers={}, text=body, elapsed_seconds=0.01
        )


_DOMAIN = "https://example.com/"
_ROBOTS_URL = "https://example.com/robots.txt"
_LLMS_URL = "https://example.com/llms.txt"


def test_open_robots_txt_scores_high_and_flags_missing_llms_txt():
    """Strong input: no robots.txt restrictions at all (empty body => allow
    all) plus no llms.txt should score just the 90/100 reachable without
    llms.txt, and flag the missing llms.txt as LOW."""
    fetcher = _StubFetcher({_ROBOTS_URL: "", _LLMS_URL: FetchError("404")})

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert result.dimension == "technical_geo"
    assert result.score == 90.0
    assert any(f.title == "No llms.txt found" for f in result.findings)
    assert result.raw["llms_txt_present"] is False


def test_llms_txt_present_adds_ten_points():
    fetcher = _StubFetcher({_ROBOTS_URL: "", _LLMS_URL: "# llms.txt"})

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert result.score == 100.0
    assert result.raw["llms_txt_present"] is True


def test_blocking_a_tier1_agent_is_critical_and_drops_score():
    """Weak input: robots.txt explicitly disallows GPTBot (Tier 1). Must
    produce a CRITICAL finding naming it and a materially lower score."""
    robots = "User-agent: GPTBot\nDisallow: /\n"
    fetcher = _StubFetcher({_ROBOTS_URL: robots, _LLMS_URL: FetchError("404")})

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert any(
        f.title == "GPTBot blocked in robots.txt" and f.severity.value == "critical"
        for f in result.findings
    )
    assert result.raw["tier1_allowed"] == len(TIER_1_AGENTS) - 1
    assert result.score < 90.0


def test_blanket_wildcard_block_zeroes_everything():
    """Boundary/worst case: Disallow: / for User-agent: * blocks every
    crawler including tier1/tier2 -- score should collapse toward 0 and the
    blanket-block finding must fire."""
    robots = "User-agent: *\nDisallow: /\n"
    fetcher = _StubFetcher({_ROBOTS_URL: robots, _LLMS_URL: FetchError("404")})

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert result.raw["blanket_block"] is True
    assert any(f.title == "Blanket wildcard block detected" for f in result.findings)
    assert result.score == 0.0


def test_robots_txt_unreachable_falls_back_to_allow_all():
    """External failure: robots.txt fetch raises FetchError. Per RFC 9309
    default, that must be treated as allow-all rather than crashing or
    silently scoring everything as blocked."""
    fetcher = _StubFetcher(
        {_ROBOTS_URL: FetchError("connection reset"), _LLMS_URL: FetchError("404")}
    )

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert result.raw["robots_txt_fetched"] is False
    assert result.raw["tier1_allowed"] == len(TIER_1_AGENTS)
    assert 0.0 <= result.score <= 100.0


def test_tier2_block_is_medium_not_critical():
    robots = "User-agent: Google-Extended\nDisallow: /\n"
    fetcher = _StubFetcher({_ROBOTS_URL: robots, _LLMS_URL: FetchError("404")})

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert any(
        f.title == "Google-Extended blocked in robots.txt" and f.severity.value == "medium"
        for f in result.findings
    )
    assert result.raw["tier2_allowed"] == len(TIER_2_AGENTS) - 1


def test_score_never_exceeds_100_or_drops_below_0():
    fetcher = _StubFetcher({_ROBOTS_URL: "", _LLMS_URL: "ok"})

    result = analyze_crawler_access(_DOMAIN, fetcher)

    assert 0.0 <= result.score <= 100.0
