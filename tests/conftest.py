"""Shared pytest configuration.

Only tests/test_pipeline_real.py exists today, and it's gated behind
--run-real (see pytest_addoption below) since it hits real GitHub, Vercel,
SMTP, and IMAP APIs and must never run in a plain `pytest` invocation or
in CI without real, test-safe credentials.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = REPO_ROOT / "pipeline"
ENV_TEST_PATH = REPO_ROOT / ".env.test"
ENV_PATH = REPO_ROOT / ".env"

# pipeline/*.py use flat, non-package imports (import config, from utils
# import db) that assume pipeline/ is on sys.path -- same shim dashboard.py
# uses to reuse those modules from outside the pipeline/ directory.
sys.path.insert(0, str(PIPELINE_DIR))

# Load .env.test (or, failing that, .env) before any pipeline module is
# imported anywhere in the test session: pipeline/config.py reads
# os.environ into module-level constants at import time, so this has to
# happen at conftest collection time, not inside a fixture. override=True
# so whichever file we load always wins over an already-set shell env var.
if ENV_TEST_PATH.exists():
    load_dotenv(dotenv_path=ENV_TEST_PATH, override=True)
elif ENV_PATH.exists():
    # Fallback: run against real .env credentials rather than skipping
    # outright. DB_PATH/TRACES_PATH are forced to throwaway files
    # regardless of what .env says, though -- .env's own DB_PATH is
    # normally blank (meaning "use the real pipeline/leads.db"), and
    # trusting that here would let a test run write into your real,
    # production leads.db/traces.json. Copy .env.test.example to
    # .env.test if you want to control these paths (or use different
    # credentials) explicitly instead of relying on this fallback.
    print(
        "[conftest] .env.test not found; falling back to .env for credentials. "
        "Forcing DB_PATH/TRACES_PATH to throwaway pipeline/leads.test.db and "
        "pipeline/traces.test.json so this run can't touch production data."
    )
    load_dotenv(dotenv_path=ENV_PATH, override=True)
    os.environ["DB_PATH"] = str(PIPELINE_DIR / "leads.test.db")
    os.environ["TRACES_PATH"] = str(PIPELINE_DIR / "traces.test.json")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-real",
        action="store_true",
        default=False,
        help=(
            "Run tests marked 'real_integration', which hit real "
            "GitHub/Vercel/SMTP/IMAP APIs using credentials from .env.test "
            "(copy .env.test.example first). Skipped by default."
        ),
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_integration: opt-in test that hits real external services (needs --run-real)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-real"):
        return
    skip_real = pytest.mark.skip(
        reason="needs --run-real (hits real external APIs -- see tests/test_pipeline_real.py)"
    )
    for item in items:
        if "real_integration" in item.keywords:
            item.add_marker(skip_real)
