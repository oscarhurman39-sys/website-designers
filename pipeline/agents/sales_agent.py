"""SalesAgent: drafts and sends cold emails, then polls the inbox and
classifies replies. This module owns every automated outbound send in the
system -- nothing else calls email_utils.send_email() for a cold email.

Human-in-the-loop: a 'positive' reply pauses automation for that lead (it
moves to status 'replied'/'negotiating', which is never selected by the
automatic sending queries) and raises a console + Slack alert. main.py's
stdin command thread handles the actual "takeover" / "payment ready" flow.
"""
from __future__ import annotations

import html as html_module
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import anthropic
import requests

import config
from utils import compliance, db, email_utils, screenshot, tracer, tracker

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # slack-sdk is a soft dependency; alerts still print to console.
    WebClient = None  # type: ignore[assignment,misc]
    SlackApiError = Exception  # type: ignore[assignment,misc]

# Max output tokens for a drafted email. The email itself is capped at 150
# words (~200 tokens) plus a short subject line, so 400 leaves comfortable
# headroom without risking a mid-sentence truncation.
_DRAFT_MAX_TOKENS = 400

# In-memory set of lead ids currently under manual human takeover. A lead
# enters this set when the operator types "takeover" at the console
# (see main.py) and is intentionally process-local / non-persistent: a
# restart requires the operator to re-confirm takeover, which is the safer
# default for a human-in-the-loop system.
_TAKEOVER_LEAD_IDS: set[int] = set()

# Throttles the *next* automatic cold-email send. Reset on process restart,
# which means a restart can send one email slightly earlier than the
# previous 120-300s window would have allowed -- an acceptable tradeoff for
# a system that otherwise enforces hard per-hour/per-day caps from the DB.
_next_send_allowed_at: datetime = datetime.min.replace(tzinfo=timezone.utc)

_NEGATIVE_PATTERNS = (
    r"\bunsubscribe\b", r"\bremove me\b", r"\bstop emailing\b", r"\bnot interested\b",
    r"\bno thanks\b", r"\btake me off\b", r"\bdo not contact\b", r"\bstop\b",
)
_POSITIVE_PATTERNS = (
    r"\binterested\b", r"\btell me more\b", r"\bhow much\b", r"\bpricing\b", r"\bprice\b",
    r"\bcost\b", r"\bsounds good\b", r"\blet'?s talk\b", r"\bschedule a call\b",
    r"\bsign me up\b", r"\byes\b", r"\bwhen can we\b",
)
_OOO_PATTERNS = (
    r"\bout of (the )?office\b", r"\bauto(-| )?reply\b", r"\bautomatic reply\b",
    r"\bon vacation\b", r"\bcurrently away\b", r"\b\bOOO\b",
)


# --- Console / Slack alerting -------------------------------------------------

def _console_alert(lead: dict) -> None:
    banner = "!" * 70
    print(f"\n{banner}\nLEAD REPLIED POSITIVELY: {lead['business_name']} <{lead['contact_email']}>"
          f"\nLead ID: {lead['id']}  |  Type 'takeover {lead['id']}' to begin manual negotiation.\n{banner}\n")


def _slack_alert(lead: dict) -> None:
    if not config.SLACK_BOT_TOKEN or WebClient is None:
        return
    try:
        client = WebClient(token=config.SLACK_BOT_TOKEN)
        client.chat_postMessage(
            channel=config.SLACK_ALERT_CHANNEL,
            text=(
                f":rotating_light: *LEAD REPLIED POSITIVELY*\n"
                f"*{lead['business_name']}* <{lead['contact_email']}> (lead id {lead['id']})\n"
                f"Type `takeover {lead['id']}` in the pipeline console to begin manual negotiation."
            ),
        )
    except SlackApiError as exc:
        print(f"[sales_agent] Slack alert failed: {exc}")


def alert_positive_reply(lead: dict) -> None:
    _console_alert(lead)
    _slack_alert(lead)


# --- Human takeover state ------------------------------------------------------

def begin_takeover(lead_id: int) -> None:
    _TAKEOVER_LEAD_IDS.add(lead_id)
    db.update_lead_status(lead_id, "negotiating", notes="Human took over negotiation")


def is_under_takeover(lead_id: int) -> bool:
    return lead_id in _TAKEOVER_LEAD_IDS


def end_takeover(lead_id: int) -> None:
    _TAKEOVER_LEAD_IDS.discard(lead_id)


