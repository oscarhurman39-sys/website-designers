"""Tests for the locked client editor: utils/editor_auth.py's DB-backed
magic-link sessions, utils/db.py's site_edits storage, and
agents/editor_agent.py's edit/publish flow. Network (Vercel/GitHub) is
mocked; DB is a real tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents import design_agent, editor_agent
from utils import db, editor_auth


# --- editor_auth: DB-backed sessions ----------------------------------------------

def test_editor_session_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    token = editor_auth.issue_editor_session(lead_id)
    assert editor_auth.verify_editor_session(lead_id, token) is True


def test_editor_session_rejects_garbage_or_missing_token(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    assert editor_auth.verify_editor_session(lead_id, "not-a-real-token") is False
    assert editor_auth.verify_editor_session(lead_id, "") is False


def test_editor_session_rejects_token_for_a_different_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_a = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    lead_b = db.insert_lead("Other Co", "cafe", "Leeds")

    token = editor_auth.issue_editor_session(lead_a)
    assert editor_auth.verify_editor_session(lead_b, token) is False


def test_editor_session_expires(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    token = editor_auth.issue_editor_session(lead_id, lifetime_days=-1)  # already expired
    assert editor_auth.verify_editor_session(lead_id, token) is False


def test_revoke_all_sessions_invalidates_outstanding_links(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    token_a = editor_auth.issue_editor_session(lead_id)
    token_b = editor_auth.issue_editor_session(lead_id)
    assert editor_auth.verify_editor_session(lead_id, token_a) is True

    editor_auth.revoke_all_sessions(lead_id)

    assert editor_auth.verify_editor_session(lead_id, token_a) is False
    assert editor_auth.verify_editor_session(lead_id, token_b) is False


def test_issuing_a_new_session_does_not_revoke_other_outstanding_ones(monkeypatch, tmp_path):
    """Multiple valid links (e.g. one per device) can coexist -- issuing
    a fresh one isn't an implicit revoke of the others."""
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    token_a = editor_auth.issue_editor_session(lead_id)
    token_b = editor_auth.issue_editor_session(lead_id)

    assert editor_auth.verify_editor_session(lead_id, token_a) is True
    assert editor_auth.verify_editor_session(lead_id, token_b) is True


