"""Funnel and revenue reports.

    python run.py report            # per-niche: leads -> emailed -> replied -> won, revenue
    python run.py report --weekly   # the same funnel per 7-day window, plus the lead-quality mix

Revenue is what Stripe actually took (the webhook's amount_total). The
per-niche table decides which trades to double down on; the weekly table is
the one to read every Monday -- it shows whether a change to targeting or
copy moved replies, and how much of the queue is businesses that really
need a site versus ones that already have a healthy one.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402
from utils import db  # noqa: E402


def print_niche_report() -> None:
    rows = db.outcome_report()
    if not rows:
        print("No leads yet.")
        return
    sym = config.CURRENCY_SYMBOL
    print(f"{'niche':<16}{'leads':>7}{'emailed':>9}{'replied':>9}{'won':>6}{'revenue':>11}{'reply%':>8}{'close%':>8}")
    tot = {"leads": 0, "emailed": 0, "replied": 0, "won": 0, "revenue": 0}
    for r in rows:
        reply_pct = (100 * r["replied"] / r["emailed"]) if r["emailed"] else 0
        close_pct = (100 * r["won"] / r["emailed"]) if r["emailed"] else 0
        money = f"{sym}{r['revenue']:,}"
        print(f"{r['niche']:<16}{r['leads']:>7}{r['emailed']:>9}{r['replied']:>9}{r['won']:>6}{money:>11}{reply_pct:>7.1f}%{close_pct:>7.1f}%")
        for k in tot:
            tot[k] += r[k]
    print("-" * 74)
    money = f"{sym}{tot['revenue']:,}"
    print(f"{'total':<16}{tot['leads']:>7}{tot['emailed']:>9}{tot['replied']:>9}{tot['won']:>6}{money:>11}")


def print_weekly_report(weeks: int = 4) -> None:
    sym = config.CURRENCY_SYMBOL
    print("WEEKLY FUNNEL (7-day windows ending today, newest first)")
    print(f"{'week':<24}{'sourced':>8}{'mailable':>9}{'built':>7}{'cold':>6}{'follow':>7}{'replies':>8}{'clicks':>7}{'won':>5}{'revenue':>10}{'reply%':>8}")
    for w in db.weekly_funnel(weeks=weeks):
        label = f"{w['week_start']} .. {w['week_end']}"
        reply_pct = (100 * w["replies"] / w["cold_emails"]) if w["cold_emails"] else 0
        print(
            f"{label:<24}{w['sourced']:>8}{w['mailable']:>9}{w['designed']:>7}{w['cold_emails']:>6}"
            f"{w['follow_ups']:>7}{w['replies']:>8}{w['clicks']:>7}{w['won']:>5}{sym + format(w['revenue'], ','):>10}{reply_pct:>7.1f}%"
        )
    print()
    print("LEAD QUALITY MIX (existing web presence)")
    print(f"{'bucket':<16}{'to build':>9}{'to send':>8}{'emailed':>8}{'lost':>6}")
    for row in db.lead_quality_mix():
        print(f"{row['bucket']:<16}{row['to_build']:>9}{row['to_send']:>8}{row['emailed']:>8}{row['lost']:>6}")
    threshold = config.SITE_SCORE_GOOD_AT
    print(
        f"(site scores: 'good site' = {threshold}+ and not pitched; 'poor' 4-{threshold - 1}; 'very poor' 0-3; "
        "'unscored' = audited by `run.py rescore`)" if threshold > 0
        else "(SITE_SCORE_GOOD_AT=0: every scored site is pitched)"
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weekly", action="store_true", help="funnel per 7-day window + lead quality mix")
    parser.add_argument("--weeks", type=int, default=4, help="how many 7-day windows (default 4)")
    args = parser.parse_args(argv)
    db.init_db()
    if args.weekly:
        print_weekly_report(weeks=max(1, args.weeks))
    else:
        print_niche_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
