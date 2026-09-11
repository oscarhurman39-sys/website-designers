"""Read-only agent control board for the website sales pipeline.

Usage:

    python run.py control
    python run.py control --all
    python run.py control --json

The command assigns every open lead state to one StarNet room owner, names
the next action, and raises stale/inconsistent work to the top. It never
changes a lead, sends mail, deploys a preview, or touches a safety flag.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402
from utils import db  # noqa: E402


OWNER_BY_STATUS = {
    "new": ("PROMO-MARKETER", "marketer"),
    "researched": ("AGENCY-DESIGNER", "webdesigner"),
    "designed": ("PROMO-MARKETER", "marketer"),
    "emailed": ("PROMO-MARKETER", "marketer"),
    "replied": ("AGENCY-NEGOTIATOR", "negotiator"),
    "negotiating": ("AGENCY-NEGOTIATOR", "negotiator"),
    "payment_sent": ("AGENCY-NEGOTIATOR", "negotiator"),
    "won": ("FINN", "hello-3"),
    "lost": ("PROMO-MARKETER", "marketer"),
    "bounced": ("PROMO-MARKETER", "marketer"),
    "unsubscribed": ("PROMO-MARKETER", "marketer"),
}

# These are operating SLAs, not send timers. They only make neglected work
# visible; the command never acts on a lead or bypasses pipeline gates.
STAGE_SLA_HOURS = {
    "new": 24,
    "researched": 24,
    "designed": 24,
    "replied": 4,
    "negotiating": 24,
    "payment_sent": 24,
    "won": 24,
    "bounced": 24,
}

SEVERITY_ORDER = {
    "blocked": 0,
    "action_due": 1,
    "stalled": 2,
    "watch": 3,
    "on_track": 4,
    "complete": 5,
}
TERMINAL_STATUSES = {"lost", "unsubscribed"}


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_hours(value: Optional[str], now: datetime) -> Optional[float]:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 3600)


def _fmt_age(hours: Optional[float]) -> str:
    if hours is None:
        return "unknown"
    if hours < 1:
        return f"{max(1, round(hours * 60))}m"
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


def _cooldown_until(row: dict[str, Any]) -> Optional[datetime]:
    days = int(getattr(config, "OUTREACH_COOLDOWN_DAYS", 0) or 0)
    if row["status"] != "designed" or days <= 0:
        return None
    last = db.last_cold_email_at(row.get("niche") or "", row.get("location"), days)
    parsed = _parse_utc(last)
    return parsed + timedelta(days=days) if parsed else None


def classify(row: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    """Turn one durable lead row into an accountable next-action record."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = row.get("status") or "unknown"
    owner, agent_id = OWNER_BY_STATUS.get(status, ("UNASSIGNED", ""))
    age = _age_hours(row.get("state_changed_at"), now)
    severity = "on_track"
    next_action = "Review unknown pipeline status"
    reason = "status has no control rule"

    if status == "new":
        next_action = "Research the business and verify a usable contact route"
        reason = "waiting for lead research"
    elif status == "researched":
        next_action = "Build and validate the preview at desktop and mobile widths"
        reason = "research is ready for design"
    elif status == "designed":
        cooldown = _cooldown_until(row)
        if cooldown and cooldown > now:
            severity = "watch"
            next_action = f"Hold until outreach cooldown ends at {cooldown.strftime('%Y-%m-%d %H:%M UTC')}"
            reason = "same niche and town was contacted recently"
        else:
            next_action = "Review the preview and release it to the next permitted send slot"
            reason = "preview is waiting in the outbound queue"
    elif status == "emailed":
        inbound = _parse_utc(row.get("last_inbound_at"))
        if inbound:
            severity = "blocked"
            next_action = "Process the recorded reply; this lead should no longer be parked as emailed"
            reason = "inbound mail exists without a state handoff"
        elif row.get("follow_up_sent_at"):
            follow_age = _age_hours(row.get("follow_up_sent_at"), now)
            wait_hours = int(getattr(config, "FOLLOW_UP_AFTER_DAYS", 3) or 3) * 24
            if follow_age is not None and follow_age >= wait_hours:
                severity = "stalled"
                next_action = "Close the silent outreach loop and record it for campaign learning"
                reason = "no reply after the one permitted follow-up"
            else:
                severity = "watch"
                next_action = "Wait for a reply; the single follow-up has been attempted"
                reason = "follow-up response window is still open"
        else:
            initial_age = _age_hours(row.get("first_outbound_at") or row.get("state_changed_at"), now)
            due_after = int(getattr(config, "FOLLOW_UP_AFTER_DAYS", 3) or 3) * 24
            if bool(getattr(config, "FOLLOW_UP_ENABLED", True)) and initial_age is not None and initial_age >= due_after:
                severity = "action_due"
                next_action = "Send the one permitted follow-up in the next safe send window"
                reason = f"no reply after {int(due_after / 24)} days"
            else:
                severity = "watch"
                next_action = "Wait for a reply or the follow-up due date"
                reason = "initial outreach is inside its response window"
    elif status == "replied":
        severity = "action_due"
        next_action = "Route the legacy reply into guarded negotiation"
        reason = "replied is a legacy holding state"
    elif status == "negotiating":
        inbound = _parse_utc(row.get("last_inbound_at"))
        outbound = _parse_utc(row.get("last_outbound_at"))
        if inbound and (not outbound or inbound > outbound):
            severity = "action_due"
            next_action = "Respond inside the price and round limits"
            reason = "the prospect sent the latest message"
        else:
            severity = "watch"
            next_action = "Wait for the prospect; escalate only when a guardrail requires it"
            reason = "our reply is the latest message"
    elif status == "payment_sent":
        if not row.get("checkout_url"):
            severity = "blocked"
            next_action = "Repair the payment state; no persisted checkout URL exists"
            reason = "payment_sent is inconsistent without a checkout URL"
        elif row.get("unsubscribed"):
            severity = "action_due"
            next_action = "Human review: preserve service obligations without further marketing"
            reason = "a payment-stage lead is suppressed"
        else:
            severity = "watch"
            next_action = "Reconcile Stripe and watch for payment or a buyer question"
            reason = "checkout is outstanding"
    elif status == "won":
        if not row.get("website_id"):
            severity = "blocked"
            next_action = "Restore or locate the paid client's website record"
            reason = "paid lead has no website to hand over"
        elif row.get("transferred"):
            severity = "complete"
            next_action = "No acquisition action; move the client into the service/retention system"
            reason = "payment and handover are complete"
        elif not row.get("github_username"):
            severity = "action_due"
            next_action = "Collect the client's GitHub username and complete handover"
            reason = "paid client is waiting for ownership transfer"
        else:
            next_action = "Complete and verify GitHub/Vercel handover"
            reason = "payment is confirmed but transfer is incomplete"
    elif status == "bounced":
        severity = "action_due"
        next_action = "Review the address once; keep it suppressed unless a verified replacement exists"
        reason = "delivery failed"
    elif status == "lost":
        severity = "complete"
        next_action = "No further automated contact; retain the outcome for campaign learning"
        reason = "lead is closed lost"
    elif status == "unsubscribed":
        severity = "complete"
        next_action = "Keep suppressed; never re-enter automated outreach"
        reason = "lead opted out"

    sla = STAGE_SLA_HOURS.get(status)
    if severity == "on_track" and sla is not None and (age is None or age >= sla):
        severity = "stalled"
        reason = "stage timestamp is missing" if age is None else f"stage SLA of {sla}h exceeded"

    return {
        "lead_id": row.get("id"),
        "business": row.get("business_name") or "(unnamed)",
        "status": status,
        "owner": owner,
        "agent_id": agent_id,
        "severity": severity,
        "stage_age_hours": round(age, 2) if age is not None else None,
        "stage_age": _fmt_age(age),
        "next_action": next_action,
        "reason": reason,
    }


