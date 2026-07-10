"""Post-send delivery diagnostics against the SendGrid API.

A 202 from SendGrid only means "accepted for processing" -- the message can
still die afterwards, invisibly from the console:

  * Gmail/Outlook can reject it at SMTP time (recorded as a Bounce/Block in
    SendGrid), typically when the From address isn't domain-authenticated.
  * SendGrid itself silently Drops sends to any address on a suppression
    list (bounces, blocks, spam reports, invalid) -- one historical bounce
    means every later send returns 202 and then goes nowhere.

These helpers make both visible from the pipeline itself using the same
SENDGRID_API_KEY that sends. Everything degrades gracefully: a restricted
key (403) or an unavailable endpoint just reports "unknown", never raises.
Used by test_email.py; deliberately NOT wired into the automated loop.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import requests

import config

_API_BASE = "https://api.sendgrid.com/v3"
_TIMEOUT = 20

# Suppression list kinds that silently swallow sends, with single-address
# lookup + delete endpoints (per SendGrid's Suppressions API reference).
SUPPRESSION_KINDS = ("bounces", "blocks", "spam_reports", "invalid_emails")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.SENDGRID_API_KEY}"}


def check_suppressions(email: str) -> dict[str, Any]:
    """Which suppression lists `email` is on. Returns {kind: record} for
    every hit; {"_error": reason} if the key can't read suppressions at
    all; empty dict when clean."""
    found: dict[str, Any] = {}
    for kind in SUPPRESSION_KINDS:
        try:
            resp = requests.get(
                f"{_API_BASE}/suppression/{kind}/{quote(email)}", headers=_headers(), timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            return {"_error": f"suppression lookup failed: {exc}"}
        if resp.status_code == 401 or resp.status_code == 403:
            return {"_error": f"API key lacks suppression access (HTTP {resp.status_code})"}
        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError:
                continue
            # Single-address lookups return a list (possibly empty) or an object.
            if body and body != []:
                found[kind] = body
    return found


def clear_suppression(kind: str, email: str) -> bool:
    """Remove `email` from one suppression list. Used by test_email.py for
    the operator's OWN test address only -- production suppressions are
    meaningful history and are never auto-cleared."""
    try:
        resp = requests.delete(
            f"{_API_BASE}/suppression/{kind}/{quote(email)}", headers=_headers(), timeout=_TIMEOUT
        )
        return resp.status_code in (200, 204)
    except requests.RequestException:
        return False


def try_activity_lookup(email: str) -> Optional[list[dict]]:
    """Best-effort Email Activity query for recent messages to `email`.
    Returns a list of {status, last_event_time, subject} dicts, or None
    when unavailable (the Activity API needs the paid 'email activity
    history' add-on -- most accounts get 403 here; the dashboard UI still
    shows it for free)."""
    query = quote(f'to_email="{email}"')
    try:
        resp = requests.get(
            f"{_API_BASE}/messages?query={query}&limit=5", headers=_headers(), timeout=_TIMEOUT
        )
        if resp.status_code != 200:
            return None
        messages = resp.json().get("messages", [])
    except (requests.RequestException, ValueError):
        return None
    return [
        {
            "status": m.get("status"),
            "last_event_time": m.get("last_event_time"),
            "subject": m.get("subject"),
        }
        for m in messages
    ]
