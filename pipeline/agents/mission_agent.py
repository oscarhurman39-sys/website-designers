"""MissionAgent: the pipeline's boss. One number is the mission --
MISSION_DAILY_SENDS cold emails a day -- and every few minutes this agent
measures the whole line against it, names whichever stage is holding the
number down, and fixes what it is allowed to fix itself.

    python pipeline/mission.py        # the same assessment, printed

What it does on its own: retakes preview screenshots that have gone missing
under queued leads, and tells Slack once a day where the line stands and
what the worst blocker is. What it never does: change a send cap, a mailbox
or a spending limit. Those stay the operator's, in .env; the report says
exactly which setting to change and to what.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
from utils import db, mailboxes, screenshot, tracker

_RUN_INTERVAL_SECONDS = 10 * 60
_HEAL_PER_RUN = 3          # screenshots retaken per pass; each one opens a browser
_YIELD_WINDOW_DAYS = 14
_DEFAULT_YIELD = 0.25      # sourced lead -> sendable preview, until there is history
_PER_MAILBOX_SAFE = 25     # cold emails a day one mailbox carries before reputation suffers
_VERCEL_DEPLOYS_PER_DAY = 100
_VERCEL_PROJECTS = 200

_last_run_at: Optional[float] = None
_last_brief_day: Optional[str] = None


@dataclass(frozen=True)
class Blocker:
    stage: str
    problem: str
    fix: str
    limit: Optional[int] = None  # cold emails a day this stage allows; None = stops the line


def _count(sql: str, params: tuple = ()) -> int:
    with db.get_connection() as conn:
        return conn.execute(sql, params).fetchone()[0] or 0


def _cold_sent_since(since: datetime) -> int:
    return _count(
        "SELECT COUNT(*) FROM email_threads WHERE direction = 'outbound' "
        "AND COALESCE(classification, '') = '' AND timestamp >= ?",
        (since.strftime("%Y-%m-%d %H:%M:%S"),),
    )


def sourcing_yield() -> float:
    """Share of recently sourced leads that became a preview worth sending."""
    since = (datetime.now(timezone.utc) - timedelta(days=_YIELD_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    sourced = _count("SELECT COUNT(*) FROM leads WHERE place_id IS NOT NULL AND created_at >= ?", (since,))
    if sourced < 20:
        return _DEFAULT_YIELD
    built = _count(
        "SELECT COUNT(*) FROM leads l WHERE l.place_id IS NOT NULL AND l.created_at >= ? "
        "AND EXISTS (SELECT 1 FROM websites w WHERE w.lead_id = l.id)",
        (since,),
    )
    return max(built / sourced, 0.02)


def _queued_without_screenshot() -> list[dict]:
    return [lead for lead in db.list_leads_by_status("designed")
            if screenshot.get_cached_screenshot(lead["id"]) is None]


def assess() -> dict:
    """Everything the mission depends on, measured now. `blockers` is sorted
    worst first: line-stoppers, then stages by how few sends they allow."""
    target = config.MISSION_DAILY_SENDS
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    cold_today = _cold_sent_since(midnight)
    cold_week = _cold_sent_since(week_ago)
    queued = db.count_leads_by_status("designed")
    yield_rate = sourcing_yield()
    follow_ups = config.FOLLOW_UP_ENABLED
    blockers: list[Blocker] = []

    def add(stage: str, problem: str, fix: str, limit: Optional[int] = None) -> None:
        blockers.append(Blocker(stage, problem, fix, limit))

    if not config.ENABLE_LIVE_SEND:
        add("send", "live sending is off, so every send is a dry run", "set ENABLE_LIVE_SEND=true")
    elif not tracker.public_endpoint_up():
        add("send", f"{config.PUBLIC_BASE_URL}/health is not answering, so sending is paused "
            "(unsubscribe links would be dead)", "start the webhook server and tunnel (start_public.bat)")

    # Follow-ups share the daily cap, and nearly every cold email earns one.
    cap_needed = target * 2 if follow_ups else target
    cold_under_cap = config.EMAIL_MAX_PER_DAY // 2 if follow_ups else config.EMAIL_MAX_PER_DAY
    if cold_under_cap < target:
        add("send cap", f"EMAIL_MAX_PER_DAY={config.EMAIL_MAX_PER_DAY}"
            + (" and follow-ups share it" if follow_ups else ""),
            f"set EMAIL_MAX_PER_DAY={cap_needed}" + (" (or FOLLOW_UP_ENABLED=false and "
            f"EMAIL_MAX_PER_DAY={target})" if follow_ups else ""), cold_under_cap)

    accounts = len(config.EMAIL_ACCOUNTS)
    mailbox_total = accounts * config.EMAIL_MAX_PER_DAY_PER_ACCOUNT
    mailbox_cold = mailbox_total // 2 if follow_ups else mailbox_total
    if mailbox_cold < target:
        add("mailboxes", f"{accounts} mailbox(es) x EMAIL_MAX_PER_DAY_PER_ACCOUNT="
            f"{config.EMAIL_MAX_PER_DAY_PER_ACCOUNT} = {mailbox_total} sends a day",
            f"add mailboxes to EMAIL_ACCOUNTS ({-(-cap_needed // _PER_MAILBOX_SAFE)} at "
            f"{_PER_MAILBOX_SAFE} a day each carries the mission) or raise the per-mailbox cap", mailbox_cold)
    elif accounts and cap_needed / accounts > _PER_MAILBOX_SAFE:
        add("mailboxes", f"the mission puts {cap_needed // accounts} sends a day on each mailbox; "
            f"above about {_PER_MAILBOX_SAFE} a mailbox starts landing in spam or gets suspended",
            f"spread it over {-(-cap_needed // _PER_MAILBOX_SAFE)} mailboxes in EMAIL_ACCOUNTS", target)

    window_hours = max(config.SEND_WINDOW_END_HOUR - config.SEND_WINDOW_START_HOUR, 0)
    avg_gap = (config.EMAIL_MIN_DELAY_SECONDS + config.EMAIL_MAX_DELAY_SECONDS) / 2
    paced = int(window_hours * min(config.EMAIL_MAX_PER_HOUR, 3600 / avg_gap))
    paced_cold = paced // 2 if follow_ups else paced
    if paced_cold < target:
        add("pacing", f"a {window_hours}-hour send window at one email every "
            f"{int(avg_gap // 60)}-ish minutes is {paced} sends a day",
            "widen SEND_WINDOW_START_HOUR/SEND_WINDOW_END_HOUR", paced_cold)

    if not config.SOURCING_ENABLED:
        add("sourcing", "SOURCING_ENABLED is off; only hand-fed leads enter the line",
            "set SOURCING_ENABLED=true", 0 if not queued else None)
    else:
        fed = int(config.SOURCING_DAILY_LIMIT * yield_rate)
        if fed < target:
            gate_on = config.SITE_SCORE_GOOD_AT > 0
            if gate_on and yield_rate < 0.1:
                # Raising the limit alone would mean thousands of paid lookups a day.
                fix = (f"set SITE_SCORE_GOOD_AT=0 first (the healthy-site gate at {config.SITE_SCORE_GOOD_AT} "
                       "is parking most leads), then size SOURCING_DAILY_LIMIT from the new yield")
            else:
                fix = f"set SOURCING_DAILY_LIMIT={-(-target // yield_rate):.0f}" + (
                    f", or SITE_SCORE_GOOD_AT=0 to stop parking businesses whose site already "
                    f"scores {config.SITE_SCORE_GOOD_AT}+" if gate_on else "")
            add("sourcing", f"SOURCING_DAILY_LIMIT={config.SOURCING_DAILY_LIMIT} and about "
                f"{yield_rate:.0%} of sourced leads become a sendable preview", fix, fed)

    buckets = len(config.SOURCING_NICHES) * len(config.SOURCING_LOCATIONS)
    if config.OUTREACH_COOLDOWN_DAYS > 0 and buckets:
        cooled = buckets // config.OUTREACH_COOLDOWN_DAYS
        if cooled < target:
            add("cooldown", f"one cold email per niche+town every {config.OUTREACH_COOLDOWN_DAYS} days "
                f"across {buckets} niche+town pairs", "lower OUTREACH_COOLDOWN_DAYS or add towns to "
                "SOURCING_LOCATIONS", cooled)

    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    failed_builds = _count(
        "SELECT COUNT(*) FROM state_history WHERE notes LIKE 'Design failed:%' AND timestamp >= ?", (day_ago,))
    built = _count("SELECT COUNT(*) FROM websites WHERE created_at >= ?", (day_ago,))
    if failed_builds and not built:
        with db.get_connection() as conn:
            latest = conn.execute(
                "SELECT notes FROM state_history WHERE notes LIKE 'Design failed:%' ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        add("design", f"{failed_builds} preview build(s) failed in the last day and none succeeded; "
            f"latest: {latest[len('Design failed: '):][:160]}",
            "fix the hosting error (a 403 from api.vercel.com means VERCEL_TOKEN has lost access "
            "to the team: create a new token scoped to it)")

    if config.design_backlog_cap() < target:
        add("design", f"at most {config.design_backlog_cap()} previews are kept waiting to send",
            f"set DESIGN_BACKLOG_MAX={target * 2}", config.design_backlog_cap())

    live_projects = target * config.PREVIEW_TTL_DAYS
    if target >= _VERCEL_DEPLOYS_PER_DAY or live_projects > _VERCEL_PROJECTS:
        add("preview hosting", f"{target} previews a day kept {config.PREVIEW_TTL_DAYS} days is "
            f"{live_projects} live Vercel projects; the Hobby plan stops at {_VERCEL_PROJECTS} "
            f"(and {_VERCEL_DEPLOYS_PER_DAY} deployments a day)",
            "move previews off Vercel Hobby (Vercel Pro, or another host)",
            _VERCEL_PROJECTS // max(config.PREVIEW_TTL_DAYS, 1))

    missing = _queued_without_screenshot()
    blockers.sort(key=lambda b: (b.limit is not None, b.limit if b.limit is not None else 0))
    return {
        "target": target,
        "cold_today": cold_today,
        "cold_last_7_days": cold_week,
        "sends_today": db.emails_sent_today(),
        "any_mailbox_free": mailboxes.any_account_under_cap(),
        "queued": queued,
        "researched": db.count_leads_by_status("researched"),
        "new": db.count_leads_by_status("new"),
        "sourced_today": db.count_sourced_leads_today(),
        "sourcing_yield": yield_rate,
        "queued_without_screenshot": len(missing),
        "blockers": blockers,
    }


def format_report(state: dict) -> str:
    target = state["target"]
    lines = [
        f"MISSION: {target} cold emails a day" if target else "MISSION: not set (MISSION_DAILY_SENDS=0)",
        f"  today: {state['cold_today']} cold sent ({state['sends_today']} sends incl. follow-ups); "
        f"last 7 days: {state['cold_last_7_days']} cold ({state['cold_last_7_days'] / 7:.1f} a day)",
        f"  line:  {state['new']} new -> {state['researched']} researched -> {state['queued']} built and "
        f"waiting; {state['sourced_today']} sourced today, {state['sourcing_yield']:.0%} of sourced leads "
        "become a preview",
    ]
    if state["queued_without_screenshot"]:
        lines.append(f"  {state['queued_without_screenshot']} waiting preview(s) have no screenshot; "
                     "retaking them a few at a time")
    if not target:
        return "\n".join(lines)
    if not state["blockers"]:
        lines.append("  nothing in the settings holds the mission down.")
    for i, blocker in enumerate(state["blockers"], 1):
        ceiling = "stops the line" if blocker.limit is None else f"allows about {blocker.limit} a day"
        lines.append(f"  {i}. {blocker.stage}: {blocker.problem} -- {ceiling}. Fix: {blocker.fix}.")
    return "\n".join(lines)


def heal_screenshots(limit: int = _HEAL_PER_RUN) -> int:
    """Retake missing screenshots for previews waiting to send. Returns how
    many were restored."""
    from agents import design_agent  # local import: keeps this module cheap for the CLI report

    healed = 0
    for lead in _queued_without_screenshot()[:limit]:
        website = db.get_website_by_lead(lead["id"])
        if not website or not website.get("preview_url") or website.get("torn_down_at"):
            continue
        _url, path = design_agent._capture_and_publish_screenshot(lead["id"], website["preview_url"])
        healed += bool(path)
    return healed


def run() -> None:
    """Called every cycle by main.py; does its work at most every ten minutes."""
    global _last_run_at, _last_brief_day
    now = time.monotonic()
    if _last_run_at is not None and now - _last_run_at < _RUN_INTERVAL_SECONDS:
        return
    _last_run_at = now

    healed = heal_screenshots()
    if healed:
        print(f"[mission] Retook {healed} missing preview screenshot(s)")

    if not config.MISSION_DAILY_SENDS:
        return
    local_now = datetime.now()
    today = local_now.strftime("%Y-%m-%d")
    if _last_brief_day == today or local_now.hour < config.SEND_WINDOW_START_HOUR:
        return
    _last_brief_day = today
    report = format_report(assess())
    print(report)
    from agents import sales_agent  # local import: sales_agent pulls in the mail stack

    sales_agent._slack_notify(f"```{report}```")
