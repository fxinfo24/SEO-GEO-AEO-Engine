"""SSRF-guard tests for SafeFetcher. These do NOT hit the network — they
test the DNS-resolution/IP-range validation logic that runs before any
request is made."""
from __future__ import annotations

import pytest

from seo_geo_aeo.core.fetcher import SafeFetcher, UnsafeURLError


@pytest.fixture
def fetcher() -> SafeFetcher:
    return SafeFetcher()


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://[::1]/",
        "ftp://example.com/",
        "file:///etc/passwd",
        "http://",  # no hostname
    ],
)
def test_validate_url_blocks_unsafe_targets(fetcher: SafeFetcher, url: str):
    with pytest.raises(UnsafeURLError):
        fetcher.validate_url(url)


def test_validate_url_allows_public_https(fetcher: SafeFetcher):
    # example.com resolves to a public IP; should not raise.
    hostname = fetcher.validate_url("https://example.com/some/path")
    assert hostname == "example.com"


def test_fetch_refuses_redirect_to_private_ip(fetcher: SafeFetcher, httpx_mock):
    """Phase 1.4: a 302 whose Location points at a private/link-local
    target must be refused BEFORE it is requested -- not discovered after
    the fact. Only the first hop is mocked; if the fetcher tried to follow
    the redirect, pytest-httpx would raise its own 'no response registered'
    error for the second hop instead of UnsafeURLError, which is how this
    test would catch a regression back to `follow_redirects=True`."""
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(
        url="https://example.com/redirect",
        status_code=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )

    with pytest.raises(UnsafeURLError):
        fetcher.fetch("https://example.com/redirect")


def test_fetch_refuses_redirect_to_relative_private_path(fetcher: SafeFetcher, httpx_mock):
    """Same as above but with a relative Location header resolved against
    a private host -- urljoin() must not be a bypass for the same check."""
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(
        url="https://example.com/redirect2",
        status_code=302,
        headers={"Location": "//127.0.0.1/admin"},
    )

    with pytest.raises(UnsafeURLError):
        fetcher.fetch("https://example.com/redirect2")


def test_fetch_follows_redirect_chain_to_safe_final_target(fetcher: SafeFetcher, httpx_mock):
    """Positive case: a redirect chain that stays on public hosts the whole
    way should resolve normally and report the correct final_url."""
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(
        url="https://example.com/start", status_code=302, headers={"Location": "https://example.com/end"}
    )
    httpx_mock.add_response(url="https://example.com/end", status_code=200, text="ok")

    result = fetcher.fetch("https://example.com/start")

    assert result.status_code == 200
    assert result.final_url == "https://example.com/end"
    assert result.text == "ok"
