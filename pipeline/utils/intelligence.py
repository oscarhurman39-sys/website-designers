"""Conversion intelligence: turn the pipeline's own history into signal.

Every send, click, reply, classification, and state transition is already
recorded (leads / email_threads / clicks / state_history). This module
reads that history back and answers the questions the pipeline otherwise
flies blind on: which niches convert, which subject lines earn replies,
whether the screenshot is actually pulling its weight, and where leads
leak out of the funnel. Nothing here writes -- it's pure, read-only
analysis over data you already have, no new dependencies and no API cost.

The one non-obvious idea: don't measure the funnel from a lead's *current*
status. A won lead's status is 'won', not 'emailed', so current-status
counts undercount every earlier stage. Instead we reconstruct "did this
lead EVER reach stage X" from state_history, which logs every transition.
That gives a truthful funnel where each stage is a strict superset of the
next.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from utils import db

# Forward progression a lead moves through, in order. Terminal-negative
# states (lost/bounced/unsubscribed) sit outside this and are reported
# separately -- they're exits, not stages.
FUNNEL_STAGES: tuple[str, ...] = (
    "new",
    "researched",
    "designed",
    "emailed",
    "replied",
    "negotiating",
    "payment_sent",
    "won",
)

_POSITIVE_OUTCOME = "replied"  # first stage that means a human engaged back


def _pct(numerator: int, denominator: int) -> float:
    """Percentage, guarding against an empty denominator (0.0, never a crash)."""
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


@dataclass
class FunnelStage:
    stage: str
    reached: int          # leads that EVER reached this stage
    conversion_from_prev: float   # % of previous stage that made it here
    drop_off: int         # leads that reached prev but never this one


@dataclass
class NichePerformance:
    niche: str
    total: int
    emailed: int
    clicked: int
    replied: int
    won: int
    click_rate: float     # % of emailed that clicked the preview link
    reply_rate: float     # % of emailed that replied
    win_rate: float       # % of emailed that became 'won'


@dataclass
class SubjectPerformance:
    subject: str
    sent: int
    replied: int
    reply_rate: float


@dataclass
class Intelligence:
    total_leads: int
    funnel: list[FunnelStage]
    niches: list[NichePerformance]
    subjects: list[SubjectPerformance]
    screenshot_lift: dict[str, Any]
    exits: dict[str, int]
    recommendations: list[str] = field(default_factory=list)


# --- Core reconstruction -----------------------------------------------------

def _reached_lead_ids() -> dict[str, set[int]]:
    """For every funnel stage, the set of lead ids that EVER reached it,
    reconstructed from state_history (plus each lead's current status, in
    case a transition predates history logging)."""
    reached: dict[str, set[int]] = {stage: set() for stage in FUNNEL_STAGES}
    with db.get_connection() as conn:
        for row in conn.execute("SELECT DISTINCT lead_id, to_state FROM state_history"):
            if row["to_state"] in reached:
                reached[row["to_state"]].add(row["lead_id"])
        # 'new' is the implicit entry point for every lead ever created.
        for row in conn.execute("SELECT id, status FROM leads"):
            reached["new"].add(row["id"])
            if row["status"] in reached:
                reached[row["status"]].add(row["id"])
    return reached


def _build_funnel(reached: dict[str, set[int]]) -> list[FunnelStage]:
    stages: list[FunnelStage] = []
    prev_ids: Optional[set[int]] = None
    for stage in FUNNEL_STAGES:
        ids = reached[stage]
        if prev_ids is None:
            conv, drop = 100.0, 0
        else:
            conv = _pct(len(ids & prev_ids), len(prev_ids))
            drop = len(prev_ids - ids)
        stages.append(FunnelStage(stage=stage, reached=len(ids), conversion_from_prev=conv, drop_off=drop))
        prev_ids = ids  # each stage is the baseline the next is measured against
    return stages


# --- Slices ------------------------------------------------------------------

def _niche_performance(reached: dict[str, set[int]]) -> list[NichePerformance]:
    clicked_ids = _clicked_lead_ids()
    rows: dict[str, dict[str, set[int]]] = {}
    with db.get_connection() as conn:
        for lead in conn.execute("SELECT id, niche FROM leads"):
            niche = lead["niche"] or "(unknown)"
            bucket = rows.setdefault(niche, {"total": set(), "emailed": set(), "won": set(), "replied": set()})
            bucket["total"].add(lead["id"])

    out: list[NichePerformance] = []
    for niche, bucket in rows.items():
        total_ids = bucket["total"]
        emailed = total_ids & reached["emailed"]
        replied = total_ids & reached[_POSITIVE_OUTCOME]
        won = total_ids & reached["won"]
        clicked = total_ids & clicked_ids
        out.append(
            NichePerformance(
                niche=niche,
                total=len(total_ids),
                emailed=len(emailed),
                clicked=len(clicked),
                replied=len(replied),
                won=len(won),
                click_rate=_pct(len(clicked & emailed), len(emailed)),
                reply_rate=_pct(len(replied), len(emailed)),
                win_rate=_pct(len(won), len(emailed)),
            )
        )
    # Best-performing first (by reply rate, then volume as a tiebreak).
    out.sort(key=lambda n: (n.reply_rate, n.emailed), reverse=True)
    return out


def _clicked_lead_ids() -> set[int]:
    with db.get_connection() as conn:
        return {row["lead_id"] for row in conn.execute("SELECT DISTINCT lead_id FROM clicks")}


def _replied_lead_ids() -> set[int]:
    """Leads that produced at least one inbound message -- a real human
    reply, regardless of how it was later classified."""
    with db.get_connection() as conn:
        return {
            row["lead_id"]
            for row in conn.execute("SELECT DISTINCT lead_id FROM email_threads WHERE direction = 'inbound'")
        }


def _subject_performance() -> list[SubjectPerformance]:
    """Reply rate grouped by the subject line actually sent. This is the
    cheapest A/B test you'll ever run -- it's just reading what already
    happened."""
    replied = _replied_lead_ids()
    by_subject: dict[str, set[int]] = {}
    with db.get_connection() as conn:
        for row in conn.execute(
            "SELECT DISTINCT lead_id, subject FROM email_threads WHERE direction = 'outbound' AND subject IS NOT NULL"
        ):
            by_subject.setdefault(row["subject"], set()).add(row["lead_id"])

    out = [
        SubjectPerformance(
            subject=subject,
            sent=len(ids),
            replied=len(ids & replied),
            reply_rate=_pct(len(ids & replied), len(ids)),
        )
        for subject, ids in by_subject.items()
    ]
    out.sort(key=lambda s: (s.reply_rate, s.sent), reverse=True)
    return out


def _screenshot_lift(reached: dict[str, set[int]]) -> dict[str, Any]:
    """Does embedding the preview screenshot actually earn more replies?
    Compares reply rate for leads whose website row has a screenshot vs
    those without -- turning a feature you built on faith into a measured
    one."""
    replied = _replied_lead_ids()
    with_shot: set[int] = set()
    without_shot: set[int] = set()
    with db.get_connection() as conn:
        for row in conn.execute("SELECT lead_id, screenshot_path, screenshot_url FROM websites"):
            has = bool(row["screenshot_path"] or row["screenshot_url"])
            (with_shot if has else without_shot).add(row["lead_id"])

    emailed = reached["emailed"]
    ws_emailed, wos_emailed = with_shot & emailed, without_shot & emailed
    ws_rate = _pct(len(ws_emailed & replied), len(ws_emailed))
    wos_rate = _pct(len(wos_emailed & replied), len(wos_emailed))
    return {
        "with_screenshot": {"emailed": len(ws_emailed), "reply_rate": ws_rate},
        "without_screenshot": {"emailed": len(wos_emailed), "reply_rate": wos_rate},
        "lift_points": round(ws_rate - wos_rate, 1),
    }


def _exit_counts() -> dict[str, int]:
    with db.get_connection() as conn:
        return {
            state: conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status = ?", (state,)).fetchone()["n"]
            for state in ("lost", "bounced", "unsubscribed")
        }


# --- Recommendations ---------------------------------------------------------

def _recommend(intel: "Intelligence") -> list[str]:
    """Plain-English 'do more of X' derived from the numbers. Deliberately
    conservative: only fires a recommendation when there's enough volume
    behind it to not be noise."""
    recs: list[str] = []

    ranked_niches = [n for n in intel.niches if n.emailed >= 5]
    if len(ranked_niches) >= 2:
        best, worst = ranked_niches[0], ranked_niches[-1]
        if best.reply_rate > worst.reply_rate:
            recs.append(
                f"Lean into '{best.niche}': {best.reply_rate}% reply rate over {best.emailed} sends, "
                f"vs '{worst.niche}' at {worst.reply_rate}%. Weight your next CSV toward it."
            )

    ranked_subjects = [s for s in intel.subjects if s.sent >= 5]
    if ranked_subjects:
        top = ranked_subjects[0]
        recs.append(
            f"Best subject line so far: \"{top.subject}\" ({top.reply_rate}% reply over {top.sent} sends). "
            "Feed it back into the drafting prompt as the style to imitate."
        )

    lift = intel.screenshot_lift
    if lift["with_screenshot"]["emailed"] >= 5 and lift["without_screenshot"]["emailed"] >= 5:
        pts = lift["lift_points"]
        if pts > 0:
            recs.append(f"The preview screenshot is working: +{pts} points of reply rate when embedded. Keep it.")
        elif pts < 0:
            recs.append(f"Screenshot is underperforming ({pts} points). Worth A/B testing the image itself.")

    # Biggest funnel leak (largest drop-off past the point of no return).
    leaks = [s for s in intel.funnel if s.stage not in ("new", "researched") and s.drop_off > 0]
    if leaks:
        worst_leak = max(leaks, key=lambda s: s.drop_off)
        recs.append(
            f"Biggest leak: {worst_leak.drop_off} leads reached the prior stage but never '{worst_leak.stage}' "
            f"({worst_leak.conversion_from_prev}% conversion). That's where to focus next."
        )

    if not recs:
        recs.append("Not enough send volume yet for reliable signal -- keep the pipeline running and check back.")
    return recs


# --- Public entrypoint -------------------------------------------------------

def analyze() -> Intelligence:
    """Run the full read-only analysis over current DB history."""
    reached = _reached_lead_ids()
    intel = Intelligence(
        total_leads=len(reached["new"]),
        funnel=_build_funnel(reached),
        niches=_niche_performance(reached),
        subjects=_subject_performance(),
        screenshot_lift=_screenshot_lift(reached),
        exits=_exit_counts(),
    )
    intel.recommendations = _recommend(intel)
    return intel


if __name__ == "__main__":
    # `python -m utils.intelligence` from pipeline/ prints a text report.
    db.init_db()
    result = analyze()
    print(f"\n{'=' * 60}\nCONVERSION INTELLIGENCE  ({result.total_leads} leads)\n{'=' * 60}")
    print("\nFUNNEL (ever-reached):")
    for s in result.funnel:
        print(f"  {s.stage:14s} {s.reached:5d}   {s.conversion_from_prev:5.1f}% from prev   (-{s.drop_off} leaked)")
    print("\nTOP NICHES (by reply rate, >=1 send):")
    for n in result.niches:
        if n.emailed:
            print(f"  {n.niche:14s} sent {n.emailed:3d}  click {n.click_rate:4.1f}%  reply {n.reply_rate:4.1f}%  win {n.win_rate:4.1f}%")
    print("\nRECOMMENDATIONS:")
    for r in result.recommendations:
        print(f"  - {r}")
    print()
