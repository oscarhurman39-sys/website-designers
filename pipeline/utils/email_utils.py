"""SMTP sending and IMAP inbox polling.

Deliverability & compliance notes:
- All sends go through `send_email()`, which unconditionally attaches the
  CAN-SPAM footer and `List-Unsubscribe` header -- there is no code path
  that sends a cold email without them.
- Rate limiting (delay between sends, hourly/daily caps) is enforced by the
  caller (agents/sales_agent.py), not here -- this module is a thin,
  stateless transport layer.
- A dedicated secondary sending domain should be used (see config.py /
  SENDING_DOMAIN), and a real warm-up tool should precede high-volume
  sending on any new domain; this module does not warm up domains for you.
"""
from __future__ import annotations

import base64
import email
import imaplib
import mimetypes
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Optional

import config
from utils import compliance


def _require_real_physical_address() -> None:
    """Refuse to send while PHYSICAL_ADDRESS is empty or an obvious
    placeholder (see config.physical_address_problem) -- the footer's postal
    address is a legal requirement, not decoration. Loud on purpose, and
    enforced even in DRY_RUN so a bad address is caught at test time."""
    problem = config.physical_address_problem()
    if problem:
        bar = "!" * 70
        print(
            f"\n{bar}\nREFUSING TO SEND: PHYSICAL_ADDRESS is {problem}.\n"
            f"Set a real postal address in .env (PHYSICAL_ADDRESS=...) and retry.\n{bar}\n"
        )
        raise RuntimeError(f"PHYSICAL_ADDRESS is {problem}; refusing to send.")


@dataclass
class InboundEmail:
    message_id: str
    in_reply_to: str
    subject: str
    from_addr: str
    to_addr: str
    body: str
    content_type: str
    date: str


def send_email(
    to_addr: str,
    subject: str,
    body_text: str,
    lead_id: int,
    body_html: Optional[str] = None,
    inline_image_path: Optional[str] = None,
    inline_image_cid: str = "preview",
) -> str:
    """Send a compliant cold email. Returns the generated Message-ID.

    Raises RuntimeError if the recipient is unsubscribed -- this is a hard
    stop enforced at the transport layer as a last line of defense, in
    addition to the caller checking `db.is_unsubscribed()` beforehand.

    `inline_image_path`, if given, embeds that image as a `multipart/related`
    part with the given Content-ID (default "preview") -- `body_html` must
    reference it as `cid:<inline_image_cid>` and must be provided in that
    case (there'd be nothing to display the image inline in otherwise).
    Callers that never pass `inline_image_path` (which is every caller as
    of this writing except sales_agent.py's cold-email send) get back the
    exact same `multipart/alternative`-only structure as before -- this
    parameter is purely additive.
    """
    from utils import db  # local import to avoid a circular import at module load time

    if db.is_unsubscribed(to_addr):
        raise RuntimeError(f"Refusing to send: {to_addr} is unsubscribed.")
    _require_real_physical_address()
    if inline_image_path and not body_html:
        raise ValueError("inline_image_path requires body_html (the image is referenced via cid: inside it).")

    text_with_footer = compliance.append_footer(body_text, lead_id)

    content = MIMEMultipart("alternative")
    content.attach(MIMEText(text_with_footer, "plain"))
    if body_html:
        html_with_footer = compliance.append_footer_html(body_html, lead_id)
        content.attach(MIMEText(html_with_footer, "html"))

    if inline_image_path:
        msg = MIMEMultipart("related")
        msg.attach(content)
        image_path = Path(inline_image_path)
        image_part = MIMEImage(image_path.read_bytes())
        image_part.add_header("Content-ID", f"<{inline_image_cid}>")
        image_part.add_header("Content-Disposition", "inline", filename=image_path.name)
        msg.attach(image_part)
    else:
        msg = content

    msg["Subject"] = subject
    msg["From"] = f"{config.SENDING_DOMAIN} <{config.EMAIL_USER}>"
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    message_id = make_msgid(domain=config.SENDING_DOMAIN or None)
    msg["Message-ID"] = message_id
    msg["List-Unsubscribe"] = compliance.list_unsubscribe_header(lead_id)
    if compliance.one_click_supported():
        # RFC 8058 one-click requires a public HTTPS unsubscribe endpoint;
        # with the mailto-only fallback this header would be invalid.
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    # Signals legitimate bulk mail to receivers (helps Outlook placement)
    # and suppresses out-of-office auto-replies from most mail systems.
    msg["Precedence"] = "bulk"

    # Dry run: the full compliant message was built (footer, headers, parts)
    # but nothing leaves the machine. Placed after the unsubscribe hard-stop
    # on purpose, so dry runs exercise every guard a real send would.
    if config.DRY_RUN:
        _print_dry_run("SMTP", to_addr, subject, text_with_footer)
        return message_id

    with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT, timeout=30) as server:
        server.starttls()
        server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_USER, [to_addr], msg.as_string())

    # SMTP has no HTTP status code to report; a clean return from sendmail()
    # means the server accepted it for delivery (login/auth failures raise
    # before reaching here, so this line printing at all is itself the
    # "accepted" signal).
    print(f"[email_utils] SMTP send accepted for {to_addr} (Message-ID: {message_id})")
    return message_id


