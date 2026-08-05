"""Client editor session auth -- DB-backed magic links (db.editor_sessions),
NOT the stateless HMAC-signed-token pattern compliance.py/tracker.py use
for unsubscribe/click links.

Why different: publishing authority over a paying client's LIVE site is a
much higher-stakes grant than an unsubscribe click or a click-tracking
redirect. Those two are fine as permanent, unrevocable links -- worst
case, a lead unsubscribes early or a click gets logged that shouldn't
have been. A leaked or ancient forwarded editor link, by contrast, would
let a stranger publish content to a live client site indefinitely with no
way to shut it off short of rotating the whole application SECRET_KEY
(which would invalidate every unsubscribe/click/editor token pipeline-
wide, not just this one). So this needs an expiry, per-session
revocation, and a way to reissue a fresh link (see
agents/editor_agent.py's request_new_editor_link, which emails a new one
to the lead's ON-FILE contact address -- never returns the link directly
in an HTTP response, or "request a new link" would itself be an auth
bypass for anyone who can guess a lead_id).

Only the SHA-256 hash of each token is ever stored -- the raw token exists
only in the URL sent to the client and in the request that redeems it.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import config
from utils import db

SESSION_LIFETIME_DAYS = 30
_TOKEN_BYTES = 32


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now_utc_naive() -> datetime:
    """Timezone-naive UTC "now", matching how db.py stores every
    timestamp (SQLite's datetime('now') is UTC and naive)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def issue_editor_session(lead_id: int, *, lifetime_days: int = SESSION_LIFETIME_DAYS) -> str:
    """Create a brand-new session and return the raw (unhashed) token --
    the only moment it ever exists outside the URL it gets embedded in.
    Does not touch any of the lead's other outstanding sessions (multiple
    valid links, e.g. one per device, can coexist -- see
    revoke_all_sessions to invalidate all of them at once)."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = _now_utc_naive() + timedelta(days=lifetime_days)
    db.insert_editor_session(lead_id, _hash_token(token), expires_at)
    return token


def create_editor_link(lead_id: int) -> str:
    """Build a fully-qualified edit-your-site URL for a given lead,
    issuing a fresh session for it."""
    token = issue_editor_session(lead_id)
    return f"{config.PUBLIC_BASE_URL}/edit/{lead_id}?token={token}"


def create_onboarding_link(lead_id: int) -> str:
    """Build a fully-qualified claim-your-website URL (see
    agents/onboarding_agent.py, webhook_server.py's /onboard route) --
    same session mechanism as create_editor_link, since a valid session
    proves the same thing (control of this lead's identity) regardless of
    which form it lands on; only the URL differs."""
    token = issue_editor_session(lead_id)
    return f"{config.PUBLIC_BASE_URL}/onboard/{lead_id}?token={token}"


def verify_editor_session(lead_id: int, token: str) -> bool:
    """True iff `token` is an unexpired, unrevoked session for `lead_id`.
    Marks the session as just-used on success (last_used_at) -- purely
    informational, doesn't affect validity."""
    if not token:
        return False
    session = db.get_editor_session(_hash_token(token))
    if session is None or session["lead_id"] != lead_id:
        return False
    if session["revoked_at"]:
        return False
    if datetime.strptime(session["expires_at"], "%Y-%m-%d %H:%M:%S") <= _now_utc_naive():
        return False
    db.touch_editor_session(session["id"])
    return True


def revoke_all_sessions(lead_id: int) -> None:
    """Revoke every outstanding editor session for a lead -- e.g. a
    designer suspects a link leaked, or a fresh one is being issued."""
    db.revoke_editor_sessions(lead_id)
