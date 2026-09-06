"""Launch-readiness tools for the cold-email sales pipeline.

Run from the repo root through run.py:

    python run.py preflight                     # readiness report; exit 1 on any BLOCK
    python run.py render-previews [--niche X]   # write every template to pipeline/previews/
    python run.py reset-db --yes                # back up + wipe the DB, keeping unsubscribes

Preflight never sends anything, never touches GitHub/Vercel/Stripe, and
never prints a secret. It reads config, opens the SQLite database, and
renders every template offline.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import config
from agents import design_agent
from utils import db

PIPELINE_DIR = Path(__file__).resolve().parent
PREVIEWS_DIR = PIPELINE_DIR / "previews"

OK = "OK"
WARN = "WARN"
BLOCK = "BLOCK"

# Names from test_lead.csv / test_email.py. Any of these still in the
# database at launch means the test data was never cleaned out.
KNOWN_TEST_BUSINESSES = {
    "green valley landscaping",
    "sunrise cafe",
    "rapid flow plumbing",
    "test business",
    "acme cafe",
}
TEST_EMAIL_MARKERS = ("@example.com", "@example.org", "@example.net", "@test.")

# Gates this codebase version does not read itself but an operator may
# carry in .env from other tooling. Reported verbatim so the report shows
# the whole gate picture in one place; never interpreted.
OTHER_GATES = ("SOURCING_ENABLED", "SOURCING_REQUIRE_WEBSITE", "EMAIL_MAX_PER_DAY_PER_ACCOUNT")

SAMPLE_LEAD = {
    "id": 0,
    "business_name": "Sample Business",
    "niche": "default",
    "location": "Springfield",
    "phone": "(555) 010-0100",
    "pain_point": "",
    "testimonial": "",
    "scraped_info": "",
}
_OFFLINE_HERO = "https://picsum.photos/seed/preview/1600/900"


def _check(level: str, label: str, detail: str = "") -> tuple[str, str, str]:
    return level, label, detail


# --- Checks -------------------------------------------------------------------

def check_env() -> list[tuple[str, str, str]]:
    checks = []
    missing = [name for name in config.REQUIRED_VARS if not os.getenv(name)]
    if missing:
        checks.append(_check(BLOCK, "Required env", "missing: " + ", ".join(missing)))
    else:
        checks.append(_check(OK, "Required env", f"all {len(config.REQUIRED_VARS)} required values set"))

    url = urlparse(config.PUBLIC_BASE_URL)
    host = (url.hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "0.0.0.0", "") or host.endswith(".local")
    if url.scheme == "https" and not local:
        detail = config.PUBLIC_BASE_URL
        if "ngrok" in host:
            detail += " (ngrok: unsubscribe/click links in sent emails stop working when the tunnel URL changes)"
            checks.append(_check(WARN if config.ENABLE_LIVE_SEND else OK, "PUBLIC_BASE_URL", detail))
        else:
            checks.append(_check(OK, "PUBLIC_BASE_URL", detail))
    else:
        level = BLOCK if config.ENABLE_LIVE_SEND else WARN
        checks.append(_check(level, "PUBLIC_BASE_URL", f"{config.PUBLIC_BASE_URL} must be public HTTPS before live send"))

    if config.SENDGRID_API_KEY:
        detail = "SendGrid" + ("" if config.SENDGRID_FROM_EMAIL else " (SENDGRID_FROM_EMAIL missing)")
        checks.append(_check(OK if config.SENDGRID_FROM_EMAIL else BLOCK, "Transport", detail))
    else:
        checks.append(_check(OK, "Transport", f"SMTP {config.EMAIL_HOST or '?'}:{config.EMAIL_PORT}"))

    key = config.STRIPE_SECRET_KEY
    if key.startswith("sk_live_"):
        checks.append(_check(OK, "Stripe key", "live key"))
    elif key.startswith("sk_test_"):
        checks.append(_check(WARN if config.ENABLE_LIVE_SEND else OK, "Stripe key", "TEST key; real payments will fail"))
    elif key:
        checks.append(_check(WARN, "Stripe key", "unrecognised prefix"))

    if os.getenv("SECRET_KEY", "").strip():
        checks.append(_check(OK, "SECRET_KEY", "set in .env"))
    else:
        checks.append(_check(OK, "SECRET_KEY", "generated and persisted in pipeline/.secret_key (back it up; links die without it)"))
    return checks


def check_send_gates() -> list[tuple[str, str, str]]:
    checks = []
    if config.ENABLE_LIVE_SEND:
        checks.append(_check(WARN, "ENABLE_LIVE_SEND", "TRUE -- real email will be sent"))
    else:
        checks.append(_check(OK, "ENABLE_LIVE_SEND", f"false -- emails are written to {config.DRY_RUN_DIR}"))

    caps = f"{config.EMAIL_MAX_PER_DAY}/day, {config.EMAIL_MAX_PER_HOUR}/hour"
    if config.EMAIL_MAX_PER_DAY > config.FIRST_WEEK_MAX_PER_DAY:
        checks.append(_check(WARN, "Email caps", f"{caps}; first week should be <= {config.FIRST_WEEK_MAX_PER_DAY}/day (set EMAIL_MAX_PER_DAY in .env)"))
    elif config.EMAIL_MAX_PER_DAY < 1:
        checks.append(_check(BLOCK, "Email caps", f"{caps}; nothing can ever send"))
    else:
        checks.append(_check(OK, "Email caps", caps))

    for name in OTHER_GATES:
        value = os.getenv(name, "").strip()
        if value:
            checks.append(_check(OK, name, f"{value} (present in .env; not read by this code version)"))
    return checks


def check_database() -> list[tuple[str, str, str]]:
    checks = []
    path = Path(config.DB_PATH)
    if "test" in path.name.lower():
        checks.append(_check(WARN, "DB_PATH", f"{path} looks like a test database"))
    if not path.exists():
        checks.append(_check(OK, "Database", f"{path} does not exist yet; will be created empty"))
        return checks
    try:
        db.init_db()
        counts = db.count_leads_by_status()
        leads = db.list_all_leads()
        unsubs = db.list_unsubscribes()
    except Exception as exc:  # noqa: BLE001 - report, do not crash the report
        checks.append(_check(BLOCK, "Database", f"{path}: {exc}"))
        return checks

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "empty"
    checks.append(_check(OK, "Database", f"{path}: {len(leads)} lead(s) [{summary}], {len(unsubs)} unsubscribe(s)"))

    test_leads = [
        lead for lead in leads
        if (lead.get("business_name") or "").strip().lower() in KNOWN_TEST_BUSINESSES
        or any(marker in (lead.get("contact_email") or "").lower() for marker in TEST_EMAIL_MARKERS)
    ]
    if test_leads:
        names = ", ".join(sorted({lead["business_name"] for lead in test_leads})[:5])
        checks.append(_check(WARN, "Test data", f"{len(test_leads)} test lead(s) present ({names}); run: python run.py reset-db --yes"))

    dry = db.list_dry_run_emailed_lead_ids()
    if dry:
        checks.append(_check(WARN, "Dry-run leads", f"{len(dry)} lead(s) marked 'emailed' by dry runs only; reset them before live send"))
    return checks


def _sample_context(niche: str) -> dict:
    lead = dict(SAMPLE_LEAD, niche=niche, business_name=f"Sample {design_agent.niche_display_name(niche)}")
    return design_agent.build_context(lead, hero_image_url=_OFFLINE_HERO)


def check_templates() -> list[tuple[str, str, str]]:
    checks = []
    niches = sorted(design_agent.available_niches())
    if design_agent.DEFAULT_NICHE not in niches:
        checks.append(_check(BLOCK, "Templates", f"templates/{design_agent.DEFAULT_NICHE} is missing; unknown niches cannot render"))
    broken = []
    for niche in niches:
        try:
            files = design_agent.render_template_files(niche, _sample_context(niche))
            html = files.get("index.html", "")
            if not html.strip() or "{{" in html or "{%" in html:
                broken.append(f"{niche} (unrendered placeholders)")
        except Exception as exc:  # noqa: BLE001
            broken.append(f"{niche} ({exc})")
    if broken:
        checks.append(_check(BLOCK, "Templates", "failed: " + "; ".join(broken)))
    else:
        checks.append(_check(OK, "Templates", f"{len(niches)} render cleanly: {', '.join(niches)}"))
    return checks


def check_tooling() -> list[tuple[str, str, str]]:
    checks = []
    checks.append(_check(OK if importlib.util.find_spec("pytest") else WARN, "pytest", "installed" if importlib.util.find_spec("pytest") else "missing; pip install -r requirements.txt"))
    if importlib.util.find_spec("playwright"):
        checks.append(_check(OK, "playwright", "installed (screenshots embed in emails)"))
    else:
        checks.append(_check(WARN, "playwright", "missing; emails go out without the inline screenshot"))
    return checks


def run_preflight() -> int:
    checks = [*check_env(), *check_send_gates(), *check_database(), *check_templates(), *check_tooling()]
    width = max(len(label) for _, label, _ in checks)
    print("Launch preflight")
    print("=" * 72)
    for level, label, detail in checks:
        print(f"{level:5s} {label:{width}s} {detail}".encode("ascii", "replace").decode("ascii"))
    print("=" * 72)
    blocks = sum(1 for level, _, _ in checks if level == BLOCK)
    warns = sum(1 for level, _, _ in checks if level == WARN)
    mode = "LIVE SEND" if config.ENABLE_LIVE_SEND else "DRY RUN"
    print(f"{blocks} blocking, {warns} warning(s). Mode: {mode}.")
    if blocks:
        print("NOT READY: fix every BLOCK line above.")
        return 1
    if config.ENABLE_LIVE_SEND:
        print("Live send is ON. Every cold email will really go out; watch pipeline/dry_run/ history first.")
    else:
        print("Ready for a dry run. Approve files in pipeline/dry_run/ before setting ENABLE_LIVE_SEND=true.")
    return 0


# --- render-previews ------------------------------------------------------------

def render_previews(niche: str | None = None, out_dir: Path | None = None) -> int:
    out_dir = out_dir or PREVIEWS_DIR
    niches = sorted(design_agent.available_niches())
    if niche:
        if niche not in niches:
            print(f"No template folder for niche {niche!r}. Available: {', '.join(niches)}")
            return 1
        niches = [niche]
    if not niches:
        print("No templates found.")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    links = []
    for name in niches:
        target = out_dir / name
        target.mkdir(parents=True, exist_ok=True)
        files = design_agent.render_template_files(name, _sample_context(name))
        for filename, content in files.items():
            (target / filename).write_text(content, encoding="utf-8")
        links.append(f'<li><a href="{name}/index.html">{name}</a></li>')
        print(f"[previews] {name:12s} -> {target / 'index.html'}")

    index = out_dir / "index.html"
    index.write_text(
        "<!doctype html><meta charset='utf-8'><title>Template previews</title>"
        "<h1>Template previews</h1><p>Rendered offline with sample data. Open each and approve visually.</p>"
        f"<ul>{''.join(links)}</ul>",
        encoding="utf-8",
    )
    print(f"[previews] Open {index} in a browser (templates load Tailwind from a CDN, so you need internet).")
    return 0


# --- reset-db --------------------------------------------------------------------

def reset_db(yes: bool = False, drop_unsubscribes: bool = False) -> int:
    path = Path(config.DB_PATH)
    if not path.exists():
        db.init_db()
        print(f"[reset-db] {path} did not exist; created empty.")
        return 0
    if not yes:
        print(f"[reset-db] Refusing to wipe {path} without --yes. A timestamped backup is made first; the unsubscribe list is kept unless --drop-unsubscribes.")
        return 1

    db.init_db()
    kept = [] if drop_unsubscribes else db.list_unsubscribes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    db.init_db()
    db.restore_unsubscribes(kept)
    print(f"[reset-db] Backup: {backup}")
    print(f"[reset-db] Fresh database at {path}; {len(kept)} unsubscribe(s) carried over.")
    return 0


# --- CLI -----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="launch-readiness report")
    p_render = sub.add_parser("render-previews", help="render every template offline")
    p_render.add_argument("--niche", help="render only this template folder")
    p_render.add_argument("--out", help=f"output directory (default {PREVIEWS_DIR})")
    p_reset = sub.add_parser("reset-db", help="back up and wipe the database")
    p_reset.add_argument("--yes", action="store_true", help="actually do it")
    p_reset.add_argument("--drop-unsubscribes", action="store_true", help="do NOT carry the unsubscribe list over")
    args = parser.parse_args(argv)

    if args.command == "preflight":
        return run_preflight()
    if args.command == "render-previews":
        return render_previews(args.niche, Path(args.out) if args.out else None)
    if args.command == "reset-db":
        return reset_db(yes=args.yes, drop_unsubscribes=args.drop_unsubscribes)
    return 2


if __name__ == "__main__":
    sys.exit(main())
