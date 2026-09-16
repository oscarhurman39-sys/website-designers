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
    python pipeline/utils/teardown.py --all       # PRE-LAUNCH ONLY: every deployed
                                                  # preview, whatever its age/status
    python pipeline/utils/teardown.py --repos-from pipeline/archive/orphan-repos-<stamp>.txt
                                                  # delete repos an earlier run could not

`--all` exists for the one-off reset before the first real batch (see
cleanup_tests.py --reset): it ignores the TTL and the status rules, but still
never touches a site that was handed to a client, and refuses outright if the
database holds a paid lead.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

if __name__ == "__main__":
    # pipeline/*.py use flat imports (import config, from utils import db)
    # that assume pipeline/ is on sys.path; when run directly as a script
    # only utils/ is, so add the parent -- same shim tests/conftest.py uses.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from utils import db, github_api, vercel_api

ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"


def _describe(row: dict[str, Any]) -> str:
    return (
        f"lead {row['lead_id']} ({row['business_name']!r}, status {row['status']}) "
        f"created {row['created_at']} -> {row['preview_url'] or 'no preview url'}"
    )


def _teardown_one(row: dict[str, Any], note: str) -> Optional[str]:
    """Delete the Vercel project and GitHub repo behind one website row and
    record it. Both deletes are idempotent on not-found, so a transient
    GitHub failure after Vercel succeeded leaves the row untouched and it is
    retried on the next pass.

    The one exception is a 403 (the token lacks `delete_repo`): retrying can
    never succeed, and the Vercel project is already gone, so the preview URL
    advertised in the prospect's email is dead -- which is the whole promise
    `torn_down_at` records. The row is marked torn down and the undeletable
    repo is returned so the caller can report it as housekeeping debt."""
    project_name = github_api.make_repo_name(row["business_name"], row["lead_id"])
    vercel_api.delete_project(project_name)
    orphan: Optional[str] = None
    if row.get("repo_full_name"):
        try:
            github_api.delete_repo(row["repo_full_name"])
        except github_api.RepoDeleteForbidden:
            orphan = row["repo_full_name"]
            note = f"{note}; GitHub repo {orphan} left in place (token lacks delete_repo)"
    db.mark_website_torn_down(row["website_id"])
    db.log_state_history(row["lead_id"], row["status"], row["status"], notes=note)
    return orphan


def _report_orphan_repos(orphans: list[str]) -> Optional[Path]:
    """Print the repos that could not be deleted and save the list, which
    has to outlive the database (`cleanup_tests.py --reset` archives it).
    Returns the file written, or None when there is nothing to report."""
    if not orphans:
        return None
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ARCHIVE_DIR / f"orphan-repos-{stamp}.txt"
    path.write_text("\n".join(orphans) + "\n", encoding="utf-8")
    bar = "!" * 70
    print(
        f"\n{bar}\n"
        f"{len(orphans)} GitHub repo(s) could NOT be deleted: GITHUB_TOKEN has the 'repo'\n"
        "scope but not 'delete_repo'. Their Vercel projects ARE deleted, so every\n"
        "preview URL is dead and no prospect can reach one; only private repos remain.\n"
        f"List saved to {path}\n"
        "Fix: github.com/settings/tokens -> edit the token -> tick delete_repo -> Update,\n"
        "then re-run this command to clear them (deleting an already-gone repo is a no-op).\n"
        f"{bar}\n"
    )
    return path


def _paid_lead_count() -> int:
    with db.get_connection() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status IN ('won', 'payment_sent')"
        ).fetchone()[0])


def delete_listed_repos(list_path: Path, dry_run: bool = False) -> int:
    """Delete the GitHub repos named in a file written by `_report_orphan_repos`.

    Those repos outlive the `websites` rows that named them -- the pre-launch
    reset archives the database -- so once GITHUB_TOKEN gains the
    `delete_repo` scope there would otherwise be nothing left to drive the
    cleanup from. One repo per line; already-deleted repos are no-ops."""
    names = [line.strip() for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not names:
        print(f"[teardown] {list_path} lists no repos.")
        return 0

    deleted = 0
    for name in names:
        if dry_run:
            print(f"[teardown] DRY RUN would delete repo {name}")
            continue
        try:
            github_api.delete_repo(name)
        except github_api.RepoDeleteForbidden as exc:
            print(f"[teardown] STILL FORBIDDEN {name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one bad repo must not abort the rest
            print(f"[teardown] FAILED {name}: {exc}")
            continue
        deleted += 1
        print(f"[teardown] Deleted repo {name}")

    if dry_run:
        print(f"[teardown] DRY RUN: {len(names)} repo(s) would be deleted.")
    else:
        print(f"[teardown] Deleted {deleted}/{len(names)} repo(s).")
        if deleted == len(names):
            print(f"[teardown] All clear -- {list_path} can be deleted.")
    return deleted


def expire_previews(dry_run: bool = False, all_previews: bool = False) -> int:
    """Tear down every expired preview (see module docstring for the
    selection rules) and return how many were removed. One row failing is
    printed and skipped so a single flaky API call can't stall the batch;
    that row is picked up again on the next pass.

    `all_previews=True` selects every deployed preview instead (the
    pre-launch reset) and refuses to run at all if any lead has paid."""
    ttl_days = config.PREVIEW_TTL_DAYS
    if all_previews:
        paid = _paid_lead_count()
        if paid:
            print(f"[teardown] Refusing --all: {paid} paid lead(s) in this database; it is not a test database.")
            return 0
        rows = db.list_live_previews()
        note = "preview torn down by operator (teardown --all, pre-launch reset)"
    else:
        rows = db.list_expired_previews(ttl_days)
        note = f"preview expired after {ttl_days} days; Vercel project + GitHub repo removed"
    if not rows:
        print("[teardown] No deployed previews to tear down." if all_previews
              else f"[teardown] No previews older than {ttl_days} days to expire.")
        return 0

    removed = 0
    orphans: list[str] = []
    for row in rows:
        if dry_run:
            print(f"[teardown] DRY RUN would tear down {_describe(row)}")
            continue
        try:
            orphan = _teardown_one(row, note)
        except Exception as exc:  # noqa: BLE001 - one bad row must not abort the batch
            print(f"[teardown] FAILED {_describe(row)}: {exc}")
            continue
        removed += 1
        if orphan:
            orphans.append(orphan)
        suffix = f" (repo {orphan} left in place)" if orphan else ""
        print(f"[teardown] Tore down {_describe(row)}{suffix}")

    if dry_run:
        print(f"[teardown] DRY RUN: {len(rows)} preview(s) would be torn down.")
    else:
        print(f"[teardown] Tore down {removed}/{len(rows)} preview(s).")
        _report_orphan_repos(orphans)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Tear down preview sites past their TTL.")
    parser.add_argument(
        "--dry-run", action="store_true", help="List the previews that would be torn down without touching anything."
    )
    parser.add_argument(
        "--all", action="store_true",
        help="PRE-LAUNCH ONLY: tear down every deployed preview regardless of age or lead status.",
    )
    parser.add_argument(
        "--repos-from", metavar="FILE", type=Path,
        help="Delete the GitHub repos listed in FILE (written by an earlier run whose token lacked delete_repo).",
    )
    args = parser.parse_args()
    config.validate()
    if args.repos_from:
        delete_listed_repos(args.repos_from, dry_run=args.dry_run)
        return
    db.init_db()
    expire_previews(dry_run=args.dry_run, all_previews=args.all)


if __name__ == "__main__":
    main()
