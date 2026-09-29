"""SSRF-guard tests for SafeFetcher. These do NOT hit the network — they
test the DNS-resolution/IP-range validation logic that runs before any
request is made."""
from __future__ import annotations

import time

import pytest

from seo_geo_aeo.core.fetcher import Deadline, SafeFetcher, UnsafeURLError


@pytest.fixture
def fetcher() -> SafeFetcher:
    return SafeFetcher()


@pytest.fixture
def fetcher_with_generous_deadline() -> SafeFetcher:
    return SafeFetcher(deadline=Deadline(seconds=30.0))


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


# --- deny-by-default (not is_global) vs the old enumerated-flags approach --------------


def test_resolve_public_ip_rejects_carrier_grade_nat_range(monkeypatch):
    """Regression: 100.64.0.0/10 (RFC 6598) is neither is_private nor
    is_reserved in Python's ipaddress module, so the old check (which
    enumerated is_loopback/is_link_local/is_private/is_reserved/is_multicast/
    is_unspecified) would have let a fetch to this range through. is_global
    correctly reports False for it; deny-by-default catches what the
    enumerated flags missed."""
    import socket

    from seo_geo_aeo.core.fetcher import UnsafeURLError, _resolve_public_ip

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("100.64.0.1", 0))],
    )

    with pytest.raises(UnsafeURLError, match="100.64.0.1"):
        _resolve_public_ip("cgnat.example")


def test_resolve_public_ip_rejects_if_any_of_several_records_is_private(monkeypatch):
    import socket

    from seo_geo_aeo.core.fetcher import UnsafeURLError, _resolve_public_ip

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("127.0.0.1", 0)),
        ],
    )

    with pytest.raises(UnsafeURLError, match="127.0.0.1"):
        _resolve_public_ip("multi-a-record.example")


def test_resolve_public_ip_accepts_ordinary_public_address(monkeypatch):
    import socket

    from seo_geo_aeo.core.fetcher import _resolve_public_ip

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))]
    )

    assert _resolve_public_ip("example.example") == "93.184.216.34"


# --- pinned resolution backend (DNS-rebinding TOCTOU) -----------------------------------


def test_pinned_backend_rejects_private_resolution_at_connect_time(monkeypatch):
    """`_PinnedNetworkBackend.connect_tcp` is the authoritative, connect-time
    check the docstring describes -- it must reject on its own, not merely
    rely on the earlier pre-hop `_validate_url` call having already run."""
    import socket

    from seo_geo_aeo.core.fetcher import UnsafeURLError, _PinnedNetworkBackend

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 0))]
    )
    backend = _PinnedNetworkBackend()

    with pytest.raises(UnsafeURLError):
        backend.connect_tcp("rebinding-target.example", 443)


def test_pinned_backend_connects_to_the_exact_resolved_ip_not_the_hostname(monkeypatch):
    """The whole point of pinning: the literal validated IP is what actually
    gets passed on to open the socket, not the original hostname (which
    would let the OS resolve it all over again)."""
    from seo_geo_aeo.core import fetcher as fetcher_module

    monkeypatch.setattr(
        fetcher_module, "_resolve_public_ip", lambda host: "203.0.113.7"
    )
    seen = {}

    def fake_super_connect(self, host, port, **kwargs):
        seen["host"] = host
        seen["port"] = port
        return "a-network-stream"

    monkeypatch.setattr(
        fetcher_module.httpcore.SyncBackend, "connect_tcp", fake_super_connect
    )
    backend = fetcher_module._PinnedNetworkBackend()

    result = backend.connect_tcp("totally-different-hostname.example", 443)

    assert seen == {"host": "203.0.113.7", "port": 443}
    assert result == "a-network-stream"


# --- max response size (RoadMap.md Phase 10) --------------------------------------------


def test_fetch_rejects_response_whose_declared_content_length_exceeds_the_cap(
    fetcher: SafeFetcher, httpx_mock
):
    from seo_geo_aeo.core.fetcher import FetchError

    fetcher.max_response_bytes = 100
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(
        url="https://example.com/huge",
        headers={"Content-Length": "999999"},
        content=b"x" * 10,  # body itself is small; the declared header is what must trip this
    )

    with pytest.raises(FetchError, match="999999 bytes"):
        fetcher.fetch("https://example.com/huge")


def test_fetch_rejects_body_exceeding_cap_when_content_length_understates_it(
    fetcher: SafeFetcher, httpx_mock
):
    """An understated Content-Length must not be a bypass -- the streamed
    byte count, not the header, is what's authoritative once bytes start
    arriving."""
    from seo_geo_aeo.core.fetcher import FetchError

    fetcher.max_response_bytes = 10
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(
        url="https://example.com/nolen",
        headers={"Content-Length": "5"},  # lies -- real body is 1000 bytes
        content=b"y" * 1000,
    )

    with pytest.raises(FetchError, match="exceeded the 10-byte limit while streaming"):
        fetcher.fetch("https://example.com/nolen")


def test_fetch_allows_response_under_the_cap(fetcher: SafeFetcher, httpx_mock):
    fetcher.max_response_bytes = 1000
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(url="https://example.com/small", text="hello world")

    result = fetcher.fetch("https://example.com/small")

    assert result.text == "hello world"


# --- shared deadline / total audit timeout (RoadMap.md Phase 10) ------------------------


def test_fetch_raises_immediately_once_deadline_has_elapsed(httpx_mock):
    """An already-elapsed deadline must stop the fetch before any request is
    attempted -- proven here by registering no httpx_mock response at all:
    if the fetch tried to make a request, pytest-httpx's own 'no match'
    error would fire instead of AuditTimeoutError."""
    from seo_geo_aeo.core.fetcher import AuditTimeoutError, Deadline, FetchError, SafeFetcher

    fetcher = SafeFetcher(deadline=Deadline(seconds=0))
    time.sleep(0.01)

    with pytest.raises(AuditTimeoutError):
        fetcher.fetch("https://example.com/never-requested")
    # AuditTimeoutError must be catchable as a plain FetchError too.
    assert issubclass(AuditTimeoutError, FetchError)


def test_fetch_succeeds_within_an_unexpired_deadline(fetcher_with_generous_deadline, httpx_mock):
    httpx_mock.add_response(url="https://example.com/robots.txt", text="")
    httpx_mock.add_response(url="https://example.com/ok", text="fine")

    result = fetcher_with_generous_deadline.fetch("https://example.com/ok")

    assert result.text == "fine"


def test_deadline_remaining_and_check_are_independent_of_seconds_being_none():
    from seo_geo_aeo.core.fetcher import Deadline

    unbounded = Deadline(seconds=None)
    assert unbounded.remaining() is None
    unbounded.check()  # must never raise


def test_deadline_shared_across_multiple_fetches_on_one_fetcher(httpx_mock):
    """The whole point of attaching a Deadline to a fetcher instance instead
    of a single call: it accumulates across every fetch that instance makes."""
    from seo_geo_aeo.core.fetcher import AuditTimeoutError, Deadline, SafeFetcher

    fetcher = SafeFetcher(deadline=Deadline(seconds=0.05), respect_robots=False)
    httpx_mock.add_response(url="https://example.com/first", text="ok")

    fetcher.fetch("https://example.com/first")
    time.sleep(0.06)

    with pytest.raises(AuditTimeoutError):
        fetcher.fetch("https://example.com/second-never-requested")
