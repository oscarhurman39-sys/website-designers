"""Orchestrator loop for the cold-email sales pipeline.

Run with: `python main.py`  (from inside the `pipeline/` directory, with the
virtualenv active and `.env` populated).

Each iteration:
  1. Ingests any new CSVs dropped into leads_inbox/ (LeadAgent) and -- if
     SOURCING_ENABLED, at most once an hour -- sources new leads from Google
     Places (SourcingAgent).
  2. Runs LeadAgent research on 'new' leads.
  3. Runs DesignAgent on 'researched' leads.
  4. Sends at most one rate-limited cold email via SalesAgent on a 'designed' lead.
  5. Polls the inbox and classifies replies via SalesAgent. Positive replies
     route straight into the autonomous negotiation agent, which counters
     within a code-enforced price band and, on a close, creates + emails a
     Stripe Checkout link itself (see agents/sales_agent.py).
  6. Finalizes 'won' leads: once Stripe's webhook marks a lead paid, this
     loop automatically fires the GitHub repo invite (once the client's
     GitHub username is known -- requested by email automatically) and the
     Vercel project invite, then emails a handover confirmation.
  7. At most once an hour, tears down preview sites past PREVIEW_TTL_DAYS
     whose lead never replied (utils/teardown.py).
Then sleeps MAIN_LOOP_SLEEP_SECONDS and repeats, until Ctrl-C.

A second thread reads operator commands from stdin. All remaining commands
are optional conveniences -- nothing in the pipeline waits on them:
  transfer <lead_id>        - manual handover override (e.g. no GitHub
                              username reply, or to remove your own repo access)
  status                    - print a lead-count-by-status summary
  pause / resume            - pause/resume the automated loop
  help                      - list commands
  quit                      - shut down

Run exactly ONE orchestrator instance. The loop's inbox dedup, negotiation
round counting, and handover are guarded against the separate webhook process
(which only advances status via guarded writes), but two concurrent main.py
loops polling the same inbox/DB are not coordinated and could double-process a
reply. scheduler.py is PID-guarded to enforce this; if you run main.py by hand,
don't start a second copy against the same database.
"""
from __future__ import annotations

import shutil
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import config
from agents import design_agent, lead_agent, sales_agent, sourcing_agent
from utils import db, github_api, stripe_utils, teardown, vercel_api

PIPELINE_DIR = Path(__file__).resolve().parent
LEADS_INBOX = PIPELINE_DIR / "leads_inbox"
LEADS_PROCESSED = PIPELINE_DIR / "leads_processed"
PAUSE_FLAG = PIPELINE_DIR / ".paused"  # dashboard.py toggles this file to pause/resume

_shutdown_event = threading.Event()
# time.monotonic() of the last sourcing attempt (None = never in this process).
_last_sourcing_at: float | None = None

# In-memory per-lead backoff for failed automated handover attempts (e.g. the
# customer sent a typo'd GitHub username, so the invite 404s). Without this,
# the 60s main loop would hammer the GitHub API and re-alert every cycle.
# Process-local on purpose: a restart retrying once immediately is fine.
_HANDOVER_RETRY_DELAY = timedelta(hours=1)
_handover_next_attempt: dict[int, datetime] = {}

# Preview teardown only needs to run hourly -- the cycle is every ~60s and
# the TTL is measured in days, so running it every cycle would just hammer
# the Vercel/GitHub APIs (and their rate limits) for no gain. Optional so
# the very first cycle after startup always runs it (time.monotonic() can
# legitimately be < 1h right after boot).
_TEARDOWN_INTERVAL_SECONDS = 3600
_last_teardown_at: Optional[float] = None


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


