"""Flask app serving these public-facing routes:

  GET  /click?lead_id=<id>&token=<token>   -- click-tracking redirect to the preview site
  GET  /unsubscribe/<token>                -- one-click CAN-SPAM unsubscribe
  GET  /screenshots/<lead_id>.png          -- serves a cached preview screenshot
  GET  /edit/<lead_id>?token=<token>       -- client site editor (see agents/editor_agent.py)
  POST /edit/<lead_id>                     -- save + publish edits from that form
  POST /webhook/stripe                     -- Stripe `checkout.session.completed` events

Run standalone with `python webhook_server.py` (dev server) or behind a real
WSGI server (gunicorn/uwsgi) in production. Must be reachable at
config.PUBLIC_BASE_URL for the click-tracking, unsubscribe, screenshot, and
editor links embedded in outgoing emails to work.
"""
from __future__ import annotations

import stripe
from flask import Flask, Response, abort, redirect, render_template_string, request, send_from_directory

import config
from agents import editor_agent
from utils import compliance, content_importer, db, editor_auth, screenshot, stripe_utils, tracker

# Server-rendered (via Flask's autoescaping render_template_string --
# important since every value here can originate from an arbitrary
# scraped third-party site, see utils/content_importer.py) client editor
# form. Lives here rather than in agents/editor_agent.py to keep that
# module presentation-free -- business logic vs. HTTP/HTML is the same
# split every other agent/utils pair in this codebase follows.
_EDIT_FORM_TEMPLATE = """
<!doctype html><html><head><title>Edit {{ lead.business_name }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #1e293b; }
  label { display: block; font-weight: 600; margin-bottom: 4px; }
  input[type=text], textarea { width: 100%; padding: 8px; box-sizing: border-box; font: inherit; border: 1px solid #cbd5e1; border-radius: 6px; }
  .field { margin-bottom: 20px; }
  .message { padding: 12px; background: #eef2ff; border-radius: 6px; margin-bottom: 20px; }
  button { padding: 10px 24px; font-size: 1rem; border: none; border-radius: 6px; background: #1e293b; color: white; cursor: pointer; }
</style>
</head>
<body>
<h1>Edit {{ lead.business_name }}'s website</h1>
<p>Text, photos, opening hours, services and reviews are yours to update
here. Layout, colours, fonts and navigation stay locked so your site keeps
looking professional -- contact your designer for changes to those.</p>
{% if message %}<p class="message">{{ message }}</p>{% endif %}
<form method="post">
<input type="hidden" name="token" value="{{ token }}">
{% for field, field_type in fields.items() %}
<div class="field">
  <label for="{{ field }}">{{ labels[field] }}</label>
  {% if field_type == "list" %}
  <textarea id="{{ field }}" name="{{ field }}" rows="4">{{ values[field] }}</textarea>
  <small>One per line.</small>
  {% else %}
  <input type="text" id="{{ field }}" name="{{ field }}" value="{{ values[field] }}">
  {% endif %}
</div>
{% endfor %}
<button type="submit">Save &amp; publish</button>
</form>
</body></html>
"""


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

    @app.route("/edit/<int:lead_id>", methods=["GET", "POST"])
    def edit_site(lead_id: int) -> Response:
        token = request.values.get("token", "")
        if editor_auth.verify_editor_token(token) != lead_id:
            abort(403)
        lead = db.get_lead(lead_id)
        if lead is None:
            abort(404)

        message = ""
        if request.method == "POST":
            for field, field_type in editor_agent.EDITABLE_FIELDS.items():
                raw = request.form.get(field, "")
                value = (
                    [line.strip() for line in raw.splitlines() if line.strip()]
                    if field_type == "list" else raw.strip()
                )
                editor_agent.apply_edit(lead_id, field, value)
            try:
                result = editor_agent.publish(lead_id)
                message = f"Published! Live at {result['preview_url']}"
            except Exception as exc:  # noqa: BLE001 - show the client what happened instead of a 500
                message = f"Your changes were saved, but publishing failed: {exc}"

        current = editor_agent.effective_lead(lead_id) or lead
        content = content_importer.load_content(current)
        field_values = {
            "phone": current.get("phone") or "",
            "location": current.get("location") or "",
            "logo_url": content["logo_url"],
            "hours": "\n".join(content["hours"]),
            "scraped_services": "\n".join(content["services"]),
            "reviews": "\n".join(content["reviews"]),
            "photos": "\n".join(content["photos"]),
        }
        return render_template_string(
            _EDIT_FORM_TEMPLATE, lead=lead, token=token, message=message,
            fields=editor_agent.EDITABLE_FIELDS, labels=editor_agent.FIELD_LABELS, values=field_values,
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

        # Stripe retries any event that doesn't get a timely 2xx, so the
        # same event can arrive more than once. Record-once before acting:
        # a replay is acknowledged with 200 but changes nothing.
        if not db.record_event_once(event.get("id") or "", source="stripe"):
            return "", 200

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
