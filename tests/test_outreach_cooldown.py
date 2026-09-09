"""The niche+town outreach cooldown.

Two businesses in the same trade and the same town get the same template with
their name swapped in, so sending to both inside a few days is what makes the
outreach read as a mailshot. `OUTREACH_COOLDOWN_DAYS` stops that.

The load-bearing tests here are the ones proving what must NOT arm the
cooldown: a follow-up reminder, a lead inserted straight at 'emailed', and any
other outbound mail. All three write rows that look like a cold email if you
squint at them, and getting this wrong silently freezes a whole town.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import config
from agents import sales_agent
from utils import db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "SENDGRID_API_KEY", "")
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(config, "OUTREACH_COOLDOWN_DAYS", 2)
    monkeypatch.setattr(sales_agent, "_next_send_allowed_at", datetime.min.replace(tzinfo=timezone.utc))
    db.init_db()
    return tmp_path


def _lead(name="Acme Plumbing", niche="plumber", location="Maidstone, Kent", status="designed") -> int:
    lead_id = db.insert_lead(name, niche, location, status=status)
    db.update_lead_fields(lead_id, contact_email=f"owner{lead_id}@acme.test")
    return lead_id


def _cold_email(lead_id: int, days_ago: float) -> None:
    """Backdate a cold email for this lead.

    Written through SQLite's own datetime('now', ?) -- a Python
    datetime.now(timezone.utc).isoformat() appends '+00:00' and string-compares
    wrongly against the rows the app writes.
    """
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes, timestamp) "
            "VALUES (?, 'designed', 'emailed', 'Cold email sent', datetime('now', ?))",
            (lead_id, f"-{days_ago} days"),
        )


def _blocked(lead_id: int) -> bool:
    return sales_agent._cooldown_blocked_until(db.get_lead(lead_id)) is not None


# --- the bucket ---------------------------------------------------------------

def test_same_niche_and_town_is_blocked_inside_the_window(env):
    _cold_email(_lead("First Plumbing"), days_ago=0.5)
    assert _blocked(_lead("Second Plumbing")) is True


def test_same_niche_and_town_is_clear_once_the_window_passes(env):
    _cold_email(_lead("First Plumbing"), days_ago=3)  # cooldown is 2 days
    assert _blocked(_lead("Second Plumbing")) is False


def test_a_different_town_in_the_same_niche_is_never_blocked(env):
    _cold_email(_lead("First Plumbing", location="Maidstone, Kent"), days_ago=0.5)
    assert _blocked(_lead("Second Plumbing", location="Ashford, Kent")) is False


def test_a_different_niche_in_the_same_town_is_never_blocked(env):
    _cold_email(_lead("First Plumbing", niche="plumber"), days_ago=0.5)
    assert _blocked(_lead("Bright Spark", niche="electrician")) is False


def test_blank_locations_share_one_bucket(env):
    """'' and NULL are the same town. leads.location is nullable and
    dashboard.py adds leads with location='', so exempting blanks would open a
    silent hole for every hand-added lead."""
    _cold_email(_lead("First Plumbing", location=""), days_ago=0.5)
    assert _blocked(_lead("Second Plumbing", location="")) is True


def test_zero_days_disables_the_cooldown(env, monkeypatch):
    monkeypatch.setattr(config, "OUTREACH_COOLDOWN_DAYS", 0)
    _cold_email(_lead("First Plumbing"), days_ago=0)
    assert _blocked(_lead("Second Plumbing")) is False


# --- what must NOT arm it -----------------------------------------------------

def test_a_follow_up_reminder_does_not_re_arm_the_cooldown(env):
    """send_follow_up_if_due writes to_state='emailed' too. Counting it would
    let one lead's reminders freeze its whole town indefinitely."""
    first = _lead("First Plumbing")
    _cold_email(first, days_ago=5)
    db.log_state_history(first, "emailed", "emailed", notes="Follow-up reminder sent")
    assert _blocked(_lead("Second Plumbing")) is False


def test_a_lead_inserted_straight_at_emailed_does_not_arm_it(env):
    """db.insert_lead writes state_history(to_state=<status>, notes='lead
    created'), so a lead created directly at 'emailed' looks like a send."""
    _lead("First Plumbing", status="emailed")
    assert _blocked(_lead("Second Plumbing")) is False


def test_other_outbound_mail_does_not_arm_the_cooldown(env):
    """The cooldown reads state_history, not email_threads -- which also holds
    negotiation replies, payment links and handover mail."""
    first = _lead("First Plumbing")
    db.insert_email_thread(lead_id=first, direction="outbound", subject="quote",
                           body="hi", from_addr="casey@x.test", to_addr="owner@acme.test",
                           message_id="<out@t>")
    assert _blocked(_lead("Second Plumbing")) is False