def _print_dry_run(transport: str, to_addr: str, subject: str, body_with_footer: str) -> None:
    """Console dump of an email a live run would have sent (DRY_RUN=true)."""
    bar = "=" * 66
    print(
        f"\n{bar}\nDRY RUN -- email NOT sent (transport: {transport})\n"
        f"To: {to_addr}\nSubject: {subject}\n{'-' * 66}\n{body_with_footer}\n{bar}\n"
    )


def send_email_sendgrid(
    to_addr: str,
    subject: str,
    body_text: str,
    lead_id: int,
    body_html: Optional[str] = None,
    inline_image_path: Optional[str] = None,
    inline_image_cid: str = "preview",
) -> str:
    """SendGrid v3 API counterpart of send_email(), with an identical
    signature and the same compliance guarantees:

      * hard-stops on an unsubscribed recipient (last line of defense),
      * appends the CAN-SPAM footer to the plain-text and HTML parts,
      * sets the one-click List-Unsubscribe headers,
      * embeds `inline_image_path` inline via a `cid:` attachment that
        `body_html` references as `cid:<inline_image_cid>`.

    Returns a Message-ID string (SendGrid's X-Message-Id when present) so
    callers record it exactly as they do the SMTP Message-ID. Requires
    config.SENDGRID_API_KEY; the verified sender is config.SENDGRID_FROM_EMAIL
    (falling back to EMAIL_USER). Raises RuntimeError on a non-2xx response
    so a failed send is never silently recorded as sent.
    """
    from utils import db  # local import to avoid a circular import at module load time

    if db.is_unsubscribed(to_addr):
        raise RuntimeError(f"Refusing to send: {to_addr} is unsubscribed.")
    _require_real_physical_address()
    if inline_image_path and not body_html:
        raise ValueError("inline_image_path requires body_html (the image is referenced via cid: inside it).")
    if not config.SENDGRID_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY is not configured.")

    from_email = config.SENDGRID_FROM_EMAIL or config.EMAIL_USER
    if not from_email:
        raise RuntimeError("Set SENDGRID_FROM_EMAIL (or EMAIL_USER) as the SendGrid sender address.")

    # Imported lazily so the SMTP path (and the rest of the pipeline) never
    # requires the sendgrid package to be installed.
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import (
        Attachment,
        ClickTracking,
        Content,
        ContentId,
        CustomArg,
        Disposition,
        FileContent,
        FileName,
        FileType,
        Header,
        Mail,
        OpenTracking,
        ReplyTo,
        TrackingSettings,
    )

    text_with_footer = compliance.append_footer(body_text, lead_id)

    message = Mail(from_email=from_email, to_emails=to_addr, subject=subject)
    # Replies must land in the inbox check_inbox() actually polls (IMAP on
    # EMAIL_USER). Once a domain-authenticated From address is in use, that
    # differs from EMAIL_USER -- without this Reply-To, every reply would go
    # to an unpolled mailbox and the reply/takeover loop would silently die.
    if config.EMAIL_USER and from_email.lower() != config.EMAIL_USER.lower():
        message.reply_to = ReplyTo(config.EMAIL_USER)
    # text/plain must precede text/html (increasing richness); SendGrid uses
    # the last part as the primary display candidate.
    message.add_content(Content("text/plain", text_with_footer))
    if body_html:
        message.add_content(Content("text/html", compliance.append_footer_html(body_html, lead_id)))

    # Parity with the SMTP path's one-click unsubscribe (one-click only when
    # a public HTTPS unsubscribe endpoint exists -- see there).
    message.add_header(Header("List-Unsubscribe", compliance.list_unsubscribe_header(lead_id)))
    if compliance.one_click_supported():
        message.add_header(Header("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"))
    # Same legitimate-bulk-mail signal as the SMTP path (see there).
    message.add_header(Header("Precedence", "bulk"))

    # Enable SendGrid open + click tracking, and tag the message with lead_id
    # via a custom arg SendGrid echoes back on every Event Webhook event --
    # that's how /webhook/sendgrid attributes opens/clicks to a lead (and its
    # subject-line A/B variant) in the dashboard.
    message.tracking_settings = TrackingSettings(
        click_tracking=ClickTracking(enable=True, enable_text=True),
        open_tracking=OpenTracking(enable=True),
    )
    message.add_custom_arg(CustomArg("lead_id", str(lead_id)))

    if inline_image_path:
        image_path = Path(inline_image_path)
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        message.add_attachment(
            Attachment(
                FileContent(encoded),
                FileName(image_path.name),
                FileType(mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"),
                Disposition("inline"),
                ContentId(inline_image_cid),
            )
        )

    # Dry run: same contract as the SMTP path -- full message built, all
    # guards exercised, nothing sent.
    if config.DRY_RUN:
        _print_dry_run("SendGrid", to_addr, subject, text_with_footer)
        return make_msgid(domain=config.SENDING_DOMAIN or None)

    # Retry ONLY definitive 429/5xx API rejections (3 attempts, 2s/4s
    # backoff). Ambiguous failures (timeouts/resets) are deliberately NOT
    # retried: the message may have already been accepted, and retrying
    # would send the same cold email twice. python_http_client is the
    # transport sendgrid itself uses, so its HTTPError carries status_code.
    from python_http_client.exceptions import HTTPError as SendGridHTTPError

    from utils import retry

    @retry.with_retries(
        retriable=(SendGridHTTPError,),
        transient=retry.is_retriable_http_response,
        label="sendgrid.send",
    )
    def _send_once():
        return SendGridAPIClient(config.SENDGRID_API_KEY).send(message)

    response = _send_once()
    headers = getattr(response, "headers", None) or {}
    sg_id = headers.get("X-Message-Id") or headers.get("x-message-id")
    accepted = response.status_code in (200, 201, 202)
    print(
        f"[email_utils] SendGrid response for {to_addr}: HTTP {response.status_code} "
        f"({'ACCEPTED' if accepted else 'REJECTED'})"
        + (f", message_id={sg_id}" if sg_id else ", message_id=<none returned>")
        + f", from={from_email}"
    )
    if not accepted:
        raise RuntimeError(f"SendGrid send failed with HTTP {response.status_code}")

    return f"<{sg_id}@sendgrid.net>" if sg_id else make_msgid(domain=config.SENDING_DOMAIN or None)


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            decoded += text.decode(enc or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    """Return (plain_text_body, content_type_summary)."""
    if msg.is_multipart():
        content_types = []
        plain_text = ""
        for part in msg.walk():
            ctype = part.get_content_type()
            content_types.append(ctype)
            disposition = str(part.get("Content-Disposition", ""))
            if ctype == "text/plain" and "attachment" not in disposition and not plain_text:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                plain_text = payload.decode(charset, errors="replace")
        summary = "multipart/report" if "multipart/report" in content_types or msg.get_content_type() == "multipart/report" else msg.get_content_type()
        return plain_text, summary
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace"), msg.get_content_type()


def fetch_unseen_emails() -> list[InboundEmail]:
    """Poll the IMAP inbox for unseen messages and return them as InboundEmail.

    Messages are marked \\Seen as a side effect of fetching RFC822 (standard
    IMAP behavior), so each message is only returned once across polls.
    """
    results: list[InboundEmail] = []
    with imaplib.IMAP4_SSL(config.EMAIL_HOST) as imap:
        imap.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        imap.select("INBOX")
        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            return results
        message_nums = data[0].split()
        for num in message_nums:
            status, msg_data = imap.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            body, content_type = _extract_body(msg)
            from_name, from_addr = parseaddr(_decode(msg.get("From")))
            _, to_addr = parseaddr(_decode(msg.get("To")))
            results.append(
                InboundEmail(
                    message_id=_decode(msg.get("Message-ID")) or f"<generated-{uuid.uuid4()}@local>",
                    in_reply_to=_decode(msg.get("In-Reply-To")),
                    subject=_decode(msg.get("Subject")),
                    from_addr=from_addr,
                    to_addr=to_addr,
                    body=body,
                    content_type=content_type,
                    date=_decode(msg.get("Date")) or datetime.utcnow().isoformat(),
                )
            )
    return results
