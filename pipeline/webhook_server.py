"""Flask app serving these public-facing routes:

  GET  /click?lead_id=<id>&token=<token>   -- click-tracking redirect to the preview site
  GET  /unsubscribe/<token>                -- one-click CAN-SPAM unsubscribe
  GET  /screenshots/<lead_id>.png          -- serves a cached preview screenshot
  POST /webhook/stripe                     -- Stripe `checkout.session.completed` events
  POST /webhook/sendgrid                   -- SendGrid bounce/drop/spam events

Run standalone with `python webhook_server.py` (dev server) or behind a real
WSGI server (gunicorn/uwsgi) in production. Must be reachable at
config.PUBLIC_BASE_URL for the click-tracking, unsubscribe, and screenshot
links embedded in outgoing emails to work.
"""
from __future__ import annotations

import time

import stripe
from flask import Flask, Response, abort, redirect, request, send_from_directory

import config
from utils import compliance, db, screenshot, stripe_utils, tracker

# Replay-attack window for SendGrid's signed webhook timestamp -- a request
# whose X-Twilio-Email-Event-Webhook-Timestamp is older than this is
# rejected even with a valid signature, since a captured request could
# otherwise be replayed indefinitely.
_SENDGRID_TIMESTAMP_MAX_AGE_SECONDS = 300

# Reject any webhook body larger than this before Flask buffers it into
# memory -- SendGrid/Stripe payloads are a few KB at most, so anything
# bigger is either malformed or an attempted DoS via a massive payload.
_MAX_WEBHOOK_CONTENT_LENGTH = 256 * 1024


def _verify_sendgrid_signature(payload: bytes, timestamp: str, signature: str, key: str) -> bool:
    """Verify a SendGrid signed-event-webhook request.

    SendGrid signs webhook payloads with an ECDSA keypair, not a shared
    HMAC secret -- SENDGRID_WEBHOOK_VERIFICATION_KEY is the *public* half
    of that pair (base64-encoded), so verification must go through
    sendgrid.helpers.eventwebhook.EventWebhook (NOT a raw hmac.new() call,
    which would check an entirely different, incompatible construction and
    silently reject every genuine SendGrid request). Any error here (bad
    key, malformed signature, import failure) is treated as a rejection.
    """
    try:
        from sendgrid.helpers.eventwebhook import EventWebhook

        ew = EventWebhook()
        public_key = ew.convert_public_key_to_ecdsa(key)
        return bool(ew.verify_signature(payload.decode("utf-8"), signature, timestamp, public_key))
    except Exception:  # noqa: BLE001 - any verification error is a rejection
        return False


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = _MAX_WEBHOOK_CONTENT_LENGTH

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
        if config.SAFE_MODE:
            abort(503)

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
                # Never trust the webhook body alone: re-fetch the session
                # from Stripe's API and independently verify payment
                # status/amount/metadata before marking a lead 'won'. A
                # signature-valid webhook only proves Stripe sent it, not
                # that the specific claims inside it (amount, status) are
                # still accurate/complete -- see stripe_utils.verify_paid_checkout_session().
                if lead is not None and stripe_utils.verify_paid_checkout_session(
                    event["data"]["object"]["id"], expected_lead_id=lead_id
                ):
                    db.update_lead_status(lead_id, "won", notes="Stripe checkout.session.completed (re-verified)")
                    banner = "*" * 70
                    print(
                        f"\n{banner}\nPAYMENT RECEIVED: {lead['business_name']} (lead {lead_id})\n"
                        f"Run 'transfer {lead_id}' in the pipeline console to hand over the "
                        f"GitHub repo and Vercel project.\n{banner}\n"
                    )
                elif lead is not None:
                    print(f"[webhook_server] Stripe session for lead {lead_id} did not pass re-verification -- not marking won.")
        return "", 200

    @app.route("/webhook/sendgrid", methods=["POST"])
    def sendgrid_webhook() -> tuple[str, int]:
        if config.SAFE_MODE:
            abort(503)
        if not config.SENDGRID_WEBHOOK_VERIFICATION_KEY:
            abort(401)

        payload = request.get_data()
        sig_header = request.headers.get("X-Twilio-Email-Event-Webhook-Signature", "")
        timestamp_header = request.headers.get("X-Twilio-Email-Event-Webhook-Timestamp", "")

        if not sig_header or not timestamp_header:
            abort(400)

        # Replay-attack prevention: reject a stale timestamp even if the
        # signature over it is technically valid (a captured, previously
        # valid request must not be replayable indefinitely).
        try:
            timestamp_age = abs(time.time() - int(timestamp_header))
        except ValueError:
            abort(400)
        if timestamp_age > _SENDGRID_TIMESTAMP_MAX_AGE_SECONDS:
            abort(401)

        try:
            if not _verify_sendgrid_signature(
                payload, timestamp_header, sig_header, config.SENDGRID_WEBHOOK_VERIFICATION_KEY
            ):
                abort(401)
        except Exception:
            abort(401)

        try:
            events = request.json or []
        except Exception:
            abort(400)

        if not isinstance(events, list):
            events = [events]

        for event in events:
            event_type = event.get("event", "").lower()
            email = event.get("email", "").lower()

            if not email:
                continue

            if event_type in ("bounce", "dropped", "spamreport"):
                lead = db.get_lead_by_email(email)
                if lead is not None:
                    new_status = "unsubscribed" if event_type == "spamreport" else "bounced"
                    db.update_lead_status(
                        lead["id"],
                        new_status,
                        notes=f"SendGrid webhook: {event_type} event",
                    )
                if event_type == "spamreport":
                    db.add_to_suppression_list(email, reason="SendGrid spam report")
            elif event_type == "unsubscribe":
                lead = db.get_lead_by_email(email)
                if lead is not None:
                    db.mark_unsubscribed(lead["id"], email)
                else:
                    db.add_to_suppression_list(email, reason="SendGrid unsubscribe event")

        return "", 200

    return app


app = create_app()

if __name__ == "__main__":
    config.validate()
    db.init_db()
    # Debug/dev server only. Use a production WSGI server (gunicorn, etc.)
    # behind TLS for real deployments, since Stripe requires HTTPS webhooks.
    app.run(host="0.0.0.0", port=5000)
