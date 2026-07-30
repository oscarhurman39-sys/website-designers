"""End-to-end integration test against REAL external services.

Skipped by default (see tests/conftest.py). Run explicitly with:

    pytest --run-real tests/test_pipeline_real.py -v -s

Requires a `.env.test` at the repo root -- copy `.env.test.example` and
fill in real, test-safe credentials (GITHUB_TOKEN, VERCEL_TOKEN,
EMAIL_HOST/PORT/USER/PASSWORD, HF_API_TOKEN, etc.). conftest.py loads
`.env.test` before any pipeline module is imported.

What this test actually does, in order:
  1. Creates one real, throwaway lead in a disposable test DB.
  2. Runs LeadAgent -- or, if MOCK_SCRAPING=true (the default in
     .env.test.example), skips real scraping and enriches the lead with
     fixed fake data instead, so the rest of the test doesn't depend on a
     real business website staying online and scrapeable.
  3. Runs DesignAgent for real: creates a private GitHub repo, pushes the
     rendered template, and deploys it to a real Vercel preview.
  4. Runs SalesAgent for real: sends a cold email to EMAIL_USER itself
     (not ADMIN_EMAIL, which this pipeline typically has no IMAP access
     to), then polls that same mailbox over IMAP to confirm it arrived.
  5. Confirms a trace was recorded for each agent step in traces.json.
  6. Deletes the GitHub repo and Vercel project it created, regardless of
     whether the test passed or failed.

This test does NOT modify any pipeline agent/util code -- it only calls
the existing public functions the same way main.py does.
"""
from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.real_integration

REQUIRED_ENV_VARS = (
    "GITHUB_TOKEN",
    "VERCEL_TOKEN",
    "EMAIL_HOST",
    "EMAIL_PORT",
    "EMAIL_USER",
    "EMAIL_PASSWORD",
    "HF_API_TOKEN",
)


def _missing_env_vars() -> list[str]:
    return [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]


@pytest.fixture(scope="module")
def real_env():
    """Imports pipeline modules and returns them by name. Deferred until
    here (rather than at module import time) so a plain `pytest` run --
    which skips this whole module via conftest's marker-based skip -- never
    has to successfully import pipeline modules with test env vars unset."""
    missing = _missing_env_vars()
    if missing:
        pytest.skip(
            f"--run-real was passed but .env.test is missing: {', '.join(missing)}. "
            "Copy .env.test.example to .env.test and fill in real, test-safe credentials."
        )

    import config
    from agents import design_agent, lead_agent, sales_agent
    from utils import db, email_utils, github_api, tracer, vercel_api

    db.init_db()
    return {
        "config": config,
        "design_agent": design_agent,
        "lead_agent": lead_agent,
        "sales_agent": sales_agent,
        "db": db,
        "email_utils": email_utils,
        "github_api": github_api,
        "tracer": tracer,
        "vercel_api": vercel_api,
    }


@pytest.fixture(scope="module")
def test_lead(real_env):
    db = real_env["db"]
    lead_id = db.insert_lead(
        business_name="Pipeline Integration Test Cafe",
        niche="cafe",
        location="Testville",
        status="new",
    )
    return lead_id


@pytest.fixture(scope="module", autouse=True)
def cleanup_created_resources(real_env):
    """Tracks real external resources created during the test and deletes
    them afterward, whether or not the test passed."""
    created: dict[str, str | None] = {"repo_full_name": None, "vercel_project_raw_name": None}
    yield created

    github_api = real_env["github_api"]
    vercel_api = real_env["vercel_api"]

    if created["repo_full_name"]:
        try:
            github_api.delete_repo(created["repo_full_name"])
            print(f"[cleanup] deleted GitHub repo {created['repo_full_name']}")
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the real test result
            print(f"[cleanup] WARNING: failed to delete GitHub repo {created['repo_full_name']}: {exc}")

    if created["vercel_project_raw_name"]:
        try:
            vercel_api.delete_project(created["vercel_project_raw_name"])
            print(f"[cleanup] deleted Vercel project for {created['vercel_project_raw_name']}")
        except Exception as exc:  # noqa: BLE001
            print(
                f"[cleanup] WARNING: failed to delete Vercel project for "
                f"{created['vercel_project_raw_name']}: {exc}"
            )


