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
    place_id        TEXT,
    website_status  TEXT,
    site_score      INTEGER,
    lead_score      INTEGER,
    contact_channel TEXT,
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
    torn_down_at        TEXT,
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
    with get_connection(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_add_column(conn, "websites", "screenshot_url", "TEXT")
        _migrate_add_column(conn, "websites", "screenshot_path", "TEXT")
        # Autonomous negotiation (agents/sales_agent.py): the last code-clamped
        # price quoted to the lead, the GitHub username parsed from their
        # post-payment reply for the automated repo handover (main.py), and the
        # open Stripe Checkout URL (persisted so a follow-up reply re-sends the
        # SAME link instead of minting a second payable session).
        _migrate_add_column(conn, "leads", "quoted_price_usd", "INTEGER")
        _migrate_add_column(conn, "leads", "github_username", "TEXT")
        _migrate_add_column(conn, "leads", "checkout_url", "TEXT")
        _migrate_add_column(conn, "websites", "torn_down_at", "TEXT")
        # Google place id for leads found by agents/sourcing_agent.py (NULL for
        # manual/CSV leads). Its index is created here rather than in _SCHEMA
        # because on a pre-existing DB the column only exists after the migration.
        _migrate_add_column(conn, "leads", "place_id", "TEXT")
        # Structured business facts from Google Places (agents/sourcing_agent.py),
        # rendered straight into the preview site by agents/design_agent.py:
        # a real phone/address/rating on the page is what makes it look like
        # *their* site rather than a template.
        _migrate_add_column(conn, "leads", "address", "TEXT")
        _migrate_add_column(conn, "leads", "google_rating", "REAL")
        _migrate_add_column(conn, "leads", "google_reviews_count", "INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_place_id ON leads(place_id)")
        # Multi-mailbox sending (utils/mailboxes.py): the mailbox user that
        # first emailed this lead, so every later message in the thread leaves
        # from the same address and replies land in the same inbox.
        _migrate_add_column(conn, "leads", "sender_account", "TEXT")
        # Follow-up reminder (sales_agent.send_follow_up_if_due) and the amount
        # actually paid (webhook_server), so niches can be ranked by revenue.
        _migrate_add_column(conn, "leads", "follow_up_sent_at", "TEXT")
        _migrate_add_column(conn, "leads", "won_amount", "INTEGER")
        # Persist acquisition quality so the sales queue can prefer clear website needs.
        _migrate_add_column(conn, "leads", "website_status", "TEXT")
        _migrate_add_column(conn, "leads", "site_score", "INTEGER")
        _migrate_add_column(conn, "leads", "lead_score", "INTEGER")
        _migrate_add_column(conn, "leads", "contact_channel", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_send_priority ON leads(status, lead_score DESC, id ASC)")
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


def list_sendable_leads_by_priority() -> list[dict[str, Any]]:
    """Return designed leads in explicit acquisition priority order."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM leads WHERE status = 'designed' "
            "ORDER BY CASE WHEN lead_score IS NULL THEN 1 ELSE 0 END, lead_score DESC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def last_cold_email_at(niche: str, location: Optional[str], within_days: int) -> Optional[str]:
    """Timestamp of the most recent COLD email to any lead in this
    (niche, town) bucket inside the window, or None.

    Anchored on state_history rather than email_threads (which also holds
    follow-ups, negotiation replies and handover mail), and on
    notes='Cold email sent' rather than to_state='emailed' alone --
    send_follow_up_if_due() and insert_lead() both write to_state='emailed'
    rows of their own, and either would otherwise re-arm the cooldown.
    """
    if within_days <= 0:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT MAX(sh.timestamp) AS ts FROM state_history sh "
            "JOIN leads l ON l.id = sh.lead_id "
            "WHERE sh.to_state = 'emailed' AND sh.notes = 'Cold email sent' "
            "AND l.niche = ? COLLATE NOCASE "
            # COALESCE both sides: leads.location is nullable, and `l.location = ?`
            # never matches when either side is NULL, so a town-less lead would
            # otherwise neither block nor be blocked.
            "AND COALESCE(l.location, '') = COALESCE(?, '') COLLATE NOCASE "
            "AND sh.timestamp > datetime('now', ?)",
            (niche, location or "", f"-{int(within_days)} days"),
        ).fetchone()
        return row["ts"] if row and row["ts"] else None


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


def update_lead_status_unless(
    lead_id: int, new_status: str, notes: str = "", unless_current: tuple[str, ...] = ()
) -> bool:
    """Like update_lead_status, but a no-op returning False if the lead's
    *current* status is in `unless_current`. Check and write happen in one
    guarded UPDATE, so a concurrent writer in another process (e.g.
    webhook_server.py marking a lead 'won' the moment Stripe confirms
    payment) can never be overwritten by a slower negotiation-loop write."""
    if new_status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {ALLOWED_STATUSES}")
    if not unless_current:
        # `status NOT IN ()` is a SQLite syntax error, not a tautology; with
        # nothing to guard against this is just an unconditional update.
        old = get_lead(lead_id)
        if old is None:
            return False
        update_lead_status(lead_id, new_status, notes)
        return True
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if row is None:
            return False
        old_status = row["status"]
        placeholders = ",".join("?" for _ in unless_current)
        cur = conn.execute(
            f"UPDATE leads SET status = ? WHERE id = ? AND status NOT IN ({placeholders})",
            (new_status, lead_id, *unless_current),
        )
        if cur.rowcount == 0:
            return False
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, ?, ?)",
            (lead_id, old_status, new_status, notes),
        )
        return True


def mark_unsubscribed(lead_id: int, email: str, notes: str = "unsubscribe link clicked") -> Optional[str]:
    """Suppress `email` and flag the lead unsubscribed. Returns the lead's
    status *before* this call (or None if the lead is gone).

    Suppression (the unsubscribes-table insert and the `unsubscribed` flag) is
    unconditional -- an opt-out must always block future sends. The *status*
    write, however, is guarded: a paid lead ('won'/'payment_sent') is never
    demoted to 'unsubscribed', because that would drop it out of the handover
    queue (main.py's _finalize_won_leads selects only 'won') and silently
    strand a customer who paid. Callers should alert a human when the returned
    prior status is a paid one."""
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        prior_status = row["status"] if row else None
        conn.execute("UPDATE leads SET unsubscribed = 1 WHERE id = ?", (lead_id,))
        conn.execute(
            "INSERT OR REPLACE INTO unsubscribes (email, timestamp) VALUES (?, datetime('now'))",
            (email,),
        )
        # Preserve a paid lead's status so its handover isn't lost; only the
        # suppression above applies to it.
        if prior_status not in ("won", "payment_sent"):
            conn.execute(
                "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, 'unsubscribed', ?)",
                (lead_id, prior_status, notes),
            )
            conn.execute("UPDATE leads SET status = 'unsubscribed' WHERE id = ?", (lead_id,))
        else:
            conn.execute(
                "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, ?, ?)",
                (lead_id, prior_status, prior_status,
                 f"Unsubscribe recorded (suppressed) but status kept ({prior_status}) to preserve handover"),
            )
        return prior_status


def suppress_email(email: str) -> None:
    """Record an email-level opt-out WITHOUT changing any lead's status.

    Used when a prospect replies to opt out (e.g. 'stop emailing me'): the
    reply sets the lead to 'lost' for reporting, but the suppression must also
    be written to the unsubscribes table so the same address can never be
    cold-emailed again -- including if the business is later re-ingested from a
    fresh CSV as a brand-new lead row. Idempotent and status-agnostic (unlike
    mark_unsubscribed, which also drives the 'unsubscribed' status)."""
    if not email:
        return
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO unsubscribes (email, timestamp) VALUES (?, datetime('now'))",
            (email,),
        )
        conn.execute("UPDATE leads SET unsubscribed = 1 WHERE contact_email = ?", (email,))


def is_unsubscribed(email: str) -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM unsubscribes WHERE email = ?", (email,)).fetchone()
        return row is not None


def place_id_exists(place_id: str) -> bool:
    """Dedupe key for sourced leads: has this Google place already been inserted?"""
    if not place_id:
        return False
    with get_connection() as conn:
        row = conn.execute("SELECT 1 FROM leads WHERE place_id = ? LIMIT 1", (place_id,)).fetchone()
        return row is not None


def lead_exists_by_name_and_location(business_name: str, location: str) -> bool:
    """Case-insensitive fallback dedupe for leads that have no place_id
    (typed in by hand or ingested from a CSV)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM leads WHERE LOWER(TRIM(business_name)) = LOWER(TRIM(?)) "
            "AND LOWER(TRIM(COALESCE(location, ''))) = LOWER(TRIM(?)) LIMIT 1",
            (business_name, location),
        ).fetchone()
        return row is not None


def count_sourced_leads_today() -> int:
    """How many sourced (place_id-bearing) leads were inserted since 00:00
    UTC today. Read from the DB rather than kept in memory so a restart
    mid-day can't blow through SOURCING_DAILY_LIMIT. created_at is written
    by SQLite's datetime('now'), which is UTC."""
    day_start = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE place_id IS NOT NULL AND created_at >= ?",
            (day_start,),
        ).fetchone()
        return int(row["n"])


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


def emails_sent_today_by_account(user: str) -> int:
    """Outbound emails sent from one mailbox in the last 24h -- the
    per-mailbox counterpart of emails_sent_today(), over the same rolling
    window so config.EMAIL_MAX_PER_DAY and EMAIL_MAX_PER_DAY_PER_ACCOUNT are
    measured the same way. Case-insensitive because an operator may spell
    the same mailbox differently in EMAIL_USER and EMAIL_ACCOUNTS."""
    since = datetime.now(timezone.utc) - timedelta(days=1)
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM email_threads "
            "WHERE direction = 'outbound' AND from_addr = ? COLLATE NOCASE AND timestamp >= ?",
            (user, since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchone()
        return int(row["n"])


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


# Lead statuses whose preview may be torn down once it's past its TTL: the
# cold email went out (or the lead ended) and nobody is talking to us. Every
# other status is either pre-email ('new'/'researched'/'designed' -- the
# 7-day promise hasn't started yet) or a live conversation / paying client
# whose site must stay up.
PREVIEW_EXPIRABLE_STATUSES = ("emailed", "lost", "bounced", "unsubscribed")


def list_live_previews() -> list[dict[str, Any]]:
    """Every preview still deployed: not transferred to a client and not yet
    torn down, regardless of age or lead status. Same row shape as
    list_expired_previews. Used by `teardown.py --all` for the pre-launch
    reset, never by the hourly expiry pass."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT w.id AS website_id, w.lead_id, w.repo_full_name, w.preview_url,
                      w.vercel_project_id, w.created_at,
                      l.business_name, l.status
               FROM websites w
               JOIN leads l ON l.id = w.lead_id
               WHERE w.transferred = 0 AND w.torn_down_at IS NULL
               ORDER BY w.id ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def list_expired_previews(ttl_days: int) -> list[dict[str, Any]]:
    """Websites whose preview has outlived its promised TTL and can be torn
    down: not transferred to a client, not already torn down, created more
    than `ttl_days` ago, and belonging to a lead in PREVIEW_EXPIRABLE_STATUSES.

    The TTL is also measured against the lead's most recent outbound email,
    not just the website row's created_at: a site can sit 'designed' for
    days before the rate-limited sender gets to it, and the "live for 7
    days" promise is made at send time, so a preview must never disappear
    less than `ttl_days` after the email that advertised it.

    Returns joined rows including the lead's business_name (needed to
    rebuild the Vercel project name) and status (for the history note).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
    status_sql = ", ".join("?" for _ in PREVIEW_EXPIRABLE_STATUSES)
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT w.id AS website_id, w.lead_id, w.repo_full_name, w.preview_url,
                       w.vercel_project_id, w.created_at,
                       l.business_name, l.status
                FROM websites w
                JOIN leads l ON l.id = w.lead_id
                WHERE w.transferred = 0
                  AND w.torn_down_at IS NULL
                  AND w.created_at < ?
                  AND l.status IN ({status_sql})
                  AND NOT EXISTS (
                      SELECT 1 FROM email_threads e
                      WHERE e.lead_id = w.lead_id
                        AND e.direction = 'outbound'
                        AND e.timestamp >= ?
                  )
                ORDER BY w.created_at ASC""",
            (cutoff, *PREVIEW_EXPIRABLE_STATUSES, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_website_torn_down(website_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE websites SET torn_down_at = datetime('now') WHERE id = ?", (website_id,)
        )


# --- Test-lead cleanup -----------------------------------------------------

def find_test_leads(
    business_names: tuple[str, ...],
    contact_email: str = "",
    locations: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Leads created by manual test runs (test_email.py / quick_run.py):
    matched by the throwaway business names those scripts use, by the
    operator's own contact email, or by a made-up location such as
    "Testville". An empty `contact_email` matches nothing (rather than every
    lead with a blank email)."""
    clauses = []
    params: list[Any] = []
    if business_names:
        clauses.append(f"business_name IN ({', '.join('?' for _ in business_names)})")
        params.extend(business_names)
    if contact_email:
        clauses.append("contact_email = ?")
        params.append(contact_email)
    if locations:
        clauses.append(f"location IN ({', '.join('?' for _ in locations)})")
        params.extend(locations)
    if not clauses:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM leads WHERE {' OR '.join(clauses)} ORDER BY id ASC", params
        ).fetchall()
        return [dict(r) for r in rows]


