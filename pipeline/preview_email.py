"""Print the exact cold email a lead would receive. Nothing is sent, written
or deployed, and no model is called.

    python run.py preview-email                        # built-in sample lead, placeholder link
    python run.py preview-email --lead 81              # a real lead: its real preview URL + screenshot state
    python run.py preview-email --lead 81 --check-link # also run the send-time preview-URL check (one HTTP GET)

Use it to review the copy before a batch goes out and to see one lead's
email without waiting for the loop. The body is built by the same functions
sales_agent uses at send time, so what prints here is what would be sent.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

import config
from agents import sales_agent
from utils import compliance, db, screenshot

SAMPLE_LEAD: dict = {
    "id": 0,
    "business_name": "Sample Plumbing",
    "niche": "plumber",
    "location": "Maidstone, Kent",
    "contact_email": "owner@example.com",
}
PLACEHOLDER_LINK = "https://sample-plumbing-preview-0.vercel.app"


def build(lead: dict, preview_link: str) -> dict:
    """Everything the send path would put on the wire, minus the transport."""
    lead_id = int(lead["id"])
    city = lead.get("location") or "your area"
    body = sales_agent._plain_text_body(lead["business_name"], preview_link, city)
    cached = screenshot.get_cached_screenshot(lead_id) if lead_id else None
    account = config.EMAIL_ACCOUNTS[0]
    return {
        "from": f"{account.display_name} <{account.user}>",
        "to": lead.get("contact_email") or "(no contact email -- the lead would be marked lost at send time)",
        "subject": sales_agent.cold_email_subject(lead),
        "list_unsubscribe": compliance.list_unsubscribe_header(lead_id),
        "text": compliance.append_footer(body, lead_id),
        "html_with_screenshot": cached is not None,
        "screenshot_path": str(cached) if cached else None,
    }


def main(argv: Optional[list[str]] = None) -> int:
    try:  # Windows consoles default to cp1252, which cannot print the checklist ticks
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lead", type=int, help="a real lead id from the DB (default: a built-in sample)")
    parser.add_argument("--check-link", action="store_true", help="run the send-time preview URL check (one HTTP GET)")
    args = parser.parse_args(argv)

    if args.lead:
        db.init_db()
        lead = db.get_lead(args.lead)
        if lead is None:
            print(f"No lead with id {args.lead}")
            return 1
        website = db.get_website_by_lead(args.lead)
        preview_link = (website or {}).get("preview_url") or ""
        link_note = "" if preview_link else " (no preview deployed yet; placeholder shown)"
        preview_link = preview_link or PLACEHOLDER_LINK
    else:
        lead, preview_link, link_note = SAMPLE_LEAD, PLACEHOLDER_LINK, " (sample lead; placeholder link)"

    email = build(lead, preview_link)
    print(f"From:    {email['from']}")
    print(f"To:      {email['to']}")
    print(f"Subject: {email['subject']}")
    print(f"List-Unsubscribe: {email['list_unsubscribe']}")
    print(f"Preview link: {preview_link}{link_note}")
    if email["html_with_screenshot"]:
        print(f"HTML part: yes, with the cached screenshot {email['screenshot_path']}")
    else:
        print("HTML part: no cached screenshot for this lead -> plain text only")
    print("-" * 66)
    print(email["text"])
    print("-" * 66)
    if args.check_link:
        try:
            sales_agent._validate_preview_link_for_send(preview_link)
        except RuntimeError as exc:
            print(f"Preview URL check: FAILED -- {exc}")
            return 1
        print("Preview URL check: OK (publicly reachable, no auth wall)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