def test_lead_research(real_env, test_lead):
    db = real_env["db"]
    lead_agent = real_env["lead_agent"]

    lead = db.get_lead(test_lead)
    assert lead["status"] == "new"

    if os.getenv("MOCK_SCRAPING", "false").lower() == "true":
        db.update_lead_fields(
            test_lead,
            website_url="https://example.com",
            contact_email=real_env["config"].EMAIL_USER,
            pain_point="slow, outdated website",
            testimonial="Best coffee in town!",
        )
        db.update_lead_status(test_lead, "researched", notes="MOCK_SCRAPING=true")
    else:
        lead_agent.research_lead(lead)

    lead = db.get_lead(test_lead)
    assert lead["status"] == "researched", f"expected 'researched', got {lead['status']!r}"
    assert lead["contact_email"], "research step did not produce a contact email"


def test_design_and_deploy(real_env, test_lead, cleanup_created_resources):
    db = real_env["db"]
    design_agent = real_env["design_agent"]
    github_api = real_env["github_api"]

    lead = db.get_lead(test_lead)
    website = design_agent.process_lead(lead)

    assert website is not None, "process_lead returned None -- design/deploy failed"
    assert website["preview_url"].startswith("https://")
    # Previews must be git-less: no repo at design time (the account was
    # once flooded by one repo per preview). The hand-off repo only exists
    # after create_handoff_repo(), exercised below.
    assert not website["repo_full_name"]
    assert website["local_dir"], "rendered files were not saved for the eventual hand-off"

    repo_url, repo_full_name = design_agent.create_handoff_repo(lead["id"])
    assert repo_full_name, "create_handoff_repo did not create/record a repo"

    # Record what was created so the module-scoped cleanup fixture deletes
    # it, whether or not later tests in this module fail. Vercel's project
    # name is recomputed the same way design_agent.py derived it (the raw,
    # pre-sanitized repo name) rather than trusting the `vercel_project_id`
    # DB column, which -- pre-existing behavior this test doesn't change --
    # actually stores a deployment id, not a project name.
    cleanup_created_resources["repo_full_name"] = repo_full_name
    cleanup_created_resources["vercel_project_raw_name"] = github_api.make_repo_name(
        lead["business_name"], lead["id"]
    )

    lead = db.get_lead(test_lead)
    assert lead["status"] == "designed"


def test_send_and_receive_email(real_env, test_lead):
    db = real_env["db"]
    sales_agent = real_env["sales_agent"]
    email_utils = real_env["email_utils"]
    config = real_env["config"]

    # Send to the sending mailbox itself so this same IMAP account can be
    # polled afterward to confirm delivery -- ADMIN_EMAIL is typically a
    # separate inbox this pipeline has no IMAP access to.
    db.update_lead_fields(test_lead, contact_email=config.EMAIL_USER)
    lead = db.get_lead(test_lead)

    sent = sales_agent.send_cold_email(lead)
    assert sent is True

    lead = db.get_lead(test_lead)
    assert lead["status"] == "emailed"

    thread = db.get_email_threads(test_lead)
    outbound = [m for m in thread if m["direction"] == "outbound"]
    assert outbound, "no outbound message logged in email_threads"
    sent_subject = outbound[-1]["subject"]

    deadline = time.monotonic() + 90
    found = False
    while time.monotonic() < deadline and not found:
        for msg in email_utils.fetch_unseen_emails():
            if msg.subject == sent_subject:
                found = True
                break
        if not found:
            time.sleep(5)

    assert found, (
        f"test email with subject {sent_subject!r} did not arrive in "
        f"{config.EMAIL_USER}'s inbox within 90s"
    )


def test_trace_recorded(real_env, test_lead):
    tracer = real_env["tracer"]

    traces = tracer.load_local_traces()
    lead_traces = [t for t in traces if t.get("input", {}).get("lead_id") == test_lead]

    agent_ids_seen = {t["agent_id"] for t in lead_traces}
    assert "design-agent" in agent_ids_seen, "no design-agent trace recorded for this lead"
    assert "sales-agent" in agent_ids_seen, "no sales-agent trace recorded for this lead"
    assert all(t["status"] == "completed" for t in lead_traces), (
        f"expected every trace for lead {test_lead} to be 'completed': {lead_traces}"
    )
