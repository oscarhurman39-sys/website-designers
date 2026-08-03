"""Flask app serving these public-facing routes:

  GET  /click?lead_id=<id>&token=<token>       -- click-tracking redirect to the preview site
  GET  /unsubscribe/<token>                    -- one-click CAN-SPAM unsubscribe
  GET  /screenshots/<lead_id>.png              -- serves a cached preview screenshot
  GET  /edit/<lead_id>?token=<token>           -- client site editor (see agents/editor_agent.py)
  POST /edit/<lead_id>                         -- save + publish edits from that form
  GET  /edit/<lead_id>/request-link            -- form to request a fresh editor link
  POST /edit/<lead_id>/request-link            -- emails a fresh link if the address matches
  GET  /buy/<lead_id>                          -- self-serve: creates a fresh Stripe Checkout Session and redirects
  GET  /onboard/<lead_id>?token=<token>        -- claim-your-website form (see agents/onboarding_agent.py)
  POST /onboard/<lead_id>                      -- runs the automated GitHub/Vercel handoff steps
  POST /webhook/stripe                         -- Stripe `checkout.session.completed` events

Run standalone with `python webhook_server.py` (dev server) or behind a real
WSGI server (gunicorn/uwsgi) in production. Must be reachable at
config.PUBLIC_BASE_URL for the click-tracking, unsubscribe, screenshot, and
editor links embedded in outgoing emails to work.
"""
from __future__ import annotations

import stripe
from flask import Flask, Response, abort, redirect, render_template_string, request, send_from_directory

import config
from agents import editor_agent, onboarding_agent
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
<p style="margin-top:24px;font-size:0.85rem;"><a href="/edit/{{ lead.id }}/request-link">Lost this link? Request a new one</a></p>
</body></html>
"""

_LINK_EXPIRED_TEMPLATE = """
<!doctype html><html><head><title>Link no longer valid</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="font-family: -apple-system, system-ui, sans-serif; max-width: 480px; margin: 60px auto; padding: 0 16px; text-align: center; color: #1e293b;">
<h1>This editing link is no longer valid</h1>
<p>It may have expired, been revoked, or been mistyped.</p>
<p><a href="/edit/{{ lead_id }}/request-link">Request a new editing link</a></p>
</body></html>
"""

_REQUEST_LINK_TEMPLATE = """
<!doctype html><html><head><title>Request an editing link</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="font-family: -apple-system, system-ui, sans-serif; max-width: 480px; margin: 60px auto; padding: 0 16px; color: #1e293b;">
<h1>Request a new editing link</h1>
<p>Enter the email address on file for this website and we'll send you a fresh link.</p>
{% if message %}<p style="padding:12px;background:#eef2ff;border-radius:6px;">{{ message }}</p>{% endif %}
<form method="post">
  <input type="email" name="email" placeholder="you@example.com" required style="width:100%;padding:8px;box-sizing:border-box;">
  <button type="submit" style="margin-top:12px;padding:10px 24px;border:none;border-radius:6px;background:#1e293b;color:white;cursor:pointer;">Send me a link</button>
