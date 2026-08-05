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
import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from huggingface_hub import InferenceClient

import config
from utils import compliance, db, email_utils, screenshot, tracer, tracker, url_safety

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # slack-sdk is a soft dependency; alerts still print to console.
    WebClient = None  # type: ignore[assignment,misc]
    SlackApiError = Exception  # type: ignore[assignment,misc]

HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"

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


# --- Email drafting (Hugging Face) --------------------------------------------

def _hf_client() -> InferenceClient:
    if not config.HF_API_TOKEN:
        raise RuntimeError("HF_API_TOKEN is not configured.")
    return InferenceClient(model=HF_MODEL, token=config.HF_API_TOKEN)


def _drafting_facts(lead: dict) -> list[str]:
    """Step A of the two-step drafting pattern: gather the concrete,
    verifiable hooks about this lead. The model writes FROM these facts
    instead of inventing details -- splitting retrieval from generation is
    the cheapest quality win for personalization."""
    facts = [
        f"Business name: {lead['business_name']}",
        f"Trade: {lead['niche']}",
        f"Location: {lead.get('location') or 'unknown'}",
    ]
    pain_point = (lead.get("pain_point") or "").strip()
    if pain_point:
        facts.append(f"Observed on their current website: {pain_point}")
    for finding in _lead_audit(lead).get("pain_points", [])[:2]:
        facts.append(f"Site audit finding: {finding}")
    return facts


def _build_prompt(lead: dict) -> str:
    facts = "\n".join(f"- {fact}" for fact in _drafting_facts(lead))
    return (
        "<s>[INST] You write the OPENING of short, casual, human-sounding cold "
        "outreach emails for a freelance web designer.\n\n"
        "Facts about the recipient (use at least one specific fact; invent nothing):\n"
        f"{facts}\n\n"
        "Write a subject line and a 2-3 sentence opening paragraph that:\n"
        "- References something specific from the facts above\n"
        "- Mentions you built them a free, live website preview, no strings attached\n"
        "- Is under 60 words total\n"
        "- Does NOT include a link, signature, or unsubscribe text (appended separately)\n"
        "- Does NOT use generic openers like 'I hope this finds you well'\n"
        "- Does NOT use hype, exclamation marks, or marketing buzzwords\n\n"
        "Respond in EXACTLY this format, nothing else:\n"
        "Subject: <short subject line>\n\n"
        "<opening paragraph>\n[/INST]"
    )


def _fallback_subject_and_intro(lead: dict) -> tuple[str, str]:
    """Deterministic subject + opening used when no LLM is configured or the
    call fails -- a bad API day never stops compliant sending."""
    return f"I built a website for {lead['business_name']}", _intro_line(lead)


def _parse_subject_body(raw_text: str, lead: dict) -> tuple[str, str]:
    match = re.search(r"Subject:\s*(.+?)\n+(.*)", raw_text, re.DOTALL | re.IGNORECASE)
    if not match:
        return _fallback_subject_and_intro(lead)
    subject = match.group(1).strip().strip('"')
    intro = match.group(2).strip()
    if not subject or not intro:
        return _fallback_subject_and_intro(lead)
    # Enforce the word cap even if the model ignores instructions.
    words = intro.split()
    if len(words) > 60:
        intro = " ".join(words[:60]) + "..."
    return subject, intro


def draft_cold_email(lead: dict) -> tuple[str, str]:
    """Return (subject, intro_paragraph) for the cold email. The rest of the
    body (screenshot, checklist, link, pricing, sign-off) stays deterministic
    -- only the personalized opening is LLM-drafted, so compliance-critical
    structure can never be dropped by a model. Falls back to a deterministic
    subject/intro when HF_API_TOKEN is unset or the call fails."""
    if not config.HF_API_TOKEN:
        return _fallback_subject_and_intro(lead)
    try:
        raw = _hf_client().text_generation(
            _build_prompt(lead), max_new_tokens=160, temperature=0.7, do_sample=True
        )
    except Exception as exc:  # noqa: BLE001 - any HF/network failure falls back gracefully
        print(f"[sales_agent] HF drafting failed, using fallback template: {exc}")
        return _fallback_subject_and_intro(lead)
    return _parse_subject_body(raw, lead)


# --- Sending (rate-limited) ----------------------------------------------------

