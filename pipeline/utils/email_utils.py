"""SMTP sending and IMAP inbox polling.

Deliverability & compliance notes:
- All sends go through `send_email()` or `send_email_sendgrid()`, which
  unconditionally attaches the CAN-SPAM footer and `List-Unsubscribe` header
  -- there is no code path that sends a cold email without them.
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
import logging
import re
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from pathlib import Path
from typing import Optional
import base64

from python_http_client.exceptions import HTTPError as SendGridHTTPError
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail,
    Attachment,
    FileContent,
    FileName,
    FileType,
    Disposition,
    MailSettings,
    TrackingSettings,
    OpenTracking,
    ClickTracking,
)

import config
from utils import compliance, retry

logger = logging.getLogger(__name__)

_DRY_RUN_LOG_PATH = Path(__file__).resolve().parent.parent / "dry_run.log"

# Simple email validation regex
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


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


def _validate_email(email_addr: str) -> bool:
    """Validate email address format."""
    return EMAIL_REGEX.match(email_addr) is not None


def _log_dry_run(transport: str, to_addr: str, subject: str, body_with_footer: str) -> None:
    """Console + file record of an email a live run would have sent
    (config.ENABLE_LIVE_SEND is not true). Appended to pipeline/dry_run.log
    so a full dry-run pass leaves a durable record, not just console
    scrollback -- placed after every other guard (unsubscribe check,
    compliance footer, message construction) so a dry run still exercises
    everything a real send would, short of the actual network call."""
    bar = "=" * 66
    entry = (
        f"\n{bar}\nDRY RUN -- email NOT sent (transport: {transport})\n"
        f"Time: {datetime.utcnow().isoformat()}Z\n"
        f"To: {to_addr}\nSubject: {subject}\n{'-' * 66}\n{body_with_footer}\n{bar}\n"
    )
    print(entry)
    with open(_DRY_RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry)


def send_email(
    to_addr: str,
    subject: str,
    body_text: str,
    lead_id: int,
    body_html: Optional[str] = None,
    inline_image_path: Optional[str] = None,
    inline_image_cid: str = "preview",
) -> str:
    """Send a compliant cold email via SMTP. Returns the generated Message-ID.

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

    if inline_image_path and Path(inline_image_path).exists():
        msg = MIMEMultipart("related")
        msg.attach(content)
        image_path = Path(inline_image_path)
        image_part = MIMEImage(image_path.read_bytes())
        image_part.add_header("Content-ID", f"<{inline_image_cid}>")
        image_part.add_header("Content-Disposition", "inline", filename=image_path.name)
        msg.attach(image_part)
    else:
        if inline_image_path:
            logger.warning(f"Inline image path {inline_image_path!r} does not exist; sending without it.")
        msg = content

    msg["Subject"] = subject
    msg["From"] = formataddr((config.SENDER_NAME, config.EMAIL_USER))
    msg["To"] = to_addr
    msg["Date"] = formatdate(localtime=True)
    message_id = make_msgid(domain=config.SENDING_DOMAIN or None)
    msg["Message-ID"] = message_id
    msg["List-Unsubscribe"] = compliance.list_unsubscribe_header(lead_id)
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if not config.ENABLE_LIVE_SEND:
        _log_dry_run("SMTP", to_addr, subject, text_with_footer)
        return message_id

    with smtplib.SMTP(config.EMAIL_HOST, config.EMAIL_PORT, timeout=30) as server:
        server.starttls()
        server.login(config.EMAIL_USER, config.EMAIL_PASSWORD)
        server.sendmail(config.EMAIL_USER, [to_addr], msg.as_string())

    return message_id


