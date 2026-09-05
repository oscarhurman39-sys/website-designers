"""Stripe Checkout session creation and webhook signature verification.

Checkout sessions are created automatically by the autonomous negotiation
agent (agents/sales_agent.py) when it closes a deal. The amount is always
clamped in code to the configured negotiation band
[config.NEGOTIATION_FLOOR, config.NEGOTIATION_CEILING] before a session is
created -- LLM output never reaches this module unclamped.

The currency is config.CURRENCY (default GBP), the same value the email copy
renders its prices with, so the amount charged always matches the amount the
prospect agreed to. config.validate() restricts CURRENCY to two-decimal
currencies because the amount below is computed as `price * 100`.
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
    return its hosted checkout URL. Amount is in config.CURRENCY units and
    defaults to config.WEBSITE_PRICE."""
    amount_minor = (amount if amount is not None else config.WEBSITE_PRICE) * 100
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=customer_email,
        line_items=[
            {
                "price_data": {
                    "currency": config.CURRENCY,
                    "unit_amount": amount_minor,
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
