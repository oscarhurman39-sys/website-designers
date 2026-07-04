"""Flask app serving these public-facing routes:

  GET  /click?lead_id=<id>&token=<token>   -- click-tracking redirect to the preview site
  GET  /unsubscribe/<token>                -- one-click CAN-SPAM unsubscribe
  GET  /screenshots/<lead_id>.png          -- serves a cached preview screenshot
  POST /webhook/stripe                     -- Stripe `checkout.session.completed` events
  POST /webhook/sendgrid                   -- SendGrid Event Webhook (opens/clicks)

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

    @app.route("/webhook/sendgrid", methods=["POST"])
    def sendgrid_events() -> tuple[str, int]:
        """Ingest SendGrid Event Webhook batches (opens, clicks, etc.).

        Each event carries the `lead_id` custom arg we set on the outgoing
        message (see email_utils.send_email_sendgrid), so we can attribute it
        to a lead and store it in email_events for the dashboard's subject-
        line A/B stats. Signature-verified when a verification key is set;
        otherwise accepted as-is (see config.SENDGRID_WEBHOOK_VERIFICATION_KEY)."""
        payload = request.get_data()
        if config.SENDGRID_WEBHOOK_VERIFICATION_KEY and not _verify_sendgrid_signature(payload, request.headers):
            abort(403)

        events = request.get_json(silent=True)
        if not isinstance(events, list):
            abort(400)

        for ev in events:
            if not isinstance(ev, dict):
                continue
            lead_id_raw = str(ev.get("lead_id", "")).strip()
            event_type = str(ev.get("event", "")).strip()
            if not lead_id_raw.isdigit() or not event_type:
                continue  # e.g. SendGrid's test event, which carries no lead_id
            try:
                db.record_email_event(int(lead_id_raw), event_type, str(ev.get("sg_event_id", "")))
            except Exception:  # noqa: BLE001 - one bad event must not fail the whole batch (SendGrid would retry it)
                continue
        # 2xx so SendGrid doesn't retry a batch we've already processed.
        return "", 204

    return app


def _verify_sendgrid_signature(payload: bytes, headers) -> bool:
    """Verify a SendGrid signed-event-webhook request against the configured
    ECDSA public key. Returns False on any error (missing headers, bad
    signature, helper unavailable) so verification failures fail closed."""
    try:
        from sendgrid.event_webhook import EventWebhook

        ew = EventWebhook()
        public_key = ew.convert_public_key_to_ecdsa(config.SENDGRID_WEBHOOK_VERIFICATION_KEY)
        return bool(
            ew.verify_signature(
                payload.decode("utf-8"),
                headers.get("X-Twilio-Email-Event-Webhook-Signature", ""),
                headers.get("X-Twilio-Email-Event-Webhook-Timestamp", ""),
                public_key,
            )
        )
    except Exception:  # noqa: BLE001 - any verification error is a rejection
        return False


app = create_app()

if __name__ == "__main__":
    config.validate()
    db.init_db()
    # Debug/dev server only. Use a production WSGI server (gunicorn, etc.)
    # behind TLS for real deployments, since Stripe requires HTTPS webhooks.
    app.run(host="0.0.0.0", port=5000)
