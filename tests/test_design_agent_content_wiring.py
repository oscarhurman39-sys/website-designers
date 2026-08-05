"""Tests that design_agent.py's build_context() (and the helpers it calls)
prefer real content imported from the lead's own site over generic niche
fallbacks/stock photos/a fabricated testimonial -- see
utils/content_importer.py. All offline, no network, no DB."""
from __future__ import annotations

from agents import design_agent
from utils import content_importer


def _lead(**extra) -> dict:
    lead = {
        "id": 1, "business_name": "Joes Cafe", "niche": "cafe", "location": "Leeds",
        "phone": "0113 496 0000", "pain_point": "", "testimonial": "", "google_maps_types": [],
    }
    lead.update(extra)
    return lead


def _with_content(lead: dict, **content) -> dict:
    base = {"logo_url": None, "photos": [], "hours": [], "services": [], "reviews": [], "brand_colors": []}
    base.update(content)
    lead.update(content_importer.serialize_for_db(base))
    return lead


# --- niche_services -------------------------------------------------------------

def test_niche_services_prefers_scraped_over_curated():
    lead = _with_content(_lead(), services=["Flat White", "Cold Brew"])
    assert design_agent.niche_services(lead) == ["Flat White", "Cold Brew"]


def test_niche_services_falls_back_to_curated_list_without_scraped():
    assert design_agent.niche_services(_lead()) == design_agent.NICHE_SERVICES["cafe"]


def test_niche_services_falls_back_to_niche_name_as_last_resort():
    lead = _lead(niche="florist", google_maps_types=[])  # no curated list, no Google types
    assert design_agent.niche_services(lead) == ["Florist"]


# --- get_hero_image_url ----------------------------------------------------------

def test_hero_image_prefers_real_scraped_photo():
    lead = _with_content(_lead(), photos=["https://joescafe.example/shop.jpg"])
    assert design_agent.get_hero_image_url(lead) == "https://joescafe.example/shop.jpg"


def test_hero_image_falls_back_to_placeholder_without_real_photo(monkeypatch):
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    url = design_agent.get_hero_image_url(_lead())
    assert url.startswith(design_agent._PLACEHOLDER_IMAGE_BASE)


# --- build_context ----------------------------------------------------------------

def test_build_context_surfaces_imported_content():
    lead = _with_content(
        _lead(),
        logo_url="https://joescafe.example/logo.png",
        photos=["https://joescafe.example/shop.jpg"],
        hours=["Mon-Fri: 9-5"],
        brand_colors=["#ff6600"],
        reviews=["Best coffee in town!"],
    )
    context = design_agent.build_context(lead)
    assert context["logo_url"] == "https://joescafe.example/logo.png"
    assert context["photos"] == ["https://joescafe.example/shop.jpg"]
    assert context["hours"] == ["Mon-Fri: 9-5"]
    assert context["brand_colors"] == ["#ff6600"]
    assert context["reviews"] == ["Best coffee in town!"]
    assert context["hero_image_url"] == "https://joescafe.example/shop.jpg"


def test_build_context_defaults_are_falsy_without_imported_content(monkeypatch):
    monkeypatch.setattr(design_agent.config, "UNSPLASH_ACCESS_KEY", "")
    context = design_agent.build_context(_lead())
    assert context["logo_url"] == ""
    assert context["photos"] == []
    assert context["hours"] == []
    assert context["brand_colors"] == []
    # No real reviews scraped -- falls back to the single testimonial
    # (possibly the fabricated one), never an empty list.
    assert context["reviews"] == [design_agent._testimonial(_lead())]


def test_build_context_reviews_prefer_real_reviews_over_fabricated_testimonial():
    lead = _with_content(_lead(), reviews=["Real review one.", "Real review two."])
    context = design_agent.build_context(lead)
    assert context["reviews"] == ["Real review one.", "Real review two."]
