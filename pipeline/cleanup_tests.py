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

Only ever deletes leads matching the rules above -- real prospects are
never touched. Use --dry-run first to see exactly what would go.
"""
from __future__ import annotations

import argparse
from typing import Any

import config
from utils import db, github_api, vercel_api

# Business names used by the manual test scripts. Matched exactly.
TEST_BUSINESS_NAMES: tuple[str, ...] = ("Test Business", "Acme Cafe")


def find_test_leads() -> list[dict[str, Any]]:
    return db.find_test_leads(TEST_BUSINESS_NAMES, contact_email=config.ADMIN_EMAIL)


def _delete_remote_artifacts(lead: dict[str, Any], website: dict[str, Any] | None) -> None:
    """Delete the Vercel project and GitHub repo. The project name is
    rebuilt from the lead the same way design_agent.py named it, so it
    works even for old rows without a website record; the repo needs the
    stored full name (owner/repo)."""
    vercel_api.delete_project(github_api.make_repo_name(lead["business_name"], lead["id"]))
    if website and website.get("repo_full_name"):
        github_api.delete_repo(website["repo_full_name"])


def cleanup(dry_run: bool = False) -> int:
    """Delete every matching test lead (remote artifacts, then DB rows) and
    return how many were removed. A failure on one lead is printed and
    skipped so the rest still get cleaned up."""
    leads = find_test_leads()
    if not leads:
        print("[cleanup] No test leads found.")
        return 0

    removed = 0
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
            _delete_remote_artifacts(lead, website)
            db.delete_lead_cascade(lead["id"])
        except Exception as exc:  # noqa: BLE001 - one bad lead must not abort the rest
            print(f"[cleanup] FAILED {label}: {exc}")
            continue
        removed += 1
        print(f"[cleanup] Deleted {label}")

    if dry_run:
        print(f"[cleanup] DRY RUN: {len(leads)} test lead(s) would be deleted.")
    else:
        print(f"[cleanup] Deleted {removed}/{len(leads)} test lead(s).")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete throwaway test leads and their preview sites.")
    parser.add_argument("--dry-run", action="store_true", help="List matching leads without deleting anything.")
    args = parser.parse_args()
    config.validate()
    db.init_db()
    cleanup(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
