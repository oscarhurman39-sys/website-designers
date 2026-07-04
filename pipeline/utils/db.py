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
    # Established-business targeting gate (lead_agent.py): a Places listing
    # with too few reviews or no photos is set aside rather than pursued.
    "filtered",
)

_STATUS_LIST_SQL = ", ".join(f"'{s}'" for s in ALLOWED_STATUSES)

# The full, current leads-table definition, kept as its own constant so the
# CHECK-constraint rebuild migration (_migrate_leads_status_check) recreates
# the table with exactly this shape rather than a hand-copied duplicate.
_LEADS_TABLE_SQL = f"""
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
    address         TEXT,
    opening_hours   TEXT,
    categories      TEXT,
    rating          REAL,
    review_count    INTEGER,
    reviews         TEXT,
    image_note      TEXT,
    followup_1_sent TEXT,
    followup_2_sent TEXT,
    status          TEXT NOT NULL DEFAULT 'new'
                    CHECK (status IN ({_STATUS_LIST_SQL})),
    unsubscribed    INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_SCHEMA = f"""
PRAGMA foreign_keys = ON;

{_LEADS_TABLE_SQL}

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

-- Email engagement events ingested from the SendGrid Event Webhook
-- (opens/clicks/etc.). sg_event_id is SendGrid's unique per-event id, used
-- for idempotency so the same event delivered twice is only stored once.
CREATE TABLE IF NOT EXISTS email_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    event_type  TEXT NOT NULL,
    sg_event_id TEXT UNIQUE,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(contact_email);
CREATE INDEX IF NOT EXISTS idx_email_threads_lead ON email_threads(lead_id);
CREATE INDEX IF NOT EXISTS idx_websites_lead ON websites(lead_id);
CREATE INDEX IF NOT EXISTS idx_email_events_lead ON email_events(lead_id);
"""


def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables/indexes if they don't already exist. Idempotent."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_add_column(conn, "websites", "screenshot_url", "TEXT")
        _migrate_add_column(conn, "websites", "screenshot_path", "TEXT")
        _migrate_add_column(conn, "leads", "address", "TEXT")
        _migrate_add_column(conn, "leads", "opening_hours", "TEXT")
        _migrate_add_column(conn, "leads", "categories", "TEXT")
        _migrate_add_column(conn, "leads", "rating", "REAL")
        _migrate_add_column(conn, "leads", "review_count", "INTEGER")
        _migrate_add_column(conn, "leads", "reviews", "TEXT")
        _migrate_add_column(conn, "leads", "image_note", "TEXT")
        # UTC timestamps ("YYYY-MM-DD HH:MM:SS") of each follow-up send;
        # NULL/empty = not sent yet (see sales_agent.send_followups).
        _migrate_add_column(conn, "leads", "followup_1_sent", "TEXT")
        _migrate_add_column(conn, "leads", "followup_2_sent", "TEXT")
        _migrate_add_column(conn, "email_threads", "subject_variant", "TEXT")
        _migrate_leads_status_check(conn)
        conn.commit()


def _migrate_leads_status_check(conn: sqlite3.Connection) -> None:
    """Widen the leads.status CHECK constraint to include newer statuses
    (e.g. 'filtered') on databases created before they existed.

    SQLite can't ALTER a CHECK constraint in place, so we do the documented
    table-rebuild: rename the old table aside, recreate it with the current
    definition, copy every row across, drop the old one. Idempotent -- it's
    a no-op once the live constraint already lists 'filtered'. Runs with
    foreign keys off (executescript COMMITs first, so the PRAGMA takes)
    because child tables reference leads(id); ids are preserved by the
    column-for-column copy, so those references stay valid."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
    ).fetchone()
    if row is None or "'filtered'" in (row["sql"] or ""):
        return  # fresh table already has the current constraint, or no table yet

    # Copy only columns that exist on the old table (image_note was just
    # added above, so both sides have it; any columns the new canonical
    # shape adds are left to their defaults on rows copied across).
    old_cols = [r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()]
    collist = ", ".join(old_cols)
    # Build the new table under a temp name, copy into it, drop the old
    # table, then rename the new one into place. This ordering matters:
    # renaming the *original* leads table would make SQLite auto-rewrite
    # child tables' foreign keys to follow it (they reference "leads"), so
    # we instead leave "leads" as the name the children point at and swap
    # the table underneath it.
    new_table_sql = _LEADS_TABLE_SQL.replace(
        "CREATE TABLE IF NOT EXISTS leads", "CREATE TABLE _leads_new"
    )
    conn.executescript(
        "PRAGMA foreign_keys=OFF;\n"
        f"{new_table_sql}\n"
        f"INSERT INTO _leads_new ({collist}) SELECT {collist} FROM leads;\n"
        "DROP TABLE leads;\n"
        "ALTER TABLE _leads_new RENAME TO leads;\n"
        "PRAGMA foreign_keys=ON;\n"
    )


