"""Regression tests for the two behaviors that used to hurt in production:

1. Previews must NOT create a GitHub repo (the old pipeline created one per
   lead and flooded the account with dozens of dead repos). The hand-off
   repo is created lazily, only by create_handoff_repo() at transfer time.
2. Stripe webhook events must be applied at most once -- Stripe retries
   delivery, and a replayed checkout.session.completed must not re-fire.
"""
from __future__ import annotations

from unittest.mock import Mock

from agents import design_agent
from utils import db


def _lead() -> dict:
    return {"id": 7, "business_name": "Joes Cafe", "niche": "cafe", "location": "Leeds", "phone": ""}


def test_process_lead_creates_no_github_repo(monkeypatch, tmp_path):
    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(
        design_agent.github_api,
        "create_repo_with_files",
        Mock(side_effect=AssertionError("GitHub repo must not be created at preview time")),
    )
    monkeypatch.setattr(
        design_agent.vercel_api,
        "deploy_files",
        Mock(return_value={
            "deployment_id": "dpl_1",
            "url": "https://joes-cafe.vercel.app",
            "ready_state": "READY",
            "project_name": "joes-cafe-preview-7",
        }),
    )
    monkeypatch.setattr(design_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(design_agent, "_capture_and_publish_screenshot", Mock(return_value=("", "")))
    insert_website = Mock(return_value=1)
    monkeypatch.setattr(design_agent.db, "insert_website", insert_website)
    monkeypatch.setattr(design_agent.db, "update_lead_status", Mock())
    monkeypatch.setattr(design_agent.db, "get_website_by_lead", Mock(return_value={"id": 1}))

    result = design_agent._process_lead_impl(_lead())

    assert result == {"id": 1}
    kwargs = insert_website.call_args.kwargs
    assert kwargs["repo_url"] == ""
    assert kwargs["repo_full_name"] == ""
    # The rendered files were saved to disk for the eventual hand-off repo.
    saved_dir = tmp_path / "lead-7"
    assert (saved_dir / "index.html").is_file()
    assert kwargs["local_dir"] == str(saved_dir)


def test_create_handoff_repo_pushes_the_saved_preview_files(monkeypatch, tmp_path):
    site_dir = tmp_path / "lead-7"
    site_dir.mkdir()
    (site_dir / "index.html").write_text("<html>exactly what the client saw</html>", encoding="utf-8")

    monkeypatch.setattr(design_agent.db, "get_lead", Mock(return_value=_lead()))
    monkeypatch.setattr(
        design_agent.db,
        "get_website_by_lead",
        Mock(return_value={
            "lead_id": 7, "template_niche": "cafe",
            "repo_url": "", "repo_full_name": "", "local_dir": str(site_dir),
        }),
    )
    create_repo = Mock(return_value=(object(), "https://github.com/u/joes-cafe-preview-7", "u/joes-cafe-preview-7"))
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", create_repo)
    update_repo = Mock()
    monkeypatch.setattr(design_agent.db, "update_website_repo", update_repo)

    repo_url, repo_full_name = design_agent.create_handoff_repo(7)

    assert repo_full_name == "u/joes-cafe-preview-7"
    pushed_files = create_repo.call_args.args[2]
    assert pushed_files == {"index.html": "<html>exactly what the client saw</html>"}
    update_repo.assert_called_once_with(7, "https://github.com/u/joes-cafe-preview-7", "u/joes-cafe-preview-7")


def test_create_handoff_repo_is_idempotent_when_repo_already_exists(monkeypatch):
    monkeypatch.setattr(design_agent.db, "get_lead", Mock(return_value=_lead()))
    monkeypatch.setattr(
        design_agent.db,
        "get_website_by_lead",
        Mock(return_value={
            "lead_id": 7, "template_niche": "cafe",
            "repo_url": "https://github.com/u/existing", "repo_full_name": "u/existing", "local_dir": "",
        }),
    )
    create_repo = Mock(side_effect=AssertionError("must not create a second repo"))
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", create_repo)

    assert design_agent.create_handoff_repo(7) == ("https://github.com/u/existing", "u/existing")


def test_record_event_once_blocks_webhook_replays(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()

    assert db.record_event_once("evt_123") is True
    assert db.record_event_once("evt_123") is False  # replay -- must not re-fire
    assert db.record_event_once("") is False  # missing id is never actionable
    assert db.record_event_once("evt_456") is True
