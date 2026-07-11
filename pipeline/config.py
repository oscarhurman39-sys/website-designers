"""Environment loading and validation for the cold-email sales pipeline.

Every other module imports `config` (not `os.environ` directly) so that
required-variable validation happens exactly once, at process startup, in
one place. Call `config.validate()` early in any entrypoint (main.py,
webhook_server.py, dashboard.py) before doing real work.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv

# Load `.env` from the repo root (one level up from pipeline/) regardless of
# the current working directory the process was started from.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

# --- Required variables -----------------------------------------------------
# These MUST be set for the pipeline to run. Missing any of these means the
# system cannot send compliant email, deploy sites, or take payment, so we
# fail loudly at startup rather than partway through a run.
REQUIRED_VARS: list[str] = [
    "GITHUB_TOKEN",
    "VERCEL_TOKEN",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_USER",
    "EMAIL_PASSWORD",
    # HF_API_TOKEN is deliberately NOT required: email drafting and website
    # copy both have deterministic fallbacks, so the pipeline is fully
    # functional without Hugging Face. Leave it blank in .env to skip the
    # HF calls entirely (instant fallback, no connection-retry wait) --
    # useful on networks where api-inference.huggingface.co is unreachable.
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "ADMIN_EMAIL",
    "SENDING_DOMAIN",
    "PHYSICAL_ADDRESS",
    "GOOGLE_PLACES_API_KEY",
]

# --- Values (all optional at import time; validated via validate()) --------
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
VERCEL_TOKEN: str = os.getenv("VERCEL_TOKEN", "")
VERCEL_TEAM_ID: str = os.getenv("VERCEL_TEAM_ID", "")

# When true, NO email is ever actually sent: both transports (SMTP and
# SendGrid) build the full compliant message, print it to the console, and
# return a Message-ID as if it were sent -- so the whole pipeline (status
# transitions, thread logging, rate limiting) can be exercised end-to-end
# with zero deliverability risk. Set DRY_RUN=false to go live.
DRY_RUN: bool = os.getenv("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

EMAIL_HOST: str = os.getenv("EMAIL_HOST", "")
EMAIL_PORT: int = int(os.getenv("EMAIL_PORT", "587") or 587)
EMAIL_USER: str = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")

# Optional SendGrid transport (preferred for cold outreach deliverability).
# When SENDGRID_API_KEY is set, sales_agent.py sends via the SendGrid v3 API
# instead of SMTP; when it's blank the pipeline falls back to the SMTP
# settings above, so neither is required on its own. SENDGRID_FROM_EMAIL is
# the verified sender address (falls back to EMAIL_USER if left blank).
# Note: replies still come back over IMAP (EMAIL_HOST/EMAIL_USER), so point
# SENDGRID_FROM_EMAIL at a mailbox you actually poll.
SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")
# Optional verification key for the SendGrid Event Webhook (/webhook/sendgrid).
# When set, incoming events are Ed25519-signature-verified before being
# recorded; when blank the endpoint accepts events unverified (fine for local
# testing, but set this in production so counts can't be spoofed).
SENDGRID_WEBHOOK_VERIFICATION_KEY: str = os.getenv("SENDGRID_WEBHOOK_VERIFICATION_KEY", "")

# --- Subject-line A/B test (optional) ---------------------------------------
# When BOTH are set, sales_agent.py picks one at random (50/50) per cold email
# and logs the choice (email_threads.subject_variant) so the dashboard can
# compare open/click rates. When either is blank, the drafted subject is used
# unchanged. `{business_name}` (and `{niche}` / `{location}`) are substituted.
SUBJECT_A: str = os.getenv("SUBJECT_A", "")
SUBJECT_B: str = os.getenv("SUBJECT_B", "")

HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")

# Used by agents/lead_agent.py's Places API (New) client -- see
# utils/places_api.py. Required because Google Maps Platform's Terms of
# Service explicitly prohibit scraping Maps/Places content ("Customer will
# not export, extract, or otherwise scrape Google Maps Content for use
# outside the Services", Maps Platform ToS 3.2.3); the Places API is the
# only ToS-compliant way to get this data. Get a key at
# https://console.cloud.google.com/google/maps-apis -- note some fields
# used here (opening hours) are billed at the "Enterprise" SKU tier, not
# the base tier; check current pricing before high-volume use.
GOOGLE_PLACES_API_KEY: str = os.getenv("GOOGLE_PLACES_API_KEY", "")

STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_ALERT_CHANNEL: str = os.getenv("SLACK_ALERT_CHANNEL", "#leads")

ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
SENDING_DOMAIN: str = os.getenv("SENDING_DOMAIN", "")
PHYSICAL_ADDRESS: str = os.getenv("PHYSICAL_ADDRESS", "")

UNSPLASH_ACCESS_KEY: str = os.getenv("UNSPLASH_ACCESS_KEY", "")
# `.strip() or default` (rather than getenv's own default) so an empty
# `DB_PATH=` line in .env falls back too, not just a fully-absent key.
DB_PATH: str = os.getenv("DB_PATH", "").strip() or str(Path(__file__).resolve().parent / "leads.db")
PUBLIC_BASE_URL: str = (os.getenv("PUBLIC_BASE_URL", "").strip() or "http://localhost:5000").rstrip("/")
WEBSITE_PRICE_USD: int = int(os.getenv("WEBSITE_PRICE_USD", "750") or 750)

# --- On-preview pricing card (display only) ---------------------------------
# These drive ONLY the "Your Offer" section rendered on generated preview
# sites (templates/*/index.html, wired via design_agent.build_context) --
# they are display strings, not currency-converted or synced with
# WEBSITE_PRICE_USD, which is the actual amount Stripe charges (in USD,
# stripe_utils.py). If you want the on-site offer to match what Stripe
# collects, set these to match manually, but be mindful WEBSITE_PRICE_USD
# is USD while the site copy defaults to a £ symbol -- adjust both if your
# pricing/currency changes.
WEBSITE_REGULAR_PRICE: int = int(os.getenv("WEBSITE_REGULAR_PRICE", "2000") or 2000)
WEBSITE_OFFER_PRICE: int = int(os.getenv("WEBSITE_OFFER_PRICE", "750") or 750)

# --- VoltAgent observability (optional) -------------------------------------
# When both keys are set, pipeline/utils/tracer.py mirrors every agent/tool
# span to VoltAgent Cloud in addition to the always-on local trace file
# below (the VoltAgent Python SDK is write-only -- there is no endpoint to
# read traces back out -- so the local file remains the dashboard's source
# of truth regardless of whether cloud mirroring is enabled).
VOLTAGENT_PUBLIC_KEY: str = os.getenv("VOLTAGENT_PUBLIC_KEY", "")
VOLTAGENT_SECRET_KEY: str = os.getenv("VOLTAGENT_SECRET_KEY", "")
VOLTAGENT_BASE_URL: str = (os.getenv("VOLTAGENT_BASE_URL", "").strip() or "https://api.voltagent.dev").rstrip("/")
TRACES_PATH: str = os.getenv("TRACES_PATH", "").strip() or str(Path(__file__).resolve().parent / "traces.json")

# --- Rate limiting (cold email deliverability safeguards) ------------------
EMAIL_MIN_DELAY_SECONDS: int = 120
EMAIL_MAX_DELAY_SECONDS: int = 300
EMAIL_MAX_PER_HOUR: int = 20
EMAIL_MAX_PER_DAY: int = 50
# NOTE: A dedicated IP/domain warm-up tool (e.g. Instantly, Mailwarm, or a
# manual warm-up schedule) is strongly recommended for the first 2-4 weeks of
# a new sending domain's life. This pipeline only staggers *send timing* and
# enforces hourly/daily caps -- it does NOT warm up the domain's reputation
# for you. Do not point this at a brand-new domain on day one.
INBOX_POLL_SECONDS: int = 300
MAIN_LOOP_SLEEP_SECONDS: int = 60


def _load_or_create_secret_key() -> str:
    """Return SECRET_KEY from env, or a persisted generated one.

    Used to HMAC-sign unsubscribe and click-tracking tokens. If not supplied
    via .env, we generate one on first run and persist it to a gitignored
    file next to this module so tokens already sent in emails keep working
    across restarts.
    """
    env_value = os.getenv("SECRET_KEY", "").strip()
    if env_value:
        return env_value

    secret_file = Path(__file__).resolve().parent / ".secret_key"
    if secret_file.exists():
        return secret_file.read_text().strip()

    generated = secrets.token_hex(32)
    secret_file.write_text(generated)
    return generated


SECRET_KEY: str = _load_or_create_secret_key()


def validate() -> None:
    """Raise RuntimeError listing every missing required variable.

    Call this once at process startup (main.py, webhook_server.py,
    dashboard.py). Intentionally fails fast with a full list rather than
    one-at-a-time so operators fix everything in a single pass.
    """
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + f"\nCopy .env.example to .env ({_ENV_PATH}) and fill them in."
        )


# Obvious placeholder fragments that mean PHYSICAL_ADDRESS was never filled
# in with a real postal address. Matched case-insensitively as substrings.
# Deliberately does NOT include "123 Test St, Testville" -- that's the
# documented fixture address for the opt-in integration test
# (.env.test.example), which must stay sendable.
_ADDRESS_PLACEHOLDER_FRAGMENTS = (
    "your the walk",
    "123 street",
    "your address here",
    "your address",
    "address here",
    "placeholder",
)


def claim_mailto(business_name: str) -> str:
    """mailto: link for the preview site's pricing CTA -- goes TO us (the
    operator), not the lead, since a static site has no idea who's viewing
    it. Same fallback chain as compliance.py's unsubscribe mailto, so it
    lands in the mailbox sales_agent.check_inbox() actually polls (or
    ADMIN_EMAIL if you route business replies there instead)."""
    addr = ADMIN_EMAIL or EMAIL_USER or SENDGRID_FROM_EMAIL
    subject = quote(f"I'd like to claim my {business_name} website")
    return f"mailto:{addr}?subject={subject}"


def physical_address_problem() -> Optional[str]:
    """Return why PHYSICAL_ADDRESS is unusable (empty, or contains an
    obvious placeholder fragment), or None if it looks like a real postal
    address. CAN-SPAM (and UK PECR/GDPR transparency) require a genuine
    postal address in every cold email's footer, so the transports in
    utils/email_utils.py refuse to send while this returns a problem."""
    address = PHYSICAL_ADDRESS.strip()
    if not address:
        return "empty"
    lowered = address.lower()
    for fragment in _ADDRESS_PLACEHOLDER_FRAGMENTS:
        if fragment in lowered:
            return f"contains placeholder text {fragment!r}"
    return None


def print_startup_diagnostics() -> None:
    """Print the handful of config values that determine whether an email
    actually sends, without revealing secrets. Call once at process startup
    (main.py, quick_run.py) right after validate() -- this is the first
    thing to check when "the email didn't arrive": a wrong/missing DRY_RUN
    or SENDGRID_API_KEY value here explains it before you go looking
    anywhere else.
    """
    print(f"DRY_RUN config: {DRY_RUN}")
    print(f"SendGrid key loaded: {bool(SENDGRID_API_KEY)} (length: {len(SENDGRID_API_KEY)})")
    from_addr = (SENDGRID_FROM_EMAIL or EMAIL_USER or "").strip()
    print(f"From address: {from_addr or '(unset)'}")
    freemail = ("@gmail.com", "@googlemail.com", "@outlook.com", "@hotmail.com", "@live.com", "@yahoo.com", "@icloud.com", "@aol.com")
    if from_addr.lower().endswith(freemail):
        bar = "!" * 70
        print(
            f"{bar}\nWARNING: the From address is a free mailbox ({from_addr}).\n"
            "SendGrid cannot DKIM-sign gmail.com/outlook.com/etc., so mail sent\n"
            "'from' a free mailbox fails DMARC at Gmail/Outlook and is silently\n"
            "spam-foldered or dropped -- SendGrid will still say 202 ACCEPTED.\n"
            "Fix: SendGrid dashboard -> Settings -> Sender Authentication ->\n"
            "Authenticate Your Domain (needs a domain you own + 3 DNS records),\n"
            f"then set SENDGRID_FROM_EMAIL=casey@yourdomain in .env.\n{bar}"
        )
    problem = physical_address_problem()
    if problem:
        bar = "!" * 70
        print(
            f"{bar}\nWARNING: PHYSICAL_ADDRESS is {problem}.\n"
            "Every cold email legally needs a real postal address in its footer,\n"
            "so ALL sends will be refused until you fix PHYSICAL_ADDRESS in .env.\n"
            f"The rest of the pipeline still runs normally.\n{bar}"
        )


if __name__ == "__main__":
    # `python config.py` doubles as a quick "am I configured?" check.
    try:
        validate()
    except RuntimeError as exc:
        print(f"[config] INVALID: {exc}")
        raise SystemExit(1)
    print("[config] All required environment variables are set.")
