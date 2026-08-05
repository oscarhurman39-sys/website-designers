"""Tests that a completed Stripe checkout automatically emails the client
an onboarding link (see agents/onboarding_agent.py, PLAN.md's automation
audit) instead of only ever printing a console banner telling the
operator to run `transfer`. Stripe signature verification is mocked; DB
is a real tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import webhook_server
from utils import db


def _completed_event(lead_id: int, event_id: str = "evt_1") -> dict:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"lead_id": str(lead_id)}}},
    }


def test_stripe_webhook_sends_onboarding_email_on_payment(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")

    monkeypatch.setattr(webhook_server.stripe_utils, "verify_webhook_signature", lambda *a, **k: _completed_event(lead_id))
    send_mock = Mock(return_value=True)
    monkeypatch.setattr(webhook_server.onboarding_agent, "send_onboarding_email", send_mock)

    resp = webhook_server.app.test_client().post("/webhook/stripe", data=b"{}", headers={"Stripe-Signature": "t"})

    assert resp.status_code == 200
    send_mock.assert_called_once_with(lead_id)
    assert db.get_lead(lead_id)["status"] == "won"


def test_stripe_webhook_replay_does_not_resend_onboarding_email(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    monkeypatch.setattr(
        webhook_server.stripe_utils, "verify_webhook_signature",
        lambda *a, **k: _completed_event(lead_id, event_id="evt_dup"),
    )
    send_mock = Mock(return_value=True)
    monkeypatch.setattr(webhook_server.onboarding_agent, "send_onboarding_email", send_mock)

    client = webhook_server.app.test_client()
    client.post("/webhook/stripe", data=b"{}", headers={"Stripe-Signature": "t"})
    client.post("/webhook/stripe", data=b"{}", headers={"Stripe-Signature": "t"})  # Stripe retry

    send_mock.assert_called_once()
