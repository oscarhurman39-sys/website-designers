"""Tests for the locked client editor: utils/editor_auth.py's magic-link
tokens, utils/db.py's site_edits storage, and agents/editor_agent.py's
edit/publish flow. Network (Vercel/GitHub) is mocked; DB is a real
tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents import design_agent, editor_agent
from utils import db, editor_auth


# --- editor_auth: token round-trip -----------------------------------------------

def test_editor_token_round_trips():
    token = editor_auth.generate_editor_token(42)
    assert editor_auth.verify_editor_token(token) == 42


def test_editor_token_rejects_tampering():
    token = editor_auth.generate_editor_token(42)
    # Flipping the LAST base64 character can be a no-op (unused padding
    # bits in the final group are ignored on decode) -- flip the first
    # character instead, which always changes a real decoded byte.
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    assert editor_auth.verify_editor_token(tampered) is None


def test_editor_token_rejects_garbage():
    assert editor_auth.verify_editor_token("not-a-real-token") is None
    assert editor_auth.verify_editor_token("") is None


def test_create_editor_link_embeds_lead_id_and_valid_token(monkeypatch):
    monkeypatch.setattr(editor_auth.config, "PUBLIC_BASE_URL", "https://example.com")
    link = editor_auth.create_editor_link(7)
    assert link.startswith("https://example.com/edit/7?token=")
    token = link.split("token=")[1]
    assert editor_auth.verify_editor_token(token) == 7


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


def test_publish_pushes_to_github_when_already_handed_off(monkeypatch, tmp_path):
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
