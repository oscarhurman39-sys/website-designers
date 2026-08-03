"""Test for utils/stripe_utils.py's create_checkout_session accepting an
optional customer_email -- needed for the self-serve /buy/<lead_id>
redirect, which often doesn't know who's clicking. Stripe's SDK call
itself is mocked."""
from __future__ import annotations

from unittest.mock import Mock

from utils import stripe_utils


def test_create_checkout_session_omits_customer_email_when_none(monkeypatch):
    create_mock = Mock(return_value=Mock(url="https://checkout.stripe.com/pay/cs_test"))
    monkeypatch.setattr(stripe_utils.stripe.checkout.Session, "create", create_mock)

    url = stripe_utils.create_checkout_session(lead_id=1, business_name="Joes Cafe", customer_email=None)

    assert url == "https://checkout.stripe.com/pay/cs_test"
    assert "customer_email" not in create_mock.call_args.kwargs


def test_create_checkout_session_includes_customer_email_when_given(monkeypatch):
    create_mock = Mock(return_value=Mock(url="https://checkout.stripe.com/pay/cs_test"))
    monkeypatch.setattr(stripe_utils.stripe.checkout.Session, "create", create_mock)

    stripe_utils.create_checkout_session(lead_id=1, business_name="Joes Cafe", customer_email="owner@example.com")

    assert create_mock.call_args.kwargs["customer_email"] == "owner@example.com"


def test_create_checkout_session_defaults_customer_email_to_none():
    """The parameter itself is now optional (no positional-required
    footgun for the /buy route, which often has no known email)."""
    import inspect
    sig = inspect.signature(stripe_utils.create_checkout_session)
    assert sig.parameters["customer_email"].default is None
