from __future__ import annotations

import json

import config
from pipeline import control, control_actions, reviewed_batch
from utils import db


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()


def _lead(status: str = "designed") -> int:
    return db.insert_lead("Acme", "plumber", "Crawley", status=status)


def _history(lead_id: int):
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM state_history WHERE lead_id = ? ORDER BY id", (lead_id,)).fetchall()]


def test_schema_has_hold_and_agent_history_columns(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    with db.get_connection() as conn:
        lead_cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
        hist_cols = {r["name"] for r in conn.execute("PRAGMA table_info(state_history)")}
    assert {"held_by", "held_at"} <= lead_cols
    assert "agent_id" in hist_cols


def test_hold_excludes_sendable_and_control_shows_hold(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    high = _lead("designed")
    low = _lead("designed")
    db.update_lead_fields(high, lead_score=99)
    db.update_lead_fields(low, lead_score=1)
    result = db.hold_lead(high, agent_id="marketer", reason="checking preview")
    assert result["ok"] is True
    assert [lead["id"] for lead in db.list_sendable_leads_by_priority()] == [low]
    report = control.build_report(include_all=True)
    held = next(item for item in report["leads"] if item["lead_id"] == high)
    assert held["held_by"] == "marketer"
    assert held["severity"] == "blocked"
    assert "Held by marketer" in held["next_action"]


def test_release_requires_same_agent_and_clears_hold(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    lead_id = _lead("designed")
    assert db.hold_lead(lead_id, agent_id="marketer", reason="hold")["ok"] is True
    assert db.release_lead(lead_id, agent_id="hello-3", reason="wrong")["ok"] is False
    assert db.get_lead(lead_id)["held_by"] == "marketer"
    assert db.release_lead(lead_id, agent_id="marketer", reason="done")["ok"] is True
    lead = db.get_lead(lead_id)
    assert lead["held_by"] is None
    assert lead["held_at"] is None


def test_close_silent_only_moves_emailed_to_lost_and_records_agent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    lead_id = _lead("emailed")
    result = db.close_silent_lead(lead_id, agent_id="marketer", reason="no reply")
    assert result["ok"] is True
    assert db.get_lead(lead_id)["status"] == "lost"
    rows = _history(lead_id)
    assert rows[-1]["from_state"] == "emailed"
    assert rows[-1]["to_state"] == "lost"
    assert rows[-1]["agent_id"] == "marketer"


def test_close_silent_refuses_protected_statuses(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    for status in ["replied", "negotiating", "payment_sent", "won", "designed"]:
        lead_id = _lead(status)
        result = db.close_silent_lead(lead_id, agent_id="marketer", reason="no reply")
        assert result["ok"] is False
        assert db.get_lead(lead_id)["status"] == status


def test_control_action_cli_json_and_exit_codes(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    lead_id = _lead("emailed")
    assert control_actions.main(["close-silent", str(lead_id), "--agent-id", "marketer"]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["ok"] is True
    assert success["action"] == "close-silent"
    assert control_actions.main(["close-silent", str(lead_id), "--agent-id", "marketer"]) == 1
    refused = json.loads(capsys.readouterr().out)
    assert refused["ok"] is False


def test_control_action_requires_agent_id(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    lead_id = _lead("designed")
    assert control_actions.main(["hold", str(lead_id), "--agent-id", ""]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "agent-id" in payload["error"]


def test_reviewed_batch_requires_agent_id_and_json_refusal(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    lead_id = _lead("designed")
    assert reviewed_batch.main(["--prepare", "--lead", str(lead_id), "--agent-id", ""]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "agent-id" in payload["error"]
