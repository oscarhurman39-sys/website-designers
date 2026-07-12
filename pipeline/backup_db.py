"""Daily SQLite backup for the leads database -- NOT run automatically by
main.py; invoke this directly (see crontab.example) or via a system cron job.

Uses sqlite3's online backup API (`Connection.backup()`) rather than a raw
file copy, so a backup taken while main.py/webhook_server.py has the
database open mid-write still produces a consistent snapshot instead of a
corrupt or torn copy.

Usage:

    python backup_db.py                # from inside pipeline/
    python pipeline/backup_db.py       # from the repo root

Runs `PRAGMA integrity_check` on the source DB before backing it up (see
check_integrity()) -- a corrupt source database gets a loud alert rather
than a silently-corrupt backup. Writes to
pipeline/backups/leads-YYYYMMDD-HHMMSS.db and keeps only the newest
KEEP_COUNT backups so backups/ doesn't grow unbounded.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

BACKUPS_DIR = Path(__file__).resolve().parent / "backups"
KEEP_COUNT = 30


def check_integrity(db_path: Path) -> bool:
    """Run `PRAGMA integrity_check` against `db_path`. Returns True if the
    database reports 'ok', False (after printing a loud alert) otherwise --
    including if the file is too corrupt to even open as SQLite."""
    banner = "!" * 70
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            result = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        print(f"\n{banner}\nDATABASE INTEGRITY CHECK FAILED for {db_path}: {exc}\n{banner}\n")
        return False
    if result != "ok":
        print(f"\n{banner}\nDATABASE INTEGRITY CHECK FAILED for {db_path}:\n{result}\n{banner}\n")
        return False
    return True


def backup_once() -> Path:
    """Verify integrity, then take one consistent snapshot of config.DB_PATH.
    Returns the backup path. Raises if the source database doesn't exist yet
    (nothing to back up) or fails its integrity check (backing up a corrupt
    database just produces a corrupt backup, so refuse rather than do that)."""
    source_path = Path(config.DB_PATH)
    if not source_path.exists():
        raise FileNotFoundError(f"No database found at {source_path} -- nothing to back up.")

    if not check_integrity(source_path):
        raise RuntimeError(f"Refusing to back up {source_path}: it failed PRAGMA integrity_check.")

    BACKUPS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_path = BACKUPS_DIR / f"leads-{timestamp}.db"

    source_conn = sqlite3.connect(str(source_path))
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    return dest_path


def prune_old_backups(keep_count: int = KEEP_COUNT) -> int:
    """Keep only the newest `keep_count` backups (by filename, which sorts
    chronologically since it's an YYYYMMDD-HHMMSS timestamp). Returns the
    count removed."""
    if not BACKUPS_DIR.exists():
        return 0
    backups = sorted(BACKUPS_DIR.glob("leads-*.db"), reverse=True)
    removed = 0
    for backup_file in backups[keep_count:]:
        backup_file.unlink()
        removed += 1
    return removed


def main() -> None:
    dest_path = backup_once()
    print(f"[backup_db] Wrote {dest_path}")
    removed = prune_old_backups()
    if removed:
        print(f"[backup_db] Pruned {removed} backup(s), keeping the newest {KEEP_COUNT}")


if __name__ == "__main__":
    main()
