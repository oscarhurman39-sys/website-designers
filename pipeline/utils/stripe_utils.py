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


def send_payment_link(lead_id: int) -> str:
    """Create a Checkout Session for a lead and email them the link,
    recording the send and moving the lead to 'payment_sent'. Returns the
    checkout URL. Raises ValueError for a missing lead/email and lets
    Stripe/transport errors propagate for the caller to surface.

    This is the same explicit human-triggered flow as main.py's
    `payment ready <lead_id>` console command, packaged so the dashboard's
    "Send payment link" button can use it too. It does NOT mark anything
    paid -- 'won' only ever comes from Stripe's checkout.session.completed
    webhook confirming real money moved."""
    from utils import db, email_utils  # local imports to avoid a cycle at module load

    lead = db.get_lead(lead_id)
    if lead is None:
        raise ValueError(f"No such lead {lead_id}")
    email_addr: str = lead.get("contact_email") or ""
    if not email_addr:
        raise ValueError(f"Lead {lead_id} has no contact email; cannot send a payment link.")

    checkout_url = create_checkout_session(
        lead_id=lead_id, business_name=lead["business_name"], customer_email=email_addr
    )

    subject = f"Payment link for your new {lead['business_name']} website"
    body = (
        f"Hi, here's the secure payment link we discussed: {checkout_url}\n\n"
        "Once payment goes through, I'll get the site handed over to you right away."
    )
    # Same transport preference as sales_agent: SendGrid when configured,
    # SMTP otherwise. Both apply the compliance footer + unsubscribe hard-stop.
    if config.SENDGRID_API_KEY:
        message_id = email_utils.send_email_sendgrid(email_addr, subject, body, lead_id)
    else:
        message_id = email_utils.send_email(email_addr, subject, body, lead_id)
    db.insert_email_thread(
        lead_id=lead_id, direction="outbound", subject=subject, body=body,
        from_addr=config.EMAIL_USER, to_addr=email_addr, message_id=message_id,
    )
    db.update_lead_status(lead_id, "payment_sent", notes=f"Checkout link sent: {checkout_url}")
    return checkout_url


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
