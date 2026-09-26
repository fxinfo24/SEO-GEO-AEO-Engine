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
