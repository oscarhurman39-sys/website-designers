"""Tests for utils/ssrf_guard.py: the SSRF-safe HTTP helper used anywhere
this pipeline fetches a URL that came from (or was influenced by) a
client/lead -- a photo/logo URL submitted through the editor, a link
found while crawling rendered HTML. DNS resolution is mocked so these
tests don't depend on real network access."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests as requests_module

from utils import ssrf_guard


def _mock_dns(monkeypatch, ip: str) -> None:
    monkeypatch.setattr(ssrf_guard.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", (ip, 0))])


# --- validate_submittable_url: scheme/hostname -----------------------------------

def test_rejects_missing_scheme():
    with pytest.raises(ssrf_guard.BlockedURLError, match="scheme"):
        ssrf_guard.validate_submittable_url("example.com/photo.jpg")


def test_rejects_non_http_scheme():
    with pytest.raises(ssrf_guard.BlockedURLError, match="scheme"):
        ssrf_guard.validate_submittable_url("file:///etc/passwd")


def test_rejects_url_with_no_hostname():
    with pytest.raises(ssrf_guard.BlockedURLError, match="hostname"):
        ssrf_guard.validate_submittable_url("http:///photo.jpg")


def test_rejects_unresolvable_host(monkeypatch):
    monkeypatch.setattr(
        ssrf_guard.socket, "getaddrinfo",
        Mock(side_effect=ssrf_guard.socket.gaierror("Name or service not known")),
    )
    with pytest.raises(ssrf_guard.BlockedURLError, match="resolve"):
        ssrf_guard.validate_submittable_url("http://does-not-exist.invalid/photo.jpg")


# --- validate_submittable_url: blocked IP ranges ---------------------------------

@pytest.mark.parametrize("ip,label", [
    ("127.0.0.1", "loopback"),
    ("169.254.169.254", "cloud metadata (link-local)"),
    ("169.254.1.1", "link-local"),
    ("10.0.0.5", "private 10/8"),
    ("172.16.0.5", "private 172.16/12"),
    ("192.168.1.1", "private 192.168/16"),
    ("0.0.0.0", "unspecified"),
    ("224.0.0.1", "multicast"),
    ("::1", "IPv6 loopback"),
    ("fc00::1", "IPv6 unique-local (private)"),
    ("fe80::1", "IPv6 link-local"),
])
def test_blocks_private_and_internal_addresses(monkeypatch, ip, label):
    _mock_dns(monkeypatch, ip)
    with pytest.raises(ssrf_guard.BlockedURLError, match="disallowed"):
        ssrf_guard.validate_submittable_url("http://attacker-controlled.example/x")


def test_allows_public_address(monkeypatch):
    _mock_dns(monkeypatch, "93.184.216.34")
    assert ssrf_guard.validate_submittable_url("https://example.com/photo.jpg") == "https://example.com/photo.jpg"


def test_blocks_when_any_resolved_address_is_private(monkeypatch):
    """A hostname resolving to multiple addresses is blocked if even ONE
    of them is internal -- an attacker shouldn't be able to hide a private
    address behind a public-looking multi-A-record host."""
    monkeypatch.setattr(
        ssrf_guard.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("127.0.0.1", 0))],
    )
    with pytest.raises(ssrf_guard.BlockedURLError):
        ssrf_guard.validate_submittable_url("http://mixed.example/x")


# --- safe_request: redirect re-validation ----------------------------------------

def test_safe_request_returns_response_for_public_host(monkeypatch):
    _mock_dns(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(
        requests_module, "request",
        lambda method, url, **kw: Mock(status_code=200, is_redirect=False, is_permanent_redirect=False),
    )
    resp = ssrf_guard.safe_request("get", "https://example.com/photo.jpg")
    assert resp.status_code == 200


def test_safe_request_follows_redirect_to_a_public_host(monkeypatch):
    _mock_dns(monkeypatch, "93.184.216.34")
    calls = []

    def fake_request(method, url, **kw):
        calls.append(url)
        if url == "https://example.com/old":
            return Mock(status_code=302, is_redirect=True, is_permanent_redirect=False,
                        headers={"Location": "https://example.com/new"})
        return Mock(status_code=200, is_redirect=False, is_permanent_redirect=False, headers={})

    monkeypatch.setattr(requests_module, "request", fake_request)
    resp = ssrf_guard.safe_request("get", "https://example.com/old")
    assert resp.status_code == 200
    assert calls == ["https://example.com/old", "https://example.com/new"]


def test_safe_request_blocks_a_redirect_to_a_private_host():
    """The exact attack the review flagged: a public-looking URL that
    302s to an internal address must be blocked at the redirect hop, not
    just at the original URL."""
    import requests as _requests

    call_hosts = []

    def fake_getaddrinfo(host, port):
        call_hosts.append(host)
        ip = "93.184.216.34" if host == "public.example" else "127.0.0.1"
        return [(2, 1, 6, "", (ip, 0))]

    def fake_request(method, url, **kw):
        if "public.example" in url:
            return Mock(status_code=302, is_redirect=True, is_permanent_redirect=False,
                        headers={"Location": "http://internal.example/secret"})
        return Mock(status_code=200, is_redirect=False, is_permanent_redirect=False, headers={})

    import unittest.mock as mock
    with mock.patch.object(ssrf_guard.socket, "getaddrinfo", fake_getaddrinfo), \
         mock.patch.object(_requests, "request", fake_request):
        with pytest.raises(ssrf_guard.BlockedURLError, match="disallowed"):
            ssrf_guard.safe_request("get", "http://public.example/start")

    assert "internal.example" in call_hosts  # the redirect hop really was checked, not skipped


def test_safe_request_raises_on_too_many_redirects(monkeypatch):
    _mock_dns(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(
        requests_module, "request",
        lambda method, url, **kw: Mock(status_code=302, is_redirect=True, is_permanent_redirect=False,
                                       headers={"Location": url + "x"}),
    )
    with pytest.raises(ssrf_guard.BlockedURLError, match="redirects"):
        ssrf_guard.safe_request("get", "https://example.com/a", max_redirects=3)
