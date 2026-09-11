import pytest

from agents import design_agent


ALL_NICHES = tuple(design_agent.NICHE_THEMES) + ("picture-framer",)


def _lead(niche: str) -> dict:
    return {
        "id": 9000,
        "business_name": "Example Business",
        "niche": niche,
        "location": "Crawley, West Sussex",
        "phone": "01293 555 010",
        "address": "1 High Street, Crawley",
        "contact_email": "hello@example.test",
        "google_rating": 4.8,
        "google_reviews_count": 24,
        "testimonial": "A real customer said this.",
        "google_maps_types": ["emergency_service", "family_owned"],
    }


@pytest.mark.parametrize("niche", ALL_NICHES)
def test_modern_preview_does_not_render_inferred_claims(niche, monkeypatch):
    monkeypatch.setattr(design_agent.assets, "logo", lambda lead_id: None)
    monkeypatch.setattr(design_agent.assets, "photos", lambda lead_id: [])
    context = design_agent.build_context(_lead(niche))
    rendered = design_agent.render_template_files(niche, context)["index.html"].lower()

    unsafe_claims = (
        "certified", "insured", "insurance", "available", "guarantee",
        "reliable", "specialist", "done properly", "priced fairly",
    )
    copy_fields = " ".join(
        [context["hero_tagline"], context["about"], context["trust_line"]]
        + [item["blurb"] for item in context["service_items"]]
    ).lower()
    assert not any(term in copy_fields for term in unsafe_claims)
    if context["service_items"]:
        assert "ask about" in rendered
    assert "contact example business to confirm" not in rendered


def test_modern_context_preserves_real_fields_and_neutralises_copy(monkeypatch):
    monkeypatch.setattr(design_agent.assets, "logo", lambda lead_id: None)
    monkeypatch.setattr(design_agent.assets, "photos", lambda lead_id: [])
    context = design_agent.build_context(_lead("plumber"))

    assert context["business_name"] == "Example Business"
    assert context["phone"] == "01293 555 010"
    assert context["address"] == "1 High Street, Crawley"
    assert context["google_rating"] == 4.8
    assert context["testimonial"] == "A real customer said this."
    assert context["hero_tagline"] == "Example Business | Plumbing & Heating in Crawley"
    assert context["trust_line"] == "Contact the business directly"
    assert context["specialty"] is None
    assert all(item["blurb"].startswith("Ask about ") for item in context["service_items"])
