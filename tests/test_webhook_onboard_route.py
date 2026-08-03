"""Tests for webhook_server.py's /onboard/<lead_id> route -- the
self-serve claim-your-website form (see agents/onboarding_agent.py).
GitHub/Vercel/email are mocked; DB is a real tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import webhook_server
from utils import db, editor_auth


def _setup_won_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")
    db.update_lead_status(lead_id, "won")
    db.insert_website(lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name="", preview_url="https://joes-cafe.example")
    return lead_id


def test_onboard_route_rejects_missing_or_wrong_token(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    client = webhook_server.app.test_client()
    assert client.get(f"/onboard/{lead_id}").status_code == 403
    assert client.get(f"/onboard/{lead_id}?token=garbage").status_code == 403


def test_onboard_route_requires_payment_confirmed(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")  # status 'new', never paid
    token = editor_auth.issue_editor_session(lead_id)

    resp = webhook_server.app.test_client().get(f"/onboard/{lead_id}?token={token}")

    assert resp.status_code == 409
    assert "not yet confirmed" in resp.get_data(as_text=True).lower()


def test_onboard_route_get_shows_form_prefilled_with_contact_email(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    token = editor_auth.issue_editor_session(lead_id)

    body = webhook_server.app.test_client().get(f"/onboard/{lead_id}?token={token}").get_data(as_text=True)

    assert "Joes Cafe" in body
    assert 'value="owner@joescafe.example"' in body


def test_onboard_route_post_runs_onboarding_and_shows_message(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    token = editor_auth.issue_editor_session(lead_id)
    monkeypatch.setattr(
        webhook_server.onboarding_agent, "complete_onboarding",
        Mock(return_value={"message": "All set, octocat!"}),
    )

    resp = webhook_server.app.test_client().post(f"/onboard/{lead_id}", data={
        "token": token, "github_username": "octocat", "vercel_email": "owner@joescafe.example",
    })

    assert resp.status_code == 200
    assert "All set, octocat!" in resp.get_data(as_text=True)
    webhook_server.onboarding_agent.complete_onboarding.assert_called_once_with(
        lead_id, "octocat", "owner@joescafe.example",
    )


def test_onboard_route_post_reports_failure_without_500(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    token = editor_auth.issue_editor_session(lead_id)
    monkeypatch.setattr(
        webhook_server.onboarding_agent, "complete_onboarding",
        Mock(side_effect=RuntimeError("GitHub is down")),
    )

    resp = webhook_server.app.test_client().post(f"/onboard/{lead_id}", data={
        "token": token, "github_username": "octocat", "vercel_email": "",
    })

    assert resp.status_code == 200
    assert "Something went wrong" in resp.get_data(as_text=True)