def delete_lead_cascade(lead_id: int) -> None:
    """Hard-delete a lead and every row referencing it, children first so
    the FK constraints (PRAGMA foreign_keys = ON) don't reject the delete.
    Only for throwaway test leads -- real leads are never deleted, their
    status just changes."""
    with get_connection() as conn:
        for table in ("websites", "email_threads", "clicks", "state_history"):
            conn.execute(f"DELETE FROM {table} WHERE lead_id = ?", (lead_id,))
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))


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


def list_follow_up_due(after_days: int) -> list[dict[str, Any]]:
    """'emailed' leads whose cold email went out at least `after_days` ago,
    who never wrote back, aren't suppressed, and haven't had the one
    reminder yet. Oldest first so nobody waits longer than necessary."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT l.* FROM leads l WHERE l.status = 'emailed' AND l.unsubscribed = 0 "
            "AND l.follow_up_sent_at IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM email_threads t WHERE t.lead_id = l.id AND t.direction = 'inbound') "
            "AND (SELECT MAX(t.timestamp) FROM email_threads t WHERE t.lead_id = l.id AND t.direction = 'outbound') "
            "    <= datetime('now', ?) "
            "ORDER BY l.id",
            (f"-{int(after_days)} days",),
        ).fetchall()
        return [dict(r) for r in rows]


def outcome_report() -> list[dict[str, Any]]:
    """Per-niche funnel: leads, emailed, replied, won, revenue. Feeds the
    'which niches make money' decision once real sends have happened."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT niche, COUNT(*) AS leads, "
            "SUM(CASE WHEN status IN ('emailed','replied','negotiating','payment_sent','won','lost') THEN 1 ELSE 0 END) AS emailed, "
            "SUM(CASE WHEN EXISTS (SELECT 1 FROM email_threads t WHERE t.lead_id = leads.id AND t.direction='inbound') THEN 1 ELSE 0 END) AS replied, "
            "SUM(CASE WHEN status = 'won' THEN 1 ELSE 0 END) AS won, "
            "COALESCE(SUM(won_amount), 0) AS revenue "
            "FROM leads GROUP BY niche ORDER BY revenue DESC, won DESC, replied DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# --- State history -----------------------------------------------------------

def log_state_history(lead_id: int, from_state: Optional[str], to_state: str, notes: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes) VALUES (?, ?, ?, ?)",
            (lead_id, from_state, to_state, notes),
        )