def _finalize_won_leads() -> None:
    """Automated post-payment handover. For each paid ('won') lead whose
    website hasn't been transferred yet:

      - If we don't know the client's GitHub username, email them for it
        (send_github_username_request is idempotent -- at most one ask).
        The reply is parsed and stored by sales_agent's inbound handler.
      - Once the username is known: invite them to the GitHub repo (admin),
        invite their email to the Vercel project, mark the website
        transferred, and email a handover confirmation.

    Our own GitHub/Vercel access is deliberately NOT removed automatically --
    that's destructive and stays behind the manual `transfer` command.
    """
    now = datetime.now(timezone.utc)
    for lead in db.list_leads_by_status("won"):
        website = db.get_website_by_lead(lead["id"])
        if website is None or website.get("transferred"):
            continue
        if _handover_next_attempt.get(lead["id"], datetime.min.replace(tzinfo=timezone.utc)) > now:
            continue

        username = (lead.get("github_username") or "").strip()
        if not username:
            # If the paid customer has unsubscribed we can't email them for the
            # username, and send_github_username_request would fail every cycle.
            # Escalate once and back off instead of silently looping.
            if db.is_unsubscribed(lead.get("contact_email") or ""):
                sales_agent.alert_needs_human(
                    lead,
                    "Paid lead has no GitHub username on file and has unsubscribed, so we can't "
                    "email them for it. Collect it another way and run 'transfer' manually.",
                )
                _handover_next_attempt[lead["id"]] = now + _HANDOVER_RETRY_DELAY
                continue
            if sales_agent.send_github_username_request(lead):
                print(f"[main] Asked lead {lead['id']} for their GitHub username.")
            continue

        try:
            github_api.invite_collaborator(website["repo_full_name"], username, permission="admin")
            print(f"[main] Invited {username} to {website['repo_full_name']}")
        except Exception as exc:  # noqa: BLE001
            print(f"[main] GitHub invite failed for lead {lead['id']} ({username!r}): {exc}")
            sales_agent.alert_needs_human(
                lead,
                f"Automated GitHub invite for username {username!r} failed ({exc}). "
                "Fix the username on the lead (or run 'transfer' manually); "
                "auto-retry in 1 hour.",
            )
            _handover_next_attempt[lead["id"]] = now + _HANDOVER_RETRY_DELAY
            continue

        vercel_email = lead.get("contact_email") or ""
        if vercel_email:
            try:
                project_raw_name = github_api.make_repo_name(lead["business_name"], lead["id"])
                vercel_api.invite_collaborator(project_raw_name, vercel_email)
                print(f"[main] Invited {vercel_email} to the Vercel project.")
            except Exception as exc:  # noqa: BLE001
                # Non-fatal by design: the GitHub invite is the handover that
                # matters most, and this Vercel call was never verified against
                # a live team account (see utils/vercel_api.py). Alert and move on.
                print(f"[main] Vercel invite failed for lead {lead['id']}: {exc}")
                sales_agent.alert_needs_human(
                    lead, f"Automated Vercel invite failed ({exc}); invite manually from the dashboard."
                )

        db.mark_website_transferred(lead["id"])
        sales_agent.send_handover_confirmation(lead, website["repo_full_name"])
        print(f"[main] Automated handover complete for lead {lead['id']}.")


def _maybe_source_leads() -> None:
    """Run SourcingAgent at most once per SOURCING_INTERVAL_SECONDS. The
    timestamp is taken *before* the call so a failing API isn't retried on
    every 60-second cycle; the agent itself never raises."""
    global _last_sourcing_at
    if not config.SOURCING_ENABLED:
        return
    now = time.monotonic()
    if _last_sourcing_at is not None and now - _last_sourcing_at < config.SOURCING_INTERVAL_SECONDS:
        return
    _last_sourcing_at = now
    inserted = sourcing_agent.source_leads()
    if inserted:
        print(f"[main] Sourced {inserted} new lead(s) from Google Places")


_STAGE_ALERT_AFTER = 3
_stage_failures: dict[str, int] = {}


