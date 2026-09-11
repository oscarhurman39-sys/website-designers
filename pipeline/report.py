"""Per-niche funnel and revenue report.

    python run.py report

Leads -> emailed -> replied -> won, plus revenue actually taken (from the
Stripe webhook's amount_total). This is the data the room uses to decide
which niches to double down on once real sends have happened; until then
it will only show test data.
"""
from __future__ import annotations

import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402
from utils import db  # noqa: E402


def main() -> int:
    db.init_db()
    rows = db.outcome_report()
    if not rows:
        print("No leads yet.")
        return 0
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
