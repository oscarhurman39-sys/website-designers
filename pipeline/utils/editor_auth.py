"""Magic-link auth for the client site editor (see agents/editor_agent.py
and webhook_server.py's /edit routes).

Same stateless HMAC-signed-token pattern as utils/compliance.py's
unsubscribe links and utils/tracker.py's click-tracking links: no DB
lookup needed to verify a token, and (like those two) the link never
expires and can't be individually revoked short of rotating SECRET_KEY
(which invalidates every token pipeline-wide, not just this one) -- an
accepted tradeoff already made twice elsewhere in this codebase, not a new
gap introduced here.
"""
from __future__ import annotations

import hashlib
import hmac
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Optional
from urllib.parse import quote

import config

_TOKEN_PURPOSE = b"editor"


def _sign(lead_id: int) -> str:
    payload = str(lead_id).encode("utf-8")
    mac = hmac.new(config.SECRET_KEY.encode("utf-8"), _TOKEN_PURPOSE + b":" + payload, hashlib.sha256).digest()
    # No separator byte between payload and mac -- a sha256 digest is
    # always exactly 32 bytes, so verify_editor_token can split on that
    # fixed length unambiguously. A literal separator (e.g. b".") would be
    # wrong here: mac is effectively random bytes, so it can itself contain
    # that byte, corrupting the split about 1 in 8 times.
    token_bytes = payload + mac
    return urlsafe_b64encode(token_bytes).decode("utf-8").rstrip("=")


def generate_editor_token(lead_id: int) -> str:
    return _sign(lead_id)


def verify_editor_token(token: str) -> Optional[int]:
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


def create_editor_link(lead_id: int) -> str:
    """Build the fully-qualified edit-your-site URL for a given lead."""
    token = generate_editor_token(lead_id)
    return f"{config.PUBLIC_BASE_URL}/edit/{lead_id}?token={quote(token)}"
