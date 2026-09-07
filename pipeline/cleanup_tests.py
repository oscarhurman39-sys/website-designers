"""Remove the throwaway leads left behind by manual test runs.

test_email.py creates a "Test Business" lead and quick_run.py's help text
suggests an "Acme Cafe" one; both then build a real private GitHub repo
and a real Vercel project for it and email the operator's own address.
Nothing cleans those up, so after a few test runs the GitHub account and
Vercel dashboard fill with junk previews and the dashboard's lead counts
drift. This script finds those leads (by the known test business names or
by contact_email == ADMIN_EMAIL), deletes their Vercel project + GitHub
repo (both idempotent on not-found), then removes every DB row that
references them and finally the lead itself.

Run with (either works, same as quick_run.py):

    python cleanup_tests.py [--dry-run]              # from inside pipeline/
    python run.py cleanup-tests [--dry-run]          # from the repo root
    python run.py cleanup-tests --reset [--dry-run]  # pre-launch: archive the WHOLE database

Only ever deletes leads matching the rules above -- real prospects are
never touched. Use --dry-run first to see exactly what would go.

`--reset` is the one-off step before the first real batch, for a database
that is documented as 100% test data: it copies leads.db (and traces.json)
into pipeline/archive/ with a timestamp, removes the live file and
re-creates an empty schema. It refuses if anything in the database looks
real -- an unsubscribed address, a paid lead, a site handed to a client --
or if any preview is still deployed (run `python pipeline/utils/teardown.py
--all` first, so no Vercel project or GitHub repo is orphaned).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config
from utils import db, github_api, teardown, vercel_api
from utils.teardown import ARCHIVE_DIR

# Business names used by the manual test scripts, plus the throwaway names
# found in the pre-launch DB on 2026-09-07 (one-off smoke tests of SendGrid,
# SMTP and the full pipeline). Matched exactly, case-sensitively.
TEST_BUSINESS_NAMES: tuple[str, ...] = (
    "Test Business",
    "Acme Cafe",
    "Pipeline Test Cafe",
    "Final Test",
    "Real Send",
    "SendGrid Live Test",
    "Diag Test",
)
# Locations that only ever appear on made-up leads.
TEST_LOCATIONS: tuple[str, ...] = ("Testville",)


def find_test_leads() -> list[dict[str, Any]]:
    return db.find_test_leads(TEST_BUSINESS_NAMES, contact_email=config.ADMIN_EMAIL, locations=TEST_LOCATIONS)


def _delete_remote_artifacts(lead: dict[str, Any], website: dict[str, Any] | None) -> Optional[str]:
    """Delete the Vercel project and GitHub repo. The project name is
    rebuilt from the lead the same way design_agent.py named it, so it
    works even for old rows without a website record; the repo needs the
    stored full name (owner/repo).

    Returns the repo that had to be left behind when the token lacks the
    `delete_repo` scope (see utils/teardown._teardown_one for why that is
    reported rather than treated as a failure), else None."""
    vercel_api.delete_project(github_api.make_repo_name(lead["business_name"], lead["id"]))
    if website and website.get("repo_full_name"):
        try:
            github_api.delete_repo(website["repo_full_name"])
        except github_api.RepoDeleteForbidden:
            return website["repo_full_name"]
    return None


def cleanup(dry_run: bool = False) -> int:
    """Delete every matching test lead (remote artifacts, then DB rows) and
    return how many were removed. A failure on one lead is printed and
    skipped so the rest still get cleaned up."""
    leads = find_test_leads()
    if not leads:
        print("[cleanup] No test leads found.")
        return 0

    removed = 0
    orphans: list[str] = []
    for lead in leads:
        website = db.get_website_by_lead(lead["id"])
        label = (
            f"lead {lead['id']} ({lead['business_name']!r}, {lead.get('contact_email') or 'no email'}, "
            f"status {lead['status']}, repo {website['repo_full_name'] if website else 'none'})"
        )
        if dry_run:
            print(f"[cleanup] DRY RUN would delete {label}")
            continue
        try:
            orphan = _delete_remote_artifacts(lead, website)
            db.delete_lead_cascade(lead["id"])
        except Exception as exc:  # noqa: BLE001 - one bad lead must not abort the rest
            print(f"[cleanup] FAILED {label}: {exc}")
            continue
        removed += 1
        if orphan:
            orphans.append(orphan)
        print(f"[cleanup] Deleted {label}" + (f" (repo {orphan} left in place)" if orphan else ""))

    if dry_run:
        print(f"[cleanup] DRY RUN: {len(leads)} test lead(s) would be deleted.")
    else:
        print(f"[cleanup] Deleted {removed}/{len(leads)} test lead(s).")
        teardown._report_orphan_repos(orphans)
    return removed


# --- Pre-launch reset ------------------------------------------------------------

def _status_counts() -> dict[str, int]:
    with db.get_connection() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM leads GROUP BY status ORDER BY n DESC").fetchall()
        return {r["status"]: r["n"] for r in rows}


def reset_blockers() -> list[str]:
    """Reasons the database must NOT be archived away. Anything real in it
    -- a suppression entry, a paid lead, a handed-over site -- means this is
    no longer a test database; a still-deployed preview means remote
    artefacts would be orphaned."""
    with db.get_connection() as conn:
        unsubscribed = conn.execute("SELECT COUNT(*) FROM unsubscribes").fetchone()[0]
        paid = conn.execute("SELECT COUNT(*) FROM leads WHERE status IN ('won', 'payment_sent')").fetchone()[0]
        transferred = conn.execute("SELECT COUNT(*) FROM websites WHERE transferred = 1").fetchone()[0]
        deployed = conn.execute(
            "SELECT COUNT(*) FROM websites WHERE transferred = 0 AND torn_down_at IS NULL"
        ).fetchone()[0]
    problems = []
    if unsubscribed:
        problems.append(f"{unsubscribed} unsubscribed address(es) -- a suppression list is never reset")
    if paid:
        problems.append(f"{paid} paid lead(s) (won/payment_sent) -- this is not a test database")
    if transferred:
        problems.append(f"{transferred} website(s) handed over to a client")
    if deployed:
        problems.append(
            f"{deployed} preview(s) still deployed -- run `python pipeline/utils/teardown.py --all` first "
            "so no Vercel project or GitHub repo is left behind"
        )
    return problems


def _sibling_files(db_path: Path) -> list[Path]:
    return [db_path] + [Path(f"{db_path}{suffix}") for suffix in ("-wal", "-shm", "-journal")]


def reset_prelaunch(dry_run: bool = False) -> Optional[Path]:
    """Archive the whole database and start empty. Returns the archive path,
    or None when nothing was done (dry run, or a blocker)."""
    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        print(f"[reset] No database at {db_path}; nothing to archive.")
        return None
    counts = _status_counts()
    total = sum(counts.values())
    print(f"[reset] {db_path.name}: {total} lead(s) " + ", ".join(f"{n} {s}" for s, n in counts.items()))

    problems = reset_blockers()
    if problems:
        for problem in problems:
            print(f"[reset] BLOCKED: {problem}")
        print("[reset] Refusing to archive.")
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_db = ARCHIVE_DIR / f"leads-{stamp}.db"
    traces = Path(config.TRACES_PATH)
    archive_traces = ARCHIVE_DIR / f"traces-{stamp}.json"
    if dry_run:
        print(f"[reset] DRY RUN would copy {db_path} -> {archive_db}, then start an empty database.")
        if traces.exists():
            print(f"[reset] DRY RUN would move {traces} -> {archive_traces}.")
        return None

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(archive_db)
    try:
        source.backup(target)  # consistent copy even with WAL/journal files around
    finally:
        target.close()
        source.close()
    if traces.exists():
        shutil.move(str(traces), str(archive_traces))
    for path in _sibling_files(db_path):
        if path.exists():
            path.unlink()
    db.init_db()
    print(f"[reset] Archived {total} lead(s) to {archive_db}; {db_path.name} is now empty.")
    return archive_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete throwaway test leads and their preview sites.")
    parser.add_argument("--dry-run", action="store_true", help="List matching leads without deleting anything.")
    parser.add_argument(
        "--reset", action="store_true",
        help="PRE-LAUNCH ONLY: archive the whole database to pipeline/archive/ and start empty.",
    )
    args = parser.parse_args()
    config.validate()
    db.init_db()
    if args.reset:
        reset_prelaunch(dry_run=args.dry_run)
    else:
        cleanup(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
