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
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from huggingface_hub import InferenceClient

import config
from utils import compliance, db, email_utils, screenshot, tracer, tracker

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # slack-sdk is a soft dependency; alerts still print to console.
    WebClient = None  # type: ignore[assignment,misc]
    SlackApiError = Exception  # type: ignore[assignment,misc]

try:
    import anthropic
except ImportError:  # anthropic is a soft dependency; only needed if ANTHROPIC_API_KEY is set.
    anthropic = None  # type: ignore[assignment]

HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"
CLAUDE_MODEL = "claude-3-5-haiku-latest"

# Bumped whenever _build_prompt()/CLAUDE_MODEL/HF_MODEL's drafting logic
# changes meaningfully -- stored alongside each sent email (see
# db.insert_email_thread's prompt_version) so a regression in email quality
# can be traced back to which drafting logic produced it.
PROMPT_VERSION = "sales-agent-v1"

# In-memory set of lead ids currently under manual human takeover. A lead
# enters this set when the operator types "takeover" at the console
# (see main.py) and is intentionally process-local / non-persistent: a
# restart requires the operator to re-confirm takeover, which is the safer
# default for a human-in-the-loop system.
_TAKEOVER_LEAD_IDS: set[int] = set()

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

def begin_takeover(lead_id: int, actor: str = "operator") -> None:
    _TAKEOVER_LEAD_IDS.add(lead_id)
    db.update_lead_status(lead_id, "negotiating", notes="Human took over negotiation")
    db.log_lead_event(lead_id, "takeover", payload={"actor": actor}, actor=actor)


def is_under_takeover(lead_id: int) -> bool:
    return lead_id in _TAKEOVER_LEAD_IDS


def end_takeover(lead_id: int) -> None:
    _TAKEOVER_LEAD_IDS.discard(lead_id)


# --- Email drafting (Hugging Face) --------------------------------------------

def _hf_client() -> InferenceClient:
    if not config.HF_API_TOKEN:
        raise RuntimeError("HF_API_TOKEN is not configured.")
    return InferenceClient(model=HF_MODEL, token=config.HF_API_TOKEN)


def _build_prompt(lead: dict) -> str:
    pain_point = (lead.get("pain_point") or "a slow or outdated website").strip()
    return (
        "<s>[INST] You write short, casual, human-sounding cold outreach emails for a "
        "freelance web designer. No hype, no exclamation-point energy, no hyperbole. "
        "Sound like a real person, not a marketer.\n\n"
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
        "<email body>\n[/INST]"
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


def _draft_with_claude(lead: dict) -> Optional[str]:
    """Best-effort secondary LLM drafting attempt, used only when the
    primary Hugging Face call fails. Returns raw model text in the same
    "Subject: ...\\n\\n<body>" format as the HF prompt, or None if Claude
    isn't configured/available/fails too -- callers fall through to the
    deterministic template in that case, exactly as before this existed."""
    if not config.ANTHROPIC_API_KEY or anthropic is None:
        return None
    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=280,
            messages=[{"role": "user", "content": _build_prompt(lead)}],
        )
        return response.content[0].text
    except Exception as exc:  # noqa: BLE001 - any Claude/network failure falls through
        print(f"[sales_agent] Claude fallback drafting also failed: {exc}")
        return None


def draft_cold_email(lead: dict) -> tuple[str, str]:
    """Return (subject, body_text_without_footer_or_link).

    Refuses to draft at all (rather than only refusing to send later) if
    PHYSICAL_ADDRESS is missing/placeholder -- no point spending an HF call
    on an email that utils/compliance.py will refuse to send anyway.

    Drafting order: Hugging Face (primary) -> Anthropic Claude, only if
    ANTHROPIC_API_KEY is configured (secondary, so a primary-provider outage
    doesn't stall the pipeline) -> deterministic template (final fallback,
    always available, no API dependency)."""
    problem = config.physical_address_problem()
    if problem:
        raise RuntimeError(
            f"Refusing to draft cold email: PHYSICAL_ADDRESS is {problem}. Set a real "
            "postal address in .env (PHYSICAL_ADDRESS=...) before drafting."
        )
    try:
        raw = _hf_client().text_generation(
            _build_prompt(lead), max_new_tokens=280, temperature=0.7, do_sample=True
        )
    except Exception as exc:  # noqa: BLE001 - any HF/network failure falls back gracefully
        print(f"[sales_agent] HF drafting failed, trying fallback provider: {exc}")
        raw = _draft_with_claude(lead)
        if raw is None:
            return _fallback_email(lead)
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


