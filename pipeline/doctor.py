"""Local health check for the sales pipeline.

Run from the repo root with: python pipeline/doctor.py
It avoids paid/external API calls and catches setup problems before a live run.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402


def _line(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def main() -> int:
    failures = 0
    warnings = 0

    try:
        config.validate()
        _line("OK", "required .env values are present")
    except RuntimeError as exc:
        failures += 1
        _line("FAIL", str(exc).replace("\n", " "))

    if config.PUBLIC_BASE_URL.startswith("http://localhost"):
        warnings += 1
        _line("WARN", "PUBLIC_BASE_URL is localhost; real unsubscribe/click/Stripe links need public HTTPS")
    else:
        _line("OK", "PUBLIC_BASE_URL is not localhost")

    if not (REPO_ROOT / ".env").exists():
        warnings += 1
        _line("WARN", ".env file not found at repo root")
    else:
        _line("OK", ".env file exists")

    for package in ("pytest", "streamlit", "requests", "sendgrid", "playwright"):
        if importlib.util.find_spec(package) is None:
            warnings += 1
            _line("WARN", f"Python package not importable: {package}")
        else:
            _line("OK", f"Python package importable: {package}")

    if config.SENDGRID_API_KEY and not config.SENDGRID_FROM_EMAIL:
        failures += 1
        _line("FAIL", "SENDGRID_API_KEY is set but SENDGRID_FROM_EMAIL is empty")

    print(f"\nDoctor complete: {failures} failure(s), {warnings} warning(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
