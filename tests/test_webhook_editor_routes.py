"""Tests for webhook_server.py's /edit routes (the client editor's HTTP
layer) using Flask's test client. Vercel/GitHub calls are mocked; DB is a
real tmp_path SQLite file."""
from __future__ import annotations

from unittest.mock import Mock

import webhook_server
from agents import design_agent, editor_agent
from utils import db, editor_auth


def _setup_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_fields(lead_id, phone="0113 111 1111")
    db.insert_website(
        lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name="",
        preview_url="https://old-preview.example",
    )
    return lead_id


def test_edit_route_rejects_missing_or_wrong_token(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    client = webhook_server.app.test_client()

    assert client.get(f"/edit/{lead_id}").status_code == 403  # no token
    assert client.get(f"/edit/{lead_id}?token=garbage").status_code == 403

    other_token = editor_auth.generate_editor_token(lead_id + 1)
    assert client.get(f"/edit/{lead_id}?token={other_token}").status_code == 403  # token for a different lead


def test_edit_route_404s_for_missing_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    client = webhook_server.app.test_client()
    token = editor_auth.generate_editor_token(999)
    assert client.get(f"/edit/999?token={token}").status_code == 404


def test_edit_route_get_shows_current_values(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    token = editor_auth.generate_editor_token(lead_id)
    client = webhook_server.app.test_client()

    resp = client.get(f"/edit/{lead_id}?token={token}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Joes Cafe" in body
    assert 'value="0113 111 1111"' in body


def test_edit_route_escapes_untrusted_scraped_content(tmp_path, monkeypatch):
    """Scraped content originates from arbitrary third-party sites -- the
    form MUST autoescape it, never render it as raw HTML (XSS)."""
    lead_id = _setup_lead(tmp_path, monkeypatch)
    db.update_lead_fields(lead_id, phone='"><script>alert(1)</script>')
    token = editor_auth.generate_editor_token(lead_id)
    client = webhook_server.app.test_client()

    body = client.get(f"/edit/{lead_id}?token={token}").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_edit_route_post_persists_and_publishes(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(
        editor_agent.vercel_api, "deploy_files",
        Mock(return_value={"deployment_id": "dpl_2", "url": "https://new-preview.example", "ready_state": "READY"}),
    )
    monkeypatch.setattr(editor_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(editor_agent.site_audit, "check_broken_links", Mock(return_value=[]))

    token = editor_auth.generate_editor_token(lead_id)
    client = webhook_server.app.test_client()
    resp = client.post(f"/edit/{lead_id}", data={
        "token": token,
        "phone": "0113 222 2222",
        "location": "Leeds",
        "logo_url": "",
        "hours": "Mon-Fri: 9am - 5pm\nSat: 10am - 2pm",
        "scraped_services": "Flat White\nCold Brew",
        "reviews": "Loved it!",
        "photos": "",
    })

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Published! Live at https://new-preview.example" in body

    edits = db.get_site_edits(lead_id)
    assert edits["phone"] == "0113 222 2222"
    assert edits["hours"] == ["Mon-Fri: 9am - 5pm", "Sat: 10am - 2pm"]
    assert edits["scraped_services"] == ["Flat White", "Cold Brew"]
    assert db.get_website_by_lead(lead_id)["preview_url"] == "https://new-preview.example"


def test_edit_route_post_reports_publish_failure_without_500(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(
        editor_agent, "publish", Mock(side_effect=RuntimeError("Vercel is down")),
    )
    token = editor_auth.generate_editor_token(lead_id)
    client = webhook_server.app.test_client()

    resp = client.post(f"/edit/{lead_id}", data={
        "token": token, "phone": "0113 222 2222", "location": "Leeds", "logo_url": "",
        "hours": "", "scraped_services": "", "reviews": "", "photos": "",
    })

    assert resp.status_code == 200  # not a 500 -- the failure is shown in the page instead
    assert "publishing failed" in resp.get_data(as_text=True)
    # The edit itself was still saved even though publish failed.
    assert db.get_site_edits(lead_id)["phone"] == "0113 222 2222"
