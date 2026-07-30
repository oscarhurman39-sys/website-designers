"""Shared "is this URL safe to put in front of a customer?" validation.

Used by design_agent.py (before a deployment is recorded as 'designed')
and sales_agent.py (immediately before a cold email is sent), so both ends
of the funnel agree on what counts as a publicly viewable preview. A URL
that is blank, malformed, unreachable, erroring, or hidden behind a Vercel
authentication page must never reach a prospect's inbox.
"""
from __future__ import annotations

from urllib.parse import urlparse

import requests

AUTH_URL_PARTS = ("login", "signin", "sign-in", "auth", "authentication")
AUTH_PAGE_MARKERS = (
    "vercel authentication",
    "log in to vercel",
    "login to vercel",
    "sign in to vercel",
)
VALIDATION_TIMEOUT_SECONDS = 15


def validate_public_url(url: str, *, timeout: int = VALIDATION_TIMEOUT_SECONDS) -> str:
    """Return `url` if it is a live, public, non-auth-gated page.

    Raises RuntimeError describing the first failed check otherwise.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"Not a valid public URL: {url!r}")
    if any(part in parsed.path.lower() for part in AUTH_URL_PARTS):
        raise RuntimeError(f"URL points to an authentication path: {url}")

    try:
        resp = requests.get(url, allow_redirects=True, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"URL is not publicly accessible: {url}") from exc

    final_url = resp.url or url
    if resp.status_code >= 400:
        raise RuntimeError(f"URL returned HTTP {resp.status_code}: {url}")
    if any(part in urlparse(final_url).path.lower() for part in AUTH_URL_PARTS):
        raise RuntimeError(f"URL redirects to an authentication path: {final_url}")
    if any(marker in resp.text.lower() for marker in AUTH_PAGE_MARKERS):
        raise RuntimeError(f"URL shows an authentication page instead of the site: {url}")
    return url