_SCREENSHOT_CID = "preview"
_SENDER_NAME = "Casey"


def _lead_audit(lead: dict) -> dict:
    """The lead's stored site audit (see utils/site_audit.py) as a dict,
    or {} when research didn't produce one."""
    raw = lead.get("site_audit") or ""
    if not raw:
        return {}
    try:
        return json.loads(raw) or {}
    except (ValueError, TypeError):
        return {}


def _intro_line(lead: dict) -> str:
    """Plain-text greeting that always sits above the screenshot image.
    When the site audit found something concrete, say that -- it's
    specific, true, and phrased as a problem so the sentence reads
    naturally. (The old generic line claimed the business had no website
    at all, which research had usually just disproven.)"""
    audit_points = _lead_audit(lead).get("pain_points", [])
    if audit_points:
        return (
            f"I had a look at {lead['business_name']}'s current website and noticed "
            f"{audit_points[0]} -- so I went ahead and built you a refreshed version "
            "to show what's possible."
        )
    if (lead.get("website_url") or "").strip():
        return (
            f"I spent some time on {lead['business_name']}'s current website and put "
            "together a refreshed version to show what's possible -- no strings "
            "attached, just wanted you to see it."
        )
    return (
        f"I put together a free website preview for {lead['business_name']} -- "
        "no strings attached, just wanted to show you what a modern site for "
        "your business could look like."
    )


_CHECKLIST_HEADER = "What we improved"


def _checklist_items(city: str, lead: Optional[dict] = None) -> list[str]:
    """The "what we improved" bullets. When the lead's site audit found
    real, specific problems, lead with those (specific beats generic);
    top up with the standard items to keep the list at ~5."""
    standard = [
        "Mobile-friendly design",
        "Faster page speed",
        "Clear calls-to-action",
        f"Local SEO for {city}",
        "Professional, trust-building look",
    ]
    audited = _lead_audit(lead or {}).get("improvements", [])[:3]
    items = list(audited)
    for item in standard:
        if len(items) >= 5:
            break
        # Don't repeat a theme the audit already covered more specifically.
        if any(item.split(" (")[0].lower() in a.lower() for a in audited):
            continue
        items.append(item)
    return items


def _checklist_paragraph(city: str, lead: Optional[dict] = None) -> str:
    """Plain-text equivalent of the HTML checklist card, placed right
    after the intro (plain text has no screenshot to sit it under)."""
    lines = "\n".join(f"✅ {item}" for item in _checklist_items(city, lead))
    return f"{_CHECKLIST_HEADER}:\n{lines}"


def _checklist_html(city: str, lead: Optional[dict] = None) -> str:
    """Light-grey rounded card listing what was improved, shown right
    after the screenshot."""
    items_html = "".join(
        f'<li style="padding:2px 0;">&#9989; {html_module.escape(item)}</li>'
        for item in _checklist_items(city, lead)
    )
    return (
        '<div style="background:#f5f5f5;border-radius:8px;padding:16px 20px;margin:16px 0;">'
        f'<p style="font-weight:600;margin:0 0 8px;color:#333;">{_CHECKLIST_HEADER}</p>'
        f'<ul style="list-style:none;padding:0;margin:0;color:#444;">{items_html}</ul>'
        "</div>"
    )


def _buy_link(lead_id: int) -> str:
    """Self-serve checkout -- webhook_server.py's GET /buy/<lead_id>
    creates a fresh Stripe Checkout Session on click (never a pre-
    generated URL embedded here, since Checkout Sessions expire and this
    email might be opened days later)."""
    return f"{config.PUBLIC_BASE_URL}/buy/{lead_id}"


def _urgency_note() -> str:
    return "This preview is live for 7 days -- after that it'll be repurposed. No pressure, just didn't want you to miss it."


def _buy_or_reply_line(lead_id: int) -> str:
    return (
        f"Ready to make it yours? Buy it directly here: {_buy_link(lead_id)} -- "
        "or just reply YES and I'll take it from there."
    )


def _pricing_line() -> str:
    return f"Standard package: £2,000. This completed draft: £{config.WEBSITE_OFFER_PRICE:,}."


