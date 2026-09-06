"""Flask app serving these public-facing routes:

  GET  /click?lead_id=<id>&token=<token>   -- click-tracking redirect to the preview site
  GET  /unsubscribe/<token>                -- one-click CAN-SPAM unsubscribe
  GET  /screenshots/<lead_id>.png          -- serves a cached preview screenshot
  POST /webhook/stripe                     -- Stripe `checkout.session.completed` events

Run standalone with `python webhook_server.py` (dev server) or behind a real
WSGI server (gunicorn/uwsgi) in production. Must be reachable at
config.PUBLIC_BASE_URL for the click-tracking, unsubscribe, and screenshot
links embedded in outgoing emails to work.
"""
from __future__ import annotations

import stripe
from flask import Flask, Response, abort, redirect, request, send_from_directory

import config
from utils import compliance, db, screenshot, stripe_utils, tracker


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/click", methods=["GET"])
    def click_redirect() -> Response:
        lead_id_raw = request.args.get("lead_id", "")
        token = request.args.get("token", "")
        if not lead_id_raw.isdigit() or not token:
            abort(400)
        destination = tracker.resolve_click(int(lead_id_raw), token)
        if destination is None:
            abort(404)
        return redirect(destination, code=302)

    @app.route("/unsubscribe/<token>", methods=["GET"])
    def unsubscribe(token: str) -> tuple[str, int]:
        lead = compliance.process_unsubscribe(token)
        if lead is None:
            return "<h1>Invalid or expired unsubscribe link.</h1>", 404
        # `lead` holds the status from *before* suppression. A paid lead
        # ('won'/'payment_sent') keeps its status (see db.mark_unsubscribed) so
        # its handover isn't lost, but a human must know their comms are now
        # suppressed -- the automated handover confirmation email can't reach them.
        if lead.get("status") in ("won", "payment_sent"):
            from agents import sales_agent  # local import: heavy module, keep startup light
            sales_agent.alert_needs_human(
                lead,
                "A PAID lead just unsubscribed. Their status is preserved so the handover "
                "still runs, but automated emails to them are now suppressed -- complete the "
                "handover (GitHub/Vercel invite, domain) and follow up personally.",
            )
        return (
            "<h1>You've been unsubscribed.</h1>"
            f"<p>{lead['business_name']} will not receive any further emails from us. Sorry for the bother.</p>",
            200,
        )

    @app.route("/screenshots/<int:lead_id>.png", methods=["GET"])
    def serve_screenshot(lead_id: int) -> Response:
        """Serves the cached preview screenshot for a lead, if one exists.
        This is the URL stored in websites.screenshot_url and embedded (as
        a linked or cid: inline image) in cold emails by sales_agent.py."""
        path = screenshot.get_cached_screenshot(lead_id)
        if path is None:
            abort(404)
        return send_from_directory(screenshot.SCREENSHOTS_DIR, path.name, mimetype="image/png")

    @app.route("/payment-success", methods=["GET"])
    def payment_success() -> tuple[str, int]:
        return "<h1>Thanks for checking out.</h1><p>Payment is confirmed separately by our payment provider. We'll be in touch after confirmation.</p>", 200

    @app.route("/payment-cancelled", methods=["GET"])
    def payment_cancelled() -> tuple[str, int]:
        return "<h1>Checkout cancelled.</h1><p>No charge was made. Reach out any time.</p>", 200

    @app.route("/webhook/stripe", methods=["POST"])
    def stripe_webhook() -> tuple[str, int]:
        payload = request.get_data()
        sig_header = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe_utils.verify_webhook_signature(payload, sig_header)
        except (ValueError, stripe.error.SignatureVerificationError):
            abort(400)

        if event["type"] == "checkout.session.completed":
            session = event.get("data", {}).get("object", {}) or {}
            # A completed checkout is not necessarily a settled payment.
            # Test events must never fulfil orders under a live API key.
            key = config.STRIPE_SECRET_KEY
            expected_live = True if key.startswith(("sk_live_", "rk_live_")) else (
                False if key.startswith(("sk_test_", "rk_test_")) else None
            )
            if (expected_live is None or event.get("livemode") is not expected_live
                    or session.get("payment_status") != "paid"
                    or session.get("mode") != "payment"):
                return "", 200
            try:
                lead_id = stripe_utils.extract_lead_id(event)
            except (ValueError, TypeError, OverflowError):
                return "Invalid lead metadata", 400
            if lead_id is not None:
                lead = db.get_lead(lead_id)
                # A retry must not overwrite recorded payment or trigger handover again.
                if lead is not None and lead.get("status") != "won":
                    db.update_lead_status(lead_id, "won", notes="Stripe checkout.session.completed")
                    amount_minor = (event.get("data", {}).get("object", {}) or {}).get("amount_total")
                    if isinstance(amount_minor, int):
                        db.update_lead_fields(lead_id, won_amount=amount_minor // 100)
                    banner = "*" * 70
                    print(
                        f"\n{banner}\nPAYMENT RECEIVED: {lead['business_name']} (lead {lead_id})\n"
                        f"Automated handover will run on the pipeline's next cycle (GitHub +\n"
                        f"Vercel invites). 'transfer {lead_id}' remains available as a manual override.\n{banner}\n"
                    )
        return "", 200

    return app


app = create_app()

if __name__ == "__main__":
    config.validate()
    db.init_db()
    # Debug/dev server only. Use a production WSGI server (gunicorn, etc.)
    # behind TLS for real deployments, since Stripe requires HTTPS webhooks.
    app.run(host="0.0.0.0", port=5000)