# --- Email drafting (Claude) --------------------------------------------------

# Persona/behaviour stays constant across every lead, so it lives in the
# system prompt; the per-lead facts and output-format contract go in the user
# turn (see _build_user_prompt).
_DRAFT_SYSTEM_PROMPT = (
    "You write short, casual, human-sounding cold outreach emails for a "
    "freelance web designer. No hype, no exclamation-point energy, no hyperbole. "
    "Sound like a real person, not a marketer."
)


def _anthropic_client() -> anthropic.Anthropic:
    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _build_user_prompt(lead: dict) -> str:
    pain_point = (lead.get("pain_point") or "a slow or outdated website").strip()
    return (
        "Write a cold email to a local business with these facts:\n"
        f"- Business name: {lead['business_name']}\n"
        f"- Niche: {lead['niche']}\n"
        f"- Location: {lead.get('location', '')}\n"
        f"- Something noticed about their current site/reputation: {pain_point}\n\n"
        "The email must:\n"
        "- Mention that you built a free, live website preview for their business, no strings attached\n"
        "- Be under 150 words\n"
        "- NOT include a link (one will be appended separately)\n"
        "- NOT include a signature, footer, or unsubscribe text (appended separately)\n\n"
        "Respond in EXACTLY this format, nothing else:\n"
        "Subject: <short subject line>\n\n"
        "<email body>"
    )


def _fallback_email(lead: dict) -> tuple[str, str]:
    """Deterministic template used if the HF call fails, so a bad API day
    never stops the pipeline from sending compliant, on-brand emails."""
    subject = f"a free preview site for {lead['business_name']}"
    body = (
        f"Hi there,\n\n"
        f"I put together a free, live website preview for {lead['business_name']} -- "
        "no strings attached, just wanted to show you what's possible. "
        "I noticed your current online presence could use a refresh, so I figured "
        "I'd build one and let you take a look.\n\n"
        "Take a look whenever you get a chance -- no pressure either way."
    )
    return subject, body


def _parse_subject_body(raw_text: str, lead: dict) -> tuple[str, str]:
    match = re.search(r"Subject:\s*(.+?)\n+(.*)", raw_text, re.DOTALL | re.IGNORECASE)
    if not match:
        return _fallback_email(lead)
    subject = match.group(1).strip().strip('"')
    body = match.group(2).strip()
    if not subject or not body:
        return _fallback_email(lead)
    # Enforce the 150-word cap even if the model ignores instructions.
    words = body.split()
    if len(words) > 150:
        body = " ".join(words[:150]) + "..."
    return subject, body


def draft_cold_email(lead: dict) -> tuple[str, str]:
    """Return (subject, body_text_without_footer_or_link)."""
    try:
        response = _anthropic_client().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=_DRAFT_MAX_TOKENS,
            system=_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(lead)}],
        )
    except Exception as exc:  # noqa: BLE001 - any Claude/network failure falls back gracefully
        print(f"[sales_agent] Claude drafting failed, using fallback template: {exc}")
        return _fallback_email(lead)
    raw = "".join(block.text for block in response.content if block.type == "text")
    return _parse_subject_body(raw, lead)


# --- Sending (rate-limited) ----------------------------------------------------

_SCREENSHOT_CID = "preview"
_SENDER_NAME = "Casey"


def _intro_line(business_name: str) -> str:
    """Plain-text greeting that always sits above the screenshot image --
    frames this as solving a discoverability problem, not just a sales pitch."""
    return (
        f"I noticed people searching for {business_name} only find your Google "
        "listing. So I built a site that could help you appear more professional "
        "online."
    )


_CHECKLIST_HEADER = "What we improved"


def _checklist_items(city: str) -> list[str]:
    return [
        "Mobile-friendly design",
        "Faster page speed",
        "Clear calls-to-action",
        f"Local SEO for {city}",
        "Professional, trust-building look",
    ]


def _checklist_paragraph(city: str) -> str:
    """Plain-text equivalent of the HTML checklist card, placed right
    after the intro (plain text has no screenshot to sit it under)."""
    lines = "\n".join(f"✅ {item}" for item in _checklist_items(city))
    return f"{_CHECKLIST_HEADER}:\n{lines}"