def send_email_sendgrid(
    to_addr: str,
    subject: str,
    body_text: str,
    lead_id: int,
    body_html: Optional[str] = None,
    inline_image_path: Optional[str] = None,
    inline_image_cid: str = "preview",
) -> str:
    """Send a compliant cold email via SendGrid. Returns the generated Message-ID.

    Raises RuntimeError if the recipient is unsubscribed -- this is a hard
    stop enforced at the transport layer as a last line of defense, in
    addition to the caller checking `db.is_unsubscribed()` beforehand.

    Enables open and click tracking. Inline images are embedded with the given
    Content-ID; `body_html` must reference them as `cid:<inline_image_cid>`.

    Uses RFC 8058-compliant List-Unsubscribe headers with one-click + mailto
    fallback, matching the compliance pattern in send_email().

    Args:
        to_addr: Recipient email address.
        subject: Email subject line.
        body_text: Plain-text email body (footer will be appended).
        lead_id: Lead ID for compliance footer and tracking.
        body_html: Optional HTML email body (footer will be appended).
        inline_image_path: Optional path to an image file to embed inline.
        inline_image_cid: Content-ID for the inline image (default "preview").

    Returns:
        The generated Message-ID header value.

    Raises:
        RuntimeError: If recipient is unsubscribed, config is missing, or SendGrid API call fails.
        ValueError: If inline_image_path is given without body_html or email is invalid.
    """
    from utils import db  # local import to avoid a circular import at module load time

    # Validate inputs
    if not to_addr or not subject or not body_text:
        raise ValueError("to_addr, subject, and body_text are required.")
    
    if not _validate_email(to_addr):
        raise ValueError(f"Invalid email format: {to_addr}")

    if not config.SENDGRID_API_KEY:
        raise RuntimeError("SENDGRID_API_KEY is not configured.")
    if not config.SENDGRID_FROM_EMAIL:
        raise RuntimeError("SENDGRID_FROM_EMAIL is not configured.")

    if db.is_unsubscribed(to_addr):
        raise RuntimeError(f"Refusing to send: {to_addr} is unsubscribed.")
    if inline_image_path and not body_html:
        raise ValueError("inline_image_path requires body_html (the image is referenced via cid: inside it).")

    logger.info(f"Sending SendGrid email to {to_addr} with subject: {subject}")

    # Generate message ID in the same format as send_email() for consistency
    message_id = make_msgid(domain=config.SENDING_DOMAIN or None)

    # Append compliance footer to plain text
    text_with_footer = compliance.append_footer(body_text, lead_id)

    # Build HTML content with footer if provided
    html_content = body_html
    if body_html:
        html_with_footer = compliance.append_footer_html(body_html, lead_id)
        html_content = html_with_footer

    # Create the Mail object
    mail = Mail(
        from_email=config.SENDGRID_FROM_EMAIL,
        to_emails=to_addr,
        subject=subject,
        plain_text_content=text_with_footer,
        html_content=html_content,
    )

    # Set Message-ID header
    mail.extra_headers = {
        "Message-ID": message_id,
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    # RFC 8058-compliant List-Unsubscribe header: HTTP URL + mailto fallback.
    mail.extra_headers["List-Unsubscribe"] = compliance.list_unsubscribe_header(lead_id)

    # Enable open and click tracking
    mail.mail_settings = MailSettings()
    mail.mail_settings.tracking_settings = TrackingSettings()
    mail.mail_settings.tracking_settings.open_tracking = OpenTracking(enable=True)
    mail.mail_settings.tracking_settings.click_tracking = ClickTracking(enable=True)

    # Attach inline image if provided (and the file actually exists -- a
    # missing file must never turn into a broken "image not found" icon in
    # the recipient's inbox, so we just skip the attachment and log it).
    if inline_image_path and Path(inline_image_path).exists():
        image_path = Path(inline_image_path)
        image_bytes = image_path.read_bytes()
        # Determine MIME type from file extension
        mime_type = _get_mime_type(image_path.suffix)

        # SendGrid's FileContent expects a base64-encoded string, not raw bytes.
        # Base64-encode the image bytes and decode to an ASCII string so the
        # SendGrid helper can JSON-serialize it without errors.
        image_b64 = base64.b64encode(image_bytes).decode("ascii")

        attachment = Attachment(
            file_content=FileContent(image_b64),
            file_name=FileName(image_path.name),
            file_type=FileType(mime_type),
            disposition=Disposition("inline"),
            content_id=inline_image_cid,
        )
        mail.add_attachment(attachment)
        logger.debug(f"Added inline image: {image_path.name} ({len(image_bytes)} bytes)")
    elif inline_image_path:
        logger.warning(f"Inline image path {inline_image_path!r} does not exist; sending without it.")

    if not config.ENABLE_LIVE_SEND:
        _log_dry_run("SendGrid", to_addr, subject, text_with_footer)
        return message_id

    # Send via SendGrid. Retry ONLY definitive 429/5xx API rejections (3
    # attempts, 2s/4s backoff) -- ambiguous failures (timeouts/resets) are
    # deliberately NOT retried, since the message may have already been
    # accepted and a retry would send the same cold email twice.
    @retry.with_retries(
        retriable=(SendGridHTTPError,),
        transient=retry.is_retriable_http_response,
        label="sendgrid.send",
    )
    def _send_once():
        return SendGridAPIClient(config.SENDGRID_API_KEY).send(mail)

    try:
        logger.debug("Connecting to SendGrid API...")
        response = _send_once()

        if response.status_code != 202:
            logger.error(f"SendGrid returned status {response.status_code} for {to_addr}: {response.body}")
            raise RuntimeError(
                f"SendGrid returned status {response.status_code}: {response.body}"
            )
        
        logger.info(f"Email sent successfully to {to_addr} (Message-ID: {message_id})")
    
    except ValueError as e:
        logger.error(f"Validation error sending to {to_addr}: {e}")
        raise
    except RuntimeError as e:
        logger.error(f"Runtime error sending to {to_addr}: {e}")
        raise
    except Exception as exc:
        logger.error(f"SendGrid send failed for {to_addr}: {exc}", exc_info=True)
        raise RuntimeError(f"SendGrid send failed for {to_addr}: {exc}")

    return message_id


def _get_mime_type(file_extension: str) -> str:
    """Return MIME type for common image extensions."""
    extension = file_extension.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    return mime_types.get(extension, "application/octet-stream")


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
    with imaplib.IMAP4_SSL(config.EMAIL_IMAP_HOST) as imap:
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
