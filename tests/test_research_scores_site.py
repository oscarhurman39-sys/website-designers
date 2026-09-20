"""LeadAgent research now audits the homepage it already fetches and writes
site_score / site_defects / lead_score, and corrects the contact channel
sourcing guessed. No network: the fetch is replaced with canned pages."""
from __future__ import annotations

import pytest

import config
from agents import lead_agent
from utils import db

HOME = (
    '<html><head><title>Acme</title><meta name="description" content="Acme Plumbing, Horsham"></head><body>'
    '<p>We are Acme Plumbing, a family firm in Horsham with many happy customers over the years.</p>'
    '<a href="mailto:owner@acme.test">email us</a><footer>© 2019 Acme Plumbing</footer></body></html>'
)


class _Resp:
    def __init__(self, url: str, text: str) -> None:
        self.url, self.text, self.content = url, text, text.encode("utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    monkeypatch.setattr(lead_agent.email_verify, "verify_email", lambda addr: "@" in (addr or ""))
    db.init_db()


def _sourced_lead(url: str = "http://acme.test/", name: str = "Acme Plumbing Ltd", reviews: int = 40) -> int:
    lead_id = db.insert_lead(name, "plumber", "Horsham", status="new")
    db.update_lead_fields(
        lead_id, website_url=url, website_status="owned_site", contact_channel="email",
        phone="01403 000000", google_reviews_count=reviews, place_id=f"place-{lead_id}",
    )
    return lead_id


def _serve(pages: dict[str, str]):
    return lambda url: _Resp(url, pages[url]) if url in pages else None


def test_research_audits_the_homepage_and_scores_the_lead(monkeypatch):
    monkeypatch.setattr(lead_agent, "_fetch_response", _serve({"http://acme.test/": HOME}))
    lead_id = _sourced_lead()
    lead_agent.research_lead(db.get_lead(lead_id))
    lead = db.get_lead(lead_id)
    assert lead["status"] == "researched"
    assert lead["contact_email"] == "owner@acme.test"
    assert lead["site_score"] == 2  # http, no viewport, 2019 footer
    assert lead["site_defects"] == "no_https,no_viewport,stale_copyright:2019"
    assert lead["lead_score"] == 10  # weak site (8) + 40 reviews + 'Ltd'
    assert lead["contact_channel"] == "email"
    assert lead["pain_point"] == (
        "their current website: it doesn't adjust to a phone screen and it isn't showing as secure in the browser"
    )


def test_no_email_found_corrects_the_channel_sourcing_guessed(monkeypatch):
    page = HOME.replace('<a href="mailto:owner@acme.test">email us</a>', "")
    monkeypatch.setattr(lead_agent, "_fetch_response", _serve({"http://acme.test/": page}))
    lead_id = _sourced_lead()
    lead_agent.research_lead(db.get_lead(lead_id))
    lead = db.get_lead(lead_id)
    assert lead["contact_email"] == "" and lead["contact_channel"] == "phone"
    assert lead["site_score"] == 2 and lead["lead_score"] == 10


def test_a_healthy_site_is_scored_low_so_the_design_gate_parks_it(monkeypatch):
    page = ('<html><head><title>Acme</title><meta name="viewport" content="width=device-width">'
            '<meta name="description" content="Acme Plumbing, Horsham"></head>'
            '<body><p>Acme Plumbing, Horsham. Gas Safe registered heating engineers since 1998.</p>'
            '<a href="mailto:owner@acme.test">email</a><footer>© 2026 Acme</footer></body></html>')
    monkeypatch.setattr(lead_agent, "_fetch_response", _serve({"https://acme.test/": page}))
    lead_id = _sourced_lead(url="https://acme.test/", name="Acme Plumbing", reviews=5)
    lead_agent.research_lead(db.get_lead(lead_id))
    lead = db.get_lead(lead_id)
    assert lead["site_score"] == 10 and lead["site_defects"] == "" and lead["lead_score"] == 1
    assert lead["pain_point"].startswith("Acme Plumbing, Horsham.")  # scraped paragraph kept when nothing is wrong


def test_manual_lead_with_no_site_anywhere_scores_as_no_website(monkeypatch):
    monkeypatch.setattr(lead_agent, "find_business_website", lambda name, location: None)
    lead_id = db.insert_lead("Dave Plumbing", "plumber", "Horsham", status="new")
    db.update_lead_fields(lead_id, phone="01403 111111")
    lead_agent.research_lead(db.get_lead(lead_id))
    lead = db.get_lead(lead_id)
    assert (lead["status"], lead["website_status"], lead["site_score"], lead["contact_channel"], lead["lead_score"]) == (
        "researched", "none", 0, "phone", 10
    )


def test_a_site_found_by_search_makes_the_lead_an_owned_site(monkeypatch):
    monkeypatch.setattr(lead_agent, "find_business_website", lambda name, location: "http://acme.test/")
    monkeypatch.setattr(lead_agent, "_fetch_response", _serve({"http://acme.test/": HOME}))
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Horsham", status="new")
    db.update_lead_fields(lead_id, website_status="none", lead_score=10)
    lead_agent.research_lead(db.get_lead(lead_id))
    lead = db.get_lead(lead_id)
    assert lead["website_status"] == "owned_site" and lead["website_url"] == "http://acme.test/"
    assert lead["site_score"] == 2 and lead["lead_score"] == 8


def test_unreachable_site_leaves_the_score_unknown(monkeypatch):
    monkeypatch.setattr(lead_agent, "_fetch_response", lambda url: None)
    lead_id = _sourced_lead()
    lead_agent.research_lead(db.get_lead(lead_id))
    lead = db.get_lead(lead_id)
    assert lead["status"] == "researched" and lead["site_score"] is None and lead["lead_score"] is None