def _closing_paragraphs(lead_id: int, preview_link: str) -> list[str]:
    """Everything after the checklist: the live link, a no-pressure urgency
    note, a direct self-serve buy link alongside the reply-to-buy offer,
    plain (non-anchored) pricing, and the sign-off. Deliberately just these
    lines -- no bullet points or feature lists beyond the checklist above."""
    return [
        f"View the live preview: {preview_link}",
        _urgency_note(),
        _buy_or_reply_line(lead_id),
        _pricing_line(),
        _SENDER_NAME,
    ]


def _validate_preview_link_for_send(preview_link: str) -> str:
    """Return a public preview URL or raise RuntimeError before any cold
    email is sent. Same checks as design_agent applies at deploy time (see
    utils/url_safety.py) -- re-run here because a preview can go dark or
    fall behind Vercel authentication between deploy and send."""
    return url_safety.validate_public_url(preview_link)


def _plain_text_body(lead: dict, preview_link: str, intro: str) -> str:
    city = lead.get("location") or "your area"
    return "\n\n".join([
        intro,
        _checklist_paragraph(city, lead),
        *_closing_paragraphs(lead["id"], preview_link),
    ])


def _build_html_body(lead: dict, preview_link: str, intro: str) -> str:
    """Intro greeting, then the cached screenshot, then the "what we
    improved" checklist, then the closing paragraphs -- only called when a
    screenshot is actually available; see _send_cold_email_impl.

    Renders its own buttons for the preview link and the self-serve buy
    link (nicer than a raw URL) rather than reusing _closing_paragraphs'
    plain-text lines verbatim -- those two lines exist there for the
    plain-text body, which has no buttons to render instead."""
    city = lead.get("location") or "your area"
    escaped_link = html_module.escape(preview_link)
    escaped_buy_link = html_module.escape(_buy_link(lead["id"]))
    intro_html = f"<p>{html_module.escape(intro)}</p>"
    image_html = (
        f'<p><a href="{escaped_link}">'
        f'<img src="cid:{_SCREENSHOT_CID}" alt="Your new website preview" '
        'style="max-width:100%;border:1px solid #ddd;border-radius:8px;">'
        "</a></p>"
    )
    checklist_html = _checklist_html(city, lead)
    preview_button_html = (
        f'<a href="{escaped_link}" style="display:inline-block;padding:14px 28px;'
        'background:#2563eb;color:white;border-radius:8px;text-decoration:none;'
        'font-size:16px;font-weight:bold;margin:16px 0">View Your Free Website &rarr;</a>'
    )
    buy_button_html = (
        f'<p><a href="{escaped_buy_link}" style="display:inline-block;padding:14px 28px;'
        'background:#16a34a;color:white;border-radius:8px;text-decoration:none;'
        'font-size:16px;font-weight:bold;">Buy This Website &rarr;</a></p>'
        "<p>Or just reply YES and I'll take it from there.</p>"
    )
    closing_html = (
        preview_button_html
        + f"<p>{html_module.escape(_urgency_note())}</p>"
        + buy_button_html
        + f"<p>{html_module.escape(_pricing_line())}</p>"
        + f"<p>{html_module.escape(_SENDER_NAME)}</p>"
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
    subject, intro = draft_cold_email(lead)
    body_with_link = _plain_text_body(lead, preview_link, intro)

    # Embed the cached preview screenshot inline (cid:) if design_agent.py
    # already captured one for this lead; otherwise send exactly the same
    # plain-text-only email as before -- a missing screenshot (capture
    # failed, or an older lead from before this feature existed) must
    # never block or change the send itself.
    cached_screenshot = screenshot.get_cached_screenshot(lead["id"])
    body_html = _build_html_body(lead, preview_link, intro) if cached_screenshot else None
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


# --- Follow-ups ------------------------------------------------------------
# A single cold email leaves replies on the table; most answers come from a
# polite 2nd or 3rd touch. Sequence: initial (day 0), nudge (~day 3),
# breakup (~day 7) -- then never again. Any reply, positive or negative,
# takes the lead out of 'emailed' status and therefore out of this loop.

def _parse_db_timestamp(ts: str) -> Optional[datetime]:
    """email_threads.timestamp is SQLite's datetime('now'): UTC, in
    'YYYY-MM-DD HH:MM:SS' format."""
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _first_outbound_subject(lead_id: int) -> str:
    for msg in db.get_email_threads(lead_id):
        if msg["direction"] == "outbound" and (msg.get("subject") or ""):
            return msg["subject"]
    return ""


def _followup_copy(lead: dict, followup_index: int, preview_link: str) -> tuple[str, str]:
    """(subject, body) for follow-up N (0-based). Deliberately short, plain
    text, no screenshot -- a quick personal note, not a second brochure."""
    original_subject = _first_outbound_subject(lead["id"]) or f"a website for {lead['business_name']}"
    subject = f"Re: {original_subject}"
    buy_link = _buy_link(lead["id"])
    if followup_index == 0:
        body = (
            "Just floating this back up in case it got buried -- the free preview "
            f"site I built for {lead['business_name']} is still live here: {preview_link}\n\n"
            f"If you'd like to make it yours, buy it directly here: {buy_link} -- or "
            "just reply YES and I'll get it set up on your own domain. If not, no "
            "worries at all.\n\n"
            f"{_SENDER_NAME}"
        )
    else:
        body = (
            "Last note from me, promise. I'll be taking "
            f"{lead['business_name']}'s preview site down at the end of this week -- "
            f"if you'd like to keep it, here's the link one more time: {preview_link}\n\n"
            f"Buy it here: {buy_link} -- or reply YES any time before then and it's "
            "yours. Either way, wishing you a busy season.\n\n"
            f"{_SENDER_NAME}"
        )
    return subject, body


def _followup_due_index(lead: dict) -> Optional[int]:
    """Which follow-up (0-based) this 'emailed' lead is due for right now,
    or None. Outbound count 1 -> maybe follow-up 0; count 2 -> maybe
    follow-up 1; past the schedule -> never."""
    outbound = [m for m in db.get_email_threads(lead["id"]) if m["direction"] == "outbound"]
    if not outbound:
        return None
    followup_index = len(outbound) - 1
    if followup_index >= len(config.FOLLOWUP_GAPS_DAYS):
        return None
    last_ts = _parse_db_timestamp(outbound[-1]["timestamp"])
    if last_ts is None:
        return None
    gap = timedelta(days=config.FOLLOWUP_GAPS_DAYS[followup_index])
    return followup_index if datetime.now(timezone.utc) - last_ts >= gap else None


def _send_followup_impl(lead: dict, followup_index: int) -> bool:
    global _next_send_allowed_at

    email_addr = lead.get("contact_email") or ""
    if not email_addr or db.is_unsubscribed(email_addr):
        return False

    website = db.get_website_by_lead(lead["id"])
    preview_link = website["preview_url"] if website and website.get("preview_url") else ""
    try:
        preview_link = _validate_preview_link_for_send(preview_link)
    except RuntimeError as exc:
        db.log_state_history(
            lead["id"], lead["status"], lead["status"],
            notes=f"Follow-up {followup_index + 1} skipped: preview not sendable ({exc})",
        )
        return False

    subject, body = _followup_copy(lead, followup_index, preview_link)
    message_id = _send_via_configured_transport(
        to_addr=email_addr, subject=subject, body_text=body, lead_id=lead["id"],
    )
    db.insert_email_thread(
        lead_id=lead["id"], direction="outbound", subject=subject, body=body,
        from_addr=config.EMAIL_USER, to_addr=email_addr, message_id=message_id,
    )
    db.log_state_history(
        lead["id"], lead["status"], lead["status"], notes=f"Follow-up {followup_index + 1} sent",
    )
    _next_send_allowed_at = datetime.now(timezone.utc) + timedelta(
        seconds=random.uniform(config.EMAIL_MIN_DELAY_SECONDS, config.EMAIL_MAX_DELAY_SECONDS)
    )
    return True


def send_followups() -> Optional[int]:
    """Send at most one due follow-up, respecting the same rate limits as
    cold sends. Called by main.py only when no cold email went out this
    cycle, so the pipeline never sends more than one automated email per
    cycle. Returns the lead_id followed up, or None."""
    if not _can_send_now():
        return None
    for lead in db.list_leads_by_status("emailed"):
        if is_under_takeover(lead["id"]):
            continue
        followup_index = _followup_due_index(lead)
        if followup_index is None:
            continue
        sent = tracer.run_traced(
            agent_id="sales-agent",
            agent_name="SalesAgent",
            tool_name="send_followup_email",
            input_data={"lead_id": lead["id"], "followup_index": followup_index},
            fn=lambda lead=lead, idx=followup_index: _send_followup_impl(lead, idx),
        )
        if sent:
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
