"""LeadAgent should look beyond the first obvious subpage for contact emails,
without scraping privacy/no-reply addresses or fetching unbounded pages."""
from __future__ import annotations

import pytest

import config
from agents import lead_agent
from utils import db


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


def _lead(url: str = "https://acme.test/") -> int:
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Horsham", status="new")
    db.update_lead_fields(
        lead_id,
        website_url=url,
        website_status="owned_site",
        contact_channel="email",
        phone="01403 000000",
        google_reviews_count=22,
    )
    return lead_id


def _home_with_links(*hrefs: str) -> str:
    links = "".join(f'<a href="{href}">{href}</a>' for href in hrefs)
    return (
        '<html><head><title>Acme Plumbing</title><meta name="viewport" content="width=device-width"></head>'
        f'<body><p>Acme Plumbing serves Horsham homes and businesses.</p>{links}'
        '<footer>© 2026 Acme Plumbing</footer></body></html>'
    )


def test_research_checks_later_contact_candidates_for_email(monkeypatch):
    pages = {
        "https://acme.test/": _home_with_links("/about", "/services", "/book-a-call", "/contact-us"),
        "https://acme.test/about": "<html><body><p>About Acme Plumbing, but no email here.</p></body></html>",
        "https://acme.test/book-a-call": "<html><body>Book Acme Plumbing by email: bookings@acme.test</body></html>",
    }
    fetched: list[str] = []

    def fake_fetch(url: str):
        fetched.append(url)
        return _Resp(url, pages[url]) if url in pages else None

    monkeypatch.setattr(lead_agent, "_fetch_response", fake_fetch)

    lead_agent.research_lead(db.get_lead(_lead()))

    lead = db.get_lead(1)
    assert lead["contact_email"] == "bookings@acme.test"
    assert "https://acme.test/about" in fetched
    assert "https://acme.test/book-a-call" in fetched


def test_candidate_subpage_fetches_are_capped_at_eight(monkeypatch):
    hrefs = [f"/contact-{i}" for i in range(10)]
    pages = {"https://acme.test/": _home_with_links(*hrefs)}
    for i in range(10):
        pages[f"https://acme.test/contact-{i}"] = "<html><body>No email here.</body></html>"
    fetched: list[str] = []

    def fake_fetch(url: str):
        fetched.append(url)
        return _Resp(url, pages[url]) if url in pages else None

    monkeypatch.setattr(lead_agent, "_fetch_response", fake_fetch)

    lead_agent.research_lead(db.get_lead(_lead()))

    subpage_fetches = [url for url in fetched if url != "https://acme.test/"]
    assert subpage_fetches == [f"https://acme.test/contact-{i}" for i in range(8)]
    assert db.get_lead(1)["contact_email"] == ""


def test_privacy_and_no_reply_addresses_are_not_used(monkeypatch):
    pages = {
        "https://acme.test/": _home_with_links("/privacy-policy", "/contact"),
        "https://acme.test/privacy-policy": "<html><body>Privacy: acmeplumbing@gmail.com</body></html>",
        "https://acme.test/contact": "<html><body><a href='mailto:no-reply@acme.test'>email</a></body></html>",
    }
    fetched: list[str] = []

    def fake_fetch(url: str):
        fetched.append(url)
        return _Resp(url, pages[url]) if url in pages else None

    monkeypatch.setattr(lead_agent, "_fetch_response", fake_fetch)

    lead_agent.research_lead(db.get_lead(_lead()))

    assert "https://acme.test/privacy-policy" not in fetched
    assert db.get_lead(1)["contact_email"] == ""
    assert db.get_lead(1)["contact_channel"] == "phone"