</form>
</body></html>
"""

_ONBOARD_FORM_TEMPLATE = """
<!doctype html><html><head><title>Claim your website -- {{ lead.business_name }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 640px; margin: 40px auto; padding: 0 16px; color: #1e293b; }
  label { display: block; font-weight: 600; margin-bottom: 4px; }
  input[type=text], input[type=email] { width: 100%; padding: 8px; box-sizing: border-box; font: inherit; border: 1px solid #cbd5e1; border-radius: 6px; }
  .field { margin-bottom: 20px; }
  .message { padding: 12px; background: #eef2ff; border-radius: 6px; margin-bottom: 20px; }
  button { padding: 10px 24px; font-size: 1rem; border: none; border-radius: 6px; background: #1e293b; color: white; cursor: pointer; }
</style>
</head>
<body>
<h1>Claim {{ lead.business_name }}'s website</h1>
<p>A couple of optional details and we'll set everything up in your name --
takes under a minute. Leave anything blank if you'd rather we keep
managing it for you.</p>
{% if message %}<p class="message">{{ message }}</p>{% endif %}
<form method="post">
<input type="hidden" name="token" value="{{ token }}">
<div class="field">
  <label for="github_username">GitHub username (optional)</label>
  <input type="text" id="github_username" name="github_username" value="{{ github_username }}">
</div>
<div class="field">
  <label for="vercel_email">Email for site access</label>
  <input type="email" id="vercel_email" name="vercel_email" value="{{ vercel_email }}">
</div>
<button type="submit">Claim my website</button>
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
        if not editor_auth.verify_editor_session(lead_id, token):
            return render_template_string(_LINK_EXPIRED_TEMPLATE, lead_id=lead_id), 403
        lead = db.get_lead(lead_id)
        if lead is None:
            abort(404)

        message = ""
        if request.method == "POST":
            submitted = {}
            for field, field_type in editor_agent.EDITABLE_FIELDS.items():
                raw = request.form.get(field, "")
                submitted[field] = (
                    [line.strip() for line in raw.splitlines() if line.strip()]
                    if field_type == "list" else raw.strip()
                )
            try:
                editor_agent.apply_edits(lead_id, submitted)
            except ValueError as exc:  # noqa: BLE001 - e.g. ssrf_guard.BlockedURLError on a bad photo/logo URL
                message = f"Could not save your changes: {exc}"
            else:
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

    @app.route("/edit/<int:lead_id>/request-link", methods=["GET", "POST"])
    def request_editor_link(lead_id: int) -> Response:
        message = ""
        if request.method == "POST":
            editor_agent.request_new_editor_link(lead_id, request.form.get("email", ""))
            # Identical message regardless of whether it matched -- see
            # request_new_editor_link's docstring: this must not become an
            # oracle for enumerating valid emails per lead_id.
            message = "If that email is on file for this site, a new editing link is on its way."
        return render_template_string(_REQUEST_LINK_TEMPLATE, message=message)

    @app.route("/buy/<int:lead_id>", methods=["GET"])
    def buy_now(lead_id: int) -> Response:
        """Self-serve checkout: creates a FRESH Stripe Checkout Session on
        every click and redirects to it, rather than embedding a
        pre-generated checkout URL in an email that might be opened days
        later -- Checkout Sessions expire (Stripe's default is 24 hours),
        so a stale embedded link would silently break. This is the
        buy-now-with-zero-human-intervention path (see PLAN.md's
        automation audit); `payment ready <lead_id>` in main.py's console
        remains the operator-triggered path for a lead who replied
        instead of self-serving."""
        lead = db.get_lead(lead_id)
        if lead is None:
            abort(404)
        if lead["status"] == "won":
            return (
                "<h1>You're all set!</h1>"
                "<p>We already have your payment for this site -- no need to pay again. "
                "Reach out if anything looks off.</p>",
                200,
            )
        try:
            checkout_url = stripe_utils.create_checkout_session(
                lead_id=lead_id, business_name=lead["business_name"],
                customer_email=lead.get("contact_email") or None,
            )
        except Exception as exc:  # noqa: BLE001 - show a friendly page instead of a raw 500
            return f"<h1>Something went wrong</h1><p>Could not start checkout: {exc}</p>", 500
        return redirect(checkout_url, code=302)

    @app.route("/onboard/<int:lead_id>", methods=["GET", "POST"])
    def onboard(lead_id: int) -> Response:
        token = request.values.get("token", "")
        if not editor_auth.verify_editor_session(lead_id, token):
            return render_template_string(_LINK_EXPIRED_TEMPLATE, lead_id=lead_id), 403
        lead = db.get_lead(lead_id)
        if lead is None:
            abort(404)
        if lead["status"] != "won":
            return (
                "<h1>Payment not yet confirmed</h1>"
                "<p>Please wait a moment and refresh, or reply to your confirmation email if this persists.</p>",
                409,
            )

        message = ""
        if request.method == "POST":
            try:
                result = onboarding_agent.complete_onboarding(
                    lead_id, request.form.get("github_username", ""), request.form.get("vercel_email", ""),
                )
                message = result["message"]
            except Exception as exc:  # noqa: BLE001 - show what happened instead of a 500
                message = f"Something went wrong: {exc}"

        return render_template_string(
            _ONBOARD_FORM_TEMPLATE, lead=lead, token=token, message=message,
            github_username="", vercel_email=lead.get("contact_email") or "",
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
                    onboarding_sent = onboarding_agent.send_onboarding_email(lead_id)
                    banner = "*" * 70
                    print(
                        f"\n{banner}\nPAYMENT RECEIVED: {lead['business_name']} (lead {lead_id})\n"
                        + (
                            "An onboarding email was sent automatically -- the client can claim their "
                            "own site without you doing anything.\n"
                            if onboarding_sent else
                            "Could not auto-email onboarding (no contact email on file, or the send "
                            "failed -- see the log above).\n"
                        )
                        + f"You can still run 'transfer {lead_id}' in the pipeline console any time "
                        f"to do it yourself instead.\n{banner}\n"
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
