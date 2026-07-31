"""Scheduled maintenance pass for SOLD (status='won') client sites.

Re-audits every live client site, alerts on regressions (site down, or a
readiness-score drop), and can generate a plain-text monthly report from
real, already-tracked data (audit score/uptime history + click counts on
the tracked preview/share link). It deliberately does NOT claim visitor or
enquiry analytics -- this pipeline has no Google-Analytics-style pageview
tracking or contact-form-submission tracking, so a report claiming those
numbers would be fabricated. See monthly_report()'s own note.

Two modes:

    python maintenance.py check
        Re-audits every 'won' lead's live site (utils/site_audit.py's
        audit_readiness, reused from the QA/readiness scanner), persists
        the result (site_audits, audited_target='live_client_site'), and
        alerts (console + optional Slack) on any regression. One-shot --
        designed to be run periodically via cron (see crontab.example),
        not as a long-running loop.

    python maintenance.py report <lead_id>
        Prints a plain-text monthly report for one lead.

Run from inside the `pipeline/` directory, same as main.py/webhook_server.py.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

import config
from utils import db, site_audit, url_safety

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
except ImportError:  # pragma: no cover - optional dependency
    WebClient = None  # type: ignore[assignment,misc]
    SlackApiError = Exception  # type: ignore[assignment,misc]

# A readiness_pct drop of at least this many points since the last check
# is treated as a regression worth alerting on (a couple of points of
# noise between runs -- e.g. a flaky link check -- shouldn't page anyone).
REGRESSION_READINESS_DROP = 10
REPORT_WINDOW_DAYS = 30
_CHECK_TIMEOUT_SECONDS = 15


def _console_alert(text: str) -> None:
    banner = "!" * 70
    print(f"\n{banner}\n{text}\n{banner}\n")


def _slack_alert(text: str) -> None:
    """Best-effort Slack notification -- exact same soft-import pattern as
    agents/sales_agent.py's positive-reply alert, reused rather than
    duplicated with different behavior."""
    if not (config.SLACK_BOT_TOKEN and WebClient):
        return
    try:
        WebClient(token=config.SLACK_BOT_TOKEN).chat_postMessage(channel=config.SLACK_ALERT_CHANNEL, text=text)
    except SlackApiError as exc:  # noqa: BLE001 - a Slack failure must not break the maintenance run
        print(f"[maintenance] Slack alert failed: {exc}")


def _alert(lead: dict, message: str) -> None:
    full_message = f"{lead['business_name']} (lead {lead['id']}): {message}"
    _console_alert(full_message)
    _slack_alert(f":warning: {full_message}")


def _down_result(reason: str) -> dict[str, Any]:
    return {"checks": {}, "issues": [f"Site unreachable: {reason}"], "readiness_pct": 0,
            "broken_links": [], "contrast_failures": []}


def check_site(lead: dict) -> Optional[dict[str, Any]]:
    """Re-audit one lead's live site; alert on a regression since the last
    check. Returns the new audit result, or None if the lead has no
    deployed website to check."""
    website = db.get_website_by_lead(lead["id"])
    if not website or not website.get("preview_url"):
        return None
    url = website["preview_url"]
    previous = db.get_latest_site_audit(lead["id"], "live_client_site")

    try:
        url_safety.validate_public_url(url, timeout=_CHECK_TIMEOUT_SECONDS)
    except RuntimeError as exc:
        result = _down_result(str(exc))
        db.insert_site_audit(lead["id"], "live_client_site", result, url=url)
        _alert(lead, f"site appears DOWN -- {exc}")
        return result

    try:
        resp = requests.get(url, timeout=_CHECK_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        result = _down_result(str(exc))
        db.insert_site_audit(lead["id"], "live_client_site", result, url=url)
        _alert(lead, f"site appears DOWN -- {exc}")
        return result

    # No link-check crawl here -- this runs unattended and often, so keep
    # it to one request per site; the link/contrast checks still ran at
    # deploy/publish time (design_agent.py, editor_agent.py).
    result = site_audit.audit_readiness(resp.text, url, check_links=False)
    db.insert_site_audit(lead["id"], "live_client_site", result, url=url)

    if previous and previous.get("readiness_pct") is not None:
        drop = previous["readiness_pct"] - result["readiness_pct"]
        if drop >= REGRESSION_READINESS_DROP:
            top_issues = "; ".join(result["issues"][:3]) or "no specific issue text"
            _alert(
                lead,
                f"readiness dropped {previous['readiness_pct']}% -> {result['readiness_pct']}% ({top_issues})",
            )
    return result


def run_check() -> None:
    """Re-audit every SOLD lead's live site. One-shot -- see module docstring."""
    leads = [lead for lead in db.list_all_leads() if lead["status"] == "won"]
    print(f"[maintenance] Checking {len(leads)} live client site(s)...")
    for lead in leads:
        try:
            check_site(lead)
        except Exception as exc:  # noqa: BLE001 - one bad site must not kill the batch
            print(f"[maintenance] Check failed for lead {lead['id']}: {exc}")
    print("[maintenance] Done.")


