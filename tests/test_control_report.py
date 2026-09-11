"""Agent control board: ownership, next action, stale work and read-only output."""
from __future__ import annotations

from datetime import datetime, timezone

import config
from pipeline import control
from utils import db


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _lead(status: str, name: str = "Acme") -> int:
    return db.insert_lead(name, "plumber", "Crawley", status=status)


def _set_state_age(lead_id: int, modifier: str) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE state_history SET timestamp = datetime(?, ?) WHERE id = "
            "(SELECT MAX(id) FROM state_history WHERE lead_id = ?)",
            (NOW.strftime("%Y-%m-%d %H:%M:%S"), modifier, lead_id),
        )


def _email(lead_id: int, direction: str, modifier: str, classification: str = "") -> None:
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO email_threads "
            "(lead_id, direction, subject, body, from_addr, to_addr, classification, timestamp) "
            "VALUES (?, ?, 'subject', 'body', 'a@test', 'b@test', ?, datetime(?, ?))",
            (lead_id, direction, classification, NOW.strftime("%Y-%m-%d %H:%M:%S"), modifier),
        )


def test_stale_research_is_owned_by_designer(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    lead_id = _lead("researched")
    _set_state_age(lead_id, "-30 hours")
    item = control.classify(db.control_queue()[0], NOW)
    assert item["owner"] == "AGENCY-DESIGNER"
    assert item["agent_id"] == "webdesigner"
    assert item["severity"] == "stalled"
    assert "Build and validate" in item["next_action"]


def test_silent_emailed_lead_becomes_follow_up_due(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "FOLLOW_UP_ENABLED", True)
    monkeypatch.setattr(config, "FOLLOW_UP_AFTER_DAYS", 3)
    db.init_db()
    lead_id = _lead("emailed")
    _email(lead_id, "outbound", "-4 days")
    item = control.classify(db.control_queue()[0], NOW)
    assert item["severity"] == "action_due"
    assert item["owner"] == "PROMO-MARKETER"
    assert "one permitted follow-up" in item["next_action"]


def test_inbound_still_parked_as_emailed_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    lead_id = _lead("emailed")
    _email(lead_id, "outbound", "-1 day")
    _email(lead_id, "inbound", "-1 hour")
    item = control.classify(db.control_queue()[0], NOW)
    assert item["severity"] == "blocked"
    assert "Process the recorded reply" in item["next_action"]


def test_paid_lead_without_website_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    _lead("won")
    item = control.classify(db.control_queue()[0], NOW)
    assert item["owner"] == "FINN"
    assert item["severity"] == "blocked"
    assert "website record" in item["next_action"]


def test_report_is_read_only_and_excludes_terminal_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    _lead("new", "Open")
    _lead("lost", "Closed")
    before = tmp_path.joinpath("leads.db").read_bytes()
    report = control.build_report(now=NOW)
    after = tmp_path.joinpath("leads.db").read_bytes()
    assert report["read_only"] is True
    assert [item["business"] for item in report["leads"]] == ["Open"]
    assert before == after


def test_unknown_status_is_visible_as_unassigned():
    item = control.classify(
        {"id": 9, "business_name": "Odd", "status": "mystery", "state_changed_at": None},
        NOW,
    )
    assert item["owner"] == "UNASSIGNED"
    assert item["severity"] == "on_track"
    assert item["stage_age"] == "unknown"
