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
    amount_usd: Optional[int] = None,
) -> str:
    """Create a Stripe Checkout Session for the fixed website price and
    return its hosted checkout URL. Amount defaults to config.WEBSITE_PRICE_USD."""
    if config.SAFE_MODE:
        # No real Stripe call -- lets the pipeline (and an operator testing
        # the "payment ready" flow) run end-to-end without a live account.
        print(f"[stripe_utils] SAFE_MODE: skipping real Stripe checkout session for lead {lead_id}.")
        return f"{config.PUBLIC_BASE_URL}/payment-success?lead_id={lead_id}&safe_mode=1"

    amount_cents = (amount_usd if amount_usd is not None else config.WEBSITE_PRICE_USD) * 100
    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        customer_email=customer_email,
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


def verify_paid_checkout_session(
    session_id: str, expected_lead_id: int, expected_amount_usd: Optional[int] = None
) -> bool:
    """Defense-in-depth check before ever marking a lead 'won': a
    signature-valid webhook only proves Stripe sent it, not that the
    payment claims inside it still hold, so re-fetch the session fresh from
    Stripe's API and independently verify payment_status, amount, and
    metadata all match what's expected. Any mismatch or API error returns
    False (fail closed -- a lead is never marked paid on ambiguous data)."""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception as exc:  # noqa: BLE001 - any retrieval failure is a verification failure
        print(f"[stripe_utils] Could not re-fetch checkout session {session_id}: {exc}")
        return False

    if session.get("payment_status") != "paid":
        print(f"[stripe_utils] Session {session_id} payment_status is {session.get('payment_status')!r}, not 'paid'.")
        return False

    metadata_lead_id = (session.get("metadata") or {}).get("lead_id")
    if metadata_lead_id is None or int(metadata_lead_id) != expected_lead_id:
        print(f"[stripe_utils] Session {session_id} metadata.lead_id {metadata_lead_id!r} != expected {expected_lead_id}.")
        return False

    expected_cents = (expected_amount_usd if expected_amount_usd is not None else config.WEBSITE_PRICE_USD) * 100
    if session.get("amount_total") != expected_cents:
        print(f"[stripe_utils] Session {session_id} amount_total {session.get('amount_total')} != expected {expected_cents}.")
        return False

    return True