def _parse_sqlite_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def monthly_report(lead_id: int, *, window_days: int = REPORT_WINDOW_DAYS) -> str:
    """Plain-text report built ONLY from data this pipeline actually
    tracks: readiness-audit history/uptime over the window (site_audits)
    and clicks on the tracked preview/share link (clicks). No fabricated
    visitor counts, enquiry counts, or SEO rankings -- see the note in the
    output itself."""
    lead = db.get_lead(lead_id)
    if lead is None:
        raise ValueError(f"No lead {lead_id}")

    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    since_naive = since.replace(tzinfo=None)
    window_audits = [
        a for a in db.list_site_audits(lead_id, "live_client_site")
        if _parse_sqlite_timestamp(a["created_at"]) >= since_naive
    ]
    clicks = db.count_clicks_since(lead_id, since)

    lines = [f"Monthly website report -- {lead['business_name']}", f"(last {window_days} days)", ""]

    if not window_audits:
        lines.append("No automated checks have run yet this period.")
    else:
        latest = window_audits[-1]
        up_checks = sum(1 for a in window_audits if (a["readiness_pct"] or 0) > 0)
        uptime_pct = round(100 * up_checks / len(window_audits))
        latest_result = json.loads(latest["result"])
        performed, total = latest_result.get("checks_performed"), latest_result.get("checks_total")
        scope_note = f" ({performed}/{total} checks performed" + (
            f", {len(latest_result['checks_skipped'])} skipped)" if latest_result.get("checks_skipped") else ")"
        ) if performed is not None else ""
        lines.append(f"Automated checks run: {len(window_audits)}")
        lines.append(f"Uptime: {uptime_pct}% of checks found your site live and responding.")
        lines.append(f"Current basic publishing checks: {latest['readiness_pct']}%{scope_note}.")
        lines.append(
            "(Not a WCAG compliance certification or a complete link audit -- see "
            "utils/site_audit.py's audit_readiness() for exactly what's covered.)"
        )
        issues = latest_result.get("issues", [])
        if issues:
            lines.append(f"{len(issues)} issue(s) currently flagged:")
            lines.extend(f"  - {issue}" for issue in issues)
        else:
            lines.append("No issues currently flagged.")

    lines.append("")
    lines.append(f"Your preview/share link was opened {clicks} time(s) in this period.")
    lines.append("")
    lines.append(
        "Note: this pipeline does not integrate real visitor or enquiry "
        "analytics (e.g. Google Analytics, a contact-form backend) -- the "
        "click count above is only the tracked preview/share link, not "
        "overall site traffic."
    )
    return "\n".join(lines)


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Re-audit every live client site and alert on regressions.")
    report_parser = subparsers.add_parser("report", help="Print a monthly report for one lead.")
    report_parser.add_argument("lead_id", type=int)
    args = parser.parse_args()

    db.init_db()
    if args.command == "check":
        run_check()
    elif args.command == "report":
        print(monthly_report(args.lead_id))


if __name__ == "__main__":
    _main()
