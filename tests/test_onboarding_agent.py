"""Tests for agents/onboarding_agent.py: the automated post-payment
handoff (create repo, invite collaborators, config-gated auto-remove our
own access, email the editor link). GitHub/Vercel/email are mocked; DB is
a real tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents import design_agent, onboarding_agent
from utils import db


def _setup_won_lead(tmp_path, monkeypatch, repo_full_name="", transferred=False, contact_email="owner@joescafe.example"):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    if contact_email:
        db.update_lead_fields(lead_id, contact_email=contact_email)
    db.update_lead_status(lead_id, "won")
    db.insert_website(lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name=repo_full_name, preview_url="https://joes-cafe.example")
    if transferred:
        db.mark_website_transferred(lead_id)
    return lead_id


# --- send_onboarding_email ---------------------------------------------------------

def test_send_onboarding_email_sends_and_logs_thread(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    send_mock = Mock(return_value="<msgid@test>")
    monkeypatch.setattr(onboarding_agent.email_utils, "send_email", send_mock)

    assert onboarding_agent.send_onboarding_email(lead_id) is True
    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["to_addr"] == "owner@joescafe.example"
    assert len(db.get_email_threads(lead_id)) == 1


def test_send_onboarding_email_false_without_contact_email(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, contact_email="")
    send_mock = Mock()
    monkeypatch.setattr(onboarding_agent.email_utils, "send_email", send_mock)

    assert onboarding_agent.send_onboarding_email(lead_id) is False
    send_mock.assert_not_called()


# --- complete_onboarding ------------------------------------------------------------

def test_complete_onboarding_raises_for_unknown_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with pytest.raises(RuntimeError, match="No lead/website found"):
        onboarding_agent.complete_onboarding(999, "", "")


def test_complete_onboarding_invites_github_collaborator_and_creates_repo(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    create_repo_mock = Mock(return_value=(object(), "https://github.com/u/joes-cafe", "u/joes-cafe"))
    monkeypatch.setattr(onboarding_agent.design_agent.github_api, "create_repo_with_files", create_repo_mock)
    invite_mock = Mock()
    monkeypatch.setattr(onboarding_agent.github_api, "invite_collaborator", invite_mock)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))
    monkeypatch.setattr(onboarding_agent.config, "VERCEL_TEAM_ID", "")

    result = onboarding_agent.complete_onboarding(lead_id, "octocat", "")

    create_repo_mock.assert_called_once()
    invite_mock.assert_called_once_with("u/joes-cafe", "octocat", permission="admin")
    assert "octocat" in result["message"]
    assert "GitHub" in result["message"]


def test_complete_onboarding_skips_repo_creation_when_already_exists(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, repo_full_name="u/joes-cafe")
    create_repo_mock = Mock(side_effect=AssertionError("must not create a second repo"))
    monkeypatch.setattr(onboarding_agent.design_agent.github_api, "create_repo_with_files", create_repo_mock)
    invite_mock = Mock()
    monkeypatch.setattr(onboarding_agent.github_api, "invite_collaborator", invite_mock)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))

    onboarding_agent.complete_onboarding(lead_id, "octocat", "")

    invite_mock.assert_called_once_with("u/joes-cafe", "octocat", permission="admin")


def test_complete_onboarding_invites_vercel_collaborator_only_when_team_configured(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))
    vercel_invite_mock = Mock()
    monkeypatch.setattr(onboarding_agent.vercel_api, "invite_collaborator", vercel_invite_mock)

    monkeypatch.setattr(onboarding_agent.config, "VERCEL_TEAM_ID", "")
    onboarding_agent.complete_onboarding(lead_id, "", "client@example.com")
    vercel_invite_mock.assert_not_called()

    monkeypatch.setattr(onboarding_agent.config, "VERCEL_TEAM_ID", "team_123")
    onboarding_agent.complete_onboarding(lead_id, "", "client@example.com")
    vercel_invite_mock.assert_called_once()


def test_complete_onboarding_github_failure_does_not_block_the_rest(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, repo_full_name="u/joes-cafe")
    monkeypatch.setattr(
        onboarding_agent.github_api, "invite_collaborator",
        Mock(side_effect=RuntimeError("GITHUB_TOKEN is not configured.")),
    )
    monkeypatch.setattr(onboarding_agent.config, "VERCEL_TEAM_ID", "team_123")
    vercel_invite_mock = Mock()
    monkeypatch.setattr(onboarding_agent.vercel_api, "invite_collaborator", vercel_invite_mock)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))

    result = onboarding_agent.complete_onboarding(lead_id, "octocat", "client@example.com")

    vercel_invite_mock.assert_called_once()  # still ran despite the GitHub failure
    assert "Could not invite octocat" in result["message"]


def test_complete_onboarding_always_sends_editor_link(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    send_link_mock = Mock(return_value=True)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", send_link_mock)

    result = onboarding_agent.complete_onboarding(lead_id, "", "")

    send_link_mock.assert_called_once_with(lead_id)
    assert "edit your site" in result["message"]


def test_complete_onboarding_falls_back_message_when_nothing_happened(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, contact_email="")  # no email -> editor link can't send either
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=False))

    result = onboarding_agent.complete_onboarding(lead_id, "", "")

    assert "keep managing the site" in result["message"]


# --- config-gated auto-remove-access -----------------------------------------------

def test_complete_onboarding_does_not_remove_access_by_default(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, repo_full_name="u/joes-cafe")
    monkeypatch.setattr(onboarding_agent.config, "AUTO_REMOVE_GITHUB_ACCESS", False)
    remove_mock = Mock()
    monkeypatch.setattr(onboarding_agent.github_api, "remove_collaborator", remove_mock)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))

    onboarding_agent.complete_onboarding(lead_id, "octocat", "")

    remove_mock.assert_not_called()
    assert db.get_website_by_lead(lead_id)["transferred"] == 0


def test_complete_onboarding_removes_access_when_opted_in(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, repo_full_name="u/joes-cafe")
    monkeypatch.setattr(onboarding_agent.config, "AUTO_REMOVE_GITHUB_ACCESS", True)
    monkeypatch.setattr(onboarding_agent.github_api, "get_authenticated_username", Mock(return_value="bot-account"))
    remove_mock = Mock()
    monkeypatch.setattr(onboarding_agent.github_api, "remove_collaborator", remove_mock)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))

    result = onboarding_agent.complete_onboarding(lead_id, "octocat", "")

    remove_mock.assert_called_once_with("u/joes-cafe", "bot-account")
    assert db.get_website_by_lead(lead_id)["transferred"] == 1
    assert "fully yours" in result["message"]


def test_complete_onboarding_does_not_re_remove_access_if_already_transferred(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch, repo_full_name="u/joes-cafe", transferred=True)
    monkeypatch.setattr(onboarding_agent.config, "AUTO_REMOVE_GITHUB_ACCESS", True)
    remove_mock = Mock()
    monkeypatch.setattr(onboarding_agent.github_api, "remove_collaborator", remove_mock)
    monkeypatch.setattr(onboarding_agent.editor_agent, "send_editor_link", Mock(return_value=True))

    onboarding_agent.complete_onboarding(lead_id, "", "")

    remove_mock.assert_not_called()
