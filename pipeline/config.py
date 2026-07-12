"""Environment loading and validation for the cold-email sales pipeline.

Every other module imports `config` (not `os.environ` directly) so that
required-variable validation happens exactly once, at process startup, in
one place. Call `config.validate()` early in any entrypoint (main.py,
webhook_server.py, dashboard.py) before doing real work.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

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
    "HF_API_TOKEN",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "ADMIN_EMAIL",
    "SENDING_DOMAIN",
    "PHYSICAL_ADDRESS",
    "SECRET_KEY",
]

# --- Values (all optional at import time; validated via validate()) --------
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
VERCEL_TOKEN: str = os.getenv("VERCEL_TOKEN", "")
VERCEL_TEAM_ID: str = os.getenv("VERCEL_TEAM_ID", "")
# Sent as the `x-vercel-protection-bypass` header by screenshot.py as a
# secondary defense against Vercel's own login wall, on top of
# vercel_api.py disabling deployment protection outright. Optional --
# only needed if disabling protection ever lags or fails.
VERCEL_BYPASS_TOKEN: str = os.getenv("VERCEL_BYPASS_TOKEN", "")

EMAIL_HOST: str = os.getenv("EMAIL_HOST", "")
EMAIL_PORT: int = int(os.getenv("EMAIL_PORT", "587") or 587)
EMAIL_USER: str = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")

HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")

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
# Discounted price quoted in cold emails for the pre-built draft (see
# sales_agent.py's offer copy). Separate from WEBSITE_PRICE_USD, which is
# the amount actually charged via Stripe checkout once a lead says yes.
WEBSITE_OFFER_PRICE: int = int(os.getenv("WEBSITE_OFFER_PRICE", "750") or 750)

# --- SendGrid configuration (optional) ----------------------------------------
SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")

# --- Live-send safety valve --------------------------------------------------
# Defaults to OFF (dry run) so a fresh checkout / misconfigured .env can
# never blast real cold emails by accident. Every send funnels through
# utils/email_utils.py's send_email()/send_email_sendgrid(), so gating there
# covers every caller (sales_agent.py's cold + goodbye emails, main.py's
# payment-link email) without needing a separate check in each entrypoint.
# Set ENABLE_LIVE_SEND=true in .env once you're ready to actually send.
ENABLE_LIVE_SEND: bool = os.getenv("ENABLE_LIVE_SEND", "").strip().lower() in ("1", "true", "yes")

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


# Used to HMAC-sign unsubscribe and click-tracking tokens. Must be supplied
# via .env and stay stable across restarts -- a regenerated key invalidates
# every unsubscribe/click token already sent, which would silently break
# unsubscribe links (a CAN-SPAM violation) the moment the container
# restarts. No auto-generated fallback: config.validate() fails startup
# loudly instead.
SECRET_KEY: str = os.getenv("SECRET_KEY", "").strip()

# Obvious leftover-placeholder fragments a real postal address would never
# contain -- catches an unfilled .env.example value getting copied verbatim
# into .env, which config.validate()'s mere non-empty check can't catch.
_ADDRESS_PLACEHOLDER_FRAGMENTS = (
    "your the walk",
    "123 street",
    "123 test",
    "123 main st",
    "testville",
    "your address here",
    "your address",
    "address here",
    "placeholder",
)


def physical_address_problem() -> Optional[str]:
    """Return why PHYSICAL_ADDRESS is unusable (empty, or contains an
    obvious placeholder fragment), or None if it looks like a real postal
    address. CAN-SPAM (and UK PECR/GDPR transparency) require a genuine
    postal address in every cold email's footer, so utils/compliance.py and
    agents/sales_agent.py refuse to draft/send while this returns a problem."""
    address = PHYSICAL_ADDRESS.strip()
    if not address:
        return "empty"
    lowered = address.lower()
    for fragment in _ADDRESS_PLACEHOLDER_FRAGMENTS:
        if fragment in lowered:
            return f"contains placeholder text {fragment!r}"
    return None


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


if __name__ == "__main__":
    # `python config.py` doubles as a quick "am I configured?" check.
    try:
        validate()
    except RuntimeError as exc:
        print(f"[config] INVALID: {exc}")
        raise SystemExit(1)
    print("[config] All required environment variables are set.")
