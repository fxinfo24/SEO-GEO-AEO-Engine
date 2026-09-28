"""
Tests for RenderFetcher's SSRF-relevant request interception (Phase 1.4's
"rendered sub-resource" case). A real Chromium binary is not installed in
this environment (nor assumed in CI), so these test the actual decision
logic RenderFetcher wires into Playwright's page.route() -- _handle_route()
and _validate_request_url() -- with duck-typed Route/Request stand-ins
that only implement the interface those methods actually use
(request.url, route.abort(), route.continue_()). A full browser-integration
test exercising the real Playwright routing pipeline is a reasonable
follow-up once `playwright install chromium` is part of the CI image.
"""
from __future__ import annotations

import pytest

from seo_geo_aeo.core.render_fetcher import RenderFetcher


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeRoute:
    def __init__(self) -> None:
        self.aborted = False
        self.continued = False

    def abort(self) -> None:
        self.aborted = True

    def continue_(self) -> None:
        self.continued = True


def _make_render_fetcher() -> RenderFetcher:
    # No browser is ever launched by these tests -- _handle_route and
    # _validate_request_url only touch self._safe_fetcher.
    return RenderFetcher()


def test_subresource_to_private_ip_is_aborted_not_sent():
    """The core Phase 1.4 contract: a page's own sub-resource request (an
    <img src>, an XHR, a JS fetch()) to a private/link-local target must be
    aborted via route.abort() -- never allowed through to route.continue_(),
    which is what would actually put the request on the wire."""
    rf = _make_render_fetcher()
    blocked: list[str] = []
    route = _FakeRoute()
    request = _FakeRequest("http://169.254.169.254/latest/meta-data/")

    rf._handle_route(route, request, blocked)

    assert route.aborted is True
    assert route.continued is False
    assert blocked == ["http://169.254.169.254/latest/meta-data/"]


def test_subresource_to_localhost_is_aborted():
    rf = _make_render_fetcher()
    blocked: list[str] = []
    route = _FakeRoute()
    request = _FakeRequest("http://127.0.0.1:8080/internal-api")

    rf._handle_route(route, request, blocked)

    assert route.aborted is True
    assert blocked == ["http://127.0.0.1:8080/internal-api"]


def test_subresource_to_public_host_is_allowed_through():
    """Positive case: a normal same-site or public CDN sub-resource request
    must be let through via route.continue_(), not blocked."""
    rf = _make_render_fetcher()
    blocked: list[str] = []
    route = _FakeRoute()
    request = _FakeRequest("https://example.com/style.css")

    rf._handle_route(route, request, blocked)

    assert route.continued is True
    assert route.aborted is False
    assert blocked == []


def test_multiple_subresources_each_evaluated_independently():
    """A page with a mix of safe and unsafe sub-resources should block only
    the unsafe ones and accumulate all of them in blocked_urls, not stop at
    the first one."""
    rf = _make_render_fetcher()
    blocked: list[str] = []
    requests = [
        ("https://example.com/logo.png", False),
        ("http://169.254.169.254/", True),
        ("https://example.com/app.js", False),
        ("http://10.0.0.1/internal", True),
    ]

    for url, should_block in requests:
        route = _FakeRoute()
        rf._handle_route(route, _FakeRequest(url), blocked)
        assert route.aborted is should_block
        assert route.continued is not should_block

    assert blocked == ["http://169.254.169.254/", "http://10.0.0.1/internal"]


def test_validate_request_url_matches_safe_fetcher_directly():
    """_validate_request_url is a thin wrapper around SafeFetcher.validate_url
    -- confirm it doesn't diverge (e.g. swallow an error type it shouldn't)."""
    rf = _make_render_fetcher()

    assert rf._validate_request_url("https://example.com/") is True
    assert rf._validate_request_url("http://169.254.169.254/") is False
    assert rf._validate_request_url("ftp://example.com/") is False
    assert rf._validate_request_url("file:///etc/passwd") is False


@pytest.mark.parametrize("value", ["load", "domcontentloaded", "networkidle", "commit"])
def test_wait_until_accepts_playwright_values(value: str):
    assert RenderFetcher(wait_until=value).wait_until == value


def test_wait_until_rejects_unknown_value_at_construction():
    """Fails fast with a clear message instead of deep inside Playwright."""
    with pytest.raises(ValueError, match="wait_until"):
        RenderFetcher(wait_until="whenever")
