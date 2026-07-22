"""Shared URL-safety checks for preview links.

Both DesignAgent (right after a Vercel deploy) and SalesAgent (again at
send time, since a deploy can expire or flip behind auth between the two)
must prove a preview URL is publicly reachable before it can ever be put
in front of a lead. The checks live here, once -- the two agents briefly
carried diverging copies of this logic and the drift broke sending, so
neither should reimplement it.
"""
from __future__ import annotations

import requests

from urllib.parse import urlparse

# URL path fragments that mean the link points at a login wall, not a site.
AUTH_URL_PARTS = ("login", "signin", "sign-in", "auth", "authentication")

# Page-content markers of Vercel's auth interstitial -- a deployment that
# is "READY" but protected still serves one of these instead of the site.
AUTH_PAGE_MARKERS = (
    "vercel authentication",
    "log in to vercel",
    "login to vercel",
    "sign in to vercel",
)


def validate_public_page(url: str, timeout_seconds: int, label: str = "URL") -> str:
    """Return `url` if it serves a public page; raise RuntimeError otherwise.

    Checks, in order: well-formed http(s) URL, no auth-looking path, actually
    fetchable, non-error status, no redirect onto an auth path, and no auth
    interstitial in the served page body. `label` names the URL in error
    messages (e.g. "Deployment URL", "Preview URL") so state-history notes
    stay as readable as before this logic was shared.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"{label} is missing or invalid: {url!r}")

    if any(part in parsed.path.lower() for part in AUTH_URL_PARTS):
        raise RuntimeError(f"{label} points to an authentication path: {url}")

    try:
        resp = requests.get(url, allow_redirects=True, timeout=timeout_seconds)
    except requests.RequestException as exc:
        raise RuntimeError(f"{label} is not publicly accessible: {url}") from exc

    final_url = resp.url or url
    final_path = urlparse(final_url).path.lower()
    if resp.status_code >= 400:
        raise RuntimeError(f"{label} returned HTTP {resp.status_code}: {url}")
    if any(part in final_path for part in AUTH_URL_PARTS):
        raise RuntimeError(f"{label} redirects to an authentication path: {final_url}")
    if any(marker in resp.text.lower() for marker in AUTH_PAGE_MARKERS):
        raise RuntimeError(f"{label} shows an authentication page: {url}")

    return url
