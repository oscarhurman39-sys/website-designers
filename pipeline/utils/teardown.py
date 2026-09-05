"""Preview expiry: tear down preview sites once their promised TTL is up.

Every cold email says the preview "is live for 7 days -- after that it'll
be repurposed" (agents/sales_agent.py). Without this module nothing ever
made that true, so every lead -- including the ones that never replied --
kept a Vercel project and a private GitHub repo forever. `expire_previews`
deletes both for previews older than config.PREVIEW_TTL_DAYS whose lead is
in a dead-end status (emailed with no reply, lost, bounced, unsubscribed),
and records the teardown on the `websites` row plus a state_history note.
Lead status is deliberately left alone: expiry is housekeeping, not a
change in where the lead is in the funnel.

main.py runs this at most once per hour at the end of its cycle. It can
also be run by hand from the repo root:

    python pipeline/utils/teardown.py --dry-run   # list what would go
    python pipeline/utils/teardown.py             # actually tear down
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __name__ == "__main__":
    # pipeline/*.py use flat imports (import config, from utils import db)
    # that assume pipeline/ is on sys.path; when run directly as a script
    # only utils/ is, so add the parent -- same shim tests/conftest.py uses.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from utils import db, github_api, vercel_api


def _describe(row: dict[str, Any]) -> str:
    return (
        f"lead {row['lead_id']} ({row['business_name']!r}, status {row['status']}) "
        f"created {row['created_at']} -> {row['preview_url'] or 'no preview url'}"
    )


def _teardown_one(row: dict[str, Any], ttl_days: int) -> None:
    """Delete the Vercel project and GitHub repo behind one website row and
    record it. Both deletes are idempotent on not-found, so if the GitHub
    step fails after Vercel succeeded the row is simply retried next pass
    (torn_down_at is only set once both have gone through)."""
    project_name = github_api.make_repo_name(row["business_name"], row["lead_id"])
    vercel_api.delete_project(project_name)
    if row.get("repo_full_name"):
        github_api.delete_repo(row["repo_full_name"])
    db.mark_website_torn_down(row["website_id"])
    db.log_state_history(
        row["lead_id"],
        row["status"],
        row["status"],
        notes=f"preview expired after {ttl_days} days; Vercel project + GitHub repo removed",
    )


def expire_previews(dry_run: bool = False) -> int:
    """Tear down every expired preview (see module docstring for the
    selection rules) and return how many were removed. One row failing is
    printed and skipped so a single flaky API call can't stall the batch;
    that row is picked up again on the next pass."""
    ttl_days = config.PREVIEW_TTL_DAYS
    rows = db.list_expired_previews(ttl_days)
    if not rows:
        print(f"[teardown] No previews older than {ttl_days} days to expire.")
        return 0

    removed = 0
    for row in rows:
        if dry_run:
            print(f"[teardown] DRY RUN would tear down {_describe(row)}")
            continue
        try:
            _teardown_one(row, ttl_days)
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
            print(f"[teardown] FAILED {_describe(row)}: {exc}")
            continue
        removed += 1
        print(f"[teardown] Tore down {_describe(row)}")

    if dry_run:
        print(f"[teardown] DRY RUN: {len(rows)} preview(s) would be torn down.")
    else:
        print(f"[teardown] Tore down {removed}/{len(rows)} expired preview(s).")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Tear down preview sites past their TTL.")
    parser.add_argument(
        "--dry-run", action="store_true", help="List the previews that would be torn down without touching anything."
    )
    args = parser.parse_args()
    config.validate()
    db.init_db()
    expire_previews(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