def _stage(name: str, fn) -> None:
    """Run one cycle stage in isolation. Before this, one SMTP error in the
    send stage skipped reply polling and post-payment handover for the rest
    of the cycle, and the only trace was a console line. A stage that fails
    _STAGE_ALERT_AFTER cycles in a row raises a Slack alert once; the counter
    resets on the next success."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - one stage must not take the others down
        count = _stage_failures.get(name, 0) + 1
        _stage_failures[name] = count
        print(f"[main] Stage '{name}' failed ({count} in a row): {exc}")
        if count == _STAGE_ALERT_AFTER:
            sales_agent._slack_notify(
                f":rotating_light: *PIPELINE STAGE FAILING* -- '{name}' has failed "
                f"{count} cycles in a row. Latest: {exc}"
            )
        return
    _stage_failures.pop(name, None)


def _research_new_leads() -> None:
    for lead in db.list_leads_by_status("new"):
        lead_agent.research_lead(lead)


def _send_outreach() -> None:
    sent_lead_id = sales_agent.send_next_pending()
    if sent_lead_id:
        print(f"[main] Sent cold email to lead {sent_lead_id}")
    elif (followed := sales_agent.send_follow_up_if_due()):
        print(f"[main] Sent follow-up reminder to lead {followed}")


def _poll_replies() -> None:
    replies = sales_agent.check_inbox()
    if replies:
        print(f"[main] Processed {replies} inbound reply(ies)")


_RECONCILE_INTERVAL_SECONDS = 10 * 60
_last_reconcile_at: Optional[float] = None


def _maybe_reconcile_payments() -> None:
    """Stripe's webhook is the only thing that marks a lead paid, and it only
    arrives if the tunnel and webhook process are both up. Every ten minutes
    ask Stripe directly about every lead holding a checkout link, so a
    customer who paid while the webhook was deaf still gets their handover."""
    global _last_reconcile_at
    now = time.monotonic()
    if _last_reconcile_at is not None and now - _last_reconcile_at < _RECONCILE_INTERVAL_SECONDS:
        return
    _last_reconcile_at = now
    for lead in db.list_leads_by_status("payment_sent"):
        if not lead.get("checkout_url"):
            continue
        paid = stripe_utils.find_paid_session(lead["id"])
        if paid and sales_agent.record_payment(lead["id"], paid["id"], paid["amount_total"], source="reconciliation"):
            print(f"[main] Reconciliation found a settled payment for lead {lead['id']} the webhook missed.")


_BACKUP_INTERVAL_SECONDS = 24 * 60 * 60
_last_backup_at: Optional[float] = None


def _maybe_backup_db() -> None:
    global _last_backup_at
    now = time.monotonic()
    if _last_backup_at is not None and now - _last_backup_at < _BACKUP_INTERVAL_SECONDS:
        return
    _last_backup_at = now
    print(f"[main] Database backed up to {db.backup_database()}")


def _run_cycle() -> None:
    # Money-side stages run before the send so a mail failure can no longer
    # starve reply handling or handover, and each is isolated regardless.
    _stage("inbox-csv", _process_inbox_csvs)
    _stage("sourcing", _maybe_source_leads)
    _stage("research", _research_new_leads)
    _stage("design", design_agent.run)
    _stage("replies", _poll_replies)
    _stage("reconcile-payments", _maybe_reconcile_payments)
    _stage("handover", _finalize_won_leads)
    _stage("send", _send_outreach)
    _stage("expire-previews", _maybe_expire_previews)
    _stage("backup", _maybe_backup_db)


def _maybe_expire_previews() -> None:
    """Run the preview teardown pass, but at most once per hour. The
    timestamp is taken before the run (not after a success) so a failing
    pass doesn't get retried every 60s either."""
    global _last_teardown_at
    if not config.PREVIEW_TEARDOWN_ENABLED:
        return
    now = time.monotonic()
    if _last_teardown_at is not None and now - _last_teardown_at < _TEARDOWN_INTERVAL_SECONDS:
        return
    _last_teardown_at = now
    try:
        removed = teardown.expire_previews()
    except Exception as exc:  # noqa: BLE001 - housekeeping must never kill the cycle
        print(f"[main] Preview teardown failed: {exc}")
        return
    if removed:
        print(f"[main] Tore down {removed} expired preview(s)")


def _print_status() -> None:
    leads = db.list_all_leads()
    counts: dict[str, int] = {}
    for lead in leads:
        counts[lead["status"]] = counts.get(lead["status"], 0) + 1
    print("[main] Lead status summary:")
    for status, count in sorted(counts.items()):
        print(f"    {status:15s} {count}")
    print(f"    {'TOTAL':15s} {len(leads)}")


def _handle_transfer(lead_id: int) -> None:
    """Manual handover override. The normal path is fully automated (see
    _finalize_won_leads); use this only when the client never replies with a
    usable GitHub username, or to remove your own repo access afterwards."""
    lead = db.get_lead(lead_id)
    website = db.get_website_by_lead(lead_id)
    if lead is None or website is None:
        print(f"[main] No lead/website found for {lead_id}")
        return
    if lead["status"] != "won":
        print(f"[main] Lead {lead_id} is not marked 'won' yet (status: {lead['status']}). Aborting.")
        return

    # --- GitHub: invite the client as a collaborator on their repo ---
    stored_username = (lead.get("github_username") or "").strip()
    prompt_suffix = f" [{stored_username}]" if stored_username else ""
    github_username = (
        input(f"GitHub username to invite for lead {lead_id}{prompt_suffix} (blank to skip): ").strip()
        or stored_username
    )
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

    if cmd == "transfer" and len(parts) == 2 and parts[1].isdigit():
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
    print(f"[main] Autonomous negotiation band: {config.CURRENCY_SYMBOL}{config.NEGOTIATION_FLOOR:,}"
          f"-{config.CURRENCY_SYMBOL}{config.NEGOTIATION_CEILING:,} ({config.CURRENCY.upper()}, "
          f"max {config.MAX_NEGOTIATION_ROUNDS} auto-replies/lead)")
    print("[main] Type 'help' for the operator command list.")

    # One-time bridge: pick up leads left in the legacy 'replied' status by the
    # old human-takeover system and route them into autonomous negotiation.
    # Idempotent (a handled lead leaves 'replied'), so it's safe every startup.
    try:
        sales_agent.catch_up_pending_negotiations()
    except Exception as exc:  # noqa: BLE001 - a catch-up hiccup must not stop the pipeline
        print(f"[main] Startup negotiation catch-up failed: {exc}")

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
