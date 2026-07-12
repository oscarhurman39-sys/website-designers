"""SQLite setup and query helpers for the cold-email sales pipeline.

All other modules talk to the database exclusively through this module --
nobody else should write raw SQL. This keeps the status state-machine and
schema constraints enforced in one place.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import config

# Every lead must be in exactly one of these statuses. Enforced with a SQL
# CHECK constraint so a bug can't silently write an invalid status.
ALLOWED_STATUSES = (
    "new",
    "researched",
    "designed",
    "emailed",
    "replied",
    "negotiating",
    "payment_sent",
    "won",
    "lost",
    "bounced",
    "unsubscribed",
)

_STATUS_LIST_SQL = ", ".join(f"'{s}'" for s in ALLOWED_STATUSES)

_SCHEMA = f"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS leads (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name   TEXT NOT NULL,
    niche           TEXT NOT NULL,
    location        TEXT,
    contact_email   TEXT,
    website_url     TEXT,
    scraped_info    TEXT,
    pain_point      TEXT,
    testimonial     TEXT,
    phone           TEXT,
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ({_STATUS_LIST_SQL})),
    unsubscribed    INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS email_threads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    direction   TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    message_id  TEXT,
    subject     TEXT,
    body        TEXT,
    from_addr   TEXT,
    to_addr     TEXT,
    classification TEXT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS websites (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id             INTEGER NOT NULL REFERENCES leads(id),
    template_niche      TEXT NOT NULL,
    repo_url            TEXT,
    repo_full_name      TEXT,
    preview_url         TEXT,
    vercel_project_id   TEXT,
    screenshot_url      TEXT,
    screenshot_path     TEXT,
    transferred         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS state_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    from_state  TEXT,
    to_state    TEXT NOT NULL,
    notes       TEXT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS unsubscribes (
    email       TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clicks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(contact_email);
CREATE INDEX IF NOT EXISTS idx_email_threads_lead ON email_threads(lead_id);
CREATE INDEX IF NOT EXISTS idx_websites_lead ON websites(lead_id);
"""


def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables/indexes if they don't already exist. Idempotent."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_add_column(conn, "websites", "screenshot_url", "TEXT")
        _migrate_add_column(conn, "websites", "screenshot_path", "TEXT")
        conn.commit()


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Idempotently add `column` to `table` if it doesn't already exist --
    covers DB files created before this column was added to the schema
    above (CREATE TABLE IF NOT EXISTS is a no-op against an existing
    table, so new columns need an explicit, safe-to-rerun ALTER TABLE)."""
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_connection(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Context-managed connection that commits on success, rolls back on error."""
    conn = _connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


# --- Leads -------------------------------------------------------------------

def insert_lead(
    business_name: str,
    niche: str,
    location: str = "",
    status: str = "new",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO leads (business_name, niche, location, status) VALUES (?, ?, ?, ?)",
            (business_name, niche, location, status),
        )
        lead_id = cur.lastrowid
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, NULL, ?, 'lead created')",
            (lead_id, status),
        )
        return lead_id


def get_lead(lead_id: int) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _row_to_dict(row)


def get_lead_by_email(email: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE contact_email = ? ORDER BY id DESC LIMIT 1", (email,)
        ).fetchone()
        return _row_to_dict(row)


def list_leads_by_status(status: str) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE status = ? ORDER BY id ASC", (status,)
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_leads() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_lead_fields(lead_id: int, **fields: Any) -> None:
    """Update arbitrary lead columns (e.g. contact_email, scraped_info, pain_point)."""
    if not fields:
        return
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [lead_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE leads SET {columns} WHERE id = ?", values)


def update_lead_status(lead_id: int, new_status: str, notes: str = "") -> None:
    if new_status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {ALLOWED_STATUSES}")
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        old_status = row["status"] if row else None
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id))
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, ?, ?)",
            (lead_id, old_status, new_status, notes),
        )


def mark_unsubscribed(lead_id: int, email: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE leads SET unsubscribed = 1 WHERE id = ?", (lead_id,))
        conn.execute(
            "INSERT OR REPLACE INTO unsubscribes (email, timestamp) VALUES (?, datetime('now'))",
            (email,),
        )
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES "
            "(?, (SELECT status FROM leads WHERE id = ?), 'unsubscribed', 'unsubscribe link clicked')",
            (lead_id, lead_id),
        )
        conn.execute("UPDATE leads SET status = 'unsubscribed' WHERE id = ?", (lead_id,))


def is_unsubscribed(email: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM unsubscribes WHERE email = ?", (email,)).fetchone()
        return row is not None


# --- Email threads -------------------------------------------------------------

def insert_email_thread(
    lead_id: int,
    direction: str,
    subject: str,
    body: str,
    from_addr: str,
    to_addr: str,
    message_id: str = "",
    classification: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO email_threads
               (lead_id, direction, message_id, subject, body, from_addr, to_addr, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, direction, message_id, subject, body, from_addr, to_addr, classification),
        )
        return cur.lastrowid


def get_email_threads(lead_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM email_threads WHERE lead_id = ? ORDER BY timestamp ASC", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def count_outbound_emails_since(since: datetime) -> int:
    """Used to enforce hourly/daily sending caps."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM email_threads WHERE direction = 'outbound' AND timestamp >= ?",
            (since.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchone()
        return int(row["n"])


def emails_sent_last_hour() -> int:
    return count_outbound_emails_since(datetime.now(timezone.utc) - timedelta(hours=1))


def emails_sent_today() -> int:
    return count_outbound_emails_since(datetime.now(timezone.utc) - timedelta(days=1))


def message_id_seen(message_id: str) -> bool:
    """Idempotency guard so re-polling the inbox never double-logs a reply."""
    if not message_id:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM email_threads WHERE message_id = ? LIMIT 1", (message_id,)
        ).fetchone()
        return row is not None


# --- Websites --------------------------------------------------------------

def insert_website(
    lead_id: int,
    template_niche: str,
    repo_url: str,
    repo_full_name: str,
    preview_url: str,
    vercel_project_id: str = "",
    screenshot_url: str = "",
    screenshot_path: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO websites
               (lead_id, template_niche, repo_url, repo_full_name, preview_url, vercel_project_id,
                screenshot_url, screenshot_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, template_niche, repo_url, repo_full_name, preview_url, vercel_project_id,
             screenshot_url, screenshot_path),
        )
        return cur.lastrowid


def update_website_screenshot_url(lead_id: int, screenshot_url: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE websites SET screenshot_url = ? WHERE lead_id = ?", (screenshot_url, lead_id)
        )


def get_website_by_lead(lead_id: int) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM websites WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lead_id,)
        ).fetchone()
        return _row_to_dict(row)


def mark_website_transferred(lead_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE websites SET transferred = 1 WHERE lead_id = ?", (lead_id,))


# --- Clicks ------------------------------------------------------------------

def log_click(lead_id: int) -> None:
    with get_connection() as conn:
        conn.execute("INSERT INTO clicks (lead_id) VALUES (?)", (lead_id,))


def get_last_email_timestamp(lead_id: int) -> Optional[str]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT timestamp FROM email_threads WHERE lead_id = ? AND direction = 'outbound' "
            "ORDER BY timestamp DESC LIMIT 1",
            (lead_id,),
        ).fetchone()
        return row["timestamp"] if row else None


# --- State history -----------------------------------------------------------

def log_state_history(lead_id: int, from_state: Optional[str], to_state: str, notes: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, ?, ?)",
            (lead_id, from_state, to_state, notes),
        )
