"""The money path must not fail silently.

Covers the hardening that followed the 2026-09-08 audit: a settled payment
is recorded exactly once whether it arrives by webhook or by polling Stripe,
a second paid session raises a human alert instead of being swallowed, a
bounce from MAILER-DAEMON reaches the bounce handler, one failing cycle
stage cannot take the others down, and the database runs in WAL with a
real backup.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import config
import main
from agents import sales_agent
from utils import db, email_utils, stripe_utils, tracker


def _lead(status: str = "payment_sent", **fields) -> int:
    db.init_db()
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Horsham", status=status)
    if fields:
        db.update_lead_fields(lead_id, **fields)
    return lead_id


# ------------------------------------------------------------ payments

def test_record_payment_promotes_once_and_stores_the_session(monkeypatch):
    alerts = []
    monkeypatch.setattr(sales_agent, "alert_needs_human", lambda lead, reason: alerts.append(reason))
    lead_id = _lead(checkout_url="https://checkout.stripe.com/c/pay/cs_1")

    assert sales_agent.record_payment(lead_id, "cs_1", 58900, source="checkout.session.completed")
    lead = db.get_lead(lead_id)
    assert lead["status"] == "won" and lead["won_amount"] == 589 and lead["paid_session_id"] == "cs_1"

    # A webhook replay (same session) is a no-op, not an alert.
    assert not sales_agent.record_payment(lead_id, "cs_1", 58900, source="checkout.session.completed")
    assert alerts == []
    assert db.get_lead(lead_id)["won_amount"] == 589


def test_a_second_paid_session_is_a_double_charge_alert(monkeypatch):
    alerts = []
    monkeypatch.setattr(sales_agent, "alert_needs_human", lambda lead, reason: alerts.append(reason))
    lead_id = _lead()
    sales_agent.record_payment(lead_id, "cs_first", 58900, source="reconciliation")

    assert not sales_agent.record_payment(lead_id, "cs_second", 58900, source="checkout.session.completed")
    assert len(alerts) == 1 and "cs_second" in alerts[0] and "cs_first" in alerts[0]


def test_find_paid_session_matches_lead_metadata_and_key_mode(monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_abc")
    sessions = [
        {"id": "cs_other", "metadata": {"lead_id": "99"}, "payment_status": "paid", "mode": "payment", "livemode": False},
        {"id": "cs_unpaid", "metadata": {"lead_id": "7"}, "payment_status": "unpaid", "mode": "payment", "livemode": False},
        {"id": "cs_live", "metadata": {"lead_id": "7"}, "payment_status": "paid", "mode": "payment", "livemode": True},
        {"id": "cs_hit", "metadata": {"lead_id": "7"}, "payment_status": "paid", "mode": "payment", "livemode": False,
         "amount_total": 58900},
    ]
    monkeypatch.setattr(stripe_utils.stripe.checkout.Session, "list", lambda **kw: sessions)

    assert stripe_utils.find_paid_session(7) == {"id": "cs_hit", "amount_total": 58900}
    assert stripe_utils.find_paid_session(8) is None

    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "not-a-stripe-key")
    assert stripe_utils.find_paid_session(7) is None  # unknown key mode: trust nothing


# -------------------------------------------------------------- bounces

def test_a_dsn_from_mailer_daemon_marks_the_lead_bounced_and_suppresses_it(monkeypatch):
    # Unique per run: leads.test.db persists between pytest runs, and
    # message_id_seen would otherwise skip a DSN it already handled last time.
    tag = uuid.uuid4().hex[:8]
    dead = f"owner-{tag}@deadhost.test"
    lead_id = _lead(status="emailed", contact_email=dead)
    dsn = email_utils.InboundEmail(
        message_id=f"<dsn-{tag}@mx.test>", in_reply_to="", subject="Undelivered Mail Returned to Sender",
        from_addr="MAILER-DAEMON@mx.test", to_addr="hello@caseywebsites.com",
        body=f"The following address failed:\n\n<{dead}>: host said 550 no such user",
        content_type="multipart/report", date="2026-09-09",
    )
    monkeypatch.setattr(sales_agent.email_utils, "fetch_unseen_emails", lambda: [dsn])

    assert sales_agent.check_inbox() == 1
    assert db.get_lead(lead_id)["status"] == "bounced"
    assert db.is_unsubscribed(dead)


# --------------------------------------------------------- cycle stages

def test_a_failing_stage_is_isolated_and_alerts_after_three_in_a_row(monkeypatch):
    notes = []
    monkeypatch.setattr(sales_agent, "_slack_notify", lambda text: notes.append(text) or True)
    main._stage_failures.clear()

    def boom():
        raise RuntimeError("smtp down")

    for _ in range(4):
        main._stage("send", boom)  # never raises
    assert len(notes) == 1 and "send" in notes[0]

    main._stage("send", lambda: None)
    assert "send" not in main._stage_failures


def test_cycle_handles_replies_and_handover_before_sending(monkeypatch):
    order = []
    for name in ("_process_inbox_csvs", "_maybe_source_leads", "_research_new_leads", "_poll_replies",
                 "_maybe_reconcile_payments", "_finalize_won_leads", "_send_outreach",
                 "_maybe_expire_previews", "_maybe_backup_db"):
        monkeypatch.setattr(main, name, (lambda n: lambda: order.append(n))(name))
    monkeypatch.setattr(main.design_agent, "run", lambda: order.append("design"))

    main._run_cycle()

    assert order.index("_poll_replies") < order.index("_send_outreach")
    assert order.index("_finalize_won_leads") < order.index("_send_outreach")
    assert order.index("_maybe_reconcile_payments") < order.index("_send_outreach")


# ------------------------------------------------------------- database

def test_database_runs_in_wal_mode():
    db.init_db()
    with db.get_connection() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_backup_is_a_real_copy_and_prunes_to_keep(monkeypatch, tmp_path):
    lead_id = _lead(status="won")
    monkeypatch.setattr(config, "DB_BACKUP_DIR", str(tmp_path))
    for _ in range(3):
        dest = db.backup_database(keep=2)
    assert dest.exists()
    assert len(list(tmp_path.glob("leads-*.db"))) <= 2

    import sqlite3
    copy = sqlite3.connect(str(dest))
    try:
        assert copy.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()[0] == "won"
    finally:
        copy.close()


# -------------------------------------------------------- click tracking

def test_tracked_link_is_only_used_when_the_endpoint_answers(monkeypatch):
    tracker._health_cache = (0.0, False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:5000")
    assert tracker.public_endpoint_up() is False  # a lead can never reach localhost

    tracker._health_cache = (0.0, False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")
    monkeypatch.setattr(tracker.requests, "get", lambda url, timeout: SimpleNamespace(status_code=200))
    assert tracker.public_endpoint_up() is True
    tracker._health_cache = (0.0, False)


# ------------------------------------------------------- dry-run re-queue

def test_dry_run_leads_are_requeued_once_live_send_is_armed():
    db.init_db()
    def emailed(note):
        lead_id = db.insert_lead("Acme Plumbing", "plumber", "Horsham", status="designed")
        db.update_lead_status(lead_id, "emailed", notes=note)
        return lead_id
    rehearsed = emailed("Cold email drafted (dry run -- not sent)")
    real = emailed("Cold email sent")
    real_after_rehearsal = emailed("Cold email drafted (dry run -- not sent)")
    db.log_state_history(real_after_rehearsal, "emailed", "emailed", notes="Cold email sent")
    followed_up = emailed("Cold email drafted (dry run -- not sent)")
    db.log_state_history(followed_up, "emailed", "emailed", notes="Follow-up reminder sent")

    assert db.requeue_dry_run_leads() == 2
    assert db.get_lead(rehearsed)["status"] == "designed"
    assert db.get_lead(followed_up)["status"] == "designed"   # a follow-up note does not mask the dry run
    assert db.get_lead(real)["status"] == "emailed"
    assert db.get_lead(real_after_rehearsal)["status"] == "emailed"
    assert db.requeue_dry_run_leads() == 0  # idempotent
