"""Email address shape check shared by lead research and the send path.

Shape only -- no DNS or mailbox lookup. This is the cheap gate that keeps a
literal "null" from a site's `mailto:` link out of the send queue: on
2026-09-11 one such address (lead 55) was first in priority order, Zoho
refused it with 553 every cycle, and the send stage never reached the 25
real leads queued behind it.
"""
from __future__ import annotations

import re

# One local part, one @, a dotted domain ending in an alphabetic TLD. Same
# pattern lead_agent uses to find addresses in page text.
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def verify_email(email: str) -> bool:
    """Whether `email` has the shape of a deliverable address."""
    return bool(EMAIL_RE.fullmatch((email or "").strip()))
