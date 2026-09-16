"""Click-tracking link generation for cold emails.

Every preview link sent in an email is wrapped so we know when a lead
actually opens their preview site -- this is a much stronger buying signal
than an email open. The webhook_server.py `/click` route verifies the
token, logs the click, then 302-redirects to the real preview URL.
"""
from __future__ import annotations

import hashlib
import hmac
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Optional
from urllib.parse import quote

import time

import requests

import config
from utils import db

_HEALTH_CACHE_SECONDS = 300
_health_cache: tuple[float, bool] = (0.0, False)


def public_endpoint_up() -> bool:
    """Is PUBLIC_BASE_URL/click going to work for a lead right now? A tracked
    link only redirects if webhook_server.py is reachable there, so a cold
    email must fall back to the raw preview URL when it is not. Cached for
    five minutes so a send does not cost a probe every time."""
    global _health_cache
    checked_at, up = _health_cache
    if checked_at and time.monotonic() - checked_at < _HEALTH_CACHE_SECONDS:
        return up
    base = (config.PUBLIC_BASE_URL or "").rstrip("/")
    up = False
    if base and not any(h in base for h in ("localhost", "127.0.0.1")):
        try:
            up = requests.get(f"{base}/health", timeout=3).status_code == 200
        except requests.RequestException:
            up = False
    _health_cache = (time.monotonic(), up)
    return up

_TOKEN_PURPOSE = b"click"


def _sign(lead_id: int) -> str:
    payload = str(lead_id).encode("utf-8")
    mac = hmac.new(config.SECRET_KEY.encode("utf-8"), _TOKEN_PURPOSE + b":" + payload, hashlib.sha256).digest()
    token_bytes = payload + b"." + mac
    return urlsafe_b64encode(token_bytes).decode("utf-8").rstrip("=")


def verify_click_token(token: str) -> Optional[int]:
    """Return the lead_id encoded in `token` if the signature is valid, else None."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = urlsafe_b64decode(padded.encode("utf-8"))
        payload, mac = raw.rsplit(b".", 1)
        expected = hmac.new(
            config.SECRET_KEY.encode("utf-8"), _TOKEN_PURPOSE + b":" + payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(mac, expected):
            return None
        return int(payload.decode("utf-8"))
    except (ValueError, TypeError):
        return None


def create_click_link(lead_id: int) -> str:
    """Build a tracked link that redirects to the lead's website preview.

    The actual destination is looked up server-side from the `websites`
    table at click time (not embedded in the URL), so the link can't be
    tampered with to redirect somewhere else.
    """
    token = _sign(lead_id)
    return f"{config.PUBLIC_BASE_URL}/click?lead_id={lead_id}&token={quote(token)}"


def resolve_click(lead_id: int, token: str) -> Optional[str]:
    """Verify the token matches lead_id, log the click, and return the preview
    URL to redirect to. Returns None if the token is invalid or no site exists."""
    verified_lead_id = verify_click_token(token)
    if verified_lead_id is None or verified_lead_id != lead_id:
        return None
    website = db.get_website_by_lead(lead_id)
    if website is None or not website.get("preview_url"):
        return None
    db.log_click(lead_id)
    return website["preview_url"]
