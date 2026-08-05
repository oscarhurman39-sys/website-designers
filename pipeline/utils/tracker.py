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

import config
from utils import db

_TOKEN_PURPOSE = b"click"


def _sign(lead_id: int) -> str:
    payload = str(lead_id).encode("utf-8")
    mac = hmac.new(config.SECRET_KEY.encode("utf-8"), _TOKEN_PURPOSE + b":" + payload, hashlib.sha256).digest()
    # No separator byte between payload and mac -- a sha256 digest is
    # always exactly 32 bytes, so verify_click_token can split on that
    # fixed length unambiguously. A literal separator (e.g. b".") would be
    # wrong here: mac is effectively random bytes, so it can itself
    # contain that byte, corrupting the split about 1 in 8 times
    # (confirmed empirically -- every click link using the old scheme had
    # a ~12% chance of 404ing instead of redirecting to the real preview).
    token_bytes = payload + mac
    return urlsafe_b64encode(token_bytes).decode("utf-8").rstrip("=")


def verify_click_token(token: str) -> Optional[int]:
    """Return the lead_id encoded in `token` if the signature is valid, else None."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = urlsafe_b64decode(padded.encode("utf-8"))
        payload, mac = raw[:-32], raw[-32:]  # sha256 digest is always exactly 32 bytes
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
