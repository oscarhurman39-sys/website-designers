from __future__ import annotations

import base64
from io import BytesIO
from unittest.mock import Mock

from PIL import Image

from agents import design_agent
from utils import visuals


def _lead(niche: str = "gardener") -> dict:
    return {
        "id": 41,
        "business_name": "Example & Sons",
        "niche": niche,
        "location": "Leeds",
        "phone": "0113 123 4567",
        "testimonial": "",
        "google_rating": None,
        "google_reviews_count": 0,
        "google_maps_types": [],
    }


def test_gardener_direction_uses_botanical_macro_not_fake_work():
    prompt = visuals.image_prompt(_lead("gardener")).lower()
    negative = visuals.negative_prompt().lower()

    assert "macro close-up" in prompt
    assert "no visible garden" in prompt
    assert "finished project" in negative
    assert "hedge" in negative
    assert visuals.normalized_niche("gardening") == "landscaper"


def test_mechanic_direction_uses_parts_not_a_vehicle_or_garage():
    prompt = visuals.image_prompt(_lead("mechanic")).lower()
    negative = visuals.negative_prompt().lower()

    assert "nuts, bolts" in prompt
    assert "no vehicle" in prompt
    assert "garage interior" in negative
    assert visuals.normalized_niche("car repair") == "vehicle-repair"


def test_unknown_trade_keeps_universal_fake_portfolio_guardrail():
    prompt = visuals.image_prompt(_lead("window-cleaner")).lower()

    assert "materials" in prompt
    assert "never documentary evidence" in prompt
    assert "no text, logo or watermark" in prompt


def test_context_never_invents_testimonial_or_trust_claim(monkeypatch):
    monkeypatch.setattr(visuals, "hero_image_data_uri", Mock(return_value=""))

    context = design_agent.build_context(_lead("plasterer"))

    assert context["testimonial"] == ""
    assert context["hero_headline"] == "Example & Sons"
    assert "Trusted" not in context["hero_headline"]
    assert context["hero_image_url"] == ""


def test_studio_template_uses_art_direction_without_stock_urls(monkeypatch):
    monkeypatch.setattr(visuals, "hero_image_data_uri", Mock(return_value=""))
    context = design_agent.build_context(_lead("mechanic"))

    files = design_agent.render_template_files("mechanic", context)

    assert "Example &amp; Sons" in files["index.html"]
    assert "hero--fallback" in files["index.html"]
    assert "picsum.photos" not in files["index.html"]
    assert "unsplash" not in files["index.html"].lower()
    assert "Trusted Local" not in files["index.html"]
    assert "Customer feedback" not in files["index.html"]
    assert "HERO IMAGE BRIEF" in files["ART-DIRECTION.txt"]
    assert "fake portfolio" in files["ART-DIRECTION.txt"]


def test_ai_image_is_cached_and_returned_as_data_uri(monkeypatch, tmp_path):
    fake_image = Image.new("RGB", (64, 36), "green")
    client = Mock()
    client.text_to_image.return_value = fake_image
    client_factory = Mock(return_value=client)

    monkeypatch.setattr(visuals, "InferenceClient", client_factory)
    monkeypatch.setattr(visuals.config, "ENABLE_AI_IMAGES", True)
    monkeypatch.setattr(visuals.config, "HF_API_TOKEN", "test-token")
    monkeypatch.setattr(visuals.config, "GENERATED_ASSETS_DIR", str(tmp_path))

    first = visuals.hero_image_data_uri(_lead("gardener"))
    second = visuals.hero_image_data_uri(_lead("gardener"))

    assert first.startswith("data:image/jpeg;base64,")
    assert second == first
    assert Image.open(BytesIO(base64.b64decode(first.split(",", 1)[1]))).size == (64, 36)
    client.text_to_image.assert_called_once()
