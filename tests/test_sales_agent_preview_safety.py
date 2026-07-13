from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents import sales_agent


def _response(url: str, text: str = "<html><body>site</body></html>", status_code: int = 200) -> Mock:
    resp = Mock()
    resp.url = url
    resp.text = text
    resp.status_code = status_code
    return resp


def test_validate_preview_link_for_send_accepts_public_site(monkeypatch):
    monkeypatch.setattr(
        sales_agent.requests,
        "get",
        Mock(return_value=_response("https://example.vercel.app")),
    )

    assert sales_agent._validate_preview_link_for_send("https://example.vercel.app") == "https://example.vercel.app"


def test_validate_preview_link_for_send_strips_url_and_does_not_read_lead_db(monkeypatch):
    request_get = Mock(return_value=_response("https://example.vercel.app"))
    monkeypatch.setattr(sales_agent.requests, "get", request_get)
    monkeypatch.setattr(sales_agent.db, "get_website_by_lead", Mock(side_effect=AssertionError("unexpected DB read")))

    assert sales_agent._validate_preview_link_for_send("  https://example.vercel.app  ") == "https://example.vercel.app"
    request_get.assert_called_once_with(
        "https://example.vercel.app",
        allow_redirects=True,
        headers={},
        timeout=sales_agent._PREVIEW_VALIDATION_TIMEOUT_SECONDS,
    )


def test_validate_preview_link_for_send_uses_bypass_header_only_in_dry_run(monkeypatch):
    request_get = Mock(return_value=_response("https://example.vercel.app"))
    monkeypatch.setattr(sales_agent.requests, "get", request_get)
    monkeypatch.setattr(sales_agent.config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(sales_agent.config, "VERCEL_AUTOMATION_BYPASS_SECRET", "secret")

    assert sales_agent._validate_preview_link_for_send("https://example.vercel.app") == "https://example.vercel.app"
    request_get.assert_called_once_with(
        "https://example.vercel.app",
        allow_redirects=True,
        headers={"x-vercel-protection-bypass": "secret"},
        timeout=sales_agent._PREVIEW_VALIDATION_TIMEOUT_SECONDS,
    )


@pytest.mark.parametrize(
    "preview_url",
    [
        "",
        "not-a-url",
        "https://example.vercel.app/login",
    ],
)
def test_validate_preview_link_for_send_rejects_bad_urls(preview_url):
    with pytest.raises(RuntimeError):
        sales_agent._validate_preview_link_for_send(preview_url)


def test_validate_preview_link_for_send_rejects_auth_page(monkeypatch):
    monkeypatch.setattr(
        sales_agent.requests,
        "get",
        Mock(return_value=_response("https://example.vercel.app", "Vercel Authentication")),
    )

    with pytest.raises(RuntimeError):
        sales_agent._validate_preview_link_for_send("https://example.vercel.app")


def test_validate_preview_link_for_send_rejects_auth_redirect(monkeypatch):
    monkeypatch.setattr(
        sales_agent.requests,
        "get",
        Mock(return_value=_response("https://example.vercel.app/login")),
    )

    with pytest.raises(RuntimeError):
        sales_agent._validate_preview_link_for_send("https://example.vercel.app")


def test_send_cold_email_blocks_invalid_preview_before_sending(monkeypatch):
    lead = {"id": 123, "business_name": "Example Co", "contact_email": "owner@example.com", "location": "Leeds"}

    monkeypatch.setattr(sales_agent.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(sales_agent.db, "get_website_by_lead", Mock(return_value={"preview_url": ""}))
    monkeypatch.setattr(sales_agent.db, "update_lead_status", Mock())
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport", Mock())

    assert sales_agent._send_cold_email_impl(lead) is False

    sales_agent._send_via_configured_transport.assert_not_called()
    sales_agent.db.update_lead_status.assert_called_once()
    assert sales_agent.db.update_lead_status.call_args.args[1] == "researched"
