"""SSRF-safe outbound HTTP helper.

Use this (not bare `requests`) anywhere this pipeline makes an HTTP
request to a URL that originated from, or was influenced by, a client or
lead -- a photo/logo URL submitted through the client editor, a link
found while crawling rendered HTML (site_audit.check_broken_links) --
rather than a URL this pipeline generated itself (Vercel/GitHub/Stripe API
endpoints, which are config-controlled constants, not user input).

Without this, a client (or anyone with an editor link) could submit a
"photo" URL pointing at http://169.254.169.254/ (cloud metadata),
http://localhost/admin, or an internal-network address, and the readiness
scanner's own outbound crawl would dutifully fetch it on the server's
behalf -- a classic SSRF. This resolves the hostname BEFORE connecting and
rejects loopback/link-local/private/reserved/multicast addresses
(is_link_local covers the 169.254.0.0/16 metadata range), then re-
validates after every redirect hop, since a public-looking URL can 302 to
an internal one.

KNOWN LIMITATION: this validates the resolved IP at request time, then
lets `requests` do its own (separate) DNS resolution when it actually
connects. A sophisticated attacker controlling DNS with a very short TTL
could theoretically flip the answer between those two lookups ("DNS
rebinding"). Closing that gap fully requires pinning the validated IP for
the actual socket connection (e.g. a custom transport adapter), which is
real additional engineering, not a one-line fix -- flagging it here
rather than claiming complete protection this doesn't yet provide.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_REDIRECTS = 5
DEFAULT_TIMEOUT = 8


class BlockedURLError(ValueError):
    """Raised when a URL is disallowed -- bad scheme/host, or resolves to
    a private/internal/reserved address. Subclasses ValueError so callers
    that already catch ValueError for other input-validation failures
    (e.g. agents/editor_agent.py's field-type checks) catch this too."""


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_loopback
        or ip.is_link_local  # covers 169.254.0.0/16, including the 169.254.169.254 cloud-metadata address
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_submittable_url(url: str) -> str:
    """Raise BlockedURLError unless `url` is http(s) with a hostname that
    resolves to only public addresses. Returns `url` unchanged on success
    (so it can be used inline: `field = validate_submittable_url(value)`)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError(f"Unsupported or missing scheme in URL: {url!r}")
    if not parsed.hostname:
        raise BlockedURLError(f"No hostname in URL: {url!r}")
    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise BlockedURLError(f"Could not resolve host {parsed.hostname!r}: {exc}") from exc
    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        if _is_blocked_ip(sockaddr[0]):
            raise BlockedURLError(f"{url!r} resolves to a disallowed private/internal address: {sockaddr[0]}")
    return url


def safe_request(method: str, url: str, *, timeout: int = DEFAULT_TIMEOUT,
                 max_redirects: int = MAX_REDIRECTS, **kwargs) -> requests.Response:
    """Like requests.request(), but validates the destination -- and every
    redirect hop -- resolves to a public address before connecting.
    Raises BlockedURLError for a disallowed hop, requests.RequestException
    for ordinary network failures."""
    current_url = url
    for _ in range(max_redirects + 1):
        validate_submittable_url(current_url)
        resp = requests.request(method, current_url, timeout=timeout, allow_redirects=False, **kwargs)
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            if not location:
                return resp
            current_url = urljoin(current_url, location)
            continue
        return resp
    raise BlockedURLError(f"Too many redirects (>{max_redirects}) for {url!r}")
