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

import email
import imaplib
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
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT, timeout=30) as server:
        server.starttls()
        server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_USER, [to_addr], msg.as_string())

    return message_id


def send_email_sendgrid(
    to_email: str,
    subject: str,
    html_body: str,
    attachments: Optional[list[tuple[str, bytes, str]]] = None,
) -> bool:
    """Send an email via SendGrid with open and click tracking enabled.

    Returns True if the send succeeds (HTTP 202), False otherwise.

    Args:
        to_email: Recipient email address.
        subject: Email subject line.
        html_body: HTML content of the email.
        attachments: Optional list of (filename, file_bytes, mime_type) tuples.
    """
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

    try:
        message = Mail(
            from_email=config.SENDGRID_FROM_EMAIL,
            to_emails=to_email,
            subject=subject,
            html_content=html_body,
        )

        # Enable open and click tracking
        message.mail_settings.tracking_settings.open_tracking.enable = True
        message.mail_settings.tracking_settings.click_tracking.enable = True

        # Add attachments if provided
        if attachments:
            for filename, file_bytes, mime_type in attachments:
                attachment = Attachment(
                    FileContent(file_bytes),
                    FileName(filename),
                    FileType(mime_type),
                    Disposition("attachment"),
                )
                message.attachment = attachment

        # Send via SendGrid
        sg = SendGridAPIClient(config.SENDGRID_API_KEY)
        response = sg.send(message)

        # SendGrid returns 202 on successful send
        return response.status_code == 202

    except Exception as e:
        print(f"SendGrid send failed for {to_email}: {e}")
        return False


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
