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
import time

import config
from agents import design_agent, sales_agent
from utils import db, sendgrid_diagnostics


def _print_step(label: str) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")


def _check_preview_is_public(preview_url: str) -> None:
    """Fetch the deployed preview exactly like an emailed lead would (no
    auth, no cookies). A 401/403 means Vercel deployment protection is
    still walling it off -- exactly what produced the Vercel login page in
    a real emailed screenshot -- so say so loudly instead of letting a
    login wall go out in a cold email."""
    import requests

    try:
        resp = requests.get(preview_url, timeout=25)
    except requests.RequestException as exc:
        print(f"-> public reachability: could not check ({exc})")
        return
    if resp.status_code in (401, 403):
        bar = "!" * 70
        print(
            f"{bar}\nPREVIEW IS NOT PUBLIC (HTTP {resp.status_code}): anyone clicking the emailed link\n"
            "gets a Vercel login wall, not the website. The pipeline now tries to\n"
            "disable this automatically; since it's still on, turn it off manually:\n"
            "Vercel dashboard -> this project -> Settings -> Deployment Protection ->\n"
            f"Vercel Authentication -> Disabled. Then rerun this test.\n{bar}"
        )
    elif resp.status_code < 400 and "web design team" in resp.text:
        print("-> public reachability: OK (loads without login, shows the generated site)")
    else:
        print(f"-> public reachability: unexpected response (HTTP {resp.status_code}) -- open the URL in a private/incognito window to inspect")


def _check_and_clear_suppressions(email: str) -> None:
    """A single historical bounce puts an address on SendGrid's suppression
    lists, after which every send to it returns 202 and is silently
    Dropped -- the classic '202 but nothing ever arrives'. Detect that
    before sending, and clear it (safe here: this is the operator's own
    test address, never a lead's)."""
    found = sendgrid_diagnostics.check_suppressions(email)
    if "_error" in found:
        print(f"-> suppression check skipped ({found['_error']})")
        return
    if not found:
        print(f"-> SendGrid suppression lists: clean for {email}")
        return
    bar = "!" * 70
    print(f"{bar}\nFOUND IT: {email} is on SendGrid suppression list(s): {', '.join(found)}.")
    print("Every send since it landed there was silently dropped AFTER the 202.")
    for kind in found:
        cleared = sendgrid_diagnostics.clear_suppression(kind, email)
        print(f"  - clearing '{kind}': {'done' if cleared else 'FAILED -- remove it manually in the SendGrid dashboard'}")
    print(f"Proceeding with the send now that it's cleared.\n{bar}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send one end-to-end test email to yourself.")
    parser.add_argument("email", help="YOUR email address -- the test email goes here")
    args = parser.parse_args()

    config.validate()
    config.print_startup_diagnostics()
    db.init_db()

    # The self-send trap: mail sent via a third party (SendGrid) "from" the
    # same mailbox it's addressed to looks like spoofing to Gmail, which
    # discards it silently -- no inbox, no spam, no bounce. Exactly the
    # hardest symptom to diagnose, so call it out before sending.
    from_addr = (config.SENDGRID_FROM_EMAIL or config.EMAIL_USER or "").strip().lower()
    if from_addr and from_addr == args.email.strip().lower():
        bar = "!" * 70
        print(
            f"{bar}\nWARNING: From and To are the SAME mailbox ({from_addr}).\n"
            "Gmail treats third-party mail 'from yourself' as spoofing and usually\n"
            "discards it with no trace -- no inbox, no spam, no bounce. Send this\n"
            "test to a DIFFERENT address you own, or (the real fix) authenticate a\n"
            f"domain in SendGrid and send from casey@yourdomain instead.\n{bar}"
        )

    # The '202 accepted but nothing arrives' check: is this address on a
    # SendGrid suppression list from an earlier bounce?
    if config.SENDGRID_API_KEY and not config.DRY_RUN:
        _check_and_clear_suppressions(args.email)

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
        if website.get("screenshot_path"):
            print("-> screenshot: captured (will be embedded in the email)")
        else:
            print("-> screenshot: NOT captured (email goes out without the preview image; "
                  "see the [design_agent] warning above for why)")
        _check_preview_is_public(website["preview_url"])
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
        if config.SENDGRID_API_KEY:
            # Bounce/delivered events can take a minute to register; poll a
            # few times rather than sampling once and printing 'processing'.
            print("\nAsking SendGrid what happened to the message (polling up to 60s)...")
            events = None
            for _ in range(4):
                time.sleep(15)
                events = sendgrid_diagnostics.try_activity_lookup(args.email)
                if events is None:
                    break  # Activity API unavailable on this plan; stop polling
                if events and all(ev["status"] != "processing" for ev in events):
                    break
            if events:
                for ev in events:
                    print(f"  - status={ev['status']}  subject={ev['subject']!r}  last_event={ev['last_event_time']}")
                print("  ('delivered' = Gmail took it (check Spam/Promotions); 'not_delivered' = bounced/blocked;"
                      " 'processing' = still in flight)")
            else:
                print("  (Email Activity API not enabled on this SendGrid plan -- the dashboard UI still shows it"
                      " for free: app.sendgrid.com -> Activity -> search the recipient.)")
        print(
            f"\nDone -- SendGrid accepted it. If it isn't in the {args.email} inbox within ~2 minutes:\n"
            "  1. Check SPAM and (in Gmail) the Promotions tab; also search All Mail for the subject.\n"
            "  2. SendGrid dashboard -> Activity Feed -> search this recipient. The status there\n"
            "     (Delivered / Bounced / Blocked / Dropped) says exactly where it died.\n"
            "  3. If the From address warning printed at startup, that IS the cause: authenticate\n"
            "     a real domain in SendGrid and send from it instead of a free mailbox."
        )


if __name__ == "__main__":
    main()