# --- the send path ------------------------------------------------------------

def test_a_blocked_lead_stays_designed_with_no_sender_pinned(env, monkeypatch):
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport",
                        lambda **kw: pytest.fail("a blocked lead must not be emailed"))
    _cold_email(_lead("First Plumbing"), days_ago=0.5)
    second = _lead("Second Plumbing")

    assert sales_agent._send_cold_email_impl(db.get_lead(second)) is False

    row = db.get_lead(second)
    assert row["status"] == "designed"      # comes back round on a later cycle
    assert row["sender_account"] is None    # no mailbox burned on it


def test_send_next_pending_skips_the_blocked_bucket_and_sends_the_next_one(env, monkeypatch):
    monkeypatch.setattr(sales_agent, "_can_send_now", lambda: True)
    sent: list[int] = []
    monkeypatch.setattr(sales_agent, "send_cold_email", lambda lead: sent.append(lead["id"]) or True)

    _cold_email(_lead("First Plumbing", location="Maidstone, Kent"), days_ago=0.5)
    _lead("Second Plumbing", location="Maidstone, Kent")          # blocked
    clear = _lead("Ashford Plumbing", location="Ashford, Kent")   # different town

    assert sales_agent.send_next_pending() == clear
    assert sent == [clear]


def test_follow_ups_still_go_out_while_the_bucket_is_cooling_down(env, monkeypatch):
    """The regression this whole design is arranged around: the cooldown must
    never reach _can_send_now(), which follow-ups share. A lead awaiting a
    reminder is by definition inside its own cooldown window."""
    monkeypatch.setattr(config, "FOLLOW_UP_ENABLED", True)
    monkeypatch.setattr(config, "FOLLOW_UP_AFTER_DAYS", 3)
    monkeypatch.setattr(sales_agent, "_can_send_now", lambda: True)
    replies: list[str] = []
    monkeypatch.setattr(sales_agent, "_send_thread_reply",
                        lambda lead, body, classification="": replies.append(classification) or True)

    # The silent lead was emailed 4 days ago, so its reminder is due. A
    # NEIGHBOUR in the same town was emailed half a day ago, so the town is
    # cooling down. Both must be true at once -- that is the whole point.
    silent = _lead("First Plumbing", status="emailed")
    _cold_email(silent, days_ago=4)
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO email_threads (lead_id, direction, subject, body, from_addr, to_addr, "
            "message_id, timestamp) VALUES (?, 'outbound', 's', 'b', 'casey@x.test', "
            "'owner@acme.test', '<a@t>', datetime('now', '-4 days'))",
            (silent,),
        )
    _cold_email(_lead("Neighbour Plumbing", status="emailed"), days_ago=0.5)

    assert _blocked(silent) is True                        # its town is cooling down
    assert sales_agent.send_follow_up_if_due() == silent   # and the reminder still goes
    assert replies == ["follow_up"]


# --- dry runs ------------------------------------------------------------------

def test_a_dry_run_does_not_record_a_send_or_arm_the_cooldown(env, monkeypatch):
    """ENABLE_LIVE_SEND=false logs to dry_run.log instead of sending, but the
    transport still returns a message_id, so the state-history note is the only
    place the difference can be recorded. If a dry run wrote 'Cold email sent'
    it would both lie in the audit trail and freeze a real town for a day."""
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport", lambda **kw: "<dry@run>")
    monkeypatch.setattr(sales_agent, "_validate_preview_link_for_send", lambda link: "https://preview.test")
    monkeypatch.setattr(sales_agent.db, "get_website_by_lead",
                        lambda lead_id: {"preview_url": "https://preview.test"})
    monkeypatch.setattr(sales_agent, "draft_cold_email", lambda lead: ("s", "b"))

    first = _lead("First Plumbing")
    assert sales_agent._send_cold_email_impl(db.get_lead(first)) is True
    assert db.get_lead(first)["status"] == "emailed"   # still moves on, no retry loop

    with db.get_connection() as conn:
        note = conn.execute(
            "SELECT notes FROM state_history WHERE lead_id = ? AND to_state = 'emailed' "
            "ORDER BY id DESC LIMIT 1", (first,)
        ).fetchone()["notes"]
    assert "dry run" in note.lower()
    assert note != "Cold email sent"

    # ...and the town is still open for business.
    assert _blocked(_lead("Second Plumbing")) is False
