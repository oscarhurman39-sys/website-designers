"""The modern template must never print a placeholder: every optional fact
is None when unknown and the template hides the element. These tests pin
that contract and the theme routing."""
from __future__ import annotations

import json

import pytest

import config
from agents import design_agent

_PLACEHOLDERS = ("YOUR LOGO", "Call Call", "coming soon", "will sit right here", "Your Offer", "None")


def _lead(**overrides) -> dict:
    base = {"id": 4242, "business_name": "Acme Plumbing", "niche": "plumber", "location": "Oxted, Surrey"}
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _modern(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")


def test_unknown_facts_are_none_not_placeholders():
    ctx = design_agent.build_context(_lead())
    assert ctx["phone"] is None and ctx["phone_href"] is None
    assert ctx["address"] is None and ctx["map_embed_url"] is None
    assert ctx["google_rating"] is None and ctx["testimonial"] is None
    assert ctx["city"] == "Oxted"


def test_rating_needs_enough_reviews():
    few = design_agent.build_context(_lead(google_rating=5.0, google_reviews_count=3))
    enough = design_agent.build_context(_lead(google_rating=4.8, google_reviews_count=25))
    assert few["google_rating"] is None
    assert enough["google_rating"] == 4.8 and enough["google_reviews_count"] == 25
    assert "google.com/maps" in enough["google_reviews_url"]


def test_places_facts_fall_back_to_notes_blob():
    notes = json.dumps({"source": "google_places", "rating": 4.9, "review_count": 40, "address": "1 High St, Oxted"})
    ctx = design_agent.build_context(_lead(notes=notes))
    assert ctx["google_rating"] == 4.9
    assert ctx["address"] == "1 High St, Oxted"
    assert "output=embed" in ctx["map_embed_url"]


def test_phone_href_strips_formatting():
    ctx = design_agent.build_context(_lead(phone="01883 712 714"))
    assert ctx["phone_href"] == "01883712714"
    assert design_agent.build_context(_lead(phone="+44 1883 712714"))["phone_href"] == "+441883712714"


def test_short_or_missing_testimonial_is_dropped():
    assert design_agent.build_context(_lead(testimonial="Great!"))["testimonial"] is None
    real = "Fixed our boiler on a Sunday and charged a fair price. Highly recommend."
    assert design_agent.build_context(_lead(testimonial=real))["testimonial"] == real


def test_nav_only_links_to_sections_that_render():
    bare = design_agent.build_context(_lead(niche="picture-framer"))
    assert [a for _, a in bare["nav_links"]] == ["about", "contact"]
    rated = design_agent.build_context(_lead(google_rating=4.7, google_reviews_count=50))
    assert [a for _, a in rated["nav_links"]] == ["services", "about", "reviews", "contact"]


@pytest.mark.parametrize("niche", sorted(design_agent.NICHE_THEMES) + ["picture-framer"])
def test_modern_render_has_no_placeholder_text(niche):
    ctx = design_agent.build_context(_lead(niche=niche))
    files = design_agent.render_template_files(niche, ctx)
    html = files["index.html"]
    for bad in _PLACEHOLDERS:
        assert bad not in html, f"{niche}: found {bad!r}"
    assert "images.unsplash.com" in html
    assert 'href="tel:' not in html  # no phone -> no call buttons at all


def test_modern_render_uses_real_facts():
    lead = _lead(phone="01883 712714", address="12 Station Road West, Oxted", google_rating=5.0,
                 google_reviews_count=359, contact_email="info@acme.test")
    html = design_agent.render_template_files("plumber", design_agent.build_context(lead))["index.html"]
    assert 'href="tel:01883712714"' in html
    assert "359" in html and "5.0" in html
    assert "maps?q=12+Station+Road+West" in html
    assert "mailto:info@acme.test" in html


def test_legacy_style_still_renders_per_niche_folder(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "legacy")
    html = design_agent.render_template_files("plumber", design_agent.build_context(_lead()))["index.html"]
    assert "Plumbing" in html and "Call us" in html  # legacy template's own phone prompt
