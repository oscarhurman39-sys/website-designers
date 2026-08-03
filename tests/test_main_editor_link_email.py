"""Tests for main.py's _send_editor_link_email -- the editor link now
actually gets emailed to the client (see PLAN.md's automation audit),
instead of only ever being printed to the operator's console or shown in
the dashboard -- and the new `release <lead_id>` console command, the
missing counterpart to `takeover <lead_id>` (sales_agent.end_takeover()
existed but nothing ever called it). Real tmp_path DB; email sending is
mocked."""
from __future__ import annotations

from unittest.mock import Mock

import main
from utils import db


def test_send_editor_link_email_sends_and_logs_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")

    import utils.email_utils as email_utils_module
    send_mock = Mock(return_value="<msgid@test>")
    monkeypatch.setattr(email_utils_module, "send_email", send_mock)

    main._send_editor_link_email(lead_id)

    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["to_addr"] == "owner@joescafe.example"
    assert "edit" in send_mock.call_args.kwargs["subject"].lower()

    thread = db.get_email_threads(lead_id)
    assert len(thread) == 1
    assert thread[0]["direction"] == "outbound"
    assert thread[0]["to_addr"] == "owner@joescafe.example"


def test_send_editor_link_email_skips_lead_with_no_contact_email(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    import utils.email_utils as email_utils_module
    send_mock = Mock()
    monkeypatch.setattr(email_utils_module, "send_email", send_mock)

    main._send_editor_link_email(lead_id)

    send_mock.assert_not_called()
    assert db.get_email_threads(lead_id) == []


def test_send_editor_link_email_handles_send_failure_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")

    import utils.email_utils as email_utils_module
    monkeypatch.setattr(
        email_utils_module, "send_email",
        Mock(side_effect=RuntimeError("Refusing to send: owner@joescafe.example is unsubscribed.")),
    )

    main._send_editor_link_email(lead_id)  # must not raise
    assert db.get_email_threads(lead_id) == []


# --- release <lead_id> console command --------------------------------------------

def test_release_command_clears_the_takeover_block(monkeypatch):
    from agents import sales_agent
    sales_agent._TAKEOVER_LEAD_IDS.add(42)
    assert sales_agent.is_under_takeover(42) is True

    main._handle_command("release 42")

    assert sales_agent.is_under_takeover(42) is False


def test_release_command_requires_a_numeric_lead_id(monkeypatch):
    from agents import sales_agent
    end_takeover_mock = Mock()
    monkeypatch.setattr(sales_agent, "end_takeover", end_takeover_mock)

    main._handle_command("release not-a-number")
    main._handle_command("release")

    end_takeover_mock.assert_not_called()
