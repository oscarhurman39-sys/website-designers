"""Manual test-send: create a throwaway lead, build+deploy its preview site,
and send the real cold email to an address you control -- so you can see
exactly what a prospect would receive without waiting on real lead research.

Skips LeadAgent (no scraping) since the dummy lead is created directly with
status 'researched'; runs DesignAgent -> SalesAgent same as quick_run.py.

Run with (either works, same as quick_run.py):

    python test_email.py you@example.com                # from inside pipeline/
    python pipeline/test_email.py you@example.com        # from the repo root

Needs a populated .env at the repo root first (see .env.example).
"""
from __future__ import annotations

import sys

import config
from agents import design_agent, sales_agent
from utils import db

_DUMMY_BUSINESS_NAME = "Test Business"
_DUMMY_NICHE = "vehicle-repair"
_DUMMY_LOCATION = "Testville"


def _print_step(label: str) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")


def main(to_addr: str) -> None:
    config.validate()
    db.init_db()

    lead_id = db.insert_lead(_DUMMY_BUSINESS_NAME, _DUMMY_NICHE, _DUMMY_LOCATION, status="new")
    db.update_lead_fields(lead_id, contact_email=to_addr)
    db.update_lead_status(lead_id, "researched", notes="Dummy lead created by test_email.py")
    lead = db.get_lead(lead_id)
    print(f"Created dummy lead {lead_id}: {lead['business_name']} -> {to_addr}")

    _print_step("Step 1/2: DesignAgent -- building + deploying preview")
    try:
        website = design_agent.process_lead(lead)
    except Exception as exc:  # noqa: BLE001 - print clearly, never crash silently
        print(f"DesignAgent FAILED: {exc}")
        return

    lead = db.get_lead(lead_id)
    print(f"-> status: {lead['status']}")
    if website:
        print(f"-> preview: {website['preview_url']}")
        print(f"-> rendered files kept at: {website.get('local_dir') or '(not saved)'}")
        print("-> no GitHub repo created (hand-off repo is created at 'transfer' time only)")
        if website.get("screenshot_url"):
            print(f"-> screenshot: {website['screenshot_url']}")
    if lead["status"] != "designed":
        print(f"Stopping here -- lead did not reach 'designed' (got {lead['status']!r}).")
        return

    _print_step(f"Step 2/2: SalesAgent -- sending cold email to {to_addr}")
    try:
        sent = sales_agent.send_cold_email(lead)
    except Exception as exc:  # noqa: BLE001
        print(f"SalesAgent FAILED: {exc}")
        return

    lead = db.get_lead(lead_id)
    print(f"-> status: {lead['status']}, email sent: {sent}")
    print(f"\nDone. Check {to_addr}'s inbox.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python test_email.py <email-address>")
        raise SystemExit(1)
    main(sys.argv[1])
