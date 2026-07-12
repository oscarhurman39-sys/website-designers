"""Daily SQLite backup for the leads database -- NOT run automatically by
main.py; invoke this directly (see crontab.example) or via a system cron job.

Uses sqlite3's online backup API (`Connection.backup()`) rather than a raw
file copy, so a backup taken while main.py/webhook_server.py has the
database open mid-write still produces a consistent snapshot instead of a
corrupt or torn copy.

Usage:

    python backup_db.py                # from inside pipeline/
    python pipeline/backup_db.py       # from the repo root

Writes to pipeline/backups/leads-YYYYMMDD-HHMMSS.db and prunes anything
older than KEEP_DAYS days so backups/ doesn't grow unbounded.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import config

BACKUPS_DIR = Path(__file__).resolve().parent / "backups"
KEEP_DAYS = 14


def backup_once() -> Path:
    """Take one consistent snapshot of config.DB_PATH. Returns the backup path.
    Raises if the source database doesn't exist yet (nothing to back up)."""
    source_path = Path(config.DB_PATH)
    if not source_path.exists():
        raise FileNotFoundError(f"No database found at {source_path} -- nothing to back up.")

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


def prune_old_backups(keep_days: int = KEEP_DAYS) -> int:
    """Delete backup files older than `keep_days`. Returns count removed."""
    if not BACKUPS_DIR.exists():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for backup_file in BACKUPS_DIR.glob("leads-*.db"):
        if backup_file.stat().st_mtime < cutoff:
            backup_file.unlink()
            removed += 1
    return removed


def main() -> None:
    dest_path = backup_once()
    print(f"[backup_db] Wrote {dest_path}")
    removed = prune_old_backups()
    if removed:
        print(f"[backup_db] Pruned {removed} backup(s) older than {KEEP_DAYS} days")


if __name__ == "__main__":
    main()
