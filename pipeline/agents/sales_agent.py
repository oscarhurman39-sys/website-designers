"""SalesAgent: drafts and sends cold emails, polls the inbox, classifies
replies, and autonomously negotiates and closes deals. This module owns every
automated outbound send in the system -- nothing else calls
email_utils.send_email() for a cold email.

Autonomy model: a 'positive' reply routes straight into the LLM negotiation
agent -- there is no manual takeover step. The model only ever *proposes* a
move (CLOSE / COUNTER / REPLY) and a price; code enforces the hard rules:

  - Every price is clamped to [NEGOTIATION_FLOOR, NEGOTIATION_CEILING]
    before it reaches an email or a Stripe Checkout session (_clamp_price).
  - The model's body text is rejected if it contains any number, price, or
    link (_model_body_is_safe) -- every figure a prospect reads was computed
    by code, so a confused or prompt-injected model cannot misquote.
  - After MAX_NEGOTIATION_ROUNDS autonomous replies the agent stops replying
    and alerts a human instead, which also breaks autoresponder loops.

Console/Slack alerts remain, but as notifications only -- nothing waits for a
human to type anything.
"""
from __future__ import annotations

import html as html_module
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import requests
from huggingface_hub import InferenceClient

import config
from utils import assets, compliance, db, email_utils, mailboxes, screenshot, stripe_utils, tracer, tracker
# Shared with DesignAgent's post-deploy check so both sides agree on what an
# "auth wall" looks like; the timeout is separate because this check runs on
# the send path, right before an email goes out.
from agents.design_agent import _AUTH_PAGE_MARKERS, _AUTH_URL_PARTS  # noqa: E402

_PREVIEW_VALIDATION_TIMEOUT_SECONDS = 15

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # slack-sdk is a soft dependency; alerts still print to console.
    WebClient = None  # type: ignore[assignment,misc]
    SlackApiError = Exception  # type: ignore[assignment,misc]

HF_MODEL = "mistralai/Mistral-7B-Instruct-v0.2"

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
    # classify_reply lowercases the text before matching, so this acronym must
    # be lowercase here -- an uppercase "OOO" pattern could never match.
    r"\bon vacation\b", r"\bcurrently away\b", r"\booo\b",
)

# Outbound thread rows written by the negotiation agent carry this
# classification so _negotiation_rounds() can count them from the DB --
# the round cap must survive restarts, unlike in-memory state.
_NEGOTIATION_CLASSIFICATION = "negotiation"


# --- Console / Slack alerting -------------------------------------------------
# All alerts are notifications only: automation never waits on a human.

def _slack_notify(text: str) -> bool:
    """Post one alert to SLACK_ALERT_CHANNEL. Returns whether it actually
    reached Slack.

    Never raises. An alert is a notification, so a bad token, a missing
    channel or a Slack outage must not abort a send, a close or a handover
    -- every caller also prints a console banner. `test_alert.py` uses the
    return value to tell an operator whether alerts really work, which a
    token check alone cannot prove."""
    if not config.SLACK_BOT_TOKEN or WebClient is None:
        return False
    try:
        client = WebClient(token=config.SLACK_BOT_TOKEN)
        client.chat_postMessage(channel=config.SLACK_ALERT_CHANNEL, text=text)
        return True
    except Exception as exc:  # noqa: BLE001 - incl. network errors, not just SlackApiError
        print(f"[sales_agent] Slack alert failed: {exc}")
        return False


def alert_positive_reply(lead: dict) -> None:
    banner = "!" * 70
    print(f"\n{banner}\nLEAD REPLIED POSITIVELY: {lead['business_name']} <{lead['contact_email']}>"
          f"\nLead ID: {lead['id']}  |  Autonomous negotiation engaged (band "
          f"{config.CURRENCY_SYMBOL}{config.NEGOTIATION_FLOOR:,}-"
          f"{config.CURRENCY_SYMBOL}{config.NEGOTIATION_CEILING:,}).\n{banner}\n")
    _slack_notify(
        f":rotating_light: *LEAD REPLIED POSITIVELY*\n"
        f"*{lead['business_name']}* <{lead['contact_email']}> (lead id {lead['id']})\n"
        f"Autonomous negotiation engaged (band {config.CURRENCY_SYMBOL}{config.NEGOTIATION_FLOOR:,}"
        f"-{config.CURRENCY_SYMBOL}{config.NEGOTIATION_CEILING:,}). "
        "No action needed -- watch the thread in the dashboard."
    )


def alert_deal_closed(lead: dict, price: int, checkout_url: str) -> None:
    banner = "$" * 70
    print(f"\n{banner}\nDEAL CLOSED AUTONOMOUSLY: {lead['business_name']} (lead {lead['id']}) "
          f"at {price} -- checkout link sent.\n{banner}\n")
    _slack_notify(
        f":moneybag: *DEAL CLOSED AUTONOMOUSLY*\n"
        f"*{lead['business_name']}* (lead id {lead['id']}) agreed at *{price}*.\n"
        f"Checkout link sent: {checkout_url}"
    )


def alert_needs_human(lead: dict, reason: str) -> None:
    banner = "?" * 70
    print(f"\n{banner}\nNEEDS HUMAN ATTENTION: {lead['business_name']} (lead {lead['id']})\n"
          f"{reason}\n{banner}\n")
    _slack_notify(
        f":warning: *NEEDS HUMAN ATTENTION*\n"
        f"*{lead['business_name']}* (lead id {lead['id']}): {reason}"
    )


