"""One-off: re-send the preview email to leads whose first cold email carried
a stale screenshot (a July "Log in to Vercel" capture, or another business's
site) because the screenshot cache was keyed by lead id across a DB reset.

Runs against the LIVE checkout (its .env, venv, DB and screenshots):

    cd D:\\projects\\website-designers\\pipeline
    ..\\venv\\Scripts\\python.exe <this file> --preview <dir>   # write .html previews, send nothing
    ..\\venv\\Scripts\\python.exe <this file> --send             # really send, one lead at a time

Guards: skips a lead that is unsubscribed, no longer in 'emailed' (replied /
negotiating / bounced), already has a resend logged, has no fresh screenshot
from today, or whose preview URL fails the same public-access validation the
cold email uses. Respects the pipeline's hourly/daily caps, send window and
120-300 s spacing via sales_agent._can_send_now(). --send refuses to run while
ENABLE_LIVE_SEND is off.

The lead ids are hardcoded: delete this file once the resend has run.
"""
from __future__ import annotations

import argparse
import base64
import html as html_module
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
from utils import db, screenshot  # noqa: E402
from agents import sales_agent as sa  # noqa: E402

AFFECTED_LEAD_IDS = [22, 26, 27, 30, 37, 40, 43, 45, 56]
CLASSIFICATION = "resend_preview"
NOTE = "Preview re-sent with the correct screenshot (first email carried a stale image)"
_WAIT_POLL_SECONDS = 30
_MAX_WAIT_MINUTES = 90


def _correction_line() -> str:
    # Worded for every affected lead: eight got a Vercel login page, but lead
    # 22 got a test cafe site, so "it showed a login screen" would be wrong.
    return (
        "Quick correction -- the picture in my last email was wrong, it wasn't "
        "your site. My mistake. Here's what the draft actually looks like:"
    )


def _closing(link: str) -> list[str]:
    return [
        f"It's live here: {link}",
        "If you'd like to own it, reply YES -- I'll connect your domain, swap in your own "
        "photos and logo, and make any changes you want.",
        sa._pricing_line(),
        sa._guarantee_line(),
        sa._SENDER_NAME,
    ]


def body_text(link: str) -> str:
    return "\n\n".join([_correction_line(), sa._draft_note(), *_closing(link)])


def body_html(link: str) -> str:
    esc = html_module.escape(link)
    parts = [
        f"<p>{html_module.escape(_correction_line())}</p>",
        f'<p><a href="{esc}"><img src="cid:{sa._SCREENSHOT_CID}" alt="Draft website preview" '
        'style="max-width:100%;border:1px solid #ddd;border-radius:8px;"></a></p>',
        f"<p>{html_module.escape(sa._draft_note())}</p>",
        f'<a href="{esc}" style="display:inline-block;padding:14px 28px;background:#2563eb;'
        'color:white;border-radius:8px;text-decoration:none;font-size:16px;font-weight:bold;'
        'margin:16px 0">View the draft &rarr;</a>',
    ]
    parts += [f"<p>{html_module.escape(p)}</p>" for p in _closing(link)[1:]]
    return "".join(parts)


def _already_resent(lead_id: int) -> bool:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM email_threads WHERE lead_id=? AND direction='outbound' AND classification=? LIMIT 1",
            (lead_id, CLASSIFICATION),
        ).fetchone()
    return row is not None


def _fresh_screenshot(lead_id: int) -> Path | None:
    path = screenshot.get_cached_screenshot(lead_id)
    if path is None:
        return None
    try:
        stat = path.stat()
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        # Refuse corrupt, future-dated, or non-PNG cache entries.
        if age < timedelta(0) or age >= timedelta(hours=12):
            return None
        if stat.st_size < 24 or path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            return None
    except (OSError, ValueError):
        return None
    return path


def _prepare(lead_id: int) -> tuple[dict, str, Path] | None:
    lead = db.get_lead(lead_id)
    if lead is None:
        print(f"[{lead_id}] skip: no such lead")
        return None
    email_addr = lead.get("contact_email") or ""
    if not email_addr or db.is_unsubscribed(email_addr):
        print(f"[{lead_id}] skip: no address / unsubscribed")
        return None
    if lead.get("status") != "emailed":
        print(f"[{lead_id}] skip: status is {lead.get('status')!r}, not 'emailed' (they may have replied)")
        return None
    if _already_resent(lead_id):
        print(f"[{lead_id}] skip: resend already logged")
        return None
    website = db.get_website_by_lead(lead_id)
    raw_link = (website or {}).get("preview_url") or ""
    try:
        link = sa._validate_preview_link_for_send(raw_link)
    except RuntimeError as exc:
        print(f"[{lead_id}] skip: preview not sendable: {exc}")
        return None
    shot = _fresh_screenshot(lead_id)
    if shot is None:
        print(f"[{lead_id}] skip: no screenshot captured in the last 12h")
        return None
    return lead, link, shot