def _checklist_html(city: str) -> str:
    """Light-grey rounded card listing what was improved, shown right
    after the screenshot."""
    items_html = "".join(
        f'<li style="padding:2px 0;">&#9989; {html_module.escape(item)}</li>'
        for item in _checklist_items(city)
    )
    return (
        '<div style="background:#f5f5f5;border-radius:8px;padding:16px 20px;margin:16px 0;">'
        f'<p style="font-weight:600;margin:0 0 8px;color:#333;">{_CHECKLIST_HEADER}</p>'
        f'<ul style="list-style:none;padding:0;margin:0;color:#444;">{items_html}</ul>'
        "</div>"
    )


def _closing_paragraphs(preview_link: str) -> list[str]:
    """Everything after the checklist: the live link, a no-pressure urgency
    note, the reply-to-buy offer, plain (non-anchored) pricing, and the
    sign-off. Deliberately just these lines -- no bullet points or feature
    lists beyond the checklist above."""
    return [
        f"View the live preview: {preview_link}",
        "This preview is live for 7 days -- after that it'll be repurposed. No pressure, just didn't want you to miss it.",
        "If you'd like to own it, reply YES. I'll connect your domain, swap in your own photos, and make any changes you want.",
        f"Standard package: £2,000. This completed draft: £{config.WEBSITE_OFFER_PRICE:,}.",
        _SENDER_NAME,
    ]


