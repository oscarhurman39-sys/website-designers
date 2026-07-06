"""CAN-SPAM compliance helpers: unsubscribe tokens, footers, and bounce detection.

Every outbound cold email MUST run its body through `append_footer()` and
its headers through `list_unsubscribe_header()`. The `/unsubscribe/<token>`
route in webhook_server.py calls `process_unsubscribe()` on GET.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from typing import Optional

import config
from utils import db

_TOKEN_PURPOSE = b"unsubscribe"


def _sign(lead_id: int) -> str:
    """HMAC-sign a lead id so unsubscribe links can't be forged or enumerated."""
    payload = str(lead_id).encode("utf-8")
    mac = hmac.new(config.SECRET_KEY.encode("utf-8"), _TOKEN_PURPOSE + b":" + payload, hashlib.sha256).digest()
    token_bytes = payload + b"." + mac
    return urlsafe_b64encode(token_bytes).decode("utf-8").rstrip("=")


def generate_unsubscribe_token(lead_id: int) -> str:
    return _sign(lead_id)


def verify_unsubscribe_token(token: str) -> Optional[int]:
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


def _public_http_base() -> Optional[str]:
    """PUBLIC_BASE_URL when it's a real public host, else None. A localhost
    unsubscribe URL in a sent email is dead for every recipient -- and a
    dead unsubscribe link isn't a compliance mechanism at all."""
    lowered = config.PUBLIC_BASE_URL.lower()
    if "localhost" in lowered or "127.0.0.1" in lowered or "0.0.0.0" in lowered:
        return None
    return config.PUBLIC_BASE_URL


def _unsubscribe_mailto() -> str:
    addr = config.ADMIN_EMAIL or config.EMAIL_USER or config.SENDGRID_FROM_EMAIL
    return f"mailto:{addr}?subject=unsubscribe"


def one_click_supported() -> bool:
    """RFC 8058 one-click unsubscribe needs a public HTTPS endpoint; only
    true when the webhook server is actually reachable by recipients."""
    return _public_http_base() is not None


def create_unsubscribe_link(lead_id: int) -> str:
    """The unsubscribe mechanism for a given lead: the fully-qualified
    /unsubscribe/<token> URL when the webhook server is public, otherwise a
    mailto: link to the polled inbox -- sales_agent's inbox poller marks
    leads unsubscribed when a reply asks for it, so the mailto path is a
    real working opt-out, not decoration."""
    if _public_http_base() is None:
        return _unsubscribe_mailto()
    token = generate_unsubscribe_token(lead_id)
    return f"{config.PUBLIC_BASE_URL}/unsubscribe/{token}"


def process_unsubscribe(token: str) -> Optional[dict]:
    """Validate `token`, permanently unsubscribe the lead, and return it. None if invalid."""
    lead_id = verify_unsubscribe_token(token)
    if lead_id is None:
        return None
    lead = db.get_lead(lead_id)
    if lead is None:
        return None
    email = lead.get("contact_email") or ""
    db.mark_unsubscribed(lead_id, email)
    return lead


def list_unsubscribe_header(lead_id: int) -> str:
    """Value for the `List-Unsubscribe` header: HTTPS one-click + mailto
    when the webhook server is public, mailto-only otherwise."""
    if one_click_supported():
        link = create_unsubscribe_link(lead_id)
        return f"<{link}>, <{_unsubscribe_mailto()}>"
    return f"<{_unsubscribe_mailto()}>"


def append_footer(body_text: str, lead_id: int) -> str:
    """Append the mandatory CAN-SPAM footer to a plain-text email body."""
    link = create_unsubscribe_link(lead_id)
    if link.startswith("mailto:"):
        unsubscribe_line = "To opt out, just reply with 'unsubscribe' and I won't email again."
    else:
        unsubscribe_line = f"Unsubscribe: {link}"
    footer = (
        "\n\n--\n"
        f"{config.PHYSICAL_ADDRESS}\n"
        "You can opt out anytime -- no hard feelings.\n"
        f"{unsubscribe_line}"
    )
    return body_text.rstrip() + footer


def append_footer_html(body_html: str, lead_id: int) -> str:
    """Append the mandatory CAN-SPAM footer to an HTML email body."""
    link = create_unsubscribe_link(lead_id)
    footer = (
        "<br><br><hr>"
        f"<p style='font-size:12px;color:#777'>{config.PHYSICAL_ADDRESS}<br>"
        "You can opt out anytime -- no hard feelings.<br>"
        f"<a href='{link}'>Unsubscribe</a></p>"
    )
    return body_html.rstrip() + footer


# --- Bounce detection --------------------------------------------------------

_BOUNCE_SUBJECT_PATTERNS = (
    r"undeliverable",
    r"delivery status notification",
    r"delivery.?failure",
    r"failure notice",
    r"returned mail",
    r"mail delivery (failed|subsystem)",
    r"message not delivered",
)

_BOUNCE_SENDER_PATTERNS = (
    r"^mailer-daemon@",
    r"^postmaster@",
    r"^mail delivery subsystem",
)


def is_bounce_message(subject: str, from_addr: str, content_type: str = "") -> bool:
    """Heuristic bounce/DSN detection: subject or sender pattern match, or a
    multipart/report content-type (the standard MIME type for DSNs, RFC 3464)."""
    subject_l = (subject or "").lower()
    from_l = (from_addr or "").lower()
    if "multipart/report" in (content_type or "").lower():
        return True
    if any(re.search(p, subject_l) for p in _BOUNCE_SUBJECT_PATTERNS):
        return True
    if any(re.search(p, from_l) for p in _BOUNCE_SENDER_PATTERNS):
        return True
    return False
