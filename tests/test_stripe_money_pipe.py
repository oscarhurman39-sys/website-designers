from __future__ import annotations

import hmac
import hashlib
import json
import time
from unittest.mock import Mock

import pytest

import config
from agents import sales_agent
from utils import db, stripe_utils
import webhook_server


class FakeStripeSession:
    url = "https://checkout.stripe.com/c/pay/cs_test_smoke"


def stripe_signature(payload: bytes, secret: str) -> str:
    ts = int(time.time())
    signed = f"{ts}.".encode("utf-8") + payload
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def test_checkout_session_uses_gbp_amount_metadata_and_safe_urls(monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")
    monkeypatch.setattr(config, "CURRENCY", "gbp")
    create = Mock(return_value=FakeStripeSession())
    monkeypatch.setattr(stripe_utils.stripe.checkout.Session, "create", create)

    url = stripe_utils.create_checkout_session(42, "Fern Studio", "owner@example.com", 650)

    assert url == "https://checkout.stripe.com/c/pay/cs_test_smoke"
    kwargs = create.call_args.kwargs
    assert kwargs["mode"] == "payment"
    assert kwargs["customer_email"] == "owner@example.com"
    assert kwargs["metadata"] == {"lead_id": "42"}
    assert kwargs["line_items"][0]["price_data"]["currency"] == "gbp"
    assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 65000
    assert kwargs["success_url"] == "https://track.example.test/payment-success?lead_id=42"
    assert kwargs["cancel_url"] == "https://track.example.test/payment-cancelled?lead_id=42"
    assert "owner@example.com" not in kwargs["success_url"]
    assert "owner@example.com" not in kwargs["cancel_url"]


def test_sales_checkout_link_dry_run_never_creates_stripe_session(monkeypatch):
    lead = {"id": 7, "business_name": "Dry Run Florist", "contact_email": "owner@example.com", "quoted_price_usd": None}
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")
    monkeypatch.setattr(sales_agent.stripe_utils, "create_checkout_session", Mock())
    monkeypatch.setattr(sales_agent.db, "update_lead_fields", Mock())

    url = sales_agent._create_checkout_link(lead, 750)

    assert url == sales_agent._DRY_RUN_CHECKOUT_URL
    sales_agent.stripe_utils.create_checkout_session.assert_not_called()
    sales_agent.db.update_lead_fields.assert_not_called()


def test_stripe_webhook_rejects_bad_signature_and_does_not_mark_won(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    db.init_db()
    lead_id = db.insert_lead("Bad Signature Cafe", "cafe", "Leeds", status="payment_sent")

    response = webhook_server.create_app().test_client().post(
        "/webhook/stripe",
        data=b'{"type":"checkout.session.completed"}',
        headers={"Stripe-Signature": "t=1,v1=not-valid"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert db.get_lead(lead_id)["status"] == "payment_sent"


def test_stripe_webhook_marks_matching_paid_lead_won(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    db.init_db()
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_placeholder")
    lead_id = db.insert_lead("Paid Ferns", "landscaper", "York", status="payment_sent")
    payload = json.dumps({
        "id": "evt_test_paid",
        "object": "event",
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {"object": {"mode": "payment", "payment_status": "paid", "metadata": {"lead_id": str(lead_id)}}},
    }, separators=(",", ":")).encode("utf-8")

    response = webhook_server.create_app().test_client().post(
        "/webhook/stripe",
        data=payload,
        headers={"Stripe-Signature": stripe_signature(payload, "whsec_test_secret")},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert db.get_lead(lead_id)["status"] == "won"


@pytest.mark.parametrize("payment_status,event_live,mode,metadata,initial,expected_status,expected_lead", [
    ("unpaid", False, "payment", "valid", "payment_sent", 200, "payment_sent"),
    ("paid", True, "payment", "valid", "payment_sent", 200, "payment_sent"),
    ("paid", False, "subscription", "valid", "payment_sent", 200, "payment_sent"),
    ("paid", False, "payment", "garbage", "payment_sent", 400, "payment_sent"),
    ("paid", False, "payment", "valid", "won", 200, "won"),
])
def test_webhook_payment_guards(monkeypatch, tmp_path, payment_status, event_live,
                                mode, metadata, initial, expected_status, expected_lead):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "guards.db"))
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_placeholder")
    db.init_db()
    lead_id = db.insert_lead("Guard Test", "cafe", "York", status=initial)
    db.update_lead_fields(lead_id, won_amount=589)
    payload = json.dumps({
        "id": "evt_guard", "object": "event", "type": "checkout.session.completed",
        "livemode": event_live,
        "data": {"object": {"payment_status": payment_status, "mode": mode,
            "amount_total": 100, "metadata": {"lead_id": str(lead_id) if metadata == "valid" else metadata}}},
    }).encode()
    response = webhook_server.create_app().test_client().post(
        "/webhook/stripe", data=payload, content_type="application/json",
        headers={"Stripe-Signature": stripe_signature(payload, "whsec_test_secret")})
    assert response.status_code == expected_status
    lead = db.get_lead(lead_id)
    assert lead["status"] == expected_lead
    assert lead["won_amount"] == 589