def _migrate_add_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Idempotently add `column` to `table` if it doesn't already exist --
    covers DB files created before this column was added to the schema
    above (CREATE TABLE IF NOT EXISTS is a no-op against an existing
    table, so new columns need an explicit, safe-to-rerun ALTER TABLE)."""
    existing_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing_columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH)
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
    subject_variant: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO email_threads
               (lead_id, direction, message_id, subject, body, from_addr, to_addr, classification, subject_variant)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, direction, message_id, subject, body, from_addr, to_addr, classification, subject_variant),
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


# --- Email engagement events (SendGrid webhook) + subject-line A/B stats ------

def record_email_event(lead_id: int, event_type: str, sg_event_id: str = "") -> None:
    """Record an engagement event (open/click/...) from the SendGrid Event
    Webhook. Idempotent on sg_event_id: the same event redelivered is stored
    once. An empty sg_event_id is stored as NULL (SQLite UNIQUE permits many
    NULLs), so events without an id are never silently deduped against each
    other."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO email_events (lead_id, event_type, sg_event_id) VALUES (?, ?, ?)",
            (lead_id, event_type, sg_event_id or None),
        )


def _lead_variant_map(conn: sqlite3.Connection) -> dict[int, str]:
    """lead_id -> the A/B subject variant it was sent, from its most recent
    outbound email that carried one."""
    mapping: dict[int, str] = {}
    for row in conn.execute(
        "SELECT lead_id, subject_variant FROM email_threads "
        "WHERE direction = 'outbound' AND subject_variant IN ('A', 'B') "
        "ORDER BY timestamp ASC"
    ):
        mapping[row["lead_id"]] = row["subject_variant"]  # later rows overwrite -> most recent wins
    return mapping


def subject_variant_stats() -> dict[str, dict[str, int]]:
    """Per-variant engagement for the subject-line A/B test.

    Returns {"A": {...}, "B": {...}} where each dict has: sends (outbound
    emails tagged with that variant), opens and clicks (distinct leads of
    that variant that opened / clicked). Opens come from SendGrid webhook
    events (email_events); clicks from the self-hosted preview-link tracker
    (clicks table) -- so clicks are available even without SendGrid, opens
    only once the Event Webhook is wired up."""
    stats = {v: {"sends": 0, "opens": 0, "clicks": 0} for v in ("A", "B")}
    with get_connection() as conn:
        for row in conn.execute(
            "SELECT subject_variant AS v, COUNT(*) AS n FROM email_threads "
            "WHERE direction = 'outbound' AND subject_variant IN ('A', 'B') GROUP BY subject_variant"
        ):
            stats[row["v"]]["sends"] = row["n"]

        variant_of = _lead_variant_map(conn)

        opened_leads = {r["lead_id"] for r in conn.execute(
            "SELECT DISTINCT lead_id FROM email_events WHERE event_type = 'open'"
        )}
        for lead_id in opened_leads:
            v = variant_of.get(lead_id)
            if v in stats:
                stats[v]["opens"] += 1

        clicked_leads = {r["lead_id"] for r in conn.execute("SELECT DISTINCT lead_id FROM clicks")}
        for lead_id in clicked_leads:
            v = variant_of.get(lead_id)
            if v in stats:
                stats[v]["clicks"] += 1
    return stats


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