def _validate_preview_link_for_send(preview_link: str) -> str:
    """Return a public preview URL or raise before any cold email is sent."""
    website = db.get_website_by_lead(lead["id"])
    preview_link = website["preview_url"] if website and website.get("preview_url") else ""
    try:
        preview_link = _validate_preview_link_for_send(preview_link)
    except RuntimeError as exc:
        db.update_lead_status(
            lead["id"],
            "researched",
            notes=f"Email blocked: preview URL is not publicly sendable ({exc})",
        )
        return False
    parsed = urlparse(preview_link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError(f"Preview URL is missing or invalid: {preview_link!r}")

    if any(part in parsed.path.lower() for part in _AUTH_URL_PARTS):
        raise RuntimeError(f"Preview URL points to an authentication path: {preview_link}")

    try:
        resp = requests.get(preview_link, allow_redirects=True, timeout=_PREVIEW_VALIDATION_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise RuntimeError(f"Preview URL is not publicly accessible: {preview_link}") from exc

    final_url = resp.url or preview_link
    final_path = urlparse(final_url).path.lower()
    if resp.status_code >= 400:
        raise RuntimeError(f"Preview URL returned HTTP {resp.status_code}: {preview_link}")
    if any(part in final_path for part in _AUTH_URL_PARTS):
        raise RuntimeError(f"Preview URL redirects to an authentication path: {final_url}")
    if any(marker in resp.text.lower() for marker in _AUTH_PAGE_MARKERS):
        raise RuntimeError(f"Preview URL shows an authentication page: {preview_link}")

    return preview_link


def _plain_text_body(business_name: str, preview_link: str, city: str) -> str:
    return "\n\n".join([
        _intro_line(business_name),
        _checklist_paragraph(city),
        *_closing_paragraphs(preview_link),
    ])


def _build_html_body(business_name: str, preview_link: str, city: str) -> str:
    """Intro greeting, then the cached screenshot, then the "what we
    improved" checklist, then the closing paragraphs -- only called when a
    screenshot is actually available; see _send_cold_email_impl."""
    escaped_link = html_module.escape(preview_link)
    intro_html = f"<p>{html_module.escape(_intro_line(business_name))}</p>"
    image_html = (
        f'<p><a href="{escaped_link}">'
        f'<img src="cid:{_SCREENSHOT_CID}" alt="Your new website preview" '
        'style="max-width:100%;border:1px solid #ddd;border-radius:8px;">'
        "</a></p>"
    )
    checklist_html = _checklist_html(city)
    preview_button_html = (
        f'<a href="{escaped_link}" style="display:inline-block;padding:14px 28px;'
        'background:#2563eb;color:white;border-radius:8px;text-decoration:none;'
        'font-size:16px;font-weight:bold;margin:16px 0">View Your Free Website &rarr;</a>'
    )
    closing_paragraphs = _closing_paragraphs(preview_link)
    closing_html = preview_button_html + "".join(
        f"<p>{html_module.escape(para).replace(chr(10), '<br>')}</p>"
        for para in closing_paragraphs[1:]
    )
    return intro_html + image_html + checklist_html + closing_html


def _can_send_now() -> bool:
    now = datetime.now(timezone.utc)
    if now < _next_send_allowed_at:
        return False
    if db.emails_sent_last_hour() >= config.EMAIL_MAX_PER_HOUR:
        return False
    if db.emails_sent_today() >= config.EMAIL_MAX_PER_DAY:
        return False
    return True


def _send_via_configured_transport(
    to_addr: str,
    subject: str,
    body_text: str,
    lead_id: int,
    body_html: Optional[str] = None,
    inline_image_path: Optional[str] = None,
    inline_image_cid: Optional[str] = None,
) -> str:
    """Send email via configured transport: SendGrid if SENDGRID_API_KEY is set,
    otherwise fall back to SMTP via send_email.
    
    Returns the message_id of the sent email.
    """
    if config.SENDGRID_API_KEY:
        return email_utils.send_email_sendgrid(
            to_addr=to_addr,
            subject=subject,
            body_text=body_text,
            lead_id=lead_id,
            body_html=body_html,
            inline_image_path=inline_image_path,
            inline_image_cid=inline_image_cid,
        )
    else:
        return email_utils.send_email(
            to_addr=to_addr,
            subject=subject,
            body_text=body_text,
            lead_id=lead_id,
            body_html=body_html,
            inline_image_path=inline_image_path,
            inline_image_cid=inline_image_cid,
        )


def send_cold_email(lead: dict) -> bool:
    """Send the initial cold email for one 'designed' lead. Returns True if sent.
    Wrapped in a trace -> agent -> tool span (see utils/tracer.py); the
    actual drafting/sending logic lives untouched in _send_cold_email_impl."""
    return tracer.run_traced(
        agent_id="sales-agent",
        agent_name="SalesAgent",
        tool_name="draft_and_send_cold_email",
        input_data={"lead_id": lead["id"], "business_name": lead["business_name"]},
        fn=lambda: _send_cold_email_impl(lead),
    )


def _send_cold_email_impl(lead: dict) -> bool:
    global _next_send_allowed_at

    email_addr = lead.get("contact_email") or ""
    if not email_addr or db.is_unsubscribed(email_addr):
        db.update_lead_status(lead["id"], "unsubscribed" if db.is_unsubscribed(email_addr) else "lost",
                               notes="No usable email at send time")
        return False

    subject = f"I built a website for {lead['business_name']}"
    website = db.get_website_by_lead(lead["id"])
    preview_link = website["preview_url"] if website and website.get("preview_url") else ""
    try:
        preview_link = _validate_preview_link_for_send(preview_link)
    except RuntimeError as exc:
        db.update_lead_status(
            lead["id"],
            "researched",
            notes=f"Email blocked: preview URL is not publicly sendable ({exc})",
        )
        return False
    city = lead.get("location") or "your area"
    body_with_link = _plain_text_body(lead["business_name"], preview_link, city)

    # Embed the cached preview screenshot inline (cid:) if design_agent.py
    # already captured one for this lead; otherwise send exactly the same
    # plain-text-only email as before -- a missing screenshot (capture
    # failed, or an older lead from before this feature existed) must
    # never block or change the send itself.
    cached_screenshot = screenshot.get_cached_screenshot(lead["id"])
    body_html = _build_html_body(lead["business_name"], preview_link, city) if cached_screenshot else None
    inline_image_path = str(cached_screenshot) if cached_screenshot else None

    message_id = _send_via_configured_transport(
        to_addr=email_addr,
        subject=subject,
        body_text=body_with_link,
        lead_id=lead["id"],
        body_html=body_html,
        inline_image_path=inline_image_path,
        inline_image_cid=_SCREENSHOT_CID,
    )
    db.insert_email_thread(
        lead_id=lead["id"],
        direction="outbound",
        subject=subject,
        body=body_with_link,
        from_addr=config.EMAIL_USER,
        to_addr=email_addr,
        message_id=message_id,
    )
    db.update_lead_status(lead["id"], "emailed", notes="Cold email sent")

    _next_send_allowed_at = datetime.now(timezone.utc) + timedelta(
        seconds=random.uniform(config.EMAIL_MIN_DELAY_SECONDS, config.EMAIL_MAX_DELAY_SECONDS)
    )
    return True


def send_next_pending() -> Optional[int]:
    """Send at most one queued cold email, respecting rate limits. Returns
    the lead_id sent to, or None if nothing was sent this cycle."""
    if not _can_send_now():
        return None
    for lead in db.list_leads_by_status("designed"):
        if is_under_takeover(lead["id"]):
            continue
        if send_cold_email(lead):
            return lead["id"]
    return None


# --- Reply classification -----------------------------------------------------

def classify_reply(subject: str, body: str) -> str:
    """Keyword-based classifier (deliberately not an LLM call): reply
    classification gates real actions -- unsubscribing someone, marking a
    lead bounced/lost -- so it needs to be fast, free, and 100% deterministic
    rather than dependent on a third-party inference API's uptime."""
    text = f"{subject}\n{body}".lower()
    if any(re.search(p, text) for p in _OOO_PATTERNS):
        return "out_of_office"
    if any(re.search(p, text) for p in _NEGATIVE_PATTERNS):
        return "negative"
    if any(re.search(p, text) for p in _POSITIVE_PATTERNS):
        return "positive"
    # Ambiguous replies default to 'positive' so a real human reply is never
    # silently dropped -- worst case a human reviews a lukewarm reply.
    return "positive"


def _send_goodbye(lead: dict) -> None:
    """Automatic, polite acknowledgment sent when a lead declines -- required
    so 'stop emailing me' always gets a confirmation, not silence."""
    email_addr = lead.get("contact_email") or ""
    if not email_addr or db.is_unsubscribed(email_addr):
        return
    subject = "No problem"
    body = (
        f"Hi, totally understood -- I won't reach out again about this. "
        f"Wishing {lead['business_name']} all the best."
    )
    try:
        message_id = _send_via_configured_transport(
            to_addr=email_addr,
            subject=subject,
            body_text=body,
            lead_id=lead["id"],
        )
        db.insert_email_thread(
            lead_id=lead["id"], direction="outbound", subject=subject, body=body,
            from_addr=config.EMAIL_USER, to_addr=email_addr, message_id=message_id,
        )
    except RuntimeError:
        pass  # already unsubscribed between the check above and now; nothing to do


def _handle_inbound(lead: dict, msg) -> None:  # msg: email_utils.InboundEmail
    """Classify and act on one inbound reply. Wrapped in a trace -> agent ->
    tool span (see utils/tracer.py); the actual classification/action logic
    lives untouched in _handle_inbound_impl."""
    tracer.run_traced(
        agent_id="sales-agent",
        agent_name="SalesAgent",
        tool_name="classify_and_handle_reply",
        input_data={"lead_id": lead["id"], "from_addr": msg.from_addr, "subject": msg.subject},
        fn=lambda: _handle_inbound_impl(lead, msg),
    )


def _handle_inbound_impl(lead: dict, msg) -> None:  # msg: email_utils.InboundEmail
    if compliance.is_bounce_message(msg.subject, msg.from_addr, msg.content_type):
        classification = "bounce"
        db.insert_email_thread(
            lead_id=lead["id"], direction="inbound", subject=msg.subject, body=msg.body,
            from_addr=msg.from_addr, to_addr=msg.to_addr, message_id=msg.message_id,
            classification=classification,
        )
        db.update_lead_status(lead["id"], "bounced", notes="Bounce/DSN detected")
        return

    classification = classify_reply(msg.subject, msg.body)
    db.insert_email_thread(
        lead_id=lead["id"], direction="inbound", subject=msg.subject, body=msg.body,
        from_addr=msg.from_addr, to_addr=msg.to_addr, message_id=msg.message_id,
        classification=classification,
    )

    if classification == "negative":
        db.update_lead_status(lead["id"], "lost", notes="Replied negative")
        _send_goodbye(lead)
    elif classification == "out_of_office":
        db.log_state_history(lead["id"], lead["status"], lead["status"], notes="Out-of-office auto-reply")
    elif classification == "positive":
        if lead["status"] not in ("negotiating", "won", "payment_sent"):
            db.update_lead_status(lead["id"], "replied", notes="Replied positive")
        alert_positive_reply(lead)


def check_inbox() -> int:
    """Poll IMAP for unseen messages, classify, and act. Returns count processed."""
    processed = 0
    try:
        messages = email_utils.fetch_unseen_emails()
    except Exception as exc:  # noqa: BLE001 - a flaky IMAP poll must not crash the loop
        print(f"[sales_agent] IMAP poll failed: {exc}")
        return 0

    for msg in messages:
        if db.message_id_seen(msg.message_id):
            continue  # already processed in a prior poll
        lead = db.get_lead_by_email(msg.from_addr)
        if lead is None:
            continue  # reply from an address we have no lead for; nothing to act on
        _handle_inbound(lead, msg)
        processed += 1
    return processed
