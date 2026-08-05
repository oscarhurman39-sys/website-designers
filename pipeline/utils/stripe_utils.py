"""Stripe Checkout session creation and webhook signature verification.

Two ways a Checkout Session gets created:
  - Self-serve: webhook_server.py's `GET /buy/<lead_id>` creates one
    on-the-fly and redirects, for a buyer clicking "Buy Now" in the cold
    email or on the preview site itself -- zero human action on our side.
  - Operator-triggered: `payment ready <lead_id>` in the main.py console
    (see agents/sales_agent.py), for a lead who replied instead of
    self-serving.
Either way, the actual CHARGE is only ever initiated by the buyer's own
action on Stripe's hosted page -- this module never charges a card
without the customer entering their own payment details there.
"""
from __future__ import annotations

from typing import Optional

import stripe

import config

stripe.api_key = config.STRIPE_SECRET_KEY


def create_checkout_session(
    lead_id: int,
    business_name: str,
    customer_email: Optional[str] = None,
    amount_usd: Optional[int] = None,
) -> str:
    """Create a Stripe Checkout Session for the fixed website price and
    return its hosted checkout URL. Amount defaults to
    config.WEBSITE_PRICE_USD. `customer_email` pre-fills the checkout
    page when known (e.g. the operator-triggered path); when None (the
    self-serve `/buy/<lead_id>` path, where we don't necessarily know who
    is clicking), Stripe just collects it during checkout instead."""
    amount_cents = (amount_usd if amount_usd is not None else config.WEBSITE_PRICE_USD) * 100
    kwargs = {}
    if customer_email:
        kwargs["customer_email"] = customer_email
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": amount_cents,
                    "product_data": {
                        "name": f"Custom website design -- {business_name}",
                        "description": "One-time payment for a completed, custom-built business website.",
                    },
                },
                "quantity": 1,
            }
        ],
        metadata={"lead_id": str(lead_id)},
        success_url=f"{config.PUBLIC_BASE_URL}/payment-success?lead_id={lead_id}",
        cancel_url=f"{config.PUBLIC_BASE_URL}/payment-cancelled?lead_id={lead_id}",
        **kwargs,
    )
    return session.url


def verify_webhook_signature(payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and construct a Stripe event from a raw webhook request body.
    Raises stripe.error.SignatureVerificationError if the signature is invalid."""
    return stripe.Webhook.construct_event(payload, sig_header, config.STRIPE_WEBHOOK_SECRET)


def extract_lead_id(event: stripe.Event) -> Optional[int]:
    """Pull `lead_id` back out of a checkout.session.completed event's metadata."""
    obj = event.get("data", {}).get("object", {})
    metadata = obj.get("metadata", {}) or {}
    lead_id = metadata.get("lead_id")
    return int(lead_id) if lead_id is not None else None
