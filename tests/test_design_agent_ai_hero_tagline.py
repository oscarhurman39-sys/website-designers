"""Tests for design_agent.py's AI-generated, per-lead hero tagline --
hero_tagline() prefers a model-drafted line grounded in this specific
lead's own facts over the fixed NICHE_HERO_TEXT template every lead in a
niche otherwise shares. All offline: HF is mocked, never actually called."""
from __future__ import annotations

from unittest.mock import Mock

from agents import design_agent


def _lead(**extra) -> dict:
    lead = {
        "id": 1, "business_name": "Joes Cafe", "niche": "cafe", "location": "Leeds",
        "google_maps_types": [],
    }
    lead.update(extra)
    return lead


# --- Fallback (no HF configured / call fails) ------------------------------

def test_hero_tagline_uses_template_without_hf_token(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "")
    assert design_agent.hero_tagline(_lead()) == "Your daily cup in Leeds"


def test_hero_tagline_falls_back_to_template_on_hf_failure(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "fake-token")
    monkeypatch.setattr(design_agent, "_hf_client", Mock(side_effect=RuntimeError("HF is down")))
    assert design_agent.hero_tagline(_lead()) == "Your daily cup in Leeds"


def test_hero_tagline_falls_back_for_niche_without_template(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "")
    lead = _lead(niche="florist", location="York")
    assert design_agent.hero_tagline(lead) == design_agent.DEFAULT_HERO_TEXT.format(city="York")


# --- AI path (HF configured and returns a usable line) ---------------------

def _mock_hf(raw_response: str) -> Mock:
    client = Mock()
    client.text_generation = Mock(return_value=raw_response)
    return Mock(return_value=client)


def test_hero_tagline_uses_ai_output_when_configured(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "fake-token")
    monkeypatch.setattr(design_agent, "_hf_client", _mock_hf("Leeds' favorite flat white"))
    assert design_agent.hero_tagline(_lead()) == "Leeds' favorite flat white"


def test_hero_tagline_strips_quotes_and_whitespace_from_ai_output(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "fake-token")
    monkeypatch.setattr(design_agent, "_hf_client", _mock_hf('  "Leeds\' favorite flat white"  \n'))
    assert design_agent.hero_tagline(_lead()) == "Leeds' favorite flat white"


def test_hero_tagline_ai_falls_back_on_empty_response(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "fake-token")
    monkeypatch.setattr(design_agent, "_hf_client", _mock_hf("   "))
    assert design_agent.hero_tagline(_lead()) == "Your daily cup in Leeds"


def test_hero_tagline_ai_falls_back_when_response_too_long(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "fake-token")
    rambling = "This is a way too long hero tagline that ignores the word limit entirely"
    monkeypatch.setattr(design_agent, "_hf_client", _mock_hf(rambling))
    assert design_agent.hero_tagline(_lead()) == "Your daily cup in Leeds"


def test_hero_tagline_ai_only_uses_first_line(monkeypatch):
    monkeypatch.setattr(design_agent.config, "HF_API_TOKEN", "fake-token")
    monkeypatch.setattr(design_agent, "_hf_client", _mock_hf("Leeds' favorite flat white\nExtra rambling line"))
    assert design_agent.hero_tagline(_lead()) == "Leeds' favorite flat white"


# --- Facts / prompt grounding ------------------------------------------------

def test_hero_tagline_facts_include_real_services_and_specialty():
    lead = _lead(google_maps_types=["organic_coffee"])
    facts = design_agent._hero_tagline_facts(lead)
    assert any("Joes Cafe" in f for f in facts)
    assert any("Specialty: Organic Coffee" in f for f in facts)
    assert any("Services:" in f for f in facts)


def test_parse_hero_tagline_rejects_empty_and_overlong():
    assert design_agent._parse_hero_tagline("") is None
    assert design_agent._parse_hero_tagline("   \n  ") is None
    assert design_agent._parse_hero_tagline("one two three four five six seven eight nine ten eleven") is None


def test_parse_hero_tagline_accepts_clean_short_line():
    assert design_agent._parse_hero_tagline("Beautiful gardens, built for York") == "Beautiful gardens, built for York"
