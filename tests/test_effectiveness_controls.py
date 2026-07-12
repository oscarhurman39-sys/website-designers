from __future__ import annotations

from unittest.mock import Mock

from agents import sales_agent
from utils import db


def test_lead_scoring_orders_researched_leads_by_revenue_priority(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()

    low_id = db.insert_lead("Low Value Cafe", "cafe", "Leeds", status="researched")
    high_id = db.insert_lead("Bright Smile Dental", "dentist", "Leeds", status="researched")
    db.update_lead_fields(high_id, contact_email="owner@example.com", website_url="https://example.com", pain_point="Slow site")

    low_score, _ = db.refresh_lead_score(low_id)
    high_score, _ = db.refresh_lead_score(high_id)

    assert high_score > low_score
    assert [lead["id"] for lead in db.list_leads_by_status_priority("researched")] == [high_id, low_id]


def test_click_counts_are_available_for_dashboard(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()

    lead_id = db.insert_lead("Clicked Co", "plumber", "Leeds")
    db.log_click(lead_id)
    db.log_click(lead_id)

    assert db.get_click_count(lead_id) == 2
    assert db.get_click_counts() == {lead_id: 2}


def test_send_cold_email_records_dry_run_when_live_send_disabled(monkeypatch):
    lead = {"id": 123, "business_name": "Example Co", "contact_email": "owner@example.com", "location": "Leeds"}

    monkeypatch.setattr(sales_agent.config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(sales_agent.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(
        sales_agent.db,
        "get_website_by_lead",
        Mock(return_value={"preview_url": "https://example.vercel.app"}),
    )
    monkeypatch.setattr(
        sales_agent.requests,
        "get",
        Mock(return_value=Mock(url="https://example.vercel.app", text="<html>site</html>", status_code=200)),
    )
    monkeypatch.setattr(sales_agent.screenshot, "get_cached_screenshot", Mock(return_value=None))
    monkeypatch.setattr(sales_agent.db, "insert_email_thread", Mock())
    monkeypatch.setattr(sales_agent.db, "update_lead_status", Mock())
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport", Mock())

    assert sales_agent._send_cold_email_impl(lead) is True

    sales_agent._send_via_configured_transport.assert_not_called()
    sales_agent.db.insert_email_thread.assert_called_once()
    sales_agent.db.update_lead_status.assert_called_once_with(
        lead["id"],
        "emailed",
        notes="Dry-run email recorded; ENABLE_LIVE_SEND is false",
    )
