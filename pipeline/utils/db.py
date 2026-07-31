"""SQLite setup and query helpers for the cold-email sales pipeline.

All other modules talk to the database exclusively through this module --
nobody else should write raw SQL. This keeps the status state-machine and
schema constraints enforced in one place.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

-- Webhook replay protection: Stripe (and any other webhook source) retries
-- events on non-2xx responses and timeouts, so every event must be applied
-- at most once. Insert the event id here before acting on it; a conflict
-- means it was already handled.
CREATE TABLE IF NOT EXISTS processed_events (
    event_id    TEXT PRIMARY KEY,
    source      TEXT NOT NULL DEFAULT 'stripe',
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
        # Rendered template files are kept on disk so the GitHub hand-off
        # repo can be created lazily at transfer time (previews no longer
        # get a repo each -- see design_agent.py).
        _migrate_add_column(conn, "websites", "local_dir", "TEXT")
        # Design retry cap: a lead whose deploy keeps failing must not be
        # retried forever on every 60s cycle.
        _migrate_add_column(conn, "leads", "design_attempts", "INTEGER NOT NULL DEFAULT 0")
        # JSON audit of the lead's existing site (see utils/site_audit.py),
        # used for personalization and the per-lead "what we improved" list.
        _migrate_add_column(conn, "leads", "site_audit", "TEXT")
        # Structured content imported from the lead's existing site (see
        # utils/content_importer.py) -- real logo/photos/hours/services/
        # reviews/brand colors, consumed by design_agent.build_context() in
        # place of generic niche fallbacks/stock photos when present.
        _migrate_add_column(conn, "leads", "logo_url", "TEXT")
        _migrate_add_column(conn, "leads", "photos", "TEXT")
        _migrate_add_column(conn, "leads", "hours", "TEXT")
        _migrate_add_column(conn, "leads", "scraped_services", "TEXT")
        _migrate_add_column(conn, "leads", "reviews", "TEXT")
        _migrate_add_column(conn, "leads", "brand_colors", "TEXT")
        # Audits of OUR OWN generated preview/live sites (see
        # utils/site_audit.py's audited_target values below) -- distinct
        # from leads.site_audit, which is always the prospect's OLD site,
        # audited once at research time.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS site_audits (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id         INTEGER NOT NULL REFERENCES leads(id),
                audited_target  TEXT NOT NULL
                                CHECK (audited_target IN ('prospect_site', 'generated_preview', 'live_client_site')),
                url             TEXT,
                score           INTEGER,
                readiness_pct   INTEGER,
                result          TEXT NOT NULL,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_site_audits_lead ON site_audits(lead_id)")
        # Client-editor overrides (see agents/editor_agent.py) -- one row
        # per (lead_id, field), latest value wins. Which fields are
        # actually editable is a policy decision that belongs to
        # editor_agent.py, not this storage layer.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS site_edits (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL REFERENCES leads(id),
                field       TEXT NOT NULL,
                value       TEXT NOT NULL,
                edited_at   TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(lead_id, field)
            )"""
        )
        # Client editor magic-link sessions (see utils/editor_auth.py) --
        # DB-backed (not a stateless HMAC token like compliance.py/
        # tracker.py) specifically so a link can expire and be revoked
        # individually: publishing authority over a paying client's LIVE
        # site is a much higher-stakes grant than an unsubscribe click.
        # Only the token's hash is ever stored -- the raw token exists
        # only in the URL sent to the client.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS editor_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id       INTEGER NOT NULL REFERENCES leads(id),
                token_hash    TEXT NOT NULL UNIQUE,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at    TEXT NOT NULL,
                revoked_at    TEXT,
                last_used_at  TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_editor_sessions_lead ON editor_sessions(lead_id)")
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
    local_dir: str = "",
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO websites
               (lead_id, template_niche, repo_url, repo_full_name, preview_url, vercel_project_id,
                screenshot_url, screenshot_path, local_dir)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (lead_id, template_niche, repo_url, repo_full_name, preview_url, vercel_project_id,
             screenshot_url, screenshot_path, local_dir),
        )
        return cur.lastrowid


