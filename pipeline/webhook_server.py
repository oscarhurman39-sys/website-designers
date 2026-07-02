"""Flask app serving three public-facing routes:

  GET  /click?lead_id=<id>&token=<token>   -- click-tracking redirect to the preview site
  GET  /unsubscribe/<token>                -- one-click CAN-SPAM unsubscribe
  POST /webhook/stripe                     -- Stripe `checkout.session.completed` events

Run standalone with `python webhook_server.py` (dev server) or behind a real
WSGI server (gunicorn/uwsgi) in production. Must be reachable at
config.PUBLIC_BASE_URL for the click-tracking and unsubscribe links embedded
in outgoing emails to work.
"""
from __future__ import annotations

import stripe
from flask import Flask, Response, abort, redirect, request

import config
from utils import compliance, db, stripe_utils, tracker


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
        return (
            "<h1>You've been unsubscribed.</h1>"
            f"<p>{lead['business_name']} will not receive any further emails from us. Sorry for the bother.</p>",
            200,
        )

    @app.route("/payment-success", methods=["GET"])
    def payment_success() -> tuple[str, int]:
        return "<h1>Payment received -- thank you!</h1><p>We'll be in touch shortly.</p>", 200

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
            lead_id = stripe_utils.extract_lead_id(event)
            if lead_id is not None:
                lead = db.get_lead(lead_id)
                if lead is not None:
                    db.update_lead_status(lead_id, "won", notes="Stripe checkout.session.completed")
                    banner = "*" * 70
                    print(
                        f"\n{banner}\nPAYMENT RECEIVED: {lead['business_name']} (lead {lead_id})\n"
                        f"Run 'transfer {lead_id}' in the pipeline console to hand over the "
                        f"GitHub repo and Vercel project.\n{banner}\n"
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
