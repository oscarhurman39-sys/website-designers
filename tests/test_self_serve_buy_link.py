"""Tests that every outbound email surface (cold email plain-text/HTML,
both follow-ups) AND the preview site itself include the self-serve
/buy/<lead_id> link -- see PLAN.md's automation audit: a buyer should be
able to pay with zero human action on our side, not only by replying and
waiting for the operator to run `payment ready`."""
from __future__ import annotations

from unittest.mock import Mock

from agents import design_agent, sales_agent


def _lead(**extra) -> dict:
    lead = {
        "id": 42, "business_name": "Example Co", "niche": "plumber",
        "location": "Leeds", "contact_email": "owner@example.com",
        "status": "emailed", "pain_point": "", "site_audit": "",
    }
    lead.update(extra)
    return lead


def test_buy_link_format(monkeypatch):
    monkeypatch.setattr(sales_agent.config, "PUBLIC_BASE_URL", "https://example.com")
    assert sales_agent._buy_link(42) == "https://example.com/buy/42"


def test_plain_text_body_includes_buy_link(monkeypatch):
    monkeypatch.setattr(sales_agent.config, "PUBLIC_BASE_URL", "https://example.com")
    body = sales_agent._plain_text_body(_lead(), "https://preview.example", "Hi there")
    assert "https://example.com/buy/42" in body
    assert "reply YES" in body  # the reply path stays available too


def test_html_body_includes_buy_button_and_no_raw_closing_text(monkeypatch):
    monkeypatch.setattr(sales_agent.config, "PUBLIC_BASE_URL", "https://example.com")
    html = sales_agent._build_html_body(_lead(), "https://preview.example", "Hi there")
    assert 'href="https://example.com/buy/42"' in html
    assert "Buy This Website" in html
    assert "background:#16a34a" in html  # distinct color from the preview button


def test_followup_zero_includes_buy_link(monkeypatch):
    monkeypatch.setattr(sales_agent.config, "PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(sales_agent.db, "get_email_threads", Mock(return_value=[]))
    _, body = sales_agent._followup_copy(_lead(), 0, "https://preview.example")
    assert "https://example.com/buy/42" in body


def test_followup_one_includes_buy_link(monkeypatch):
    monkeypatch.setattr(sales_agent.config, "PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(sales_agent.db, "get_email_threads", Mock(return_value=[]))
    _, body = sales_agent._followup_copy(_lead(), 1, "https://preview.example")
    assert "https://example.com/buy/42" in body


def test_preview_site_itself_has_a_buy_button(monkeypatch):
    monkeypatch.setattr(design_agent.config, "PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    lead = dict(_lead(), niche="default")
    context = design_agent.build_context(lead)
    assert context["buy_url"] == "https://example.com/buy/42"

    html = design_agent.render_template_files("default", context)["index.html"]
    assert 'href="https://example.com/buy/42"' in html
    assert "Get This Website" in html
