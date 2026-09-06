"""Environment loading and validation for the cold-email sales pipeline.

Every other module imports `config` (not `os.environ` directly) so that
required-variable validation happens exactly once, at process startup, in
one place. Call `config.validate()` early in any entrypoint (main.py,
webhook_server.py, dashboard.py) before doing real work.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
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
# IMAP host for reply polling. Most providers use a different hostname for
# IMAP than for SMTP (Zoho: imap.zoho.com vs smtp.zoho.com; Google:
# imap.gmail.com vs smtp.gmail.com), so this is separate. Falls back to
# EMAIL_HOST when blank for providers that serve both on one hostname.
EMAIL_IMAP_HOST: str = os.getenv("EMAIL_IMAP_HOST", "").strip() or EMAIL_HOST
EMAIL_USER: str = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")

HF_API_TOKEN: str = os.getenv("HF_API_TOKEN", "")

STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")

SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_ALERT_CHANNEL: str = os.getenv("SLACK_ALERT_CHANNEL", "#leads")

ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
# Human name shown in the From header and used as the email sign-off.
# Prospects reply to a person, not a domain, so this should be a first name.
SENDER_NAME: str = os.getenv("SENDER_NAME", "").strip() or "Casey"
SENDING_DOMAIN: str = os.getenv("SENDING_DOMAIN", "")
PHYSICAL_ADDRESS: str = os.getenv("PHYSICAL_ADDRESS", "")

UNSPLASH_ACCESS_KEY: str = os.getenv("UNSPLASH_ACCESS_KEY", "")
# "modern" (default) renders every niche through templates/modern, the
# photo-led design driven by agents/design_agent.py's NICHE_THEMES. "legacy"
# uses the old text-only per-niche folders.
DESIGN_TEMPLATE_STYLE: str = (os.getenv("DESIGN_TEMPLATE_STYLE", "").strip().lower() or "modern")
# `.strip() or default` (rather than getenv's own default) so an empty
# `DB_PATH=` line in .env falls back too, not just a fully-absent key.
DB_PATH: str = os.getenv("DB_PATH", "").strip() or str(Path(__file__).resolve().parent / "leads.db")
PUBLIC_BASE_URL: str = (os.getenv("PUBLIC_BASE_URL", "").strip() or "http://localhost:5000").rstrip("/")
# --- Currency ----------------------------------------------------------------
# ONE source of truth for the money. Every price a prospect reads and every
# Stripe session amount derives from this, so the email copy and the actual
# charge can never disagree (they used to: the copy said GBP while Stripe
# charged USD, silently under-collecting ~21% on every autonomous close).
# Switching to USD for international leads is a one-line .env change.
#
# Restricted to two-decimal currencies on purpose: utils/stripe_utils.py bills
# `price * 100` minor units, which is correct for these and WRONG by 100x for
# zero-decimal currencies like JPY. validate() rejects anything not listed here
# rather than letting that reach a real card.
_CURRENCY_SYMBOLS = {
    "gbp": "£",
    "usd": "$",
    "eur": "€",
    "aud": "A$",
    "cad": "C$",
    "nzd": "NZ$",
}
CURRENCY: str = (os.getenv("CURRENCY", "").strip().lower() or "gbp")
CURRENCY_SYMBOL: str = _CURRENCY_SYMBOLS.get(CURRENCY, "")


def _int_env(*names: str, default: int) -> int:
    """First non-zero value among `names`, else `default`.

    Accepts the legacy `*_USD` spellings so an existing .env written before the
    currency became configurable keeps working unchanged.
    """
    for name in names:
        try:
            value = int(os.getenv(name, "0") or 0)
        except ValueError:
            continue
        if value:
            return value
    return default


# Amount charged via Stripe checkout once a lead says yes, in CURRENCY units.
WEBSITE_PRICE: int = _int_env("WEBSITE_PRICE", "WEBSITE_PRICE_USD", default=750)
# Discounted price quoted in cold emails for the pre-built draft (see
# sales_agent.py's offer copy). Separate from WEBSITE_PRICE.
WEBSITE_OFFER_PRICE: int = _int_env("WEBSITE_OFFER_PRICE", default=750)
# The higher "standard package" figure the cold email anchors against before
# quoting the discounted draft price. Config, not a magic number in the copy,
# so it moves with the currency.
STANDARD_PACKAGE_PRICE: int = _int_env("STANDARD_PACKAGE_PRICE", default=2000)

# Secondary offer: a monthly plan (hosting, edits, domain) beside the one-off
# price. Quoted in the cold email and by the negotiator; a prospect who
# picks it is handed to a human (Stripe subscriptions are not automated yet).
SUBSCRIPTION_ENABLED: bool = os.getenv("SUBSCRIPTION_ENABLED", "true").strip().lower() not in ("0", "false", "no")
SUBSCRIPTION_MONTHLY_PRICE: int = _int_env("SUBSCRIPTION_MONTHLY_PRICE", default=39)

# Trust builders stated in every email: money-back window and free-edits window.
GUARANTEE_DAYS: int = _int_env("GUARANTEE_DAYS", default=14)
FREE_EDITS_DAYS: int = _int_env("FREE_EDITS_DAYS", default=30)

# One reminder to leads who never replied, this many days after the cold email.
FOLLOW_UP_ENABLED: bool = os.getenv("FOLLOW_UP_ENABLED", "true").strip().lower() not in ("0", "false", "no")
FOLLOW_UP_AFTER_DAYS: int = _int_env("FOLLOW_UP_AFTER_DAYS", default=3)

# --- Autonomous negotiation band (see agents/sales_agent.py) -----------------
# The LLM negotiation agent may quote any whole-number price inside
# [NEGOTIATION_FLOOR, NEGOTIATION_CEILING], in CURRENCY units. The band is
# enforced in code -- every price is clamped before it reaches an email or a
# Stripe session -- so a confused or prompt-injected model can never discount
# below the floor. Defaults: ceiling = the advertised offer price
# (WEBSITE_OFFER_PRICE -- the number the cold email actually quotes),
# floor = 80% of it. The ceiling is deliberately the *offer* price, not
# WEBSITE_PRICE: negotiation is about the pre-built draft the email offered, so
# quoting above that price would be a bait-and-switch on the prospect.
NEGOTIATION_CEILING: int = _int_env(
    "NEGOTIATION_CEILING", "NEGOTIATION_CEILING_USD", default=WEBSITE_OFFER_PRICE
)
NEGOTIATION_FLOOR: int = _int_env(
    "NEGOTIATION_FLOOR", "NEGOTIATION_FLOOR_USD",
    default=max(1, (NEGOTIATION_CEILING * 80) // 100),
)
# After this many autonomous replies in one negotiation the agent stops
# replying and alerts a human instead -- a runaway back-and-forth (including
# two autoresponders emailing each other) must degrade to an alert, never an
# infinite email loop.
MAX_NEGOTIATION_ROUNDS: int = int(os.getenv("MAX_NEGOTIATION_ROUNDS", "6") or 6)

# --- SendGrid configuration (optional; ON THE BACK BURNER) --------------------
# SendGrid retired its free tier in 2025 and its terms prohibit cold outreach,
# so the pipeline sends over plain SMTP from the Zoho mailbox on the secondary
# domain instead. The SendGrid path in utils/email_utils.py is kept intact in
# case a transactional use (e.g. payment receipts) ever justifies it. Leave
# SENDGRID_API_KEY blank in .env and this branch is never taken.
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

# --- Lead sourcing via Google Places API (New) (optional) -------------------
# Off by default: sourcing spends API quota and grows the lead list (and so
# the outbound email volume) on its own, so it has to be an explicit opt-in.
# See agents/sourcing_agent.py.


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_list(name: str, default: str, separator: str) -> list[str]:
    raw = os.getenv(name, "").strip() or default
    return [item.strip() for item in raw.split(separator) if item.strip()]


GOOGLE_PLACES_API_KEY: str = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
SOURCING_ENABLED: bool = _env_bool("SOURCING_ENABLED", False)
# Businesses with no website are useless to this pipeline (LeadAgent can't
# find an email to send to), so they're filtered out unless this is false.
SOURCING_REQUIRE_WEBSITE: bool = _env_bool("SOURCING_REQUIRE_WEBSITE", True)

# Home-patch exclusion: skip businesses within this many miles of the point
# below (lat,lng). Default is Oxted, Surrey -- close enough to home that a
# cold email to someone you know is embarrassing. Blank centre or 0 = off.
def _parse_latlng(raw: str):
    parts = [p.strip() for p in (raw or "").split(",")]
    if len(parts) != 2:
        return None
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError:
        return None


SOURCING_EXCLUDE_CENTER = _parse_latlng(os.getenv("SOURCING_EXCLUDE_CENTER", "51.2572,0.0040"))
SOURCING_EXCLUDE_RADIUS_MILES: float = float(os.getenv("SOURCING_EXCLUDE_RADIUS_MILES", "6") or 6)
SOURCING_DAILY_LIMIT: int = int(os.getenv("SOURCING_DAILY_LIMIT", "30") or 30)
# Comma-separated; each entry must match a template folder under templates/.
SOURCING_NICHES: list[str] = [
    niche.lower()
    for niche in _env_list("SOURCING_NICHES", "plumber,electrician,landscaper,cafe,salon,dentist,gym,restaurant", ",")
]
# Semicolon-separated, because a useful search location ("Oxted, Surrey")
# contains a comma itself. Each is appended to the niche: "plumber in Oxted, Surrey".
SOURCING_LOCATIONS: list[str] = _env_list(
    "SOURCING_LOCATIONS",
    "Oxted, Surrey; Caterham, Surrey; Reigate, Surrey; Croydon, London; Bromley, London",
    ";",
)
# main.py runs sourcing at most this often (it's a quota-spending API call,
# and new businesses don't appear on Google every 60-second cycle).
SOURCING_INTERVAL_SECONDS: int = 3600

# --- Rate limiting (cold email deliverability safeguards) ------------------
EMAIL_MIN_DELAY_SECONDS: int = 120
EMAIL_MAX_DELAY_SECONDS: int = 300
EMAIL_MAX_PER_HOUR: int = 20
# Global daily cap across EVERY sending mailbox. Env-overridable (default
# unchanged at 50) because a multi-mailbox setup needs it raised above what
# one mailbox alone should send -- see EMAIL_ACCOUNTS below.
EMAIL_MAX_PER_DAY: int = _int_env("EMAIL_MAX_PER_DAY", default=50)
# Per-mailbox daily cap. ~25/day is the realistic deliverability ceiling for
# one cold-outreach mailbox, so volume comes from more mailboxes each under
# this cap, not from raising it. Defaults to EMAIL_MAX_PER_DAY so a
# single-mailbox setup is capped exactly as before.
EMAIL_MAX_PER_DAY_PER_ACCOUNT: int = _int_env("EMAIL_MAX_PER_DAY_PER_ACCOUNT", default=EMAIL_MAX_PER_DAY)
# NOTE: A dedicated IP/domain warm-up tool (e.g. Instantly, Mailwarm, or a
# manual warm-up schedule) is strongly recommended for the first 2-4 weeks of
# a new sending domain's life. This pipeline only staggers *send timing* and
# enforces hourly/daily caps -- it does NOT warm up the domain's reputation
# for you. Do not point this at a brand-new domain on day one.
INBOX_POLL_SECONDS: int = 300
MAIN_LOOP_SLEEP_SECONDS: int = 60

# --- Preview expiry --------------------------------------------------------
# Cold emails promise the preview "is live for 7 days -- after that it'll be
# repurposed" (see agents/sales_agent.py). utils/teardown.py makes that true
# by deleting the Vercel project + GitHub repo of previews older than
# PREVIEW_TTL_DAYS whose lead never turned into a conversation. Keep this in
# sync with the wording in the email copy if you change it.
PREVIEW_TTL_DAYS: int = int(os.getenv("PREVIEW_TTL_DAYS", "7") or 7)
# Set to false/0/no to keep the hourly teardown pass in main.py from running
# (the one-shot `python pipeline/utils/teardown.py` CLI ignores this flag).
PREVIEW_TEARDOWN_ENABLED: bool = (
    os.getenv("PREVIEW_TEARDOWN_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
)

# --- Sending mailboxes -------------------------------------------------------
# Deliverability tops out around 25 cold emails/day per mailbox, so real
# volume means several mailboxes (ideally on several domains), not one busy
# one. EMAIL_USER/EMAIL_PASSWORD is ALWAYS account 0; EMAIL_ACCOUNTS adds the
# rest. utils/mailboxes.py spreads new leads across them and pins each lead
# to the mailbox that first emailed it, so a thread never changes From
# address mid-conversation and replies always land in the inbox that sent.


@dataclass(frozen=True)
class EmailAccount:
    """One SMTP/IMAP mailbox the pipeline may send from and poll replies in."""

    user: str
    password: str
    smtp_host: str
    smtp_port: int
    imap_host: str
    display_name: str


def parse_email_accounts(raw: str, primary: EmailAccount) -> list[EmailAccount]:
    """Parse the EMAIL_ACCOUNTS env value into `[primary, *extras]`.

    Entries are ';'-separated, each `user:password` or
    `user:password:smtp_host:imap_host`. Blank hosts in the 4-field form fall
    back to the primary's (i.e. EMAIL_HOST / EMAIL_IMAP_HOST); port and
    display name are always the primary's. A password may contain ':' ONLY
    in the 4-field form: the two hosts are split off from the right, so
    everything between the user and them is the password. A 2-field entry
    with a stray ':' is ambiguous and rejected rather than guessed at -- a
    wrong split here would mean a failed login on every send and poll. An
    entry naming the primary user again is dropped so one mailbox can't
    appear twice in the rotation.
    """
    accounts = [primary]
    seen = {primary.user.strip().lower()}
    for index, entry in enumerate(raw.split(";")):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise ValueError(
                f"EMAIL_ACCOUNTS entry {index + 1} ({entry!r}) has no ':'; expected "
                "user:password or user:password:smtp_host:imap_host"
            )
        user, rest = entry.split(":", 1)
        user = user.strip()
        colons = rest.count(":")
        if colons == 0:
            password, smtp_host, imap_host = rest, "", ""
        elif colons >= 2:
            password, smtp_host, imap_host = rest.rsplit(":", 2)
        else:
            raise ValueError(
                f"EMAIL_ACCOUNTS entry {index + 1} ({user}) has three fields; use "
                "user:password or user:password:smtp_host:imap_host (a password "
                "containing ':' needs the 4-field form)"
            )
        if not user or not password:
            raise ValueError(f"EMAIL_ACCOUNTS entry {index + 1} has an empty user or password")
        if user.lower() in seen:
            continue
        seen.add(user.lower())
        accounts.append(
            EmailAccount(
                user=user,
                password=password,
                smtp_host=smtp_host.strip() or primary.smtp_host,
                smtp_port=primary.smtp_port,
                imap_host=imap_host.strip() or primary.imap_host,
                display_name=primary.display_name,
            )
        )
    return accounts


_PRIMARY_EMAIL_ACCOUNT = EmailAccount(
    user=EMAIL_USER,
    password=EMAIL_PASSWORD,
    smtp_host=EMAIL_HOST,
    smtp_port=EMAIL_PORT,
    imap_host=EMAIL_IMAP_HOST,
    display_name=SENDER_NAME,
)
# Account 0 is always the primary mailbox, so an existing single-mailbox .env
# keeps working with no changes. A malformed EMAIL_ACCOUNTS raises here, at
# import, in keeping with fail-loudly-at-startup rather than at first send.
EMAIL_ACCOUNTS: list[EmailAccount] = parse_email_accounts(
    os.getenv("EMAIL_ACCOUNTS", ""), _PRIMARY_EMAIL_ACCOUNT
)


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
    # An unsupported currency must never reach Stripe: stripe_utils bills
    # `price * 100` minor units, so a zero-decimal currency (JPY) would charge
    # 100x the agreed amount. Fail at startup instead.
    if CURRENCY not in _CURRENCY_SYMBOLS:
        raise RuntimeError(
            f"CURRENCY={CURRENCY!r} is not supported. Use one of: "
            + ", ".join(sorted(_CURRENCY_SYMBOLS))
            + ". (Zero-decimal currencies like JPY are excluded deliberately -- "
            "the Stripe amount is computed as price * 100.)"
        )
    # A non-positive band would defeat the price clamp: a negative override
    # (e.g. NEGOTIATION_FLOOR=-500) is truthy, so it bypasses the derived
    # default and, since _clamp_price uses max(floor, ...), lets a lowball
    # price through below any sane minimum. Reject it at startup.
    if NEGOTIATION_FLOOR <= 0 or NEGOTIATION_CEILING <= 0:
        raise RuntimeError(
            f"Negotiation band must be positive, got floor={NEGOTIATION_FLOOR}, "
            f"ceiling={NEGOTIATION_CEILING}. Fix NEGOTIATION_FLOOR / "
            "NEGOTIATION_CEILING in .env."
        )
    if NEGOTIATION_FLOOR > NEGOTIATION_CEILING:
        raise RuntimeError(
            f"NEGOTIATION_FLOOR ({NEGOTIATION_FLOOR}) exceeds "
            f"NEGOTIATION_CEILING ({NEGOTIATION_CEILING}); fix the "
            "negotiation band in .env before running the autonomous sales agent."
        )


if __name__ == "__main__":
    # `python config.py` doubles as a quick "am I configured?" check.
    try:
        validate()
    except RuntimeError as exc:
        print(f"[config] INVALID: {exc}")
        raise SystemExit(1)
    print("[config] All required environment variables are set.")
