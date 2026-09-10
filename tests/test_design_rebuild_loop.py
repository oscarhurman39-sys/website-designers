"""A lead that already has a preview must never get a second repo and a
second Vercel project. When the send-time URL check fails, the lead drops
back to 'researched'; before this, DesignAgent then built it again from
scratch every 60-second cycle for as long as the URL stayed bad. Failed
builds are also backed off now instead of retried every cycle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import config
from agents import design_agent
from utils import db

PREVIEW_URL = "https://acme-plumbing-preview-1.vercel.app"


@pytest.fixture
def lead(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(design_agent, "_design_next_attempt", {})
    monkeypatch.setattr(design_agent, "_design_failures", {})
    never = Mock(side_effect=AssertionError("a lead with a preview must not be built from scratch"))
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", never)
    monkeypatch.setattr(design_agent.vercel_api, "deploy_files", never)
    db.init_db()
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Maidstone, Kent", status="researched")
    db.update_lead_fields(lead_id, contact_email="owner@acme.test")
    db.insert_website(lead_id, "plumber", "https://github.com/x/acme-plumbing-preview-1",
                      "x/acme-plumbing-preview-1", PREVIEW_URL)
    return db.get_lead(lead_id)


def _page(status_code: int = 200) -> Mock:
    resp = Mock()
    resp.url = PREVIEW_URL
    resp.status_code = status_code
    resp.text = "<html><body>site</body></html>"
    return resp


def _website_rows(lead_id: int) -> int:
    with db.get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM websites WHERE lead_id = ?", (lead_id,)).fetchone()[0]


def test_a_reachable_existing_preview_is_reused_without_a_deploy(lead, monkeypatch):
    monkeypatch.setattr(design_agent.requests, "get", Mock(return_value=_page()))
    monkeypatch.setattr(design_agent, "rebuild_preview", Mock(side_effect=AssertionError("no redeploy needed")))

    website = design_agent.process_lead(lead)

    assert website["repo_full_name"] == "x/acme-plumbing-preview-1"
    assert db.get_lead(lead["id"])["status"] == "designed"
    assert _website_rows(lead["id"]) == 1


def test_an_unreachable_existing_preview_is_redeployed_in_place(lead, monkeypatch):
    monkeypatch.setattr(design_agent.requests, "get", Mock(return_value=_page(status_code=404)))
    rebuilt: list[int] = []
    monkeypatch.setattr(design_agent, "rebuild_preview",
                        lambda l: rebuilt.append(l["id"]) or db.get_website_by_lead(l["id"]))

    design_agent.process_lead(lead)

    assert rebuilt == [lead["id"]]
    assert db.get_lead(lead["id"])["status"] == "designed"
    assert _website_rows(lead["id"]) == 1


def test_a_failed_build_is_backed_off_not_retried_every_cycle(lead, monkeypatch):
    attempts: list[int] = []

    def failing(l):
        attempts.append(l["id"])
        raise RuntimeError("Deployment is not ready: ready_state='BUILDING'")
    monkeypatch.setattr(design_agent, "process_lead", failing)
    alerts: list[str] = []
    from agents import sales_agent
    monkeypatch.setattr(sales_agent, "alert_needs_human", lambda l, reason: alerts.append(reason))

    design_agent.run()
    design_agent.run()
    design_agent.run()

    assert attempts == [lead["id"]]                      # once, then backed off
    assert db.get_lead(lead["id"])["status"] == "researched"
    assert alerts == []                                   # one transient failure pages nobody

    # ...and it is tried again once the delay has passed; a second failure in a row is escalated.
    design_agent._design_next_attempt[lead["id"]] = datetime.now(timezone.utc) - timedelta(seconds=1)
    design_agent.run()
    assert attempts == [lead["id"], lead["id"]]
    assert len(alerts) == 1 and "failed 2 times" in alerts[0]
