"""Stripe Checkout session creation and webhook signature verification.

Payment is only ever triggered by an explicit human action (typing
`payment ready <lead_id>` in the main.py console, see agents/sales_agent.py),
never automatically by the pipeline itself.
"""
from __future__ import annotations

from typing import Optional

import stripe

import config

stripe.api_key = config.STRIPE_SECRET_KEY


def create_checkout_session(
    lead_id: int,
    business_name: str,
    customer_email: str,
    amount: Optional[int] = None,
) -> str:
    """Create a Stripe Checkout Session for the fixed website price and
    return its hosted checkout URL. Amount defaults to config.WEBSITE_PRICE,
    charged in config.PAYMENT_CURRENCY -- the same currency quoted in the
    cold-email offer copy (see sales_agent.py)."""
    amount_minor_units = (amount if amount is not None else config.WEBSITE_PRICE) * 100
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=customer_email,
        line_items=[
            {
                "price_data": {
                    "currency": config.PAYMENT_CURRENCY,
                    "unit_amount": amount_minor_units,
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
