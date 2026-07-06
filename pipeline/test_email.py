"""One-command "am I ready?" check: send one test email to YOURSELF.

    python test_email.py you@example.com          # from inside pipeline/
    python run.py test-email you@example.com      # from the repo root

Creates a dummy lead pointed at the given address (already 'researched', so
no Google Places call is made), then runs the real DesignAgent and
SalesAgent on it, printing every step:

  * DesignAgent creates a REAL GitHub repo and a REAL Vercel deployment --
    same as a live run, so this also verifies those tokens.
  * SalesAgent drafts and sends the cold email to the address you gave.
    With DRY_RUN=true (the shipped default) the full email is printed to
    the console instead of sent; with DRY_RUN=false it actually sends, and
    the transport logs the SendGrid HTTP status / message id (or the SMTP
    acceptance line).

If this run ends with the email in your inbox (or printed cleanly under
DRY_RUN), the pipeline is ready: `python run.py loop`.
"""
from __future__ import annotations

import argparse

import config
from agents import design_agent, sales_agent
from utils import db


def _print_step(label: str) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one end-to-end test email to yourself.")
    parser.add_argument("email", help="YOUR email address -- the test email goes here")
    args = parser.parse_args()

    config.validate()
    config.print_startup_diagnostics()
    db.init_db()

    _print_step("Step 1/3: Creating dummy test lead")
    lead_id = db.insert_lead("Pipeline Test Cafe", "cafe", "Testville")
    db.update_lead_fields(lead_id, contact_email=args.email)
    # Straight to 'researched': the point is to test design + send, not a
    # Google Places lookup for a business that doesn't exist.
    db.update_lead_status(lead_id, "researched", notes="test-email dummy lead")
    print(f"-> lead {lead_id} created, contact_email={args.email}")

    _print_step("Step 2/3: DesignAgent -- building + deploying preview (real GitHub repo + Vercel deploy)")
    try:
        website = design_agent.process_lead(db.get_lead(lead_id))
    except Exception as exc:  # noqa: BLE001 - print clearly, never crash silently
        print(f"DesignAgent FAILED: {exc}")
        raise SystemExit(1)
    lead = db.get_lead(lead_id)
    print(f"-> status: {lead['status']}")
    if website:
        print(f"-> preview: {website['preview_url']}")
        print(f"-> GitHub repo: {website['repo_url']}")
    if lead["status"] != "designed":
        print(f"Stopping -- lead did not reach 'designed' (got {lead['status']!r}).")
        raise SystemExit(1)

    _print_step("Step 3/3: SalesAgent -- drafting + sending the test email")
    try:
        sent = sales_agent.send_cold_email(db.get_lead(lead_id))
    except Exception as exc:  # noqa: BLE001
        print(f"SalesAgent FAILED: {exc}")
        raise SystemExit(1)
    lead = db.get_lead(lead_id)
    print(f"-> status: {lead['status']}, email sent: {sent}")

    if config.DRY_RUN:
        print(
            "\nDRY_RUN=true: the email above was printed, not sent. If it reads right,\n"
            "set DRY_RUN=false in .env and rerun this command for a real delivery test."
        )
    else:
        print(f"\nDone -- check the inbox for {args.email} (and the spam folder, the first time).")


if __name__ == "__main__":
    main()