def _write_preview(out_dir: Path, lead: dict, link: str, shot: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    subject = sa._reply_subject(lead)
    data_uri = "data:image/png;base64," + base64.b64encode(shot.read_bytes()).decode("ascii")
    html_body = body_html(link).replace(f"cid:{sa._SCREENSHOT_CID}", data_uri)
    page = (
        f"<!doctype html><meta charset='utf-8'><title>{html_module.escape(subject)}</title>"
        f"<body style='font-family:system-ui;max-width:680px;margin:24px auto;color:#222'>"
        f"<p style='color:#666'>To: {html_module.escape(lead['contact_email'])}<br>"
        f"Subject: {html_module.escape(subject)}</p><hr>{html_body}"
        "<hr><p style='color:#888;font-size:12px'>[unsubscribe footer is appended by the transport]</p></body>"
    )
    (out_dir / f"{lead['id']}.html").write_text(page, encoding="utf-8")
    (out_dir / f"{lead['id']}.txt").write_text(f"Subject: {subject}\n\n{body_text(link)}\n", encoding="utf-8")
    print(f"[{lead['id']}] preview written for {lead['business_name']} -> {lead['contact_email']}")


def _wait_for_slot() -> bool:
    deadline = time.monotonic() + _MAX_WAIT_MINUTES * 60
    while not sa._can_send_now():
        if time.monotonic() > deadline:
            return False
        print(f"    waiting for a send slot (caps/window/spacing)... {datetime.now():%H:%M:%S}")
        time.sleep(_WAIT_POLL_SECONDS)
    return True


def _send(lead: dict, link: str, shot: Path) -> bool:
    # The transport dry-runs on its own when live send is off, but still hands
    # back a message id -- and logging that as a resend would make
    # _already_resent() skip this lead forever without anything being sent.
    if not config.ENABLE_LIVE_SEND:
        print(f"[{lead['id']}] skipped: ENABLE_LIVE_SEND is off; use --preview")
        return False
    if not _wait_for_slot():
        print(f"[{lead['id']}] gave up waiting for a send slot")
        return False
    subject = sa._reply_subject(lead)
    text = body_text(link)
    account = sa._sender_for(lead)
    message_id = sa._send_via_configured_transport(
        to_addr=lead["contact_email"], subject=subject, body_text=text, lead_id=lead["id"],
        body_html=body_html(link), inline_image_path=str(shot), inline_image_cid=sa._SCREENSHOT_CID,
        account=account,
    )
    db.insert_email_thread(
        lead_id=lead["id"], direction="outbound", subject=subject, body=text,
        from_addr=account.user, to_addr=lead["contact_email"], message_id=message_id,
        classification=CLASSIFICATION,
    )
    sa._pin_sender(lead, account)
    db.log_state_history(lead["id"], "emailed", "emailed", notes=NOTE)
    sa._next_send_allowed_at = datetime.now(timezone.utc) + timedelta(
        seconds=random.uniform(config.EMAIL_MIN_DELAY_SECONDS, config.EMAIL_MAX_DELAY_SECONDS)
    )
    print(f"[{lead['id']}] SENT: {lead['business_name']} -> {lead['contact_email']} ({message_id})")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs="*", default=AFFECTED_LEAD_IDS)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", metavar="DIR", help="write .html/.txt previews here, send nothing")
    mode.add_argument("--send", action="store_true", help="really send (requires ENABLE_LIVE_SEND=true)")
    ap.add_argument("--ignore-window", action="store_true",
                    help="send outside the 08:00-18:00 Mon-Fri window (caps and spacing still apply)")
    args = ap.parse_args(argv)
    if args.send and not config.ENABLE_LIVE_SEND:
        ap.error("--send requires ENABLE_LIVE_SEND=true; use --preview to see the emails without sending")
    if args.ignore_window:
        sa._inside_send_window = lambda _now: True

    sent = 0
    for lead_id in args.ids:
        prepared = _prepare(lead_id)
        if prepared is None:
            continue
        lead, link, shot = prepared
        if args.preview:
            _write_preview(Path(args.preview), lead, link, shot)
        elif _send(lead, link, shot):
            sent += 1
    if args.send:
        print(f"done: {sent} re-sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
