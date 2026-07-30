"""One-off cleanup for preview repos created by the OLD pipeline behavior.

Earlier versions of design_agent.py created a private GitHub repo for every
single preview (name pattern: <business-slug>-preview-<lead_id>), flooding
the account. The pipeline no longer does that -- previews deploy git-less to
Vercel, and a repo is only created at `transfer` time for a sold site.

This script deletes the leftovers, carefully:

  python scripts/cleanup_preview_repos.py            # list what WOULD be deleted
  python scripts/cleanup_preview_repos.py --delete   # actually delete (asks once)

Safety rails:
  - Only repos owned by the authenticated user whose name matches
    ^*-preview-<digits>$ are considered.
  - Any repo recorded as transferred (websites.transferred = 1) or belonging
    to a lead in a paying status (won / payment_sent) is skipped.
  - Nothing is deleted without you typing "delete" at the prompt.

Requires GITHUB_TOKEN in .env with the `delete_repo` scope.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pipeline"))

import config  # noqa: E402
from utils import db, github_api  # noqa: E402

_PREVIEW_NAME_RE = re.compile(r"-preview-(\d+)$")
_PROTECTED_STATUSES = ("won", "payment_sent")


def _protected_lead_ids() -> set[int]:
    """Lead ids whose repos must never be auto-deleted: transferred sites
    and leads that have paid / been sent a payment link."""
    protected: set[int] = set()
    try:
        db.init_db()
        with db.get_connection() as conn:
            for row in conn.execute("SELECT lead_id FROM websites WHERE transferred = 1"):
                protected.add(int(row["lead_id"]))
            placeholders = ", ".join("?" for _ in _PROTECTED_STATUSES)
            for row in conn.execute(
                f"SELECT id FROM leads WHERE status IN ({placeholders})", _PROTECTED_STATUSES
            ):
                protected.add(int(row["id"]))
    except Exception as exc:  # noqa: BLE001 - no local DB just means no extra protection info
        print(f"[cleanup] Warning: could not read local DB ({exc}); "
              "protecting nothing beyond the name-pattern check.")
    return protected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--delete", action="store_true",
                        help="Actually delete (default is a dry-run listing).")
    args = parser.parse_args()

    if not config.GITHUB_TOKEN:
        print("GITHUB_TOKEN is not set -- fill in .env first.")
        return 1

    client = github_api._get_client()
    user = client.get_user()
    print(f"Authenticated as: {user.login}")

    protected = _protected_lead_ids()
    candidates = []
    for repo in user.get_repos(affiliation="owner"):
        match = _PREVIEW_NAME_RE.search(repo.name)
        if not match:
            continue
        lead_id = int(match.group(1))
        if lead_id in protected:
            print(f"  SKIP (sold/transferred lead {lead_id}): {repo.full_name}")
            continue
        candidates.append(repo)

    if not candidates:
        print("No leftover preview repos found. Nothing to do.")
        return 0

    print(f"\n{len(candidates)} preview repo(s) match the old-pipeline pattern:")
    for repo in candidates:
        print(f"  {repo.full_name}  (created {repo.created_at:%Y-%m-%d})")

    if not args.delete:
        print("\nDry run only. Re-run with --delete to remove them.")
        return 0

    answer = input(f"\nType 'delete' to permanently delete all {len(candidates)} repos: ").strip()
    if answer != "delete":
        print("Aborted; nothing deleted.")
        return 1

    failures = 0
    for repo in candidates:
        try:
            repo.delete()
            print(f"  deleted {repo.full_name}")
        except Exception as exc:  # noqa: BLE001 - keep going; report at the end
            failures += 1
            print(f"  FAILED to delete {repo.full_name}: {exc}")
    print(f"\nDone: {len(candidates) - failures} deleted, {failures} failed.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
