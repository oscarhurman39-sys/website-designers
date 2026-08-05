"""Integration test: lead_agent.research_lead persists imported content
(logo/photos/hours/services/reviews/brand colors) on the lead row, all
offline (mocked HTTP, real SQLite via a tmp_path DB) -- same shape as
tests/test_lazy_repo_and_webhook_idempotency.py's DB-backed tests."""
from __future__ import annotations

from unittest.mock import patch

from agents import lead_agent
from utils import content_importer, db

HOMEPAGE = """
<html><head>
<meta name="viewport" content="width=device-width">
<style>.btn { background: #ff6600; }</style>
</head><body>
<img class="logo" src="/img/brand.png">
<a href="mailto:owner@joescafe.example">email us</a>
<a href="tel:0113 496 0000">call</a>
<div class="review">Absolutely fantastic coffee, we go every single week without fail.</div>
<h2>Our Services</h2>
<ul><li>Specialty Coffee</li><li>Fresh Pastries</li></ul>
<img src="/img/shop-front.jpg" width="800" height="600">
<div class="opening-times">Monday - Friday: 8am - 4pm</div>
<p>We have been serving Leeds with great coffee since 1998, and every cup matters to us.</p>
&copy; 2019 Joes Cafe
</body></html>
"""


class FakeResp:
    def __init__(self, text: str, url: str) -> None:
        self.text = text
        self.url = url
        self.content = text.encode()
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass


def test_research_lead_persists_imported_content(monkeypatch, tmp_path):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()

    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    lead = db.get_lead(lead_id)

    with patch.object(lead_agent, "find_business_website", return_value="http://joescafe.example"), \
         patch.object(lead_agent, "_robots_allows", return_value=True), \
         patch.object(lead_agent.requests, "get", lambda *a, **k: FakeResp(HOMEPAGE, "http://joescafe.example")):
        lead_agent._research_lead_impl(lead)

    lead = db.get_lead(lead_id)
    assert lead["status"] == "researched"

    content = content_importer.load_content(lead)
    assert content["logo_url"] == "http://joescafe.example/img/brand.png"
    assert content["photos"] == ["http://joescafe.example/img/shop-front.jpg"]
    assert content["hours"] == ["Monday - Friday: 8am - 4pm"]
    assert content["services"] == ["Specialty Coffee", "Fresh Pastries"]
    assert content["reviews"] == ["Absolutely fantastic coffee, we go every single week without fail."]
    assert content["brand_colors"] == ["#ff6600"]
    # The pre-existing singular `testimonial` column still gets the first
    # review, unchanged -- sales_agent.py's cold email copy still works.
    assert lead["testimonial"] == content["reviews"][0]


def test_research_lead_with_thin_site_leaves_content_empty(monkeypatch, tmp_path):
    """A lead whose site has none of this content must still research
    successfully -- content import is additive, never a blocker."""
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()

    lead_id = db.insert_lead("Thin Site Co", "plumber", "Leeds")
    lead = db.get_lead(lead_id)
    thin_html = "<html><body><a href='mailto:owner@thinsite.example'>email</a></body></html>"

    with patch.object(lead_agent, "find_business_website", return_value="http://thinsite.example"), \
         patch.object(lead_agent, "_robots_allows", return_value=True), \
         patch.object(lead_agent.requests, "get", lambda *a, **k: FakeResp(thin_html, "http://thinsite.example")):
        lead_agent._research_lead_impl(lead)

    lead = db.get_lead(lead_id)
    assert lead["status"] == "researched"
    assert content_importer.load_content(lead) == {
        "logo_url": "", "photos": [], "hours": [], "services": [], "reviews": [], "brand_colors": [],
    }
