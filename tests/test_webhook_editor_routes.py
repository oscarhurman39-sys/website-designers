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
    db.update_lead_fields(lead_id, phone="0113 111 1111", contact_email="owner@joescafe.example")
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

    other_lead_id = db.insert_lead("Other Co", "cafe", "Leeds")
    other_token = editor_auth.issue_editor_session(other_lead_id)
    assert client.get(f"/edit/{lead_id}?token={other_token}").status_code == 403  # token for a different lead


def test_edit_route_shows_expired_link_page_with_a_request_new_link_option(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    client = webhook_server.app.test_client()
    body = client.get(f"/edit/{lead_id}?token=garbage").get_data(as_text=True)
    assert "no longer valid" in body.lower()
    assert f"/edit/{lead_id}/request-link" in body


def test_edit_route_rejects_a_revoked_session(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    token = editor_auth.issue_editor_session(lead_id)
    editor_auth.revoke_all_sessions(lead_id)
    client = webhook_server.app.test_client()
    assert client.get(f"/edit/{lead_id}?token={token}").status_code == 403


def test_edit_route_rejects_an_expired_session(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    token = editor_auth.issue_editor_session(lead_id, lifetime_days=-1)
    client = webhook_server.app.test_client()
    assert client.get(f"/edit/{lead_id}?token={token}").status_code == 403


def test_edit_route_get_shows_current_values(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    token = editor_auth.issue_editor_session(lead_id)
    client = webhook_server.app.test_client()

    resp = client.get(f"/edit/{lead_id}?token={token}")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Joes Cafe" in body
    assert 'value="0113 111 1111"' in body
    assert f"/edit/{lead_id}/request-link" in body  # "lost this link?" pointer


def test_edit_route_escapes_untrusted_scraped_content(tmp_path, monkeypatch):
    """Scraped content originates from arbitrary third-party sites -- the
    form MUST autoescape it, never render it as raw HTML (XSS)."""
    lead_id = _setup_lead(tmp_path, monkeypatch)
    db.update_lead_fields(lead_id, phone='"><script>alert(1)</script>')
    token = editor_auth.issue_editor_session(lead_id)
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

    token = editor_auth.issue_editor_session(lead_id)
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
    token = editor_auth.issue_editor_session(lead_id)
    client = webhook_server.app.test_client()

    resp = client.post(f"/edit/{lead_id}", data={
        "token": token, "phone": "0113 222 2222", "location": "Leeds", "logo_url": "",
        "hours": "", "scraped_services": "", "reviews": "", "photos": "",
    })

    assert resp.status_code == 200  # not a 500 -- the failure is shown in the page instead
    assert "publishing failed" in resp.get_data(as_text=True)
    # The edit itself was still saved even though publish failed.
    assert db.get_site_edits(lead_id)["phone"] == "0113 222 2222"


def test_edit_route_post_reports_blocked_url_without_500(tmp_path, monkeypatch):
    """A submitted photo URL resolving to a private/internal address must
    surface as a friendly message, not crash the request."""
    lead_id = _setup_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(
        editor_agent.ssrf_guard.socket, "getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("169.254.169.254", 0))],
    )
    token = editor_auth.issue_editor_session(lead_id)
    client = webhook_server.app.test_client()

    resp = client.post(f"/edit/{lead_id}", data={
        "token": token, "phone": "0113 111 1111", "location": "Leeds", "logo_url": "",
        "hours": "", "scraped_services": "", "reviews": "", "photos": "http://attacker.example/x.jpg",
    })

    assert resp.status_code == 200
    assert "Could not save your changes" in resp.get_data(as_text=True)
    assert db.get_site_edits(lead_id) == {}  # nothing was persisted, including earlier fields in the same submit


# --- request-link route -----------------------------------------------------------

def test_request_link_route_shows_identical_message_regardless_of_match(tmp_path, monkeypatch):
    """Must not be usable to enumerate which email is on file for a
    lead_id -- see editor_agent.request_new_editor_link's docstring."""
    lead_id = _setup_lead(tmp_path, monkeypatch)
    monkeypatch.setattr(editor_agent.email_utils, "send_email", Mock(return_value="<msgid@test>"))
    client = webhook_server.app.test_client()

    match_body = client.post(f"/edit/{lead_id}/request-link", data={"email": "owner@joescafe.example"}).get_data(as_text=True)
    mismatch_body = client.post(f"/edit/{lead_id}/request-link", data={"email": "wrong@example.com"}).get_data(as_text=True)

    assert "a new editing link is on its way" in match_body.lower()
    assert match_body == mismatch_body


def test_request_link_route_actually_sends_on_match(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    send_mock = Mock(return_value="<msgid@test>")
    monkeypatch.setattr(editor_agent.email_utils, "send_email", send_mock)
    client = webhook_server.app.test_client()

    client.post(f"/edit/{lead_id}/request-link", data={"email": "owner@joescafe.example"})
    send_mock.assert_called_once()
    assert send_mock.call_args.kwargs["to_addr"] == "owner@joescafe.example"


def test_request_link_route_get_shows_form(tmp_path, monkeypatch):
    lead_id = _setup_lead(tmp_path, monkeypatch)
    client = webhook_server.app.test_client()
    resp = client.get(f"/edit/{lead_id}/request-link")
    assert resp.status_code == 200
    assert "email" in resp.get_data(as_text=True).lower()