# Minimum Google review count before the "Rated X.X on Google" line is
# shown in the email -- a handful of reviews reads worse than no mention at
# all. Mirrors design_agent.py's MIN_GOOGLE_REVIEWS_FOR_BADGE threshold
# used on the website itself.
MIN_GOOGLE_REVIEWS_FOR_RATING_LINE = 15

_WATERMARK_TEXT = "Designed by Oscar"


def _google_rating_line(lead: dict) -> Optional[str]:
    """'Rated X.X on Google ⭐' if the lead has a rating backed by enough
    reviews to be worth citing, else None (omitted entirely -- no rating
    line is better than a shaky one)."""
    rating = lead.get("google_rating")
    reviews_count = lead.get("google_reviews_count") or 0
    if rating and reviews_count >= MIN_GOOGLE_REVIEWS_FOR_RATING_LINE:
        return f"Rated {rating:.1f} on Google ⭐"
    return None


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


def _plain_text_body(lead: dict, preview_link: str, city: str) -> str:
    business_name = lead["business_name"]
    rating_line = _google_rating_line(lead)
    return "\n\n".join([
        p for p in [
            _intro_line(business_name),
            rating_line,
            _checklist_paragraph(city),
            _WATERMARK_TEXT,
            *_closing_paragraphs(preview_link),
        ] if p
    ])


def _build_html_body(lead: dict, preview_link: str, city: str) -> str:
    """Intro greeting, then the cached screenshot, then an optional Google
    rating line, the "what we improved" checklist, a small designer
    watermark, then the closing paragraphs -- only called when a
    screenshot is actually available; see _send_cold_email_impl."""
    business_name = lead["business_name"]
    escaped_link = html_module.escape(preview_link)
    intro_html = f"<p>{html_module.escape(_intro_line(business_name))}</p>"
    image_html = (
        f'<p><a href="{escaped_link}">'
        f'<img src="cid:{_SCREENSHOT_CID}" alt="Your new website preview" '
        'style="max-width:100%;border:1px solid #ddd;border-radius:8px;">'
        "</a></p>"
    )
    rating_line = _google_rating_line(lead)
    rating_html = (
        f'<p style="color:#b8860b;font-weight:600;">{html_module.escape(rating_line)}</p>'
        if rating_line else ""
    )
    checklist_html = _checklist_html(city)
    watermark_html = f'<p style="font-size:12px;color:#999;">{html_module.escape(_WATERMARK_TEXT)}</p>'
    closing_html = "".join(
        f"<p>{html_module.escape(para).replace(chr(10), '<br>')}</p>"
        for para in _closing_paragraphs(preview_link)
    )
    return intro_html + image_html + rating_html + checklist_html + watermark_html + closing_html


