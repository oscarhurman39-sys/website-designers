"""Go-live readiness check for the Website Designers sales pipeline.

Answers one question: if the pipeline ran right now, would it do something
wrong? It reports the state of every switch, checks the things that must be
true before real outreach or payment, and ends with one verdict line.

    python run.py preflight            # includes two network checks: the public
                                       # URL's /health and a login to each mailbox
    python run.py preflight --offline  # config, database and content checks only

It changes nothing: no switches flipped, no Stripe objects created, no email
sent, no database writes. Exit code 1 means at least one blocker.

Severity
--------
BLOCKER  wrong in any mode (missing config, bad address, Stripe secret missing,
         cold-email copy that would go out with the wrong price or link).
WARN     needs an operator decision, or only becomes a problem once live.
INFO     state worth seeing, not a judgement.

Some checks escalate from WARN to BLOCKER when ENABLE_LIVE_SEND=true: a dead
unsubscribe link, a queue full of test leads, Stripe test keys or a mailbox
that will not log in are harmless in a dry run and a real problem the moment
sends are live.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import config

REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIR = REPO_ROOT / "pipeline"
OUT_DIR = PIPELINE_DIR / "out"
TEMPLATES_DIR = REPO_ROOT / "templates"
# Template rendering logic (NICHE_THEMES, context building) lives here too, so
# a change to it makes yesterday's renders just as stale as a CSS edit would.
RENDER_SOURCES = (TEMPLATES_DIR, PIPELINE_DIR / "agents" / "design_agent.py")

BLOCKER, WARN, INFO = "BLOCKER", "WARN", "INFO"
WEEK_ONE_DAILY_CAP = 10  # WARMUP.md: 5-10 emails/day for the first week
NETWORK_TIMEOUT_SECONDS = 10
_URL_PLACEHOLDER_FRAGMENTS = ("your-project-name", "example.", "xxxx", "something.ngrok")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    level: str = BLOCKER

    @property
    def marker(self) -> str:
        if self.level == INFO:
            return INFO
        return "OK" if self.ok else self.level


def _escalate(armed: bool) -> str:
    """WARN while sends are a dry run, BLOCKER once live send is armed."""
    return BLOCKER if armed else WARN


# --- Individual probes (each side-effect free) ----------------------------------

def _is_local_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"", "localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    return any(fragment in host for fragment in _URL_PLACEHOLDER_FRAGMENTS)


def _stripe_mode() -> str:
    key = config.STRIPE_SECRET_KEY.strip()
    if key.startswith(("sk_live_", "rk_live_")):
        return "live"
    if key.startswith(("sk_test_", "rk_test_")):
        return "test"
    return "unknown" if key else "missing"


def _probe_public_url(base_url: str) -> tuple[bool, str]:
    """GET <PUBLIC_BASE_URL>/health. Only webhook_server.py answers 200 there;
    ngrok's own "endpoint offline" page answers 404 to every path, which is
    exactly the state where every unsubscribe link in a sent email is dead."""
    import requests

    host = urlparse(base_url).hostname or base_url
    try:
        resp = requests.get(f"{base_url}/health", timeout=NETWORK_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return False, f"{host} unreachable ({type(exc).__name__})"
    if resp.status_code == 200:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("ok") is True:
            return True, f"webhook_server.py answered /health at {host}"
        return False, f"{host} answered 200 but not with webhook_server.py's /health body"
    if resp.status_code == 404:
        return False, (
            f"HTTP 404 from {host}: the tunnel is offline or webhook_server.py is not running "
            "(start_public.bat), so unsubscribe/click/payment links are dead"
        )
    return False, f"HTTP {resp.status_code} from {host}"


def _mailbox_problem(account: config.EmailAccount) -> Optional[str]:
    """Log in over SMTP (STARTTLS) and IMAP with the configured app password.
    Returns a short reason on failure, None when both logins succeed."""
    import imaplib
    import smtplib

    try:
        with smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=NETWORK_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(account.user, account.password)
    except Exception as exc:  # noqa: BLE001 - any failure is the answer we want
        return f"SMTP {account.smtp_host}:{account.smtp_port} {type(exc).__name__}: {str(exc)[:120]}"
    try:
        imap = imaplib.IMAP4_SSL(account.imap_host, timeout=NETWORK_TIMEOUT_SECONDS)
        try:
            imap.login(account.user, account.password)
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001 - logout failure is irrelevant
                pass
    except Exception as exc:  # noqa: BLE001
        return f"IMAP {account.imap_host} {type(exc).__name__}: {str(exc)[:120]}"
    return None


def _scalar(db_path: str, sql: str, params: tuple = ()) -> Optional[int]:
    path = Path(db_path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(sql, params).fetchone()
        finally:
            conn.close()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error:
        return None


def _test_leads() -> Optional[list[dict]]:
    """Leads the cleanup script would treat as throwaway test data, or None
    when the database cannot be read."""
    if not Path(config.DB_PATH).exists():
        return None
    try:
        import cleanup_tests

        return cleanup_tests.find_test_leads()
    except sqlite3.Error:
        return None


def _newest_mtime(paths: tuple[Path, ...]) -> float:
    newest = 0.0
    for root in paths:
        if root.is_file():
            candidates = [root]
        elif root.exists():
            candidates = list(root.rglob("*"))
        else:
            candidates = []
        for candidate in candidates:
            if candidate.is_file():
                newest = max(newest, candidate.stat().st_mtime)
    return newest


def _age(ts: float) -> str:
    hours = max(0.0, (time.time() - ts) / 3600)
    return f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f}d ago"


def _renders_state() -> tuple[bool, str]:
    pngs = [p for p in OUT_DIR.glob("*.png") if p.is_file()] if OUT_DIR.exists() else []
    if not pngs:
        return False, "no pipeline/out/*.png renders; run python pipeline/render_preview.py --all-widths and look at them"
    newest_png = max(p.stat().st_mtime for p in pngs)
    newest_source = _newest_mtime(RENDER_SOURCES)
    if newest_source > newest_png:
        return False, (
            f"stale: templates/design_agent changed {_age(newest_source)}, after the last render "
            f"{_age(newest_png)}; re-run python pipeline/render_preview.py --all-widths"
        )
    return True, f"{len(pngs)} render(s) in pipeline/out, newest {_age(newest_png)}"


def _email_content_state() -> tuple[bool, str]:
    """Build the cold email the way sales_agent does for a sample lead and
    check the pieces a prospect must see: current price, the monthly option,
    the guarantee, the sender, the postal address and an unsubscribe link on
    PUBLIC_BASE_URL. Catches stale copy without sending anything."""
    from agents import sales_agent
    from utils import compliance

    sym = config.CURRENCY_SYMBOL
    try:
        text = compliance.append_footer(
            sales_agent._plain_text_body("Sample Plumbing", "https://sample-preview.vercel.app", "Maidstone, Kent"),
            lead_id=0,
        )
    except RuntimeError as exc:
        return False, str(exc)

    expected = {
        f"price {sym}{config.WEBSITE_OFFER_PRICE:,} one-off": f"{sym}{config.WEBSITE_OFFER_PRICE:,} one-off",
        f"{config.GUARANTEE_DAYS}-day guarantee": f"{config.GUARANTEE_DAYS}-day money-back",
        "sender name": config.SENDER_NAME,
        "postal address": config.PHYSICAL_ADDRESS.strip(),
        "unsubscribe link on PUBLIC_BASE_URL": f"Unsubscribe: {config.PUBLIC_BASE_URL}/unsubscribe/",
    }
    if config.SUBSCRIPTION_ENABLED:
        expected[f"monthly option {sym}{config.SUBSCRIPTION_MONTHLY_PRICE}/month"] = (
            f"{sym}{config.SUBSCRIPTION_MONTHLY_PRICE}/month"
        )
    missing = [label for label, needle in expected.items() if needle not in text]
    if missing:
        return False, "cold email is missing: " + ", ".join(missing)
    return True, "cold email carries " + ", ".join(expected)


def _generated_test_artifacts() -> list[str]:
    names = []
    for rel in (".pytest-tmp", ".pytest_tmp", ".pytest_tmp_money", "pipeline/leads.test.db", "pipeline/traces.test.json"):
        if (REPO_ROOT / rel).exists():
            names.append(rel)
    return names


def _utf8_stdout() -> None:
    """Windows consoles default to cp1252, which cannot print the email's
    tick marks or some business names; never let that crash a report."""
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


# --- The check list --------------------------------------------------------------

def collect_checks(offline: bool = False) -> list[Check]:
    armed = bool(config.ENABLE_LIVE_SEND)
    checks: list[Check] = []

    # Configuration that must be right in every mode.
    try:
        config.validate()
    except RuntimeError as exc:
        checks.append(Check("required configuration", False, str(exc).splitlines()[0]))
    else:
        checks.append(Check("required configuration", True, "all required env vars are set"))

    address_problem = config.physical_address_problem()
    checks.append(Check(
        "physical mailing address",
        address_problem is None,
        "looks filled" if address_problem is None else f"unusable: {address_problem}",
    ))

    user_domain = (config.EMAIL_USER.rsplit("@", 1)[-1] if "@" in config.EMAIL_USER else "").lower()
    sending_domain = config.SENDING_DOMAIN.strip().lower()
    identity_ok = bool(user_domain) and user_domain == sending_domain
    checks.append(Check(
        "sending identity",
        identity_ok,
        f"EMAIL_USER domain {user_domain or '?'} vs SENDING_DOMAIN {sending_domain or '?'}"
        + ("" if identity_ok else " -- they must match for SPF/DKIM to line up"),
    ))

    # The switches, reported as state rather than judged.
    checks.append(Check(
        "live email send gate",
        True,
        "ARMED: ENABLE_LIVE_SEND=true, the next `python run.py loop` cycle emails real leads"
        if armed else "dry run: ENABLE_LIVE_SEND=false, sends are logged to pipeline/dry_run.log",
        level=INFO,
    ))
    if config.SOURCING_ENABLED and armed:
        checks.append(Check(
            "lead sourcing gate", False,
            "SOURCING_ENABLED=true while armed: the loop sources AND emails with nobody reviewing the batch",
            level=WARN,
        ))
    else:
        checks.append(Check(
            "lead sourcing gate", True,
            "SOURCING_ENABLED=true (hourly Google Places sourcing)" if config.SOURCING_ENABLED
            else "SOURCING_ENABLED=false; add leads with `python run.py source --limit N` or by hand",
            level=INFO,
        ))
    checks.append(Check(
        "sourcing website rule", True,
        f"SOURCING_REQUIRE_WEBSITE={config.SOURCING_REQUIRE_WEBSITE}"
        + (" (only businesses with a site, the safe first batch)" if config.SOURCING_REQUIRE_WEBSITE
           else " (no-site/platform-only leads allowed; they need phone/platform follow-up)"),
        level=INFO,
    ))

    # The public URL: configured, and actually answering.
    url_is_local = _is_local_url(config.PUBLIC_BASE_URL)
    checks.append(Check(
        "PUBLIC_BASE_URL",
        not url_is_local,
        f"{config.PUBLIC_BASE_URL!r}" + (" still points at localhost or a placeholder" if url_is_local else ""),
    ))
    if offline:
        checks.append(Check("public URL reachable", True, "skipped (--offline)", level=INFO))
    elif url_is_local:
        checks.append(Check("public URL reachable", True, "not probed: URL is local", level=INFO))
    else:
        reachable, detail = _probe_public_url(config.PUBLIC_BASE_URL)
        checks.append(Check("public URL reachable", reachable, detail, level=_escalate(armed)))

    # Money.
    stripe_mode = _stripe_mode()
    if stripe_mode == "live":
        checks.append(Check("Stripe key mode", True, "live: a YES reply creates a real, payable checkout"))
    elif stripe_mode == "test":
        checks.append(Check(
            "Stripe key mode", False,
            "test keys: prospects would receive test-mode checkout links that cannot take money",
            level=_escalate(armed),
        ))
    else:
        checks.append(Check("Stripe key mode", False, f"STRIPE_SECRET_KEY is {stripe_mode}"))
    secret_ok = config.STRIPE_WEBHOOK_SECRET.startswith("whsec_")
    checks.append(Check(
        "Stripe webhook secret", secret_ok,
        "set (whsec_...)" if secret_ok else "missing or not a whsec_ value; the webhook cannot verify events",
    ))

    # Mail.
    if offline:
        checks.append(Check("mailbox login", True, "skipped (--offline)", level=INFO))
    else:
        results = [(account.user, _mailbox_problem(account)) for account in config.EMAIL_ACCOUNTS]
        failed = [f"{user}: {problem}" for user, problem in results if problem]
        checks.append(Check(
            "mailbox login",
            not failed,
            f"SMTP+IMAP login OK for {', '.join(user for user, _ in results)}" if not failed else "; ".join(failed),
            level=_escalate(armed),
        ))

    caps_ok = (
        config.EMAIL_MAX_PER_DAY <= WEEK_ONE_DAILY_CAP
        and config.EMAIL_MAX_PER_DAY_PER_ACCOUNT <= WEEK_ONE_DAILY_CAP
    )
    checks.append(Check(
        "daily email caps", caps_ok,
        f"global={config.EMAIL_MAX_PER_DAY}, per-mailbox={config.EMAIL_MAX_PER_DAY_PER_ACCOUNT}"
        + ("" if caps_ok else f"; WARMUP.md week one is 5-{WEEK_ONE_DAILY_CAP}/day, raise only after that"),
        level=WARN,
    ))

    content_ok, content_detail = _email_content_state()
    checks.append(Check("cold email content", content_ok, content_detail))

    # Data.
    lead_count = _scalar(config.DB_PATH, "SELECT COUNT(*) FROM leads")
    checks.append(Check(
        "database",
        lead_count is not None,
        f"{config.DB_PATH} not found or unreadable (created on first run)" if lead_count is None
        else f"{lead_count} lead(s) in {Path(config.DB_PATH).name}",
        level=WARN if lead_count is None else INFO,
    ))
    if lead_count is not None:
        test_leads = _test_leads()
        n_test = None if test_leads is None else len(test_leads)
        checks.append(Check(
            "test leads in active DB",
            not n_test,
            "none detected" if not n_test
            else f"{n_test} throwaway lead(s) would be built/emailed/reported as real; "
                 "run `python run.py cleanup-tests --dry-run`, or `--reset` for a pre-launch database",
            level=_escalate(armed),
        ))
        designed = _scalar(config.DB_PATH, "SELECT COUNT(*) FROM leads WHERE status = 'designed'") or 0
        pending = _scalar(config.DB_PATH, "SELECT COUNT(*) FROM leads WHERE status IN ('new', 'researched')") or 0
        checks.append(Check(
            "send queue", True,
            f"{designed} designed (next to be emailed), {pending} new/researched (previews still to build)",
            level=INFO,
        ))
        unsubscribed = _scalar(config.DB_PATH, "SELECT COUNT(*) FROM unsubscribes") or 0
        checks.append(Check("suppression list", True, f"{unsubscribed} unsubscribed address(es) kept", level=INFO))

    renders_ok, renders_detail = _renders_state()
    checks.append(Check("render previews", renders_ok, renders_detail, level=WARN))

    # Gitignored leftovers from pytest runs. Informational: the repo-local
    # temp dirs are *supposed* to exist after a test run, so a permanent
    # warning here would only teach people to ignore warnings.
    artifacts = _generated_test_artifacts()
    checks.append(Check(
        "generated test artifacts", True,
        "none found" if not artifacts else ", ".join(artifacts) + " (gitignored; safe to delete)",
        level=INFO,
    ))
    return checks


def main(argv: Optional[list[str]] = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description="Go-live readiness check. Changes nothing.")
    parser.add_argument("--offline", action="store_true", help="skip the public-URL probe and mailbox logins")
    args = parser.parse_args(argv)

    checks = collect_checks(offline=args.offline)
    blockers = [c for c in checks if c.level == BLOCKER and not c.ok]
    warnings = [c for c in checks if c.level == WARN and not c.ok]
    armed = bool(config.ENABLE_LIVE_SEND)

    print("Website Designers preflight")
    print("===========================")
    for check in checks:
        print(f"[{check.marker:7}] {check.name}: {check.detail}")
    print()
    if blockers:
        print(f"NO-GO: {len(blockers)} blocker(s) -- " + "; ".join(c.name for c in blockers) + ".")
        return 1
    if armed:
        print(
            f"GO (ARMED): no blockers and ENABLE_LIVE_SEND=true -- the next `python run.py loop` cycle "
            f"sends real email. {len(warnings)} warning(s) to review."
        )
        return 0
    print(
        f"READY (dry run): no blockers. Sends stay logged to pipeline/dry_run.log until "
        f"ENABLE_LIVE_SEND=true; review {len(warnings)} warning(s) first."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
