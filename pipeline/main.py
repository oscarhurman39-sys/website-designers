"""Orchestrator loop for the cold-email sales pipeline.

Run with: `python main.py`  (from inside the `pipeline/` directory, with the
virtualenv active and `.env` populated).

Each iteration:
  1. Ingests any new CSVs dropped into leads_inbox/ (LeadAgent).
  2. Runs LeadAgent research on 'new' leads.
  3. Runs DesignAgent on 'researched' leads.
  4. Sends at most one rate-limited cold email via SalesAgent on a 'designed' lead.
  5. Polls the inbox and classifies replies via SalesAgent.
Then sleeps MAIN_LOOP_SLEEP_SECONDS and repeats, until Ctrl-C.

A second thread reads operator commands from stdin so human-in-the-loop
actions (takeover / payment / transfer) don't have to wait for the loop:
  takeover <lead_id>        - pause automation, hand negotiation to a human
  payment ready <lead_id>   - create + email a Stripe Checkout link
  transfer <lead_id>        - hand the GitHub repo / Vercel project to the client
  status                    - print a lead-count-by-status summary
  pause / resume            - pause/resume the automated loop
  help                      - list commands
  quit                      - shut down
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from pathlib import Path

import config
from agents import design_agent, lead_agent, sales_agent
from utils import db, github_api, stripe_utils, vercel_api

PIPELINE_DIR = Path(__file__).resolve().parent
LEADS_INBOX = PIPELINE_DIR / "leads_inbox"
LEADS_PROCESSED = PIPELINE_DIR / "leads_processed"
PAUSE_FLAG = PIPELINE_DIR / ".paused"  # dashboard.py toggles this file to pause/resume

_shutdown_event = threading.Event()


def _is_paused() -> bool:
    return PAUSE_FLAG.exists()


def _set_paused(paused: bool) -> None:
    if paused:
        PAUSE_FLAG.touch()
    else:
        PAUSE_FLAG.unlink(missing_ok=True)


def _process_inbox_csvs() -> None:
    LEADS_INBOX.mkdir(exist_ok=True)
    LEADS_PROCESSED.mkdir(exist_ok=True)
    for csv_file in sorted(LEADS_INBOX.glob("*.csv")):
        print(f"[main] Ingesting {csv_file.name}")
        try:
            lead_agent.ingest_csv(str(csv_file))
        except Exception as exc:  # noqa: BLE001 - a malformed CSV must not kill the loop
            print(f"[main] Failed to ingest {csv_file.name}: {exc}")
        finally:
            shutil.move(str(csv_file), str(LEADS_PROCESSED / csv_file.name))


def _run_cycle() -> None:
    _process_inbox_csvs()

    for lead in db.list_leads_by_status("new"):
        lead_agent.research_lead(lead)

    design_agent.run()

    sent_lead_id = sales_agent.send_next_pending()
    if sent_lead_id:
        print(f"[main] Sent cold email to lead {sent_lead_id}")

    followup_lead_id = sales_agent.send_followups()
    if followup_lead_id:
        print(f"[main] Sent follow-up to lead {followup_lead_id}")

    replies = sales_agent.check_inbox()
    if replies:
        print(f"[main] Processed {replies} inbound reply(ies)")


def _print_status() -> None:
    leads = db.list_all_leads()
    counts: dict[str, int] = {}
    for lead in leads:
        counts[lead["status"]] = counts.get(lead["status"], 0) + 1
    print("[main] Lead status summary:")
    for status, count in sorted(counts.items()):
        print(f"    {status:15s} {count}")
    print(f"    {'TOTAL':15s} {len(leads)}")


def _handle_payment_ready(lead_id: int) -> None:
    lead = db.get_lead(lead_id)
    if lead is None:
        print(f"[main] No such lead {lead_id}")
        return
    if not lead.get("contact_email"):
        print(f"[main] Lead {lead_id} has no contact email; cannot send payment link.")
        return
    try:
        checkout_url = stripe_utils.create_checkout_session(
            lead_id=lead_id, business_name=lead["business_name"], customer_email=lead["contact_email"]
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[main] Failed to create Stripe checkout session: {exc}")
        return

    from utils import email_utils  # local import to avoid a module cycle at startup

    subject = f"Payment link for your new {lead['business_name']} website"
    body = (
        f"Hi, here's the secure payment link we discussed: {checkout_url}\n\n"
        "Once payment goes through, I'll get the site handed over to you right away."
    )
    try:
        message_id = email_utils.send_email(lead["contact_email"], subject, body, lead_id)
        db.insert_email_thread(
            lead_id=lead_id, direction="outbound", subject=subject, body=body,
            from_addr=config.EMAIL_USER, to_addr=lead["contact_email"], message_id=message_id,
        )
        db.update_lead_status(lead_id, "payment_sent", notes=f"Checkout link sent: {checkout_url}")
        print(f"[main] Payment link emailed to lead {lead_id}: {checkout_url}")
    except RuntimeError as exc:
        print(f"[main] Could not email payment link: {exc}")


def _handle_transfer(lead_id: int) -> None:
    lead = db.get_lead(lead_id)
    website = db.get_website_by_lead(lead_id)
    if lead is None or website is None:
        print(f"[main] No lead/website found for {lead_id}")
        return
    if lead["status"] != "won":
        print(f"[main] Lead {lead_id} is not marked 'won' yet (status: {lead['status']}). Aborting.")
        return

    # --- GitHub: invite the client as a collaborator on their repo ---
    github_username = input(f"GitHub username to invite for lead {lead_id} (blank to skip): ").strip()
    if github_username:
        try:
            github_api.invite_collaborator(website["repo_full_name"], github_username, permission="admin")
            print(f"[main] Invited {github_username} to {website['repo_full_name']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[main] Failed to invite GitHub collaborator: {exc}")
    else:
        print("[main] Skipping GitHub invite.")

    # --- Vercel: invite the client to the project (requires VERCEL_TEAM_ID) ---
    default_email = lead.get("contact_email") or ""
    prompt_suffix = f" [{default_email}]" if default_email else ""
    vercel_email = (
        input(f"Client email to invite to the Vercel project{prompt_suffix} (blank to skip): ").strip()
        or default_email
    )
    if vercel_email:
        try:
            project_raw_name = github_api.make_repo_name(lead["business_name"], lead_id)
            vercel_api.invite_collaborator(project_raw_name, vercel_email)
            print(f"[main] Invited {vercel_email} to the Vercel project.")
        except Exception as exc:  # noqa: BLE001
            print(f"[main] Failed to invite Vercel collaborator: {exc}")
            print(
                "[main] This call wasn't verified against a live Vercel account during "
                "development (see utils/vercel_api.py). If it keeps failing, confirm "
                "VERCEL_TEAM_ID is set and check the request shape against Vercel's "
                "current REST API docs."
            )
    else:
        print("[main] Skipping Vercel invite.")

    # --- Removing your own access: GitHub only, and only on explicit confirmation ---
    answer = input("Remove your own GitHub repo access now? (y/n): ").strip().lower()
    if answer == "y":
        try:
            own_username = github_api.get_authenticated_username()
            github_api.remove_collaborator(website["repo_full_name"], own_username)
            db.mark_website_transferred(lead_id)
            print(f"[main] GitHub access removed. Website for lead {lead_id} marked as transferred.")
        except Exception as exc:  # noqa: BLE001
            print(f"[main] Failed to remove own GitHub access: {exc}")
    else:
        print("[main] Leaving GitHub access as-is (not marked transferred).")

    print(
        "[main] NOTE: Vercel access is never removed automatically. Your Vercel team "
        "membership is typically shared across every client's project, not scoped to "
        "just this one, so revoking it here could lock you out of unrelated projects "
        "too. Remove Vercel access manually via the dashboard for this specific "
        "project if you want to fully hand it over."
    )


def _handle_command(line: str) -> None:
    parts = line.strip().split()
    if not parts:
        return
    cmd = parts[0].lower()

    if cmd == "takeover" and len(parts) == 2 and parts[1].isdigit():
        sales_agent.begin_takeover(int(parts[1]))
        print(f"[main] Lead {parts[1]} is now under manual takeover. Automation paused for this lead.")
    elif cmd == "payment" and len(parts) == 3 and parts[1] == "ready" and parts[2].isdigit():
        _handle_payment_ready(int(parts[2]))
    elif cmd == "transfer" and len(parts) == 2 and parts[1].isdigit():
        _handle_transfer(int(parts[1]))
    elif cmd == "status":
        _print_status()
    elif cmd == "pause":
        _set_paused(True)
        print("[main] Pipeline paused. Type 'resume' to continue.")
    elif cmd == "resume":
        _set_paused(False)
        print("[main] Pipeline resumed.")
    elif cmd == "help":
        print(__doc__)
    elif cmd in ("quit", "exit"):
        print("[main] Shutting down...")
        _shutdown_event.set()
    else:
        print(f"[main] Unknown command: {line!r}. Type 'help' for the command list.")


def _command_listener() -> None:
    for line in sys.stdin:
        if _shutdown_event.is_set():
            return
        try:
            _handle_command(line)
        except Exception as exc:  # noqa: BLE001 - a bad command must not kill the listener
            print(f"[main] Error handling command: {exc}")


def main() -> None:
    config.validate()
    db.init_db()
    print(f"[main] Pipeline starting. DB: {config.DB_PATH}")
    print("[main] Type 'help' for the operator command list.")

    listener = threading.Thread(target=_command_listener, daemon=True)
    listener.start()

    try:
        while not _shutdown_event.is_set():
            if _is_paused():
                time.sleep(config.MAIN_LOOP_SLEEP_SECONDS)
                continue
            try:
                _run_cycle()
            except Exception as exc:  # noqa: BLE001 - one bad cycle must not kill the process
                print(f"[main] Cycle failed: {exc}")
            time.sleep(config.MAIN_LOOP_SLEEP_SECONDS)
    except KeyboardInterrupt:
        print("\n[main] KeyboardInterrupt received, shutting down gracefully...")
    finally:
        _shutdown_event.set()
        print("[main] Pipeline stopped.")


if __name__ == "__main__":
    main()
