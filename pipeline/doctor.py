"""Local health check for the sales pipeline.

Run from the repo root:

    python run.py doctor
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import urlparse

import config
from agents import design_agent
from utils import db


def _ok(label: str, detail: str = "") -> tuple[bool, str, str]:
    return True, label, detail


def _warn(label: str, detail: str = "") -> tuple[bool, str, str]:
    return False, label, detail


def _ascii_detail(value: str) -> str:
    return value.encode("ascii", errors="replace").decode("ascii")


def _check_env() -> list[tuple[bool, str, str]]:
    checks: list[tuple[bool, str, str]] = []
    missing = [name for name in config.REQUIRED_VARS if not os.getenv(name)]
    if missing:
        checks.append(_warn("Required env", "Missing: " + ", ".join(missing)))
    else:
        checks.append(_ok("Required env", "All required values are set"))

    public_url = urlparse(config.PUBLIC_BASE_URL)
    if public_url.scheme == "https" and public_url.netloc:
        checks.append(_ok("PUBLIC_BASE_URL", config.PUBLIC_BASE_URL))
    else:
        checks.append(_warn("PUBLIC_BASE_URL", f"{config.PUBLIC_BASE_URL} should be public HTTPS before live outreach"))

    if config.ENABLE_LIVE_SEND:
        checks.append(_warn("Email mode", "ENABLE_LIVE_SEND=true; real emails can be sent"))
    else:
        checks.append(_ok("Email mode", "Dry run; set ENABLE_LIVE_SEND=true only when ready"))

    for name in ("SENDING_DOMAIN", "PHYSICAL_ADDRESS", "ADMIN_EMAIL"):
        value = getattr(config, name, "")
        checks.append(_ok(name, "set") if value else _warn(name, "missing"))

    return checks


def _check_files() -> list[tuple[bool, str, str]]:
    checks: list[tuple[bool, str, str]] = []
    template_niches = design_agent.available_niches()
    if design_agent.STUDIO_TEMPLATE in template_niches:
        checks.append(_ok("Core template", f"templates/{design_agent.STUDIO_TEMPLATE} is available"))
    else:
        checks.append(_warn("Core template", f"templates/{design_agent.STUDIO_TEMPLATE} is missing"))
    checks.append(_ok("Template count", str(len(template_niches))))

    if config.ENABLE_AI_IMAGES and config.HF_API_TOKEN:
        checks.append(_ok("AI imagery", f"enabled; {config.HF_IMAGE_MODEL}"))
    elif config.ENABLE_AI_IMAGES:
        checks.append(_warn("AI imagery", "enabled but HF_API_TOKEN is missing; designed fallback will be used"))
    else:
        checks.append(_ok("AI imagery", "disabled; designed fallback will be used"))

    if importlib.util.find_spec("pytest"):
        checks.append(_ok("pytest", "installed"))
    else:
        checks.append(_warn("pytest", "not installed; run pip install -r requirements.txt"))

    db_path = Path(config.DB_PATH)
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db.init_db()
        checks.append(_ok("Database", str(db_path)))
    except Exception as exc:  # noqa: BLE001 - doctor reports setup failures instead of hiding them
        checks.append(_warn("Database", str(exc)))

    return checks


def run() -> int:
    checks = [*_check_env(), *_check_files()]
    width = max(len(label) for _, label, _ in checks)
    failures = 0
    print("Pipeline doctor")
    print("=" * 60)
    for passed, label, detail in checks:
        marker = "OK" if passed else "WARN"
        if not passed:
            failures += 1
        print(f"{marker:4s} {label:{width}s} {_ascii_detail(detail)}")
    print("=" * 60)
    print(f"{len(checks) - failures} OK, {failures} warning(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
