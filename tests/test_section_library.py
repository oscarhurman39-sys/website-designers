"""Tests for the shared section library (templates/_shared/sections.html
+ design_agent.py's NICHE_SECTIONS manifest) -- offline template rendering
only, no network/DB."""
from __future__ import annotations

from agents import design_agent
from utils import content_importer


def _lead(niche: str, **content) -> dict:
    lead = {
        "id": 99, "business_name": "Joes Cafe", "niche": niche, "location": "Leeds",
        "phone": "0113 496 0000", "pain_point": "", "testimonial": "", "google_maps_types": [],
    }
    if content:
        lead.update(content_importer.serialize_for_db(content))
    return lead


RICH_CONTENT = {
    "logo_url": "https://joescafe.example/logo.png",
    "photos": ["https://joescafe.example/shop1.jpg", "https://joescafe.example/shop2.jpg"],
    "hours": ["Mon-Fri: 9am - 5pm"],
    "services": ["Flat White", "Cold Brew"],
    "reviews": ["Best coffee in Leeds!"],
    "brand_colors": ["#ff6600"],
}


def _render(niche: str, **content) -> str:
    lead = _lead(niche, **content)
    return design_agent.render_template_files(niche, design_agent.build_context(lead))["index.html"]


# --- manifest / niche listing ---------------------------------------------------

def test_shared_partials_dir_is_not_a_selectable_niche():
    assert "_shared" not in design_agent.available_niches()


def test_enabled_sections_matches_manifest():
    assert design_agent.enabled_sections("landscaper") == ("photo_gallery", "hours_list")
    assert design_agent.enabled_sections("cafe") == ("hours_list",)
    assert design_agent.enabled_sections("plumber") == ("service_area_emergency", "hours_list")
    assert design_agent.enabled_sections("dentist") == ()  # not in the manifest -- no extras


def test_every_niche_still_renders_with_no_imported_content():
    for niche in sorted(design_agent.available_niches()):
        html = _render(niche)
        assert "<html" in html.lower()


# --- landscaper: photo gallery ---------------------------------------------------

def test_landscaper_shows_real_photo_gallery_when_photos_found():
    html = _render("landscaper", **RICH_CONTENT)
    assert 'id="gallery"' in html
    assert 'src="https://joescafe.example/shop1.jpg"' in html
    assert 'href="#gallery"' in html  # nav link
    assert "Flat White" in html and "Cold Brew" in html  # real services, not curated
    assert 'id="services"' in html  # nav anchor preserved despite using the shared macro
    assert "text-emerald-800" in html  # niche accent color preserved


def test_landscaper_hides_gallery_without_real_photos():
    html = _render("landscaper")
    assert 'id="gallery"' not in html
    assert 'href="#gallery"' not in html


# --- cafe: menu framing + hours ---------------------------------------------------

def test_cafe_renders_menu_with_real_items_and_hours():
    html = _render("cafe", **RICH_CONTENT)
    assert 'id="menu"' in html
    assert 'href="#menu"' in html
    assert "Flat White" in html and "Cold Brew" in html
    assert "Mon-Fri: 9am - 5pm" in html
    assert 'href="#hours"' in html
    assert "services_list" not in html  # the old dead placeholder key is gone


def test_cafe_hides_hours_without_real_hours():
    html = _render("cafe")
    assert 'id="hours"' not in html
    assert 'href="#hours"' not in html


# --- plumber: service area + emergency badge --------------------------------------

def test_plumber_always_shows_emergency_badge():
    """service_area_emergency is a niche-level assumption (same precedent
    as NICHE_SERVICES["plumber"] already listing "Emergency Callouts" for
    every plumber lead) -- not gated on scraped content."""
    html = _render("plumber")
    assert "24/7 Emergency Callouts" in html
    assert 'href="#service-area"' in html and "Areas Covered" in html


def test_plumber_shows_hours_only_with_real_hours():
    assert 'id="hours"' not in _render("plumber")
    assert 'id="hours"' in _render("plumber", **RICH_CONTENT)
