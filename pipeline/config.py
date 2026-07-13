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

from dotenv import load_dotenv

# Load `.env` from the repo root (one level up from pipeline/) regardless of
# the current working directory the process was started from.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

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
]

# --- Values (all optional at import time; validated via validate()) --------
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
VERCEL_TOKEN: str = os.getenv("VERCEL_TOKEN", "")
VERCEL_TEAM_ID: str = os.getenv("VERCEL_TEAM_ID", "")
VERCEL_AUTOMATION_BYPASS_SECRET: str = os.getenv("VERCEL_AUTOMATION_BYPASS_SECRET", "")

EMAIL_HOST: str = os.getenv("EMAIL_HOST", "")
EMAIL_PORT: int = int(os.getenv("EMAIL_PORT", "587") or 587)
EMAIL_USER: str = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")

HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")

# Art-directed hero generation. Images are cached per lead and embedded in
# the static preview. Failure uses designed CSS, never random stock imagery.
ENABLE_AI_IMAGES: bool = _env_bool("ENABLE_AI_IMAGES", default=True)
HF_IMAGE_MODEL: str = os.getenv("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
AI_IMAGE_WIDTH: int = int(os.getenv("AI_IMAGE_WIDTH", "1024") or 1024)
AI_IMAGE_HEIGHT: int = int(os.getenv("AI_IMAGE_HEIGHT", "576") or 576)
AI_IMAGE_STEPS: int = int(os.getenv("AI_IMAGE_STEPS", "4") or 4)
AI_IMAGE_GUIDANCE: float = float(os.getenv("AI_IMAGE_GUIDANCE", "3.5") or 3.5)
GENERATED_ASSETS_DIR: str = os.getenv("GENERATED_ASSETS_DIR", "").strip() or str(
    Path(__file__).resolve().parent / "generated_assets"
)

STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_ALERT_CHANNEL: str = os.getenv("SLACK_ALERT_CHANNEL", "#leads")

ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
SENDING_DOMAIN: str = os.getenv("SENDING_DOMAIN", "")
PHYSICAL_ADDRESS: str = os.getenv("PHYSICAL_ADDRESS", "")

# `.strip() or default` (rather than getenv's own default) so an empty
# `DB_PATH=` line in .env falls back too, not just a fully-absent key.
DB_PATH: str = os.getenv("DB_PATH", "").strip() or str(Path(__file__).resolve().parent / "leads.db")
PUBLIC_BASE_URL: str = (os.getenv("PUBLIC_BASE_URL", "").strip() or "http://localhost:5000").rstrip("/")
WEBSITE_PRICE_USD: int = int(os.getenv("WEBSITE_PRICE_USD", "750") or 750)
# Discounted price quoted in cold emails for the pre-built draft (see
# sales_agent.py's offer copy). Separate from WEBSITE_PRICE_USD, which is
# the amount actually charged via Stripe checkout once a lead says yes.
WEBSITE_OFFER_PRICE: int = int(os.getenv("WEBSITE_OFFER_PRICE", "750") or 750)

# Live-send safety. Keep this false while testing the pipeline end-to-end;
# SalesAgent will record a dry-run outbound thread instead of contacting SMTP
# or SendGrid. Set ENABLE_LIVE_SEND=true only after domain/email setup is ready.
ENABLE_LIVE_SEND: bool = _env_bool("ENABLE_LIVE_SEND", default=False)

# --- SendGrid configuration (optional) ----------------------------------------
SENDGRID_API_KEY: str = os.getenv("SENDGRID_API_KEY", "")
SENDGRID_FROM_EMAIL: str = os.getenv("SENDGRID_FROM_EMAIL", "")

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


if __name__ == "__main__":
    # `python config.py` doubles as a quick "am I configured?" check.
    try:
        validate()
    except RuntimeError as exc:
        print(f"[config] INVALID: {exc}")
        raise SystemExit(1)
    print("[config] All required environment variables are set.")