def record_payment(lead_id: int, session_id: Optional[str], amount_minor, source: str) -> bool:
    """Promote a lead to 'won' for a settled Checkout session. Idempotent:
    the status write is atomic against 'won', so the webhook and the
    reconciliation poll can both see the same payment safely. A *different*
    paid session for a lead already won is a double charge -- alert, never
    silently swallow it. Returns True only on the first promotion."""
    lead = db.get_lead(lead_id)
    if lead is None:
        return False
    if not db.update_lead_status_unless(lead_id, "won", notes=f"Stripe {source}", unless_current=("won",)):
        prior = lead.get("paid_session_id")
        if session_id and prior and prior != session_id:
            alert_needs_human(
                lead,
                f"A second paid Stripe session ({session_id}) arrived for a lead already paid via "
                f"{prior}. This is a double charge: refund one from the Stripe dashboard.",
            )
        return False
    fields: dict = {}
    if isinstance(amount_minor, int):
        fields["won_amount"] = amount_minor // 100
    if session_id:
        fields["paid_session_id"] = session_id
    if fields:
        db.update_lead_fields(lead_id, **fields)
    banner = "*" * 70
    print(
        f"\n{banner}\nPAYMENT RECEIVED ({source}): {lead['business_name']} (lead {lead_id})\n"
        f"Automated handover will run on the pipeline's next cycle (GitHub +\n"
        f"Vercel invites). 'transfer {lead_id}' remains available as a manual override.\n{banner}\n"
    )
    return True


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
        "- Say you put together a free, live DRAFT website for their business, no strings attached\n"
        "- Be honest that it is a draft built from a template, not a finished bespoke site\n"
        "- Offer to swap in their own photos and logo, free, to make it theirs\n"
        "- Never claim you built them a finished or custom website\n"
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
    subject = f"a draft website for {lead['business_name']}"
    body = (
        f"Hi there,\n\n"
        f"I put together a free, live draft website for {lead['business_name']} -- "
        "no strings attached, just wanted to show you what's possible. It's built "
        "from a template and the photos on it are stock ones, so send me your own "
        "photos and logo and I'll put them in free.\n\n"
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
    """Return (subject, body_text_without_footer_or_link).

    Refuses to draft at all (rather than only refusing to send later) if
    PHYSICAL_ADDRESS is missing/placeholder -- no point spending an HF call
    on an email that utils/compliance.py will refuse to send anyway."""
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
        print(f"[sales_agent] HF drafting failed, using fallback template: {exc}")
        return _fallback_email(lead)
    return _parse_subject_body(raw, lead)


# --- Sending (rate-limited) ----------------------------------------------------

_SCREENSHOT_CID = "preview"
_SENDER_NAME = config.SENDER_NAME


def _intro_line(business_name: str) -> str:
    """Plain-text greeting that always sits above the screenshot image --
    frames this as solving a discoverability problem, not just a sales pitch."""
    return (
        f"I noticed people searching for {business_name} only find your Google "
        "listing, so I put together a draft website to show what one could look "
        "like for you."
    )


def _draft_note() -> str:
    """The honesty line, and the offer that turns the draft into their site.
    Sits directly under the screenshot in HTML, so it lands where the stock
    photography is actually visible. Front-loaded deliberately -- it used to
    appear only at negotiation, after the first email had already implied a
    bespoke build."""
    return (
        "It's a draft, so the photos on it are stock ones for now. Send me your "
        "own photos and logo and I'll put them in free, so it's actually yours."
    )


_CHECKLIST_HEADER = "What the draft gives you"


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
        "If you'd like to own it, reply YES -- I'll connect your domain, swap in your own "
        "photos and logo, and make any changes you want.",
        _pricing_line(),
        _guarantee_line(),
        _SENDER_NAME,
    ]


def _pricing_line() -> str:
    sym = config.CURRENCY_SYMBOL
    line = (f"Standard package: {sym}{config.STANDARD_PACKAGE_PRICE:,}. "
            f"This completed draft: {sym}{config.WEBSITE_OFFER_PRICE:,} one-off")
    if config.SUBSCRIPTION_ENABLED:
        line += f", or {sym}{config.SUBSCRIPTION_MONTHLY_PRICE}/month with hosting, updates and your domain included"
    return line + "."


def _guarantee_line() -> str:
    return (f"You only pay once you're happy with it, there's a {config.GUARANTEE_DAYS}-day money-back guarantee, "
            f"and any edits you want in the first {config.FREE_EDITS_DAYS} days are free.")


_SUBSCRIPTION_RE = re.compile(r"\b(monthly|subscription|per month|a month|pay monthly|/month)\b", re.IGNORECASE)


def _wants_subscription(body: str) -> bool:
    return config.SUBSCRIPTION_ENABLED and bool(_SUBSCRIPTION_RE.search(body or ""))


