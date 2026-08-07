from __future__ import annotations

from unittest.mock import Mock

import pytest

from agents import lead_agent
from utils import db, places_api


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Point the whole db module at a throwaway SQLite file."""
    import config

    db_file = str(tmp_path / "test_leads.db")
    monkeypatch.setattr(config, "DB_PATH", db_file)
    db.init_db()
    return db_file


_FAKE_BUSINESSES = [
    {
        "place_id": "place-1",
        "name": "Joes Plumbing",
        "address": "1 High St, Leeds",
        "website": "https://joesplumbing.example.com",
        "phone": "0113 000 0000",
        "rating": 4.7,
        "reviews_count": 32,
        "types": ["plumber", "emergency_service"],
    },
    {
        "place_id": "place-2",
        "name": "Drainy McDrainface",
        "address": "2 Low St, Leeds",
        "website": "",
        "phone": "",
        "rating": None,
        "reviews_count": 0,
        "types": [],
    },
]


def test_discover_inserts_and_dedupes(tmp_db, monkeypatch):
    monkeypatch.setattr(places_api, "search_businesses", Mock(return_value=_FAKE_BUSINESSES))

    assert lead_agent.discover("plumber", "Leeds, UK") == 2
    # Same query again: every place id is already known, nothing inserted.
    assert lead_agent.discover("plumber", "Leeds, UK") == 0

    leads = db.list_all_leads()
    assert len(leads) == 2
    by_name = {lead["business_name"]: lead for lead in leads}
    joe = by_name["Joes Plumbing"]
    assert joe["website_url"] == "https://joesplumbing.example.com"
    assert joe["phone"] == "0113 000 0000"
    assert joe["google_rating"] == 4.7
    assert joe["google_reviews_count"] == 32
    # Stored as JSON, must come back as a real list (design_agent iterates it).
    assert joe["google_maps_types"] == ["plumber", "emergency_service"]
    assert joe["status"] == "new"


def test_research_skips_search_when_website_known(tmp_db, monkeypatch):
    lead_id = db.insert_lead(
        "Joes Plumbing", "plumber", "Leeds, UK",
        website_url="https://joesplumbing.example.com", google_place_id="place-1",
    )
    lead = db.get_lead(lead_id)

    search_mock = Mock()
    monkeypatch.setattr(lead_agent, "find_business_website", search_mock)
    fake_page = Mock()
    monkeypatch.setattr(lead_agent, "_fetch", Mock(return_value=fake_page))
    monkeypatch.setattr(lead_agent, "_extract_email", Mock(return_value="joe@joesplumbing.example.com"))
    monkeypatch.setattr(lead_agent, "_extract_testimonial", Mock(return_value=None))
    monkeypatch.setattr(lead_agent, "_extract_pain_point", Mock(return_value="Slow site"))
    monkeypatch.setattr(lead_agent, "_find_subpage", Mock(return_value=None))
    fake_page.get_text = Mock(return_value="page text")

    lead_agent._research_lead_impl(lead)

    search_mock.assert_not_called()
    refreshed = db.get_lead(lead_id)
    assert refreshed["status"] == "researched"
    assert refreshed["contact_email"] == "joe@joesplumbing.example.com"


def test_csv_leads_still_use_website_search(tmp_db, monkeypatch):
    lead_id = db.insert_lead("CSV Cafe", "cafe", "York, UK")
    lead = db.get_lead(lead_id)

    monkeypatch.setattr(lead_agent, "find_business_website", Mock(return_value=None))
    lead_agent._research_lead_impl(lead)

    lead_agent.find_business_website.assert_called_once()
    assert db.get_lead(lead_id)["status"] == "lost"


def test_search_businesses_requires_api_key(monkeypatch):
    import config

    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "")
    with pytest.raises(RuntimeError):
        places_api.search_businesses("plumber in Leeds")
