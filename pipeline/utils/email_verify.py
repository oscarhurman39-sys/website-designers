"""Email verification utilities.

Syntax-level verification only, deliberately: DNS/MX probing or SMTP
callouts from the sending host would add network flakiness to every send
decision and can themselves hurt sender reputation. The goal here is to
catch the garbage that web scraping produces (truncated "info@" strings,
"user@localhost", filenames that look like addresses) before it reaches a
transport, not to guarantee deliverability -- bounces are already handled
by the DSN detection in utils/compliance.py + agents/sales_agent.py.
"""
from __future__ import annotations

import re

# Anchored full-string match, same character classes as the scraping regex
# in agents/lead_agent.py and the transport check in utils/email_utils.py.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

# RFC 5321: 64 octets local part, 254 octets total path.
_MAX_TOTAL = 254
_MAX_LOCAL = 64


def verify_email(email: str) -> bool:
    """Verify if an email address is valid.

    Args:
        email: The email address to verify.

    Returns:
        True if the email is valid, False otherwise.
    """
    if not email or len(email) > _MAX_TOTAL:
        return False
    if _EMAIL_RE.match(email) is None:
        return False
    local, _, domain = email.rpartition("@")
    if len(local) > _MAX_LOCAL:
        return False
    # The regex's character class allows dots anywhere; reject the layouts
    # mail servers actually refuse (leading/trailing/consecutive dots).
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if domain.startswith((".", "-")) or ".." in domain:
        return False
    return True
