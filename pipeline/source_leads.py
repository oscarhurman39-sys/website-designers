"""One-shot lead sourcing from Google Places (see agents/sourcing_agent.py).

    python source_leads.py                 # insert up to today's remaining budget
    python source_leads.py --limit 10      # insert at most 10 (still capped by the daily limit)
    python source_leads.py --dry-run       # print what would be inserted; write nothing

Run from inside pipeline/ or via `python run.py source ...` from the repo
root. Only GOOGLE_PLACES_API_KEY is required -- unlike quick_run.py this
deliberately doesn't call config.validate(), because sourcing neither
emails nor deploys anything, so it's usable before the rest of .env is
filled in.
"""
from __future__ import annotations

import argparse

import config
from agents import sourcing_agent
from utils import db


def main() -> int:
    parser = argparse.ArgumentParser(description="Source new leads from the Google Places API (New).")
    parser.add_argument("--limit", type=int, default=None, help="Insert at most N leads this run")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the leads that would be inserted without writing to the DB"
    )
    args = parser.parse_args()

    if not config.GOOGLE_PLACES_API_KEY:
        print("GOOGLE_PLACES_API_KEY is not set in .env; cannot source leads.")
        return 1

    db.init_db()
    print(
        f"Niches: {', '.join(config.SOURCING_NICHES)}\n"
        f"Locations: {'; '.join(config.SOURCING_LOCATIONS)}\n"
        f"Daily limit: {config.SOURCING_DAILY_LIMIT} "
        f"(sourced today so far: {db.count_sourced_leads_today()})"
    )

    if args.dry_run:
        budget = sourcing_agent.remaining_today(args.limit)
        print(f"DRY RUN -- would insert up to {budget} lead(s):")
        count = 0
        for candidate in sourcing_agent.iter_candidates(budget):
            count += 1
            print(f"  {count:3d}. {candidate.describe()}")
        print(f"DRY RUN -- {count} lead(s) would be inserted. Nothing was written.")
        return 0

    inserted = sourcing_agent.source_leads(args.limit)
    print(f"Done: {inserted} new lead(s) inserted with status 'new'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
