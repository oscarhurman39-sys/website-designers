"""With live sending armed, no cold email or follow-up leaves while
PUBLIC_BASE_URL is dead: the unsubscribe link inside it would be dead on
arrival. Only the operator-driven reviewed batch checked this before; the
unattended loop sent regardless.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import config
from agents import sales_agent
from utils import db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(config, "FOLLOW_UP_ENABLED", True)
    monkeypatch.setattr(sales_agent, "_can_send_now", lambda: True)
    monkeypatch.setattr(sales_agent, "_public_url_last_alert", None)
    db.init_db()


def _designed_lead() -> int:
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Maidstone, Kent", status="designed")
    db.update_lead_fields(lead_id, contact_email="owner@acme.test")
    return lead_id


def test_live_cold_sends_are_held_while_the_public_url_is_down(env, monkeypatch):
    monkeypatch.setattr(sales_agent.tracker, "public_endpoint_up", lambda: False)
    monkeypatch.setattr(sales_agent, "send_cold_email", Mock(side_effect=AssertionError("must not send")))
    posted: list[str] = []
    monkeypatch.setattr(sales_agent, "_slack_notify", lambda text: posted.append(text) or True)
    lead_id = _designed_lead()

    assert sales_agent.send_next_pending() is None
    assert sales_agent.send_next_pending() is None

    assert db.get_lead(lead_id)["status"] == "designed"        # untouched; it goes once the URL is back
    assert len(posted) == 1 and "SENDING PAUSED" in posted[0]  # one alert, not one per cycle


def test_sends_resume_when_the_public_url_answers(env, monkeypatch):
    monkeypatch.setattr(sales_agent.tracker, "public_endpoint_up", lambda: True)
    sent: list[int] = []
    monkeypatch.setattr(sales_agent, "send_cold_email", lambda lead: sent.append(lead["id"]) or True)
    lead_id = _designed_lead()

    assert sales_agent.send_next_pending() == lead_id
    assert sent == [lead_id]


def test_dry_runs_are_exempt(env, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(sales_agent.tracker, "public_endpoint_up",
                        Mock(side_effect=AssertionError("a dry run needs no probe")))
    sent: list[int] = []
    monkeypatch.setattr(sales_agent, "send_cold_email", lambda lead: sent.append(lead["id"]) or True)
    lead_id = _designed_lead()

    assert sales_agent.send_next_pending() == lead_id


def test_follow_ups_are_held_too(env, monkeypatch):
    monkeypatch.setattr(sales_agent.tracker, "public_endpoint_up", lambda: False)
    monkeypatch.setattr(sales_agent, "_send_thread_reply", Mock(side_effect=AssertionError("must not send")))
    monkeypatch.setattr(sales_agent.db, "list_follow_up_due",
                        Mock(side_effect=AssertionError("held before the queue is even read")))

    assert sales_agent.send_follow_up_if_due() is None
