"""Tests for agents/offers/ -- the build/price/fulfill contract every offer
implements (see offers/base.py, PLATFORM.md's step 2). The 'website' offer
is thin delegation to design_agent/onboarding_agent/config, unchanged --
these tests prove the delegation is wired correctly and that the real
call sites (main.py's `payment ready`, webhook_server.py's /buy and
/onboard) go through the offer instead of hardcoding website specifics."""
from __future__ import annotations

from unittest.mock import Mock

import config
import main
import webhook_server
from agents.offers import base, registry, website
from utils import db


def _lead(**extra) -> dict:
    lead = {"id": 1, "business_name": "Joes Cafe", "niche": "cafe", "location": "Leeds"}
    lead.update(extra)
    return lead


# --- base.validate_offer -----------------------------------------------------

def test_validate_offer_passes_for_the_website_module():
    base.validate_offer(website)  # must not raise


def test_validate_offer_names_every_missing_attribute():
    class _Broken:
        __name__ = "broken_offer"
        build_artifact = staticmethod(lambda lead: None)
        # price and fulfill deliberately missing

    try:
        base.validate_offer(_Broken)
        assert False, "expected AttributeError"
    except AttributeError as exc:
        assert "price" in str(exc)
        assert "fulfill" in str(exc)
        assert "build_artifact" not in str(exc)


# --- website offer: thin delegation, nothing new -----------------------------

def test_website_build_artifact_delegates_to_design_agent(monkeypatch):
    process_mock = Mock(return_value={"preview_url": "https://x.example"})
    monkeypatch.setattr(website.design_agent, "process_lead", process_mock)

    result = website.build_artifact(_lead())

    process_mock.assert_called_once_with(_lead())
    assert result == {"preview_url": "https://x.example"}


def test_website_price_is_the_flat_config_price():
    assert website.price(_lead()) == config.WEBSITE_PRICE_USD


def test_website_fulfill_delegates_to_onboarding_agent(monkeypatch):
    complete_mock = Mock(return_value={"message": "All set!"})
    monkeypatch.setattr(website.onboarding_agent, "complete_onboarding", complete_mock)

    result = website.fulfill(42, github_username="octocat", vercel_email="owner@example.com")

    complete_mock.assert_called_once_with(42, "octocat", "owner@example.com")
    assert result == {"message": "All set!"}


def test_website_fulfill_defaults_missing_kwargs_to_empty_string(monkeypatch):
    complete_mock = Mock(return_value={"message": "All set!"})
    monkeypatch.setattr(website.onboarding_agent, "complete_onboarding", complete_mock)

    website.fulfill(42)

    complete_mock.assert_called_once_with(42, "", "")


# --- registry -----------------------------------------------------------------

def test_get_offer_resolves_every_lead_to_website_today():
    assert registry.get_offer(_lead()) is website
    assert registry.get_offer(_lead(niche="plumber", id=99)) is website


# --- real call sites go through the offer, not hardcoded website values ------

def test_payment_ready_prices_via_the_offer(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")

    create_mock = Mock(return_value="https://checkout.stripe.com/pay/cs_test_1")
    monkeypatch.setattr(main.stripe_utils, "create_checkout_session", create_mock)
    import utils.email_utils as email_utils_module
    monkeypatch.setattr(email_utils_module, "send_email", Mock(return_value="<msgid@test>"))

    main._handle_payment_ready(lead_id)

    create_mock.assert_called_once_with(
        lead_id=lead_id, business_name="Joes Cafe", customer_email="owner@joescafe.example",
        amount_usd=config.WEBSITE_PRICE_USD,
    )


def test_onboard_route_fulfills_via_the_offer(tmp_path, monkeypatch):
    from utils import editor_auth

    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_status(lead_id, "won")
    db.insert_website(lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name="", preview_url="https://x.example")
    token = editor_auth.issue_editor_session(lead_id)

    fulfill_mock = Mock(return_value={"message": "All set, octocat!"})
    monkeypatch.setattr(webhook_server.offers, "get_offer", Mock(return_value=Mock(fulfill=fulfill_mock)))

    resp = webhook_server.app.test_client().post(f"/onboard/{lead_id}", data={
        "token": token, "github_username": "octocat", "vercel_email": "owner@joescafe.example",
    })

    assert resp.status_code == 200
    assert "All set, octocat!" in resp.get_data(as_text=True)
    fulfill_mock.assert_called_once_with(lead_id, github_username="octocat", vercel_email="owner@joescafe.example")