def build_report(include_all: bool = False, now: Optional[datetime] = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    items = [classify(row, now) for row in db.control_queue()]
    if not include_all:
        items = [
            item
            for item in items
            if item["status"] not in TERMINAL_STATUSES and item["severity"] != "complete"
        ]
    items.sort(
        key=lambda item: (
            SEVERITY_ORDER.get(item["severity"], 99),
            -(item["stage_age_hours"] if item["stage_age_hours"] is not None else float("inf")),
            item["lead_id"] or 0,
        )
    )
    counts = Counter(item["severity"] for item in items)
    owners = Counter(item["owner"] for item in items)
    return {
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "read_only": True,
        "summary": {"total": len(items), **dict(sorted(counts.items()))},
        "owners": dict(sorted(owners.items())),
        "leads": items,
    }


def _print_text(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("Website Designers agent control board (read-only)")
    print("=" * 49)
    print(
        "Open: {total} | blocked: {blocked} | action due: {action_due} | "
        "stalled: {stalled} | watch: {watch}".format(
            total=summary.get("total", 0),
            blocked=summary.get("blocked", 0),
            action_due=summary.get("action_due", 0),
            stalled=summary.get("stalled", 0),
            watch=summary.get("watch", 0),
        )
    )
    if not report["leads"]:
        print("No open lead work.")
        return
    for item in report["leads"]:
        print(
            f"[{item['severity'].upper():10}] #{item['lead_id']} {item['business']} | "
            f"{item['status']} {item['stage_age']} | {item['owner']} ({item['agent_id']})"
        )
        print(f"             NEXT: {item['next_action']}")
        print(f"             WHY:  {item['reason']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only agent ownership and stalled-work report.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="include closed-lost, unsubscribed and completed handovers",
    )
    parser.add_argument("--json", action="store_true", help="emit stable machine-readable JSON")
    args = parser.parse_args(argv)
    report = build_report(include_all=args.all)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
