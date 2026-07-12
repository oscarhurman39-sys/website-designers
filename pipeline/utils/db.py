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

-- Single-row table persisting the cold-email send-pacing cursor
-- (sales_agent.py's rate limiter) so a crash/restart can't send a burst of
-- emails back-to-back by forgetting the randomized 120-300s delay that was
-- in flight -- see get_next_send_allowed_at()/set_next_send_allowed_at().
CREATE TABLE IF NOT EXISTS send_pacing (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    next_allowed_at     TEXT NOT NULL
);

-- Permanent do-not-contact list, independent of any single lead row: a
-- negative reply, unsubscribe click, or SendGrid unsubscribe/spamreport
-- event adds the address (and its domain) here so the SAME email showing up
-- again in a future CSV import (a new `leads` row) never gets emailed
-- again either -- see add_to_suppression_list()/is_suppressed().
CREATE TABLE IF NOT EXISTS global_suppression_list (
    email       TEXT PRIMARY KEY,
    domain      TEXT NOT NULL,
    reason      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Generic per-lead event log with a JSON payload column, for events that
-- don't fit state_history's from-state/to-state shape (e.g. a takeover
-- audit entry, a screenshot-captured event) -- see log_lead_event().
CREATE TABLE IF NOT EXISTS lead_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id     INTEGER NOT NULL REFERENCES leads(id),
    event_type  TEXT NOT NULL,
    payload     TEXT,
    actor       TEXT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(contact_email);
CREATE INDEX IF NOT EXISTS idx_email_threads_lead ON email_threads(lead_id);
CREATE INDEX IF NOT EXISTS idx_websites_lead ON websites(lead_id);
CREATE INDEX IF NOT EXISTS idx_suppression_domain ON global_suppression_list(domain);
CREATE INDEX IF NOT EXISTS idx_lead_events_lead ON lead_events(lead_id);
"""


def init_db(db_path: Optional[str] = None) -> None:
    """Create all tables/indexes if they don't already exist. Idempotent."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_add_column(conn, "websites", "screenshot_url", "TEXT")
        _migrate_add_column(conn, "websites", "screenshot_path", "TEXT")
        # pending_send_id: set right before handing an email to the transport,
        # cleared right after a confirmed send -- see mark_send_pending()/
        # clear_send_pending(). A non-NULL value after a crash means "we don't
        # know if this went out," so send_next_pending() skips it rather than
        # risk a duplicate send.
        _migrate_add_column(conn, "leads", "pending_send_id", "TEXT")
        _migrate_add_column(conn, "email_threads", "prompt_version", "TEXT")
        _migrate_add_column(conn, "websites", "template_version", "TEXT")
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
        _insert_suppression(conn, email, "unsubscribe link clicked")


def is_unsubscribed(email: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM unsubscribes WHERE email = ?", (email,)).fetchone()
        return row is not None


# --- Global suppression list --------------------------------------------------

def _email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def _insert_suppression(conn: sqlite3.Connection, email: str, reason: str) -> None:
    """Shared by add_to_suppression_list() and mark_unsubscribed() so both
    paths write through the same connection/transaction as their caller."""
    email = (email or "").strip().lower()
    if not email:
        return
    conn.execute(
        "INSERT OR REPLACE INTO global_suppression_list (email, domain, reason, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (email, _email_domain(email), reason),
    )


def add_to_suppression_list(email: str, reason: str = "") -> None:
    """Permanently block `email` (by exact address AND its domain) from ever
    receiving another cold email from this pipeline, regardless of which
    lead row it's attached to in the future -- see is_suppressed()."""
    with get_connection() as conn:
        _insert_suppression(conn, email, reason)


def is_suppressed(email: str) -> bool:
    """True if `email` (or any address at the same domain) is on the
    permanent do-not-contact list."""
    email = (email or "").strip().lower()
    if not email:
        return False
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM global_suppression_list WHERE email = ? OR domain = ? LIMIT 1",
            (email, _email_domain(email)),
        ).fetchone()
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
    prompt_version: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO email_threads
               (lead_id, direction, message_id, subject, body, from_addr, to_addr, classification, prompt_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, direction, message_id, subject, body, from_addr, to_addr, classification, prompt_version or None),
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


def get_last_domain_email_timestamp(domain: str) -> Optional[str]:
    """Most recent outbound send timestamp to any address @domain, or None
    if that domain has never been emailed. Used to enforce a 24h
    per-domain cooldown (see sales_agent.py's send_next_pending()) so two
    different contacts at the same company don't both get cold-emailed the
    same day."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT timestamp FROM email_threads WHERE direction = 'outbound' "
            "AND to_addr LIKE ? ORDER BY timestamp DESC LIMIT 1",
            (f"%@{domain}",),
        ).fetchone()
        return row["timestamp"] if row else None


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
    template_version: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO websites
               (lead_id, template_niche, repo_url, repo_full_name, preview_url, vercel_project_id,
                screenshot_url, screenshot_path, template_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, template_niche, repo_url, repo_full_name, preview_url, vercel_project_id,
             screenshot_url, screenshot_path, template_version or None),
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


# --- Send pacing (persisted rate-limit cursor) --------------------------------

def get_next_send_allowed_at() -> Optional[datetime]:
    """Earliest time the next cold email may go out, per the last-persisted
    randomized 120-300s delay -- None if no email has been sent yet (or the
    row was never written), meaning sending is allowed immediately."""
    with get_connection() as conn:
        row = conn.execute("SELECT next_allowed_at FROM send_pacing WHERE id = 1").fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(row["next_allowed_at"])


def set_next_send_allowed_at(when: datetime) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO send_pacing (id, next_allowed_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET next_allowed_at = excluded.next_allowed_at",
            (when.isoformat(),),
        )


# --- Idempotent sending (crash-safe pending-send marker) ----------------------

def mark_send_pending(lead_id: int, send_uuid: str) -> None:
    """Record that a send to `lead_id` is in flight, before calling the
    transport. If the process crashes before clear_send_pending() runs, this
    value survives the restart so send_next_pending() can skip the lead
    instead of risking a duplicate send -- see clear_send_pending()."""
    with get_connection() as conn:
        conn.execute("UPDATE leads SET pending_send_id = ? WHERE id = ?", (send_uuid, lead_id))


def clear_send_pending(lead_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE leads SET pending_send_id = NULL WHERE id = ?", (lead_id,))


# --- Lead events (JSON-payload timeline, e.g. takeover audit log) ------------

def log_lead_event(lead_id: int, event_type: str, payload: Optional[dict] = None, actor: str = "") -> None:
    """Record a timestamped event against a lead with an arbitrary JSON
    payload -- for events that don't fit state_history's from/to-state shape
    (operator takeover, screenshot captured, etc.)."""
    import json as _json

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO lead_events (lead_id, event_type, payload, actor) VALUES (?, ?, ?, ?)",
            (lead_id, event_type, _json.dumps(payload) if payload is not None else None, actor or None),
        )


def get_lead_events(lead_id: int) -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM lead_events WHERE lead_id = ? ORDER BY timestamp ASC", (lead_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- State history -----------------------------------------------------------

def log_state_history(lead_id: int, from_state: Optional[str], to_state: str, notes: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, ?, ?)",
            (lead_id, from_state, to_state, notes),
        )


# --- Metrics (dashboard.py's "Metrics" section) -------------------------------

def get_metrics_summary() -> dict[str, Any]:
    """Aggregate counters derived from existing tables -- everything here is
    computed from data the pipeline already records (no separate metrics
    table to keep in sync). Open/delivery rate aren't included: this
    pipeline doesn't currently process SendGrid's 'delivered'/'open' event
    types (only bounce/dropped/spamreport/unsubscribe -- see
    webhook_server.py), so there's no reliable source for them yet."""
    with get_connection() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) AS n FROM email_threads WHERE direction = 'outbound'"
        ).fetchone()["n"]
        bounced = conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status = 'bounced'").fetchone()["n"]
        unsubscribed = conn.execute("SELECT COUNT(*) AS n FROM unsubscribes").fetchone()["n"]
        won = conn.execute("SELECT COUNT(*) AS n FROM leads WHERE status = 'won'").fetchone()["n"]
        replies = conn.execute(
            "SELECT classification, COUNT(*) AS n FROM email_threads "
            "WHERE direction = 'inbound' AND classification IN ('positive', 'negative') "
            "GROUP BY classification"
        ).fetchall()
        reply_counts = {r["classification"]: r["n"] for r in replies}
        total_replied_leads = conn.execute(
            "SELECT COUNT(DISTINCT lead_id) AS n FROM email_threads WHERE direction = 'inbound'"
        ).fetchone()["n"]

        # Average time (seconds) from a lead's first outbound send to its
        # first inbound reply, across leads that have both.
        avg_response_row = conn.execute(
            """
            SELECT AVG(julianday(first_in.ts) - julianday(first_out.ts)) * 86400.0 AS avg_seconds
            FROM (
                SELECT lead_id, MIN(timestamp) AS ts FROM email_threads
                WHERE direction = 'outbound' GROUP BY lead_id
            ) first_out
            JOIN (
                SELECT lead_id, MIN(timestamp) AS ts FROM email_threads
                WHERE direction = 'inbound' GROUP BY lead_id
            ) first_in ON first_in.lead_id = first_out.lead_id
            """
        ).fetchone()
        avg_response_seconds = avg_response_row["avg_seconds"] if avg_response_row else None

    return {
        "emails_sent": sent,
        "bounce_rate": (bounced / sent) if sent else 0.0,
        "reply_rate": (total_replied_leads / sent) if sent else 0.0,
        "positive_replies": reply_counts.get("positive", 0),
        "negative_replies": reply_counts.get("negative", 0),
        "unsubscribe_rate": (unsubscribed / sent) if sent else 0.0,
        "conversion_rate": (won / sent) if sent else 0.0,
        "closed_revenue_usd": won * config.WEBSITE_PRICE_USD,
        "avg_response_time_seconds": avg_response_seconds,
    }
