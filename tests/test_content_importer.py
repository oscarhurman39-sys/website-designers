"""Tests for utils/content_importer.py: logo/photo/hours/services/reviews/
brand-color extraction from a lead's own site, all offline (BeautifulSoup
parsing only, no network)."""
from __future__ import annotations

import json

from bs4 import BeautifulSoup

from utils import content_importer

BASE_URL = "https://joescafe.example"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# --- Logo --------------------------------------------------------------------

def test_extract_logo_prefers_img_with_logo_hint():
    html = """
    <header><img src="/img/hero-banner.jpg"><img class="site-logo" src="/img/brand.png"></header>
    """
    assert content_importer.extract_logo_url(_soup(html), BASE_URL) == f"{BASE_URL}/img/brand.png"


def test_extract_logo_falls_back_to_apple_touch_icon():
    html = '<link rel="apple-touch-icon" href="/icons/apple.png">'
    assert content_importer.extract_logo_url(_soup(html), BASE_URL) == f"{BASE_URL}/icons/apple.png"


def test_extract_logo_none_when_nothing_found():
    assert content_importer.extract_logo_url(_soup("<p>hello</p>"), BASE_URL) is None


# --- Photos ------------------------------------------------------------------

def test_extract_photos_skips_icons_and_data_uris_and_dedupes():
    html = """
    <img src="/img/shop-front.jpg" width="800" height="600">
    <img src="/img/shop-front.jpg" width="800" height="600">
    <img src="/icons/menu-icon.png" width="24" height="24">
    <img src="data:image/png;base64,AAAA">
    <img class="logo" src="/img/brand.png">
    <img src="/img/tiny.jpg" width="20" height="20">
    """
    photos = content_importer.extract_photos(_soup(html), BASE_URL)
    assert photos == [f"{BASE_URL}/img/shop-front.jpg"]


def test_extract_photos_respects_cap():
    imgs = "".join(f'<img src="/img/photo{i}.jpg">' for i in range(content_importer.MAX_PHOTOS + 5))
    photos = content_importer.extract_photos(_soup(imgs), BASE_URL)
    assert len(photos) == content_importer.MAX_PHOTOS


# --- Reviews -------------------------------------------------------------------

def test_extract_reviews_collects_multiple_and_dedupes():
    html = """
    <div class="review">Absolutely fantastic service, will be back again soon for sure.</div>
    <div class="testimonial">The best coffee in town, staff are always so friendly and warm.</div>
    <div class="review">Absolutely fantastic service, will be back again soon for sure.</div>
    <div class="review">short</div>
    """
    reviews = content_importer.extract_reviews(_soup(html))
    assert len(reviews) == 2
    assert any("fantastic service" in r for r in reviews)
    assert any("best coffee" in r for r in reviews)


# --- Services ------------------------------------------------------------------

def test_extract_services_from_heading_and_list():
    html = """
    <h2>Our Services</h2>
    <ul><li>MOT Testing</li><li>Brake Repairs</li><li></li></ul>
    <h2>Unrelated</h2>
    <ul><li>Should not be picked up</li></ul>
    """
    services = content_importer.extract_services(_soup(html))
    assert services == ["MOT Testing", "Brake Repairs"]


def test_extract_services_empty_without_matching_heading():
    html = "<h2>About Us</h2><ul><li>Not a service list</li></ul>"
    assert content_importer.extract_services(_soup(html)) == []


# --- Hours ---------------------------------------------------------------------

def test_extract_hours_from_json_ld_shorthand():
    html = """
    <script type="application/ld+json">
    {"@type": "LocalBusiness", "openingHours": ["Mo-Fr 09:00-17:00", "Sa 10:00-14:00"]}
    </script>
    """
    assert content_importer.extract_hours(_soup(html)) == ["Mo-Fr 09:00-17:00", "Sa 10:00-14:00"]