def _can_send_now() -> bool:
    now = datetime.now(timezone.utc)
    # Persisted in the DB (see db.get_next_send_allowed_at/set_next_send_allowed_at)
    # rather than an in-memory global, so a crash/restart can't forget the
    # randomized 120-300s delay that was in flight and send a burst of
    # cold emails back-to-back.
    next_allowed_at = db.get_next_send_allowed_at()
    if next_allowed_at is not None and now < next_allowed_at:
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
    idempotency_key: Optional[str] = None,
) -> str:
    """Send email via configured transport: SendGrid if SENDGRID_API_KEY is set,
    otherwise fall back to SMTP via send_email.

    `idempotency_key`, if given, is attached as a SendGrid custom_arg (or an
    X-Idempotency-Key header for SMTP) so a send can be traced back to the
    pending-send marker that preceded it -- see db.mark_send_pending().

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
            idempotency_key=idempotency_key,
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
            idempotency_key=idempotency_key,
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
    email_addr = lead.get("contact_email") or ""
    blocked = email_addr and (db.is_unsubscribed(email_addr) or db.is_suppressed(email_addr))
    if not email_addr or blocked:
        db.update_lead_status(lead["id"], "unsubscribed" if blocked else "lost",
                               notes="No usable email at send time")
        return False

    subject = f"I built a website for {lead['business_name']}"
    # Link straight to the actual Vercel preview (from the websites table)
    # rather than the click-tracked localhost:5000/click redirect, so the
    # link in the email is the real, shareable site URL. Falls back to the
    # tracked link only if no website record/preview_url exists yet.
    website = db.get_website_by_lead(lead["id"])
    preview_link = website["preview_url"] if website and website.get("preview_url") else tracker.create_click_link(lead["id"])
    city = lead.get("location") or "your area"
    body_with_link = _plain_text_body(lead, preview_link, city)

    # Embed the cached preview screenshot inline (cid:) if design_agent.py
    # already captured one for this lead; otherwise send exactly the same
    # plain-text-only email as before -- a missing screenshot (capture
    # failed, or an older lead from before this feature existed) must
    # never block or change the send itself.
    cached_screenshot = screenshot.get_cached_screenshot(lead["id"])
    body_html = _build_html_body(lead, preview_link, city) if cached_screenshot else None
    inline_image_path = str(cached_screenshot) if cached_screenshot else None

    # Marked BEFORE calling the transport, cleared only after a confirmed
    # send: if the process crashes in between, pending_send_id survives the
    # restart and send_next_pending() skips this lead rather than risk
    # sending the same cold email twice -- see db.mark_send_pending().
    send_uuid = str(uuid.uuid4())
    db.mark_send_pending(lead["id"], send_uuid)

    message_id = _send_via_configured_transport(
        to_addr=email_addr,
        subject=subject,
        body_text=body_with_link,
        lead_id=lead["id"],
        body_html=body_html,
        inline_image_path=inline_image_path,
        inline_image_cid=_SCREENSHOT_CID,
        idempotency_key=send_uuid,
    )
    db.insert_email_thread(
        lead_id=lead["id"],
        direction="outbound",
        subject=subject,
        body=body_with_link,
        from_addr=config.EMAIL_USER,
        to_addr=email_addr,
        message_id=message_id,
        prompt_version=PROMPT_VERSION,
    )
    db.update_lead_status(lead["id"], "emailed", notes="Cold email sent")
    db.clear_send_pending(lead["id"])

    db.set_next_send_allowed_at(
        datetime.now(timezone.utc)
        + timedelta(seconds=random.uniform(config.EMAIL_MIN_DELAY_SECONDS, config.EMAIL_MAX_DELAY_SECONDS))
    )
    return True


_DOMAIN_COOLDOWN_HOURS = 24


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if email and "@" in email else ""


def _domain_cooldown_active(domain: str) -> bool:
    """True if this domain was emailed within the last 24h -- prevents two
    different contacts at the same company both getting cold-emailed the
    same day (see db.get_last_domain_email_timestamp)."""
    if not domain:
        return False
    last_sent = db.get_last_domain_email_timestamp(domain)
    if last_sent is None:
        return False
    last_sent_dt = datetime.fromisoformat(last_sent).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_sent_dt < timedelta(hours=_DOMAIN_COOLDOWN_HOURS)


def send_next_pending() -> Optional[int]:
    """Send at most one queued cold email, respecting rate limits. Returns
    the lead_id sent to, or None if nothing was sent this cycle."""
    if not _can_send_now():
        return None
    for lead in db.list_leads_by_status("designed"):
        if is_under_takeover(lead["id"]):
            continue
        if lead.get("pending_send_id"):
            # A previous send attempt for this lead crashed between
            # mark_send_pending() and clear_send_pending() -- we can't tell
            # whether the email actually went out, so skip it rather than
            # risk a duplicate send. Needs manual review (see db.leads.
            # pending_send_id) before it'll be picked up again.
            print(f"[sales_agent] Skipping lead {lead['id']}: a prior send attempt "
                  f"({lead['pending_send_id']}) never confirmed complete -- needs manual review.")
            continue
        if _domain_cooldown_active(_domain_of(lead.get("contact_email") or "")):
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
        # No confirmation email here on purpose: someone who just said "stop
        # emailing me" should get silence, not one more message in their inbox.
        db.update_lead_status(lead["id"], "lost", notes="Replied negative")
        if lead.get("contact_email"):
            db.add_to_suppression_list(lead["contact_email"], reason="Replied negative")
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
