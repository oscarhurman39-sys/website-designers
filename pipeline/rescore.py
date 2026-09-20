"""Audit the existing websites of leads researched before the site audit
existed, and (optionally) park the ones that already have a healthy site.

    python run.py rescore --dry-run [--limit N]   # fetch + score, print, write nothing
    python run.py rescore [--limit N]             # write site_score / site_defects / lead_score
    python run.py rescore --park-good             # also mark queued healthy-site leads lost

Only leads still waiting to be built or sent ('researched' / 'designed')
with an owned site and no score yet are fetched -- one GET per lead, the
same request LeadAgent makes, nothing else. Nothing is emailed, deployed or
torn down here; parking a lead just takes it out of the send queue (its
preview, if any, expires on the normal PREVIEW_TTL_DAYS schedule).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402
from agents import lead_agent  # noqa: E402
from utils import db, scoring, site_audit  # noqa: E402

FETCH_GAP_SECONDS = 0.3


def audit_lead(lead: dict) -> Optional[site_audit.SiteAudit]:
    response = lead_agent._fetch_response((lead.get("website_url") or "").strip())
    if response is None:
        return None
    return site_audit.audit_html(
        response.url or lead["website_url"], response.text, html_bytes=len(response.content or b"")
    )


def rescore(limit: Optional[int] = None, dry_run: bool = False, gap_seconds: float = FETCH_GAP_SECONDS) -> dict[str, int]:
    """Fetch and score every unscored owned-site lead in the queues. Returns
    counts: audited, unreachable, and how many now score as a healthy site."""
    leads = db.list_unscored_owned_site_leads()
    if limit is not None:
        leads = leads[: max(0, limit)]
    counts = {"audited": 0, "unreachable": 0, "good": 0, "written": 0}
    print(f"{'id':>5}  {'status':<10}{'score':>6}  {'lead':>5}  defects / phrase")
    for i, lead in enumerate(leads):
        if i and gap_seconds:
            time.sleep(gap_seconds)
        audit = audit_lead(lead)
        if audit is None:
            counts["unreachable"] += 1
            print(f"{lead['id']:>5}  {lead['status']:<10}{'-':>6}  {'-':>5}  could not fetch {lead.get('website_url')}")
            continue
        counts["audited"] += 1
        channel = lead.get("contact_channel") or lead_agent._contact_channel(lead, lead.get("contact_email"))
        new_lead_score = scoring.lead_score(
            scoring.WEBSITE_STATUS_OWNED, audit.score, channel,
            review_count=lead.get("google_reviews_count"), business_name=lead.get("business_name"),
        )
        if scoring.is_good_site(scoring.WEBSITE_STATUS_OWNED, audit.score):
            counts["good"] += 1
        phrase = audit.owner_phrase()
        print(f"{lead['id']:>5}  {lead['status']:<10}{audit.score:>6}  {new_lead_score if new_lead_score is not None else '-':>5}  "
              f"{audit.keys or 'none'}{(' / ' + phrase) if phrase else ''}")
        if dry_run:
            continue
        fields = dict(
            website_status=scoring.WEBSITE_STATUS_OWNED,
            contact_channel=channel,
            site_score=audit.score,
            site_defects=audit.keys,
            lead_score=new_lead_score,
        )
        if phrase:
            fields["pain_point"] = f"their current website: {phrase}"
        db.update_lead_fields(lead["id"], **fields)
        db.log_state_history(
            lead["id"], lead["status"], lead["status"],
            notes=f"Site audited by rescore: {audit.score}/10 ({audit.keys or 'no defects'})",
        )
        counts["written"] += 1
    return counts


def park_good_site_leads(dry_run: bool = False) -> int:
    """Take queued leads whose existing site scores healthy out of the build
    and send queues, the same way DesignAgent now does before a build."""
    parked = 0
    for lead in db.list_good_site_leads():
        note = (
            f"Existing website scored {lead['site_score']}/10 (healthy; SITE_SCORE_GOOD_AT="
            f"{scoring.good_site_threshold()}); not a target, parked by rescore --park-good"
        )
        print(f"{'would park' if dry_run else 'parked':<10} lead {lead['id']:>4}  {lead['status']:<10}"
              f"score {lead['site_score']}/10  {lead['business_name']}")
        if not dry_run:
            db.update_lead_status(lead["id"], "lost", notes=note)
        parked += 1
    return parked


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="fetch and print scores; write nothing")
    parser.add_argument("--limit", type=int, help="audit at most this many leads")
    parser.add_argument("--park-good", action="store_true",
                        help=f"after scoring, mark queued leads with site_score >= {config.SITE_SCORE_GOOD_AT} lost")
    args = parser.parse_args(argv)

    db.init_db()
    counts = rescore(limit=args.limit, dry_run=args.dry_run)
    print(
        f"\n{counts['audited']} audited, {counts['unreachable']} unreachable, "
        f"{counts['good']} score as a healthy site (>= {config.SITE_SCORE_GOOD_AT}); "
        f"{'nothing written (dry run)' if args.dry_run else str(counts['written']) + ' lead(s) updated'}"
    )
    if args.park_good:
        if config.SITE_SCORE_GOOD_AT <= 0:
            print("SITE_SCORE_GOOD_AT is 0: nothing is parked.")
        else:
            parked = park_good_site_leads(dry_run=args.dry_run)
            print(f"{parked} healthy-site lead(s) {'would be' if args.dry_run else ''} parked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