def test_extract_hours_from_json_ld_structured():
    spec = [
        {"dayOfWeek": ["Monday", "Tuesday"], "opens": "09:00", "closes": "17:00"},
        {"dayOfWeek": "https://schema.org/Saturday", "opens": "10:00", "closes": "14:00"},
    ]
    html = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "LocalBusiness", "openingHoursSpecification": spec})
        + "</script>"
    )
    hours = content_importer.extract_hours(_soup(html))
    assert hours == ["Mon/Tue: 09:00 - 17:00", "Sat: 10:00 - 14:00"]


def test_extract_hours_falls_back_to_text_scan():
    html = "<div class='opening-times'>Monday - Friday: 9am - 5pm</div><p>Some other paragraph.</p>"
    hours = content_importer.extract_hours(_soup(html))
    assert hours == ["Monday - Friday: 9am - 5pm"]


def test_extract_hours_empty_when_nothing_found():
    assert content_importer.extract_hours(_soup("<p>No hours here at all.</p>")) == []


# --- Brand colors --------------------------------------------------------------

def test_extract_brand_colors_ranks_by_frequency_and_skips_neutrals():
    html = """
    <style>
      .btn { color: #ffffff; background: #ff6600; }
      .hero { background: #ff6600; border: 1px solid #333333; }
      .link { color: #ff6600; }
    </style>
    <div style="color: #0044cc;"></div>
    """
    colors = content_importer.extract_brand_colors(_soup(html))
    assert colors[0] == "#ff6600"
    assert "#ffffff" not in colors
    assert "#333333" not in colors


def test_extract_brand_colors_empty_when_only_neutrals():
    html = "<style>.a { color: #fff; } .b { color: #333; }</style>"
    assert content_importer.extract_brand_colors(_soup(html)) == []


# --- Merge / serialize / load round-trip ----------------------------------------

def test_merge_content_keeps_first_scalar_and_extends_lists():
    primary = {
        "logo_url": None, "photos": ["a.jpg"], "hours": [], "services": ["Haircuts"],
        "reviews": [], "brand_colors": ["#ff6600"],
    }
    addition = {
        "logo_url": "logo.png", "photos": ["b.jpg", "a.jpg"], "hours": ["Mon: 9-5"],
        "services": ["Colouring"], "reviews": ["Great service!"], "brand_colors": ["#00cc44"],
    }
    merged = content_importer.merge_content(primary, addition)
    assert merged["logo_url"] == "logo.png"  # primary had none, addition fills it in
    assert merged["photos"] == ["a.jpg", "b.jpg"]  # deduped, order preserved
    assert merged["hours"] == ["Mon: 9-5"]
    assert merged["services"] == ["Haircuts", "Colouring"]
    assert merged["brand_colors"] == ["#ff6600", "#00cc44"]


def test_serialize_and_load_round_trip():
    content = {
        "logo_url": "https://example.com/logo.png",
        "photos": ["https://example.com/1.jpg"],
        "hours": ["Mon-Fri: 9-5"],
        "services": ["Haircuts", "Colouring"],
        "reviews": ["Loved it!"],
        "brand_colors": ["#ff6600"],
    }
    lead = {"id": 1, "business_name": "Test"}
    lead.update(content_importer.serialize_for_db(content))
    loaded = content_importer.load_content(lead)
    assert loaded == content


def test_load_content_defaults_when_nothing_scraped():
    loaded = content_importer.load_content({"id": 1, "business_name": "Empty Co"})
    assert loaded == {
        "logo_url": "", "photos": [], "hours": [], "services": [], "reviews": [], "brand_colors": [],
    }


def test_load_content_ignores_corrupt_json():
    lead = {"photos": "{not json", "hours": "null", "scraped_services": "[1, 2]"}
    loaded = content_importer.load_content(lead)
    assert loaded["photos"] == []
    assert loaded["hours"] == []  # valid JSON but not a list
    assert loaded["services"] == [1, 2]  # still a list, even if items aren't the expected type
