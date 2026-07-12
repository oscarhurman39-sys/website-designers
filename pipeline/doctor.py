"""Pre-flight self-test: `python run.py doctor` (see repo-root run.py).

Checks every external dependency and local prerequisite the pipeline needs,
printing PASS/FAIL/SKIP for each rather than letting a misconfiguration
surface as a confusing failure hours into a real run. Every check is
independently wrapped so one failing/unreachable service (no network, a
revoked token, etc.) never stops the rest from running -- you get a full
report in one pass.

Run with:

    python run.py doctor              # from the repo root
    python doctor.py                  # from inside pipeline/
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import config

PIPELINE_DIR = Path(__file__).resolve().parent

_PASS = "PASS"
_FAIL = "FAIL"
_SKIP = "SKIP"


class _Result:
    def __init__(self, name: str, status: str, detail: str = ""):
        self.name = name
        self.status = status
        self.detail = detail


def _check_env_vars() -> _Result:
    import os

    missing = [name for name in config.REQUIRED_VARS if not os.getenv(name)]
    if missing:
        return _Result(".env required variables", _FAIL, f"missing: {', '.join(missing)}")
    if len(config.SECRET_KEY) < 32:
        return _Result(".env required variables", _FAIL, f"SECRET_KEY is only {len(config.SECRET_KEY)} chars (need 32+)")
    problem = config.physical_address_problem()
    if problem:
        return _Result(".env required variables", _FAIL, f"PHYSICAL_ADDRESS is {problem}")
    return _Result(".env required variables", _PASS)


def _check_public_base_url() -> _Result:
    url = config.PUBLIC_BASE_URL
    if url == "http://localhost:5000":
        return _Result("PUBLIC_BASE_URL", _FAIL, "still the localhost default -- links in emails would be unreachable")
    if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
        return _Result("PUBLIC_BASE_URL", _FAIL, f"{url} is not HTTPS -- Stripe requires HTTPS webhooks")
    return _Result("PUBLIC_BASE_URL", _PASS, url)


def _check_folders() -> _Result:
    try:
        config._validate_filesystem()
    except RuntimeError as exc:
        return _Result("Required folders", _FAIL, str(exc))
    return _Result("Required folders", _PASS, ", ".join(config._REQUIRED_DIRS))


def _check_database() -> _Result:
    try:
        from utils import db

        db.init_db()
        import sqlite3

        conn = sqlite3.connect(config.DB_PATH)
        try:
            result = conn.execute("PRAGMA integrity_check;").fetchone()[0]
        finally:
            conn.close()
        if result != "ok":
            return _Result("Database", _FAIL, f"integrity_check: {result}")
    except Exception as exc:  # noqa: BLE001
        return _Result("Database", _FAIL, str(exc))
    return _Result("Database", _PASS, config.DB_PATH)


def _check_github() -> _Result:
    if not config.GITHUB_TOKEN:
        return _Result("GitHub", _FAIL, "GITHUB_TOKEN not set")
    try:
        from utils import github_api

        username = github_api.get_authenticated_username()
    except Exception as exc:  # noqa: BLE001
        return _Result("GitHub", _FAIL, str(exc))
    return _Result("GitHub", _PASS, f"authenticated as {username}")


def _check_vercel() -> _Result:
    if not config.VERCEL_TOKEN:
        return _Result("Vercel", _FAIL, "VERCEL_TOKEN not set")
    try:
        import requests

        from utils import vercel_api

        resp = requests.get(
            f"{vercel_api._API_BASE}/v2/user", headers=vercel_api._headers(), timeout=10
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _Result("Vercel", _FAIL, str(exc))
    return _Result("Vercel", _PASS)


def _check_sendgrid() -> _Result:
    if not config.SENDGRID_API_KEY:
        return _Result("SendGrid", _SKIP, "SENDGRID_API_KEY not set -- SMTP will be used instead")
    try:
        from sendgrid import SendGridAPIClient

        client = SendGridAPIClient(config.SENDGRID_API_KEY)
        resp = client.client.user.account.get()
        if resp.status_code >= 400:
            return _Result("SendGrid", _FAIL, f"HTTP {resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        return _Result("SendGrid", _FAIL, str(exc))
    return _Result("SendGrid", _PASS)


def _check_dns_records() -> _Result:
    """Best-effort SPF/DKIM/DMARC TXT lookup via the system `nslookup`
    (available on Linux/macOS/Windows out of the box, avoiding a new
    dnspython dependency just for this one optional check)."""
    domain = config.SENDING_DOMAIN.strip()
    if not domain:
        return _Result("DKIM/DMARC DNS", _SKIP, "SENDING_DOMAIN not set")
    if not shutil.which("nslookup"):
        return _Result("DKIM/DMARC DNS", _SKIP, "nslookup not available on this host -- check DNS manually")
    try:
        spf = subprocess.run(
            ["nslookup", "-type=TXT", domain], capture_output=True, text=True, timeout=10
        ).stdout
        dmarc = subprocess.run(
            ["nslookup", "-type=TXT", f"_dmarc.{domain}"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return _Result("DKIM/DMARC DNS", _SKIP, f"lookup failed: {exc}")
    has_spf = "v=spf1" in spf.lower()
    has_dmarc = "v=dmarc1" in dmarc.lower()
    if has_spf and has_dmarc:
        return _Result("DKIM/DMARC DNS", _PASS, f"SPF and DMARC TXT records found for {domain}")
    missing = [n for n, found in (("SPF", has_spf), ("DMARC", has_dmarc)) if not found]
    return _Result(
        "DKIM/DMARC DNS", _FAIL,
        f"missing {'/'.join(missing)} record(s) for {domain} -- DKIM is provider-side and not checkable via DNS alone; "
        "see WARMUP.md",
    )


def _check_hf() -> _Result:
    if not config.HF_API_TOKEN:
        return _Result("Hugging Face (drafting)", _FAIL, "HF_API_TOKEN not set")
    return _Result("Hugging Face (drafting)", _PASS, "token is set (not making a live call)")


def _check_anthropic_fallback() -> _Result:
    if not config.ANTHROPIC_API_KEY:
        return _Result("Anthropic (fallback drafting)", _SKIP, "ANTHROPIC_API_KEY not set -- optional")
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return _Result("Anthropic (fallback drafting)", _FAIL, "ANTHROPIC_API_KEY is set but the anthropic package isn't installed")
    return _Result("Anthropic (fallback drafting)", _PASS, "token is set (not making a live call)")


def _check_stripe() -> _Result:
    if not config.STRIPE_SECRET_KEY:
        return _Result("Stripe", _FAIL, "STRIPE_SECRET_KEY not set")
    try:
        import stripe

        stripe.api_key = config.STRIPE_SECRET_KEY
        stripe.Account.retrieve()
    except Exception as exc:  # noqa: BLE001
        return _Result("Stripe", _FAIL, str(exc))
    return _Result("Stripe", _PASS)


def _check_screenshot_pipeline() -> _Result:
    try:
        from utils import screenshot

        if screenshot._PINNED_CHROMIUM_PATH.exists():
            return _Result("Screenshot pipeline", _PASS, f"pinned Chromium at {screenshot._PINNED_CHROMIUM_PATH}")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # noqa: BLE001
        return _Result(
            "Screenshot pipeline", _FAIL,
            f"{exc} -- run `playwright install chromium`",
        )
    return _Result("Screenshot pipeline", _PASS, "Chromium launches successfully")


def _check_webhook_config() -> _Result:
    problems = []
    if not config.STRIPE_WEBHOOK_SECRET:
        problems.append("STRIPE_WEBHOOK_SECRET not set")
    if config.SENDGRID_API_KEY and not config.SENDGRID_WEBHOOK_VERIFICATION_KEY:
        problems.append("SENDGRID_API_KEY is set but SENDGRID_WEBHOOK_VERIFICATION_KEY isn't -- bounce/unsubscribe events won't be verified")
    if problems:
        return _Result("Webhook configuration", _FAIL, "; ".join(problems))
    return _Result("Webhook configuration", _PASS)


_ALL_CHECKS = [
    _check_env_vars,
    _check_public_base_url,
    _check_folders,
    _check_database,
    _check_github,
    _check_vercel,
    _check_sendgrid,
    _check_dns_records,
    _check_hf,
    _check_anthropic_fallback,
    _check_stripe,
    _check_screenshot_pipeline,
    _check_webhook_config,
]


def run_all() -> bool:
    """Run every check, print a PASS/FAIL/SKIP report. Returns True if
    nothing FAILed (SKIPs are fine -- they're for optional features)."""
    print(f"\n{'=' * 70}\nCold-Email Pipeline Pre-Flight Check\n{'=' * 70}\n")
    results = []
    for check in _ALL_CHECKS:
        try:
            result = check()
        except Exception as exc:  # noqa: BLE001 - a check itself crashing must not kill the run
            result = _Result(check.__name__, _FAIL, f"check crashed: {exc}")
        results.append(result)
        marker = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[result.status]
        detail = f" -- {result.detail}" if result.detail else ""
        print(f"{marker:8s} {result.name}{detail}")

    failed = [r for r in results if r.status == _FAIL]
    print(f"\n{'=' * 70}")
    if failed:
        print(f"{len(failed)} check(s) FAILED. Fix these before running the pipeline for real.")
    else:
        print("All checks passed (or were skipped as optional).")
    print(f"{'=' * 70}\n")
    return not failed


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