def update_website_repo(lead_id: int, repo_url: str, repo_full_name: str) -> None:
    """Record the GitHub hand-off repo once it's created (at transfer time)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE websites SET repo_url = ?, repo_full_name = ? WHERE lead_id = ?",
            (repo_url, repo_full_name, lead_id),
        )


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


def update_website_preview_url(lead_id: int, preview_url: str) -> None:
    """Record a new live preview_url after a republish (see
    agents/editor_agent.py's publish()) -- the same column DesignAgent
    itself sets on the initial deploy."""
    with get_connection() as conn:
        conn.execute("UPDATE websites SET preview_url = ? WHERE lead_id = ?", (preview_url, lead_id))


# --- Webhook idempotency -------------------------------------------------------

def record_event_once(event_id: str, source: str = "stripe") -> bool:
    """Return True exactly once per event_id: the first caller records it and
    may act on the event; every replay/retry afterwards gets False."""
    if not event_id:
        return False
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, source) VALUES (?, ?)",
            (event_id, source),
        )
        return cur.rowcount == 1


# --- Design retry cap ----------------------------------------------------------

def increment_design_attempts(lead_id: int) -> int:
    """Bump and return the lead's design_attempts counter."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE leads SET design_attempts = design_attempts + 1 WHERE id = ?", (lead_id,)
        )
        row = conn.execute("SELECT design_attempts FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return int(row["design_attempts"]) if row else 0


# --- Clicks ------------------------------------------------------------------

def log_click(lead_id: int) -> None:
    with get_connection() as conn:
        conn.execute("INSERT INTO clicks (lead_id) VALUES (?)", (lead_id,))


def count_clicks_since(lead_id: int, since: datetime) -> int:
    """Used by maintenance.py's monthly report -- the only real, already-
    tracked traffic signal this pipeline has (the click-tracked preview/
    share link, not general site analytics)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM clicks WHERE lead_id = ? AND timestamp >= ?",
            (lead_id, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()
        return int(row["n"])


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


# --- Site audits (QA/readiness scanner) ---------------------------------------

def insert_site_audit(lead_id: int, audited_target: str, result: dict[str, Any], url: str = "") -> int:
    """Persist one audit run (see utils/site_audit.py). `result` is the full
    {"checks", "pain_points", "improvements", "score", "readiness_pct", ...}
    dict, stored as JSON; `score`/`readiness_pct` are also pulled into their
    own columns so trend queries don't need to parse JSON every time."""
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO site_audits (lead_id, audited_target, url, score, readiness_pct, result)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (lead_id, audited_target, url, result.get("score"), result.get("readiness_pct"), json.dumps(result)),
        )
        return cur.lastrowid


def get_latest_site_audit(lead_id: int, audited_target: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM site_audits WHERE lead_id = ? AND audited_target = ? ORDER BY id DESC LIMIT 1",
            (lead_id, audited_target),
        ).fetchone()
        return _row_to_dict(row)


def list_site_audits(lead_id: int, audited_target: Optional[str] = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if audited_target:
            rows = conn.execute(
                "SELECT * FROM site_audits WHERE lead_id = ? AND audited_target = ? ORDER BY created_at ASC",
                (lead_id, audited_target),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM site_audits WHERE lead_id = ? ORDER BY created_at ASC", (lead_id,)
            ).fetchall()
        return [dict(r) for r in rows]


# --- Client editor overrides (see agents/editor_agent.py) ----------------------

def upsert_site_edit(lead_id: int, field: str, value: Any) -> None:
    """Persist the CURRENT value of one editable field for a lead -- a
    second edit to the same field overwrites the first (edited_at moves
    forward), it does not keep both. `value` is JSON-encoded so it can be
    a string or a list transparently."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO site_edits (lead_id, field, value, edited_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(lead_id, field) DO UPDATE SET value = excluded.value, edited_at = excluded.edited_at""",
            (lead_id, field, json.dumps(value)),
        )


def get_site_edits(lead_id: int) -> dict[str, Any]:
    """Every field this lead's client has edited, decoded back to its
    original type. A field whose stored JSON is corrupt is skipped rather
    than raising -- same defensive stance as content_importer.load_content."""
    with get_connection() as conn:
        rows = conn.execute("SELECT field, value FROM site_edits WHERE lead_id = ?", (lead_id,)).fetchall()
    result: dict[str, Any] = {}
    for row in rows:
        try:
            result[row["field"]] = json.loads(row["value"])
        except (ValueError, TypeError):
            continue
    return result


# --- Client editor sessions (see utils/editor_auth.py) -------------------------

def insert_editor_session(lead_id: int, token_hash: str, expires_at: datetime) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO editor_sessions (lead_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (lead_id, token_hash, expires_at.strftime("%Y-%m-%d %H:%M:%S")),
        )
        return cur.lastrowid


def get_editor_session(token_hash: str) -> Optional[dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM editor_sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return _row_to_dict(row)


def touch_editor_session(session_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE editor_sessions SET last_used_at = datetime('now') WHERE id = ?", (session_id,))


def revoke_editor_sessions(lead_id: int) -> None:
    """Revoke every currently-active session for a lead -- e.g. a
    designer suspects a link leaked, or the client requests a fresh one
    and the old one should stop working. Already-revoked/expired rows are
    left alone (their revoked_at, if any, keeps its original timestamp)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE editor_sessions SET revoked_at = datetime('now') WHERE lead_id = ? AND revoked_at IS NULL",
            (lead_id,),
        )
