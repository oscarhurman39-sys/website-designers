from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents import design_agent, sales_agent
from utils import url_safety


def _response(url: str, text: str = "<html><body>site</body></html>", status_code: int = 200) -> Mock:
    resp = Mock()
    resp.url = url
    resp.text = text
    resp.status_code = status_code
    return resp


def test_validate_deployment_url_accepts_public_ready_site(monkeypatch):
    monkeypatch.setattr(
        url_safety.requests,
        "get",
        Mock(return_value=_response("https://example.vercel.app")),
    )

    url = design_agent._validate_deployment_url(
        {"url": "https://example.vercel.app", "ready_state": "READY"}
    )

    assert url == "https://example.vercel.app"


@pytest.mark.parametrize(
    "deployment",
    [
        {"url": "", "ready_state": "READY"},
        {"url": "not-a-url", "ready_state": "READY"},
        {"url": "https://example.vercel.app/login", "ready_state": "READY"},
        {"url": "https://example.vercel.app", "ready_state": "ERROR"},
    ],
)
def test_validate_deployment_url_rejects_invalid_deployments(deployment):
    with pytest.raises(RuntimeError):
        design_agent._validate_deployment_url(deployment)


def test_validate_deployment_url_rejects_auth_pages(monkeypatch):
    monkeypatch.setattr(
        url_safety.requests,
        "get",
        Mock(return_value=_response("https://example.vercel.app", "Vercel Authentication")),
    )

    with pytest.raises(RuntimeError):
        design_agent._validate_deployment_url(
            {"url": "https://example.vercel.app", "ready_state": "READY"}
        )


def test_build_html_body_renders_preview_link_as_button():
    preview_url = "https://example.vercel.app"
    lead = {"id": 1, "business_name": "Example Co", "location": "Leeds"}

    html = sales_agent._build_html_body(lead, preview_url, sales_agent._intro_line(lead))

    assert (
        f'<a href="{preview_url}" style="display:inline-block;padding:14px 28px;'
        "background:#2563eb;color:white;border-radius:8px;text-decoration:none;"
        'font-size:16px;font-weight:bold;margin:16px 0">View Your Free Website &rarr;</a>'
    ) in html
    assert f"View the live preview: {preview_url}" not in html