def test_create_editor_link_embeds_lead_id_and_a_valid_token(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    monkeypatch.setattr(editor_auth.config, "PUBLIC_BASE_URL", "https://example.com")

    link = editor_auth.create_editor_link(lead_id)

    assert link.startswith(f"https://example.com/edit/{lead_id}?token=")
    token = link.split("token=")[1]
    assert editor_auth.verify_editor_session(lead_id, token) is True


# --- db.site_edits -----------------------------------------------------------------

def test_upsert_and_get_site_edits(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    db.upsert_site_edit(lead_id, "phone", "0113 000 0000")
    db.upsert_site_edit(lead_id, "hours", ["Mon-Fri: 9-5"])
    assert db.get_site_edits(lead_id) == {"phone": "0113 000 0000", "hours": ["Mon-Fri: 9-5"]}


def test_upsert_site_edit_overwrites_previous_value(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    db.upsert_site_edit(lead_id, "phone", "111")
    db.upsert_site_edit(lead_id, "phone", "222")
    assert db.get_site_edits(lead_id)["phone"] == "222"


def test_get_site_edits_empty_for_untouched_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    assert db.get_site_edits(lead_id) == {}


# --- editor_agent.apply_edit: whitelist enforcement -------------------------------

def test_apply_edit_rejects_unknown_field(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    with pytest.raises(ValueError, match="not an editable field"):
        editor_agent.apply_edit(lead_id, "business_name", "New Name Inc")
    with pytest.raises(ValueError, match="not an editable field"):
        editor_agent.apply_edit(lead_id, "brand_colors", ["#ff0000"])


def test_apply_edit_rejects_wrong_type(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    with pytest.raises(ValueError, match="must be a list"):
        editor_agent.apply_edit(lead_id, "hours", "not a list")
    with pytest.raises(ValueError, match="must be a string"):
        editor_agent.apply_edit(lead_id, "phone", ["not", "a", "string"])


def test_apply_edit_persists_valid_edit(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    editor_agent.apply_edit(lead_id, "phone", "0113 999 8888")
    assert db.get_site_edits(lead_id)["phone"] == "0113 999 8888"


# --- editor_agent.effective_lead: edits layer on top of the original row ---------

def test_effective_lead_merges_edits_over_original(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, phone="0113 111 1111")

    editor_agent.apply_edit(lead_id, "phone", "0113 222 2222")
    editor_agent.apply_edit(lead_id, "hours", ["Mon-Fri: 9-5"])

    effective = editor_agent.effective_lead(lead_id)
    assert effective["phone"] == "0113 222 2222"
    assert effective["hours"] == '["Mon-Fri: 9-5"]'  # re-encoded to the same JSON-string DB shape
    assert effective["business_name"] == "Joes Cafe"  # untouched fields pass through


def test_effective_lead_none_for_missing_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    assert editor_agent.effective_lead(999) is None


def test_effective_lead_feeds_content_importer_and_build_context(monkeypatch, tmp_path):
    """The whole point of effective_lead()'s re-encoding: build_context()
    and content_importer.load_content() need zero awareness that an edit
    ever happened."""
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")

    editor_agent.apply_edit(lead_id, "photos", ["https://example.com/new-photo.jpg"])
    editor_agent.apply_edit(lead_id, "reviews", ["Edited review text."])

    context = design_agent.build_context(editor_agent.effective_lead(lead_id))
    assert context["photos"] == ["https://example.com/new-photo.jpg"]
    assert context["hero_image_url"] == "https://example.com/new-photo.jpg"
    assert context["reviews"] == ["Edited review text."]


# --- editor_agent.publish -----------------------------------------------------------

def _setup_lead_with_website(tmp_path, monkeypatch, repo_full_name=""):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.insert_website(
        lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name=repo_full_name,
        preview_url="https://old-preview.example",
    )
    return lead_id


def test_publish_redeploys_to_the_stable_project_name_and_updates_preview_url(monkeypatch, tmp_path):
    lead_id = _setup_lead_with_website(tmp_path, monkeypatch)
    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")

    deploy_calls = []

    def fake_deploy(project_name, files):
        deploy_calls.append(project_name)
        return {"deployment_id": "dpl_2", "url": "https://joes-cafe-preview-new.vercel.app", "ready_state": "READY"}

    monkeypatch.setattr(editor_agent.vercel_api, "deploy_files", fake_deploy)
    monkeypatch.setattr(editor_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(editor_agent.site_audit, "check_broken_links", Mock(return_value=[]))

    editor_agent.apply_edit(lead_id, "phone", "0113 999 8888")
    result = editor_agent.publish(lead_id)

    assert result["preview_url"] == "https://joes-cafe-preview-new.vercel.app"
    assert result["repo_updated"] is False  # no repo_full_name was set
    # Same project name every time -- derived from the ORIGINAL (locked)
    # business_name + stable lead id, never from an edited value.
    assert deploy_calls == [design_agent.github_api.make_repo_name("Joes Cafe", lead_id)]
    assert db.get_website_by_lead(lead_id)["preview_url"] == "https://joes-cafe-preview-new.vercel.app"


def test_publish_pushes_to_github_when_repo_exists_but_not_yet_transferred(monkeypatch, tmp_path):
    """repo_full_name set but website.transferred still 0 -- we still hold
    GitHub access, so the update should happen (not best-effort-skipped)."""
    lead_id = _setup_lead_with_website(tmp_path, monkeypatch, repo_full_name="acme/joes-cafe-preview-1")
    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(
        editor_agent.vercel_api, "deploy_files",
        Mock(return_value={"deployment_id": "dpl_2", "url": "site.vercel.app", "ready_state": "READY"}),
    )
    monkeypatch.setattr(editor_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(editor_agent.site_audit, "check_broken_links", Mock(return_value=[]))

    fake_repo = Mock()
    monkeypatch.setattr(editor_agent.github_api, "_get_client", Mock(return_value=Mock(get_repo=Mock(return_value=fake_repo))))
    update_mock = Mock()
    monkeypatch.setattr(editor_agent.github_api, "update_or_create_files", update_mock)

    result = editor_agent.publish(lead_id)

    assert result["repo_updated"] is True
    update_mock.assert_called_once()
    assert update_mock.call_args.args[0] is fake_repo


def test_publish_github_push_failure_does_not_lose_the_vercel_republish(monkeypatch, tmp_path):
    lead_id = _setup_lead_with_website(tmp_path, monkeypatch, repo_full_name="acme/joes-cafe-preview-1")
    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(
        editor_agent.vercel_api, "deploy_files",
        Mock(return_value={"deployment_id": "dpl_2", "url": "https://site.vercel.app", "ready_state": "READY"}),
    )
    monkeypatch.setattr(editor_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(editor_agent.site_audit, "check_broken_links", Mock(return_value=[]))
    monkeypatch.setattr(
        editor_agent.github_api, "_get_client",
        Mock(side_effect=RuntimeError("GITHUB_TOKEN is not configured.")),
    )

    result = editor_agent.publish(lead_id)  # must not raise

    assert result["repo_updated"] is False
    assert result["preview_url"] == "https://site.vercel.app"
    assert db.get_website_by_lead(lead_id)["preview_url"] == "https://site.vercel.app"


def test_publish_raises_for_unknown_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with pytest.raises(RuntimeError, match="No lead/website found"):
        editor_agent.publish(999)


def test_publish_refuses_once_fully_transferred(monkeypatch, tmp_path):
    """The two-sources-of-truth fix: once the operator has removed their
    own GitHub access as part of `transfer` (website.transferred=1),
    publish() must refuse outright rather than silently redeploy Vercel
    only -- that would leave Vercel ahead of the GitHub repo the client
    now believes IS their website."""
    lead_id = _setup_lead_with_website(tmp_path, monkeypatch, repo_full_name="acme/joes-cafe-preview-1")
    db.mark_website_transferred(lead_id)
    deploy_mock = Mock()
    monkeypatch.setattr(editor_agent.vercel_api, "deploy_files", deploy_mock)

    with pytest.raises(RuntimeError, match="fully handed off"):
        editor_agent.publish(lead_id)

    deploy_mock.assert_not_called()  # must not even attempt a Vercel redeploy
    assert db.get_website_by_lead(lead_id)["preview_url"] == "https://old-preview.example"


# --- editor_agent.apply_edit: SSRF-guarded URL fields -----------------------------

def test_apply_edit_rejects_a_photo_url_resolving_to_a_private_address(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    monkeypatch.setattr(
        editor_agent.ssrf_guard.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )

    with pytest.raises(editor_agent.ssrf_guard.BlockedURLError):
        editor_agent.apply_edit(lead_id, "photos", ["http://attacker.example/x.jpg"])
    assert db.get_site_edits(lead_id) == {}  # rejected edit is never persisted


def test_apply_edit_allows_a_photo_url_resolving_to_a_public_address(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    monkeypatch.setattr(
        editor_agent.ssrf_guard.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", 0))],
    )

    editor_agent.apply_edit(lead_id, "photos", ["https://example.com/x.jpg"])
    assert db.get_site_edits(lead_id) == {"photos": ["https://example.com/x.jpg"]}


def test_apply_edits_is_all_or_nothing(monkeypatch, tmp_path):
    """A single bad field must not leave earlier-processed fields in the
    same submission persisted -- a partial, confusing save."""
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    monkeypatch.setattr(
        editor_agent.ssrf_guard.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )

    with pytest.raises(editor_agent.ssrf_guard.BlockedURLError):
        editor_agent.apply_edits(lead_id, {
            "phone": "0113 999 8888",
            "photos": ["http://attacker.example/x.jpg"],
        })
    assert db.get_site_edits(lead_id) == {}  # "phone" was validated fine but must not be persisted alone


def test_apply_edit_allows_empty_logo_url(monkeypatch, tmp_path):
    """An empty string means "no logo" -- not a URL to validate/resolve."""
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    editor_agent.apply_edit(lead_id, "logo_url", "")
    assert db.get_site_edits(lead_id) == {"logo_url": ""}


# --- editor_agent.request_new_editor_link -----------------------------------------

def test_request_new_editor_link_sends_when_email_matches(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")
    send_mock = Mock(return_value="<msgid@test>")
    monkeypatch.setattr(editor_agent.email_utils, "send_email", send_mock)

    assert editor_agent.request_new_editor_link(lead_id, "OWNER@JoesCafe.example") is True  # case-insensitive
    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["to_addr"] == "owner@joescafe.example"


def test_request_new_editor_link_does_not_send_on_email_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")
    send_mock = Mock()
    monkeypatch.setattr(editor_agent.email_utils, "send_email", send_mock)

    assert editor_agent.request_new_editor_link(lead_id, "someone-else@example.com") is False
    send_mock.assert_not_called()


def test_request_new_editor_link_false_for_unknown_lead(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    assert editor_agent.request_new_editor_link(999, "anyone@example.com") is False


def test_request_new_editor_link_handles_unsubscribed_recipient_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@joescafe.example")
    monkeypatch.setattr(
        editor_agent.email_utils, "send_email",
        Mock(side_effect=RuntimeError("Refusing to send: owner@joescafe.example is unsubscribed.")),
    )

    assert editor_agent.request_new_editor_link(lead_id, "owner@joescafe.example") is False