def send_follow_up_if_due() -> Optional[int]:
    """Send at most one reminder per cycle to a lead who never replied,
    FOLLOW_UP_AFTER_DAYS after the cold email. Same thread, same mailbox,
    counts against the daily caps like any send. Returns the lead id."""
    if not config.FOLLOW_UP_ENABLED or not _can_send_now():
        return None
    due = db.list_follow_up_due(config.FOLLOW_UP_AFTER_DAYS)
    if not due:
        return None
    lead = due[0]
    website = db.get_website_by_lead(lead["id"])
    link = (website or {}).get("preview_url") or ""
    body = "\n\n".join([
        f"Quick one -- did you get a chance to look at the draft site I put together for {lead['business_name']}?",
        f"It's still live here: {link}" if link else "It's still live.",
        _guarantee_line(),
        "If it's not for you, no problem at all -- just say and I won't follow up again.",
        _SENDER_NAME,
    ])
    if not _send_thread_reply(lead, body, classification="follow_up"):
        db.update_lead_fields(lead["id"], follow_up_sent_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        return None  # unusable address: mark so we don't retry every cycle
    db.update_lead_fields(lead["id"], follow_up_sent_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    db.log_state_history(lead["id"], "emailed", "emailed", notes="Follow-up reminder sent")
    return lead["id"]


def _validate_preview_link_for_send(preview_link: str) -> str:
    """Return a public preview URL or raise before any cold email is sent."""
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
        _draft_note(),
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
        f'<img src="cid:{_SCREENSHOT_CID}" alt="Draft website preview" '
        'style="max-width:100%;border:1px solid #ddd;border-radius:8px;">'
        "</a></p>"
    )
    draft_note_html = f"<p>{html_module.escape(_draft_note())}</p>"
    checklist_html = _checklist_html(city)
    preview_button_html = (
        f'<a href="{escaped_link}" style="display:inline-block;padding:14px 28px;'
        'background:#2563eb;color:white;border-radius:8px;text-decoration:none;'
        'font-size:16px;font-weight:bold;margin:16px 0">View the draft &rarr;</a>'
    )
    closing_paragraphs = _closing_paragraphs(preview_link)
    closing_html = preview_button_html + "".join(
        f"<p>{html_module.escape(para).replace(chr(10), '<br>')}</p>"
        for para in closing_paragraphs[1:]
    )
    return intro_html + image_html + draft_note_html + checklist_html + closing_html


def _can_send_now() -> bool:
    now = datetime.now(timezone.utc)
    if now < _next_send_allowed_at:
        return False
    if db.emails_sent_last_hour() >= config.EMAIL_MAX_PER_HOUR:
        return False
    if db.emails_sent_today() >= config.EMAIL_MAX_PER_DAY:
        return False
    if not mailboxes.any_account_under_cap():
        return False  # every mailbox at its own daily cap (EMAIL_MAX_PER_DAY_PER_ACCOUNT)
    return True


def _cooldown_blocked_until(lead: dict) -> Optional[str]:
    """The timestamp of the last cold email to this lead's (niche, town) bucket,
    if that bucket is still inside OUTREACH_COOLDOWN_DAYS. None means clear to
    send. Two businesses in the same trade and the same town getting the same
    template days apart is what makes the outreach look like a mailshot.

    Deliberately NOT folded into _can_send_now(): that gate is shared with
    send_follow_up_if_due(), whose leads are by definition inside their own
    cooldown window, so gating there would silently kill every follow-up."""
    days = config.OUTREACH_COOLDOWN_DAYS
    if days <= 0:
        return None
    # .get("niche") rather than ["niche"]: callers outside the queue build lead
    # dicts by hand and do not always carry one.
    return db.last_cold_email_at(lead.get("niche") or "", lead.get("location") or "", days)


def _send_via_configured_transport(
    to_addr: str,
    subject: str,
    body_text: str,
    lead_id: int,
    body_html: Optional[str] = None,
    inline_image_path: Optional[str] = None,
    inline_image_cid: Optional[str] = None,
    account: Optional[config.EmailAccount] = None,
) -> str:
    """Send email via configured transport: SendGrid if SENDGRID_API_KEY is set,
    otherwise fall back to SMTP via send_email.

    `account` is the mailbox to send from (see _sender_for). It only applies
    to the SMTP path: SendGrid always sends from SENDGRID_FROM_EMAIL, so
    mailbox rotation is an SMTP-only feature.

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
            account=account,
        )


def _sender_for(lead: dict, cold: bool = False) -> Optional[config.EmailAccount]:
    """The mailbox this lead's thread lives in (utils/mailboxes.py).

    A cold email gets None when every mailbox is at its daily cap and must
    skip. A reply to an existing conversation falls back to the primary
    mailbox instead: the reply paths are only reachable for leads we've
    already emailed, and a goodbye, payment link or handover must never be
    dropped over a *cold*-send cap."""
    account = mailboxes.account_for_lead(lead)
    if account is None and not cold:
        account = config.EMAIL_ACCOUNTS[0]
    return account


def _pin_sender(lead: dict, account: config.EmailAccount) -> None:
    """Persist the mailbox after a successful send so every later email to
    this lead leaves from the same address. Written after (not before) the
    send so a failed first attempt leaves the lead free to use whichever
    mailbox has room next cycle."""
    if (lead.get("sender_account") or "") != account.user:
        db.update_lead_fields(lead["id"], sender_account=account.user)
        lead["sender_account"] = account.user


def cold_email_subject(lead: dict) -> str:
    """Subject line of the first email. Shared with preview_email.py so an
    operator review shows exactly what would be sent."""
    return f"a draft website for {lead['business_name']}"


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

    # One cold email per niche+town per OUTREACH_COOLDOWN_DAYS. Same contract as
    # the every-mailbox-at-cap path: return False, no status change, no sender
    # pinned -- the lead stays 'designed' and comes back round on a later cycle.
    if _cooldown_blocked_until(lead):
        return False

    subject = cold_email_subject(lead)
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
    # The raw URL is what gets validated above; the tracked one only replaces
    # it in the copy when the redirect endpoint is confirmed reachable.
    if config.CLICK_TRACKING_ENABLED and tracker.public_endpoint_up():
        preview_link = tracker.create_click_link(lead["id"])
    body_with_link = _plain_text_body(lead["business_name"], preview_link, city)

    # Embed the cached preview screenshot inline (cid:) if design_agent.py
    # already captured one for this lead; otherwise send exactly the same
    # plain-text-only email as before -- a missing screenshot (capture
    # failed, or an older lead from before this feature existed) must
    # never block or change the send itself.
    cached_screenshot = screenshot.get_cached_screenshot(lead["id"])
    body_html = _build_html_body(lead["business_name"], preview_link, city) if cached_screenshot else None
    inline_image_path = str(cached_screenshot) if cached_screenshot else None

    # Chosen last, right before the send, so the per-mailbox cap is checked
    # against the freshest counts. None = every mailbox is at cap: leave the
    # lead 'designed' for a later cycle, as when the global cap is hit.
    account = _sender_for(lead, cold=True)
    if account is None:
        return False

    message_id = _send_via_configured_transport(
        to_addr=email_addr,
        subject=subject,
        body_text=body_with_link,
        lead_id=lead["id"],
        body_html=body_html,
        inline_image_path=inline_image_path,
        inline_image_cid=_SCREENSHOT_CID,
        account=account,
    )
    db.insert_email_thread(
        lead_id=lead["id"],
        direction="outbound",
        subject=subject,
        body=body_with_link,
        from_addr=account.user,
        to_addr=email_addr,
        message_id=message_id,
    )
    _pin_sender(lead, account)
    # A dry run (ENABLE_LIVE_SEND=false) still gets a message_id back from the
    # transport, so the note is the only place the difference can be recorded.
    # It has to be: this exact string is what db.last_cold_email_at() counts as
    # a real send, so writing it for a dry run would let testing the pipeline
    # freeze a real town under the niche+town cooldown -- and it would put a
    # send in the audit trail that never happened.
    db.update_lead_status(
        lead["id"], "emailed",
        notes="Cold email sent" if config.ENABLE_LIVE_SEND else "Cold email drafted (dry run -- not sent)",
    )

    _next_send_allowed_at = datetime.now(timezone.utc) + timedelta(
        seconds=random.uniform(config.EMAIL_MIN_DELAY_SECONDS, config.EMAIL_MAX_DELAY_SECONDS)
    )
    return True


def send_next_pending() -> Optional[int]:
    """Send at most one queued cold email, respecting rate limits. Returns
    the lead_id sent to, or None if nothing was sent this cycle."""
    if not _can_send_now():
        return None
    for lead in db.list_sendable_leads_by_priority():
        if _cooldown_blocked_until(lead):
            continue  # another business in this niche+town was emailed too recently
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
    # silently dropped -- worst case the negotiation agent answers a lukewarm
    # reply politely (and the round cap keeps that bounded).
    return "positive"


# --- Autonomous negotiation ----------------------------------------------------
#
# The LLM decides the *move*; the code decides the *money*. Model output is a
# (decision, price, body) triple, where the price is clamped and the body is
# discarded unless it is free of numbers and links. All prices a prospect
# reads, and the amount of every Stripe session, come from _clamp_price().

def _price_band() -> tuple[int, int]:
    return config.NEGOTIATION_FLOOR, config.NEGOTIATION_CEILING


def _clamp_price(price: Optional[int], fallback: int) -> int:
    """Hard code-side enforcement of the negotiation band. The LLM only ever
    proposes a number; whatever reaches an email or Stripe goes through here."""
    floor, ceiling = _price_band()
    if price is None or price <= 0:
        price = fallback
    return max(floor, min(ceiling, price))


def _standing_price(lead: dict) -> int:
    """The lead's current quoted price: the last code-clamped quote we made
    (persisted on the lead row), else the price we already advertised.

    Before the first negotiation round nothing is persisted, so the opening
    standing quote is the price the cold email actually quoted
    (config.WEBSITE_OFFER_PRICE) -- clamped into the band -- NOT the band
    ceiling. Starting from the ceiling would let a discounted offer
    (WEBSITE_OFFER_PRICE < the band ceiling) silently re-quote the prospect
    higher than the number they were emailed."""
    floor, ceiling = _price_band()
    quoted = lead.get("quoted_price_usd")
    if isinstance(quoted, int) and quoted > 0:
        return max(floor, min(ceiling, quoted))
    return max(floor, min(ceiling, config.WEBSITE_OFFER_PRICE))


def _negotiation_rounds(lead_id: int) -> int:
    """How many autonomous negotiation emails we've already sent this lead.
    Counted from the DB (classification='negotiation') so the round cap
    survives restarts."""
    return sum(
        1 for t in db.get_email_threads(lead_id)
        if t["direction"] == "outbound" and t["classification"] == _NEGOTIATION_CLASSIFICATION
    )


def _render_thread(lead_id: int, limit: int = 8, max_chars: int = 400) -> str:
    """Compact plain-text transcript of the recent thread for the prompt."""
    lines = []
    for t in db.get_email_threads(lead_id)[-limit:]:
        who = "ME" if t["direction"] == "outbound" else "PROSPECT"
        body = (t["body"] or "").strip()
        if len(body) > max_chars:
            body = body[:max_chars] + " [...]"
        lines.append(f"[{who}] {body}")
    return "\n\n".join(lines)


def _build_negotiation_prompt(
    lead: dict, inbound_body: str, standing: int, link_outstanding: bool, rounds_left: int
) -> str:
    floor, ceiling = _price_band()
    link_note = (
        "A payment link has already been sent to them."
        if link_outstanding else
        "No payment link has been sent yet."
    )
    return (
        "<s>[INST] You are Casey, a freelance web designer closing a deal by email. "
        "The prospect already received a finished preview website for their business "
        "and replied. Decide the next negotiation move.\n\n"
        "Facts:\n"
        f"- Business: {lead['business_name']}\n"
        f"- Current quoted price: {standing}\n"
        f"- You may agree to any whole number between {floor} and {ceiling}. "
        f"Never go below {floor}. Never reveal that a minimum exists.\n"
        f"- {link_note}\n"
        f"- Their own photos and logo on the site: included at no extra cost. Files on the preview so far: {assets.summary(lead['id'])}. "
        "If they ask about photos/logo or mention sending some, tell them to attach them to a reply "
        "(logo as PNG or SVG, three to six landscape photos) and they will be on the preview within the hour. "
        "If files are already on the preview, say so and invite them to look.\n"
        + (f"- A monthly option also exists: {config.CURRENCY_SYMBOL}{config.SUBSCRIPTION_MONTHLY_PRICE}/month with hosting, "
           "updates and their domain included. If they choose it, REPLY warmly that you'll set it up and "
           "send the details shortly (do not CLOSE at the one-off price).\n" if config.SUBSCRIPTION_ENABLED else "")
        + f"- Guarantee they already have: {config.GUARANTEE_DAYS}-day money-back and free edits for "
          f"{config.FREE_EDITS_DAYS} days. Mention it when they hesitate.\n"
        f"- Automated replies remaining before a human must step in: {rounds_left}\n\n"
        "Conversation so far:\n"
        f"{_render_thread(lead['id'])}\n\n"
        "Their new reply:\n"
        f'"""{inbound_body.strip()[:1500]}"""\n\n'
        "Rules for your move:\n"
        "- CLOSE only if they clearly agree to buy. PRICE = the agreed number "
        "(their named number if it is within your allowed range, otherwise the current quote).\n"
        f"- COUNTER to move the price, e.g. meet a lower ask part-way -- but never below {floor}. "
        "PRICE = your new number.\n"
        "- REPLY to answer questions or objections without changing the price. PRICE: NONE.\n"
        "- BODY: under 110 words, plain and friendly, no hype. Do NOT write any prices, "
        "numbers, links, or a signature in BODY -- those are appended separately.\n\n"
        "Respond in EXACTLY this format, nothing else:\n"
        "DECISION: <CLOSE|COUNTER|REPLY>\n"
        "PRICE: <whole number or NONE>\n"
        "BODY:\n"
        "<your message text> [/INST]"
    )


# PRICE must contain at least one digit -- `(\d[\d,]*)` rather than `([\d,]+)`
# so a degenerate 'PRICE: ,' can't match and hand int() an empty string.
_DECISION_RE = re.compile(
    r"DECISION:\s*(CLOSE|COUNTER|REPLY)\s*?\n\s*PRICE:\s*(?:[£$]?\s*(\d[\d,]*)|NONE)\s*?\n\s*BODY:\s*\n?(.*)",
    re.IGNORECASE | re.DOTALL,
)

# Number words that could carry a price without any digit ("five hundred").
_NUMBER_WORDS = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty "
    "thirty forty fifty sixty seventy eighty ninety hundred thousand million "
    "dozen half quarter free gratis"
).split()
_NUMBER_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)
# Bare-domain link with no scheme/www ("paypal.me/x", "bit.ly/x", "evil.com").
_BARE_DOMAIN_RE = re.compile(r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.[a-z]{2,}\b", re.IGNORECASE)


def _model_body_is_safe(body: str) -> bool:
    """Whitelist-tight gate on LLM-authored prose. The model body is only ever
    a friendly opening lede -- every price, number, and link a prospect reads
    is appended by code -- so a body has no legitimate need for any of them.
    We therefore reject a body containing ANY digit, currency symbol, number
    word, email/URL, or bare domain. This is deliberately over-strict: a
    rejected body just falls back to a deterministic template (the caller
    nulls it), whereas a hallucinated or prompt-injected price/phishing link
    slipping through would reach the prospect. Guards the money path against
    injection via the prospect's own reply text (which is fed to the model)."""
    if not body or len(body) > 1200:
        return False
    if re.search(r"\d", body):  # any digit at all
        return False
    if re.search(r"[£$€@]", body):  # currency symbols or email '@'
        return False
    if re.search(r"https?://|www\.", body, re.IGNORECASE):
        return False
    if _BARE_DOMAIN_RE.search(body):  # scheme-less links: paypal.me/x, bit.ly/x
        return False
    if _NUMBER_WORD_RE.search(body):  # spelled-out amounts: "five hundred", "free"
        return False
    return True


def _negotiation_decision(
    lead: dict, inbound_body: str, standing: int, link_outstanding: bool, rounds_left: int
) -> tuple[str, Optional[int], Optional[str]]:
    """Ask the LLM for (decision, proposed_price, body). Any HF/parse failure
    degrades to a deterministic ('COUNTER', standing, None) -- restate the
    standing offer rather than going silent or improvising."""
    fallback = ("REPLY", None, None) if link_outstanding else ("COUNTER", standing, None)
    try:
        raw = _hf_client().text_generation(
            _build_negotiation_prompt(lead, inbound_body, standing, link_outstanding, rounds_left),
            max_new_tokens=350,
            temperature=0.3,
            do_sample=True,
        )
    except Exception as exc:  # noqa: BLE001 - negotiation must degrade, never crash the loop
        print(f"[sales_agent] HF negotiation call failed, using deterministic fallback: {exc}")
        return fallback
    match = _DECISION_RE.search(raw)
    if not match:
        print(f"[sales_agent] Unparseable negotiation output, using deterministic fallback: {raw[:200]!r}")
        return fallback
    decision = match.group(1).upper()
    price = None
    if match.group(2):
        try:
            price = int(match.group(2).replace(",", ""))
        except ValueError:
            # Defensive: the regex requires a leading digit, so this should be
            # unreachable, but a malformed capture must degrade, never crash
            # the inbox loop -- fall back to the standing quote via _clamp_price.
            price = None
    body = match.group(3).strip()
    if not _model_body_is_safe(body):
        body = None  # keep the decision, replace the prose with a safe template
    return decision, price, body


def _reply_subject(lead: dict) -> str:
    return f"Re: a draft website for {lead['business_name']}"


def _send_thread_reply(lead: dict, body: str, classification: str = _NEGOTIATION_CLASSIFICATION) -> bool:
    """Send one mid-thread reply and log it. Returns False (never raises) if
    the address is unusable/unsubscribed -- a dead thread must not kill the
    inbox-processing loop."""
    email_addr = lead.get("contact_email") or ""
    if not email_addr or db.is_unsubscribed(email_addr):
        return False
    subject = _reply_subject(lead)
    account = _sender_for(lead)
    try:
        message_id = _send_via_configured_transport(
            to_addr=email_addr, subject=subject, body_text=body, lead_id=lead["id"],
            account=account,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[sales_agent] Failed to send negotiation reply for lead {lead['id']}: {exc}")
        return False
    db.insert_email_thread(
        lead_id=lead["id"], direction="outbound", subject=subject, body=body,
        from_addr=account.user, to_addr=email_addr, message_id=message_id,
        classification=classification,
    )
    _pin_sender(lead, account)
    return True


# Placeholder returned instead of a real Stripe session during a dry run.
_DRY_RUN_CHECKOUT_URL = f"{config.PUBLIC_BASE_URL}/payment-success?dry_run=1"


def _create_checkout_link(lead: dict, price: int) -> Optional[str]:
    """Create (and persist) a Stripe Checkout session at a code-clamped price.
    Returns None (and alerts) on failure -- an unsendable link must degrade to
    a polite holding reply, not a crash.

    Honors the ENABLE_LIVE_SEND dry-run valve: creating a Checkout session is a
    live, payable external side effect, so a dry run must NOT create one. In dry
    run we log and return a harmless placeholder URL so the flow still exercises
    end to end without minting a real payment link."""
    floor, ceiling = _price_band()
    price = max(floor, min(ceiling, price))  # belt-and-braces: clamp again at the money boundary
    if not config.ENABLE_LIVE_SEND:
        print(f"[sales_agent] DRY RUN -- not creating a live Stripe session for lead {lead['id']} at {price}.")
        return _DRY_RUN_CHECKOUT_URL
    try:
        url = stripe_utils.create_checkout_session(
            lead_id=lead["id"],
            business_name=lead["business_name"],
            customer_email=lead["contact_email"],
            amount=price,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[sales_agent] Stripe checkout creation failed for lead {lead['id']}: {exc}")
        alert_needs_human(lead, f"Stripe checkout creation failed at price {price}: {exc}")
        return None
    # Persist so a later question-reply re-sends THIS link, never a second one.
    db.update_lead_fields(lead["id"], checkout_url=url)
    return url


def _compose_close_body(lead: dict, price: int, model_body: Optional[str], checkout_url: str) -> str:
    lede = model_body or (
        f"Brilliant -- let's get {lead['business_name']} live. "
        "Really glad you want to go ahead with it."
    )
    return "\n\n".join([
        lede,
        f"I've locked in the completed site for you at {config.CURRENCY_SYMBOL}{price:,}.",
        f"Secure checkout link: {checkout_url}",
        "Once payment goes through, I'll start the handover right away -- I'll email you "
        "for your GitHub username so the site's code lands in your hands, connect your "
        "domain, and make any changes you want.",
        _SENDER_NAME,
    ])


def _compose_counter_body(lead: dict, price: int, model_body: Optional[str]) -> str:
    lede = model_body or (
        "Thanks for getting back to me -- happy to work with you on this."
    )
    return "\n\n".join([
        lede,
        f"Here's what I can do: the completed site, everything included, "
        f"for {config.CURRENCY_SYMBOL}{price:,}.",
        "If that works, just reply YES and I'll send over a secure payment link.",
        _SENDER_NAME,
    ])


def _compose_reply_body(lead: dict, model_body: Optional[str], checkout_url: Optional[str]) -> str:
    lede = model_body or (
        "Thanks for the note -- happy to answer anything else, and no pressure on timing."
    )
    parts = [lede]
    if checkout_url:
        parts.append(f"In case the earlier link got buried, here it is again: {checkout_url}")
    parts.append(_SENDER_NAME)
    return "\n\n".join(parts)


def _run_negotiation_round(lead: dict, inbound_body: str, github_captured: bool = False) -> None:
    """One autonomous negotiation turn: LLM proposes a move, code clamps the
    price, composes the outbound email, fires Stripe on a close, and
    advances the lead's status. Exactly one outbound email per inbound
    message, and none at all once the round cap is hit.

    `inbound_body` is the prospect's latest message text -- passed as a plain
    string (not an InboundEmail) so a startup catch-up can replay the last
    logged inbound reply through the identical path (see
    catch_up_pending_negotiations). `github_captured` says whether this same
    message just supplied a GitHub username, so a race into 'won' can route to
    the handover handler without re-alerting on the routine handoff."""
    lead = db.get_lead(lead["id"]) or lead  # refresh status/quoted price
    if lead["status"] == "won":
        # Stripe's webhook (a separate process) confirmed payment between the
        # inbox poll and now -- this is a paid customer, not a negotiation.
        _handle_won_reply(lead, github_captured)
        return
    rounds = _negotiation_rounds(lead["id"])
    if rounds >= config.MAX_NEGOTIATION_ROUNDS:
        alert_needs_human(
            lead,
            f"Negotiation round cap ({config.MAX_NEGOTIATION_ROUNDS}) reached -- not auto-replying. "
            "Latest inbound message is logged in the thread; reply manually from your mail client.",
        )
        return

    standing = _standing_price(lead)
    rounds_left = config.MAX_NEGOTIATION_ROUNDS - rounds
    decision, proposed, model_body = _negotiation_decision(
        lead, inbound_body, standing, lead["status"] == "payment_sent", rounds_left
    )
    # The LLM call above can take several seconds; re-read the lead so no branch
    # acts on a status the webhook process changed meanwhile. If payment landed
    # during the call, this is now a paid customer -- hand off, don't negotiate.
    lead = db.get_lead(lead["id"]) or lead
    if lead["status"] == "won":
        _handle_won_reply(lead, github_captured)
        return
    # A link is outstanding if the status says so OR a checkout URL was ever
    # minted: keying off status alone let a COUNTER demote 'payment_sent' to
    # 'negotiating', after which the next CLOSE minted a second live link.
    link_outstanding = lead["status"] == "payment_sent" or bool(lead.get("checkout_url"))
    if link_outstanding and decision == "COUNTER":
        # Re-pricing a deal that already has a payable link would leave two
        # live prices. Answer the message and re-send the existing link.
        decision = "REPLY"
    # Band clamp, then cap at the standing quote: a price already offered to
    # this lead can never rise, so a hallucinated "CLOSE 5000" after we
    # quoted 600 closes at 600, not at the band ceiling.
    price = min(_clamp_price(proposed, standing), standing)

    # All status writes below are guarded against 'won': the webhook process
    # can confirm payment at any instant (even mid-LLM-call), and a slower
    # negotiation write must never demote a paid lead -- that would strand
    # the automated handover, which only picks up 'won' leads.
    if decision == "CLOSE":
        # If a link is already outstanding (a prior CLOSE this negotiation),
        # re-send THAT one at its original price rather than minting a second
        # payable session -- and hold the quoted price steady so a repeat close
        # can't silently re-price the same deal.
        if link_outstanding and lead.get("checkout_url"):
            checkout_url = lead["checkout_url"]
            price = _standing_price(lead)
        else:
            checkout_url = _create_checkout_link(lead, price)
        if checkout_url is None:
            # Deal is agreed but the link couldn't be created: hold politely,
            # stay in 'negotiating' so the next inbound (or a human) retries.
            _send_thread_reply(lead, "\n\n".join([
                "Great -- let's do it. I'll send over the secure payment link shortly.",
                _SENDER_NAME,
            ]))
            db.update_lead_status_unless(
                lead["id"], "negotiating",
                notes=f"Close agreed at {price} but Stripe link creation failed",
                unless_current=("won",),
            )
            return
        body = _compose_close_body(lead, price, model_body, checkout_url)
        if _send_thread_reply(lead, body):
            db.update_lead_fields(lead["id"], quoted_price_usd=price)
            if db.update_lead_status_unless(
                lead["id"], "payment_sent",
                notes=f"Deal closed autonomously at {price}; checkout link sent",
                unless_current=("won",),
            ):
                alert_deal_closed(lead, price, checkout_url)
            else:
                # Payment landed in the sub-second between the re-read above and
                # this write. The just-sent link is live and payable, so warn a
                # human to make sure the customer isn't charged twice.
                alert_needs_human(
                    lead,
                    "Payment confirmed just as a close email went out -- the customer now has a "
                    "second live checkout link. Confirm they aren't double-charged and, if "
                    "needed, expire the extra Stripe session.",
                )
    elif decision == "COUNTER":
        body = _compose_counter_body(lead, price, model_body)
        if _send_thread_reply(lead, body):
            db.update_lead_fields(lead["id"], quoted_price_usd=price)
            if not db.update_lead_status_unless(
                lead["id"], "negotiating", notes=f"Auto-counter at {price}",
                unless_current=("negotiating", "won", "payment_sent"),
            ):
                db.log_state_history(lead["id"], lead["status"], lead["status"],
                                     notes=f"Auto-counter at {price}")
    else:  # REPLY
        # Re-send the SAME checkout link we already created (persisted on the
        # lead), never a fresh Stripe session -- minting a new one per question
        # would leave several live, payable links and risk a double charge.
        checkout_url = (lead.get("checkout_url") or None) if link_outstanding else None
        body = _compose_reply_body(lead, model_body, checkout_url)
        if _send_thread_reply(lead, body):
            db.log_state_history(lead["id"], lead["status"], lead["status"],
                                 notes="Auto-reply (no price change)")


# --- Post-payment handover emails ----------------------------------------------

_GITHUB_ASK_SUBJECT_PREFIX = "Handing over your new website"

_GITHUB_USERNAME_PATTERNS = (
    re.compile(r"github\.com/([A-Za-z\d](?:[A-Za-z\d-]{0,37}[A-Za-z\d])?)", re.IGNORECASE),
    re.compile(
        r"github(?:\s+user(?:name)?)?\s*(?:is|[:\-])\s*@?([A-Za-z\d](?:[A-Za-z\d-]{0,37}[A-Za-z\d])?)",
        re.IGNORECASE,
    ),
)


# Placeholder/example slugs that must never be accepted as a real username --
# chiefly the example our own ask email uses, which a quoted reply echoes
# back. Inviting one of these as an *admin* collaborator would hand repo
# control to an unrelated third party, so we reject them and escalate.
_GITHUB_USERNAME_DENYLIST = frozenset({
    "yourname", "your-name", "username", "user", "example", "name", "login",
    "handle", "account", "settings", "notifications", "orgs", "sponsors",
})


def extract_github_username(text: str) -> Optional[str]:
    """Conservatively pull a GitHub username out of free text: either a
    github.com/<user> link or an explicit 'github username: x' phrasing.
    Placeholder/example slugs (including the one our own ask email shows,
    which a quoted reply echoes back) are rejected. Anything fuzzier gets
    escalated to a human instead of guessed at."""
    for pattern in _GITHUB_USERNAME_PATTERNS:
        match = pattern.search(text or "")
        if match:
            candidate = match.group(1)
            if candidate.lower() in _GITHUB_USERNAME_DENYLIST:
                continue
            return candidate
    return None


def _maybe_capture_github_username(lead: dict, body: str) -> bool:
    """Store a GitHub username parsed from this reply, if we don't already have
    one. Returns True only when this call captured a new username -- callers use
    that to tell the expected handover handoff apart from other post-payment
    replies that need a human."""
    if lead.get("github_username"):
        return False
    username = extract_github_username(body)
    if username:
        db.update_lead_fields(lead["id"], github_username=username)
        print(f"[sales_agent] Captured GitHub username {username!r} for lead {lead['id']}")
        return True
    return False


def send_github_username_request(lead: dict) -> bool:
    """Ask a paid ('won') lead for their GitHub username so the repo invite
    can be sent. Idempotent: checks the thread log and sends at most once."""
    for t in db.get_email_threads(lead["id"]):
        if t["direction"] == "outbound" and (t["subject"] or "").startswith(_GITHUB_ASK_SUBJECT_PREFIX):
            return False
    email_addr = lead.get("contact_email") or ""
    if not email_addr:
        return False
    subject = f"{_GITHUB_ASK_SUBJECT_PREFIX} -- one quick thing"
    # NOTE: deliberately no "github.com/<example>" URL and no example slug in
    # this copy. The inbound parser scans replies for a github.com/<user> link
    # or "github username: x"; a mail client quoting this email back would
    # otherwise feed our own example straight into that parser. Keep it plain.
    body = (
        f"Payment received -- thank you! {lead['business_name']}'s new site is yours.\n\n"
        "To hand over the site's code I just need your GitHub username -- the name you "
        "sign in with (you can create a free account at github.com if you don't have "
        "one yet). Reply with just that username and I'll send the invite right away.\n\n"
        "I'll also invite this email address to the hosting project so the live site "
        "is under your control.\n\n"
        f"{_SENDER_NAME}"
    )
    account = _sender_for(lead)
    try:
        message_id = _send_via_configured_transport(
            to_addr=email_addr, subject=subject, body_text=body, lead_id=lead["id"],
            account=account,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[sales_agent] Failed to send GitHub-username request for lead {lead['id']}: {exc}")
        return False
    db.insert_email_thread(
        lead_id=lead["id"], direction="outbound", subject=subject, body=body,
        from_addr=account.user, to_addr=email_addr, message_id=message_id,
    )
    _pin_sender(lead, account)
    return True


_HANDOVER_CONFIRM_SUBJECT_PREFIX = "Your website handover is underway"


def send_handover_confirmation(lead: dict, repo_full_name: str) -> bool:
    """Confirmation sent right after the automated GitHub/Vercel invites fire.
    Idempotent: sends at most once, so a restart between marking the site
    transferred and this send can't email the customer a duplicate."""
    for t in db.get_email_threads(lead["id"]):
        if t["direction"] == "outbound" and (t["subject"] or "").startswith(_HANDOVER_CONFIRM_SUBJECT_PREFIX):
            return False
    email_addr = lead.get("contact_email") or ""
    if not email_addr:
        return False
    subject = f"{_HANDOVER_CONFIRM_SUBJECT_PREFIX} -- {lead['business_name']}"
    body = (
        f"All done on my side. You've been invited to the site's code repository "
        f"({repo_full_name}) on GitHub -- accept the invite from your GitHub "
        "notifications and it's yours. An invite to the hosting project was sent to "
        "this email address too.\n\n"
        "Next up: connecting your domain and any changes you'd like -- just reply "
        "here with what you need.\n\n"
        f"{_SENDER_NAME}"
    )
    account = _sender_for(lead)
    try:
        message_id = _send_via_configured_transport(
            to_addr=email_addr, subject=subject, body_text=body, lead_id=lead["id"],
            account=account,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[sales_agent] Failed to send handover confirmation for lead {lead['id']}: {exc}")
        return False
    db.insert_email_thread(
        lead_id=lead["id"], direction="outbound", subject=subject, body=body,
        from_addr=account.user, to_addr=email_addr, message_id=message_id,
    )
    _pin_sender(lead, account)
    return True


# --- Inbound handling -----------------------------------------------------------

_GOODBYE_SUBJECT = "No problem"


def _send_goodbye(lead: dict) -> None:
    """Automatic, polite acknowledgment sent when a lead declines -- required
    so 'stop emailing me' always gets a confirmation, not silence. Sent at
    most once per lead: the negative-branch status guard already prevents
    re-entry for an already-'lost' lead, and this thread-log check is a
    belt-and-braces stop against ever trading goodbyes with an autoresponder."""
    email_addr = lead.get("contact_email") or ""
    if not email_addr or db.is_unsubscribed(email_addr):
        return
    for t in db.get_email_threads(lead["id"]):
        if t["direction"] == "outbound" and t["subject"] == _GOODBYE_SUBJECT:
            return  # already said goodbye once; never again
    subject = _GOODBYE_SUBJECT
    body = (
        f"Hi, totally understood -- I won't reach out again about this. "
        f"Wishing {lead['business_name']} all the best."
    )
    account = _sender_for(lead)
    try:
        message_id = _send_via_configured_transport(
            to_addr=email_addr,
            subject=subject,
            body_text=body,
            lead_id=lead["id"],
            account=account,
        )
        db.insert_email_thread(
            lead_id=lead["id"], direction="outbound", subject=subject, body=body,
            from_addr=account.user, to_addr=email_addr, message_id=message_id,
        )
        _pin_sender(lead, account)
    except RuntimeError:
        pass  # already unsubscribed between the check above and now; nothing to do


def _handle_won_reply(lead: dict, github_captured: bool = False) -> None:
    """A paid customer replied. The one message automation handles by itself is
    the reply that supplies the GitHub username we asked for (`github_captured`
    True) -- main.py's handover pass acts on it next cycle, so no human action
    is needed. EVERY other post-payment reply is support, which is a human's
    job, so it is escalated -- never silently dropped."""
    lead = db.get_lead(lead["id"]) or lead
    website = db.get_website_by_lead(lead["id"])
    already_transferred = bool(website and website.get("transferred"))

    if github_captured and not already_transferred:
        return  # routine handoff; the handover pass takes it from here

    if already_transferred:
        reason = ("Paid, handed-over customer replied -- post-handover support is a human's job; "
                  "read the thread and reply personally.")
    elif lead.get("github_username"):
        reason = ("Paid customer replied again while their handover is in progress (GitHub username "
                  "already on file) -- read the thread and reply if it needs an answer.")
    else:
        reason = ("Paid customer replied but gave no usable GitHub username -- read the thread and "
                  "reply manually (or set their github_username so the automated handover can run).")
    alert_needs_human(lead, reason)


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
        # Guarded so a stray DSN can't demote a paid lead out of the handover queue.
        db.update_lead_status_unless(lead["id"], "bounced", notes="Bounce/DSN detected",
                                     unless_current=("won", "payment_sent"))
        if lead.get("contact_email"):
            db.suppress_email(lead["contact_email"])  # never re-source a dead address
        return

    classification = classify_reply(msg.subject, msg.body)
    db.insert_email_thread(
        lead_id=lead["id"], direction="inbound", subject=msg.subject, body=msg.body,
        from_addr=msg.from_addr, to_addr=msg.to_addr, message_id=msg.message_id,
        classification=classification,
    )
    github_captured = _maybe_capture_github_username(lead, msg.body)
    _maybe_apply_client_assets(lead, msg)

    if classification == "negative":
        # A first-time decline (active lead) gets exactly one goodbye and is
        # suppressed; a repeat negative from an already-terminal lead does
        # nothing (this is what stops two autoresponders trading goodbyes
        # forever, outside the negotiation round cap). A paid lead is never
        # demoted -- 'stop' from a customer is support, not a lost sale.
        if db.update_lead_status_unless(
            lead["id"], "lost", notes="Replied negative",
            unless_current=("won", "lost", "unsubscribed", "bounced"),
        ):
            _send_goodbye(lead)  # sent before suppression so the ack itself goes out
            db.suppress_email(lead.get("contact_email") or "")  # CAN-SPAM: honor the opt-out
        else:
            current = (db.get_lead(lead["id"]) or lead)["status"]
            if current == "won":
                db.log_state_history(lead["id"], "won", "won", notes="Negative reply post-payment")
                alert_needs_human(lead, "Paid customer sent a negative reply -- handle personally.")
            # else already lost/unsubscribed/bounced: no repeat goodbye, no churn.
    elif classification == "out_of_office":
        db.log_state_history(lead["id"], lead["status"], lead["status"], notes="Out-of-office auto-reply")
    elif classification == "positive":
        if lead["status"] == "won":
            _handle_won_reply(lead, github_captured)
            return
        # Never resurrect a terminal lead (declined / bounced / opted-out) into
        # an automated sales conversation off an ambiguous or automated reply --
        # escalate so a human decides, rather than emailing a fresh price to
        # someone who already said no (or can't be emailed at all).
        if lead["status"] in ("lost", "bounced", "unsubscribed"):
            alert_needs_human(
                lead,
                f"A '{lead['status']}' lead sent a positive-looking reply -- not auto-negotiating; "
                "review the thread and re-engage manually if appropriate.",
            )
            return
        if _wants_subscription(msg.body):
            # Monthly plan isn't automated (no Stripe subscription flow yet):
            # acknowledge, hand to a human, and don't let the negotiator
            # close them at the one-off price.
            db.update_lead_status_unless(
                lead["id"], "negotiating", notes="Asked about the monthly option; handed to a human",
                unless_current=("negotiating", "payment_sent", "won", "lost", "bounced", "unsubscribed"),
            )
            _send_thread_reply(lead, "\n\n".join([
                "Great choice -- the monthly plan covers hosting, updates and your domain, so there's nothing else to think about.",
                "I'll set it up and send you the details shortly.",
                _SENDER_NAME,
            ]), classification="subscription_interest")
            alert_needs_human(lead, f"Prospect wants the monthly plan ({config.CURRENCY_SYMBOL}{config.SUBSCRIPTION_MONTHLY_PRICE}/mo). "
                                    "Set up the subscription and reply personally.")
            return
        if db.update_lead_status_unless(
            lead["id"], "negotiating",
            notes="Replied positive; autonomous negotiation engaged",
            unless_current=("negotiating", "payment_sent", "won", "lost", "bounced", "unsubscribed"),
        ):
            alert_positive_reply(lead)
        _run_negotiation_round(lead, msg.body, github_captured)


def _maybe_apply_client_assets(lead: dict, msg) -> None:
    """If the reply carried usable photos/logo, save them, rebuild the
    preview in place and confirm by email. Runs before classification
    acts so the confirmation lands even on a lead that's about to be
    handed to the negotiator. Any failure alerts a human; it never
    blocks handling the rest of the reply."""
    attachments = getattr(msg, "attachments", None) or []
    if not attachments:
        return
    saved = assets.save_attachments(lead["id"], attachments)
    if not saved:
        return
    received = assets.summary(lead["id"])
    db.log_state_history(lead["id"], lead["status"], lead["status"],
                         notes=f"Received client assets by email ({len(saved)} file(s)); now on file: {received}")
    try:
        from agents import design_agent  # local import: heavy module, avoids a cycle
        website = design_agent.rebuild_preview(lead)
    except Exception as exc:  # noqa: BLE001
        alert_needs_human(lead, f"Client sent photos/logo but the preview rebuild failed ({exc}). "
                                "Fix and run: python run.py rebuild " + str(lead["id"]))
        return
    if website is None:
        alert_needs_human(lead, "Client sent photos/logo but there is no preview site to rebuild yet.")
        return
    _send_thread_reply(
        lead,
        "Thanks for the files -- I've put them on the preview, take a look:\n\n"
        f"{website['preview_url']}\n\n"
        "If you'd like different ones or a different order, just send them over.",
        classification="assets_received",
    )


_EMAIL_IN_TEXT = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def _lead_from_bounce(msg) -> Optional[dict]:
    """The lead whose address a DSN reports as undeliverable, if any."""
    seen: set[str] = set()
    for addr in _EMAIL_IN_TEXT.findall(f"{msg.subject}\n{msg.body}"):
        addr = addr.lower()
        if addr in seen or addr == (msg.from_addr or "").lower():
            continue
        seen.add(addr)
        lead = db.get_lead_by_email(addr)
        if lead is not None:
            return lead
    return None


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
        if lead is None and compliance.is_bounce_message(msg.subject, msg.from_addr, msg.content_type):
            # A delivery failure is sent by the receiving server, never by the
            # prospect, so it can only be matched by the failed address in its
            # body. Without this, no bounce was ever recorded or suppressed.
            lead = _lead_from_bounce(msg)
        if lead is None:
            continue  # reply from an address we have no lead for; nothing to act on
        if not lead.get("sender_account") and getattr(msg, "account_user", ""):
            # The prospect replied to the address we emailed them from, so the
            # mailbox this landed in is where their thread lives. Pins leads
            # emailed before sender_account existed, so our reply goes back
            # out from the same address they wrote to. Never overrides an
            # existing pin: a reply landing elsewhere doesn't move the thread.
            arrived_in = mailboxes.account_by_user(msg.account_user)
            if arrived_in is not None:
                _pin_sender(lead, arrived_in)
        try:
            _handle_inbound(lead, msg)
        except Exception as exc:  # noqa: BLE001
            # One malformed message (or a downstream hiccup handling it) must
            # never abort the rest of the batch. The inbound row was already
            # logged inside _handle_inbound_impl, so message_id_seen would skip
            # this message next poll -- alert a human so it isn't silently lost.
            print(f"[sales_agent] Failed to handle reply from {msg.from_addr} for lead {lead['id']}: {exc}")
            alert_needs_human(
                lead,
                f"Error handling an inbound reply ({exc}); the message is logged in the "
                "thread but was not auto-answered. Review and reply manually.",
            )
            continue
        processed += 1
    return processed


def catch_up_pending_negotiations() -> int:
    """One-time migration bridge, run once at startup (main.py).

    Under the old human-in-the-loop system a positive reply parked the lead
    at status 'replied' and waited for an operator to type 'takeover'. The
    autonomous system never uses 'replied' -- no code sets it and no query
    selects it -- so any lead sitting there when this version is deployed
    would be stranded: the prospect already replied and is waiting on us, but
    nothing is reply-driven for them anymore.

    This sweeps every legacy 'replied' lead into the negotiation flow by
    replaying their last logged inbound message through the normal
    negotiation round (moving them to 'negotiating' first). Idempotent: a
    processed lead leaves 'replied', so a second run is a no-op. Returns the
    count handled.

    Leads at 'negotiating' are intentionally NOT swept -- that status now also
    means active auto-negotiation, so it can't be distinguished from a
    legacy human-owned conversation by status alone; those resume naturally
    when the prospect next replies (check_inbox), bounded by the round cap.
    """
    handled = 0
    for lead in db.list_leads_by_status("replied"):
        threads = db.get_email_threads(lead["id"])
        last_inbound = next(
            (t for t in reversed(threads) if t["direction"] == "inbound"), None
        )
        inbound_body = (last_inbound["body"] if last_inbound else "") or ""
        if db.update_lead_status_unless(
            lead["id"], "negotiating",
            notes="Startup catch-up: legacy 'replied' lead routed into autonomous negotiation",
            unless_current=("won", "payment_sent", "lost", "unsubscribed", "bounced"),
        ):
            alert_positive_reply(lead)
        try:
            _run_negotiation_round(db.get_lead(lead["id"]) or lead, inbound_body)
            handled += 1
        except Exception as exc:  # noqa: BLE001 - one bad lead must not abort the sweep
            print(f"[sales_agent] Catch-up failed for lead {lead['id']}: {exc}")
            alert_needs_human(lead, f"Startup negotiation catch-up failed ({exc}); reply manually.")
    if handled:
        print(f"[sales_agent] Startup catch-up engaged autonomous negotiation for {handled} legacy lead(s).")
    return handled
