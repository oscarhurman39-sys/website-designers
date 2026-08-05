"""Tests for webhook_server.py's GET /buy/<lead_id> -- the self-serve
checkout redirect that lets a buyer pay with zero human intervention on
our side (see PLAN.md's automation audit). Stripe is mocked; DB is a real
tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import config
import webhook_server
from utils import db


def _setup_lead(tmp_path, monkeypatch, **fields):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    if fields:
        db.update_lead_fields(lead_id, **fields)
    return lead_id


def test_buy_now_redirects_to_a_fresh_checkout_session(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch, contact_email="owner@joescafe.example")
    create_mock = Mock(return_value="https://checkout.stripe.com/pay/cs_test_123")
    monkeypatch.setattr(webhook_server.stripe_utils, "create_checkout_session", create_mock)

    client = webhook_server.app.test_client()
    resp = client.get(f"/buy/{lead_id}", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://checkout.stripe.com/pay/cs_test_123"
    create_mock.assert_called_once_with(
        lead_id=lead_id, business_name="Joes Cafe", customer_email="owner@joescafe.example",
        amount_usd=config.WEBSITE_PRICE_USD,
    )


def test_buy_now_omits_customer_email_when_unknown(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)  # no contact_email
    create_mock = Mock(return_value="https://checkout.stripe.com/pay/cs_test_456")
    monkeypatch.setattr(webhook_server.stripe_utils, "create_checkout_session", create_mock)

    client = webhook_server.app.test_client()
    client.get(f"/buy/{lead_id}")

    create_mock.assert_called_once_with(
        lead_id=lead_id, business_name="Joes Cafe", customer_email=None, amount_usd=config.WEBSITE_PRICE_USD,
    )


def test_buy_now_404s_for_missing_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    client = webhook_server.app.test_client()
    assert client.get("/buy/999").status_code == 404


def test_buy_now_does_not_create_a_second_session_once_already_won(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    db.update_lead_status(lead_id, "won")
    create_mock = Mock()
    monkeypatch.setattr(webhook_server.stripe_utils, "create_checkout_session", create_mock)

    resp = webhook_server.app.test_client().get(f"/buy/{lead_id}")

    assert resp.status_code == 200
    assert "already have your payment" in resp.get_data(as_text=True)
    create_mock.assert_not_called()


def test_buy_now_shows_friendly_error_instead_of_500(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(
        webhook_server.stripe_utils, "create_checkout_session",
        Mock(side_effect=RuntimeError("STRIPE_SECRET_KEY is not configured.")),
    )

    resp = webhook_server.app.test_client().get(f"/buy/{lead_id}")

    assert resp.status_code == 500
    assert "Something went wrong" in resp.get_data(as_text=True)
