"""utils/scoring.py: one number that orders the build and send queues."""
from __future__ import annotations

import pytest

import config
from agents import sourcing_agent
from utils import scoring


def test_no_site_and_platform_only_keep_their_sourcing_scores():
    assert scoring.lead_score("none", None, "email") == 10
    assert scoring.lead_score("none", None, "unknown") == 8
    assert scoring.lead_score("platform_only", None, "phone") == 9
    assert scoring.lead_score("platform_only", None, "unknown") == 7
    # sourcing_agent delegates here, so a Places candidate scores exactly as before
    assert sourcing_agent.lead_score_for_candidate("none", "phone") == 10
    assert sourcing_agent.lead_score_for_candidate("platform_only", "unknown") == 7
    assert sourcing_agent.lead_score_for_candidate("owned_site", "email") is None


def test_owned_site_is_unscored_until_its_homepage_is_audited():
    assert scoring.lead_score("owned_site", None, "email") is None
    assert scoring.is_good_site("owned_site", None) is False


@pytest.mark.parametrize("site_score,expected", [(0, 8), (3, 8), (4, 6), (5, 6), (6, 4), (7, 4), (8, 1), (10, 1)])
def test_the_weaker_the_existing_site_the_higher_the_lead_scores(site_score, expected, monkeypatch):
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    assert scoring.lead_score("owned_site", site_score, "email") == expected


def test_modifiers_reviews_limited_company_and_unknown_contact(monkeypatch):
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    assert scoring.lead_score("owned_site", 2, "email", review_count=25) == 9
    assert scoring.lead_score("owned_site", 2, "email", review_count=25, business_name="Acme Ltd") == 10
    assert scoring.lead_score("owned_site", 2, "unknown") == 6
    assert scoring.lead_score("owned_site", 9, "unknown") == 0  # never below zero


def test_good_site_threshold_is_configurable_and_zero_turns_the_gate_off(monkeypatch):
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 6)
    assert scoring.is_good_site("owned_site", 6) is True
    assert scoring.lead_score("owned_site", 6, "email") == 1
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 0)
    assert scoring.is_good_site("owned_site", 10) is False
    assert scoring.lead_score("owned_site", 10, "email") == 4


def test_only_owned_sites_are_ever_good():
    assert scoring.is_good_site("none", 10) is False
    assert scoring.is_good_site("platform_only", 10) is False


def test_limited_company_detection_is_word_bounded():
    assert scoring.looks_like_limited_company("Acme Plumbing Ltd")
    assert scoring.looks_like_limited_company("Acme Plumbing Ltd.")
    assert scoring.looks_like_limited_company("Acme Limited")
    assert scoring.looks_like_limited_company("Acme LLP")
    assert not scoring.looks_like_limited_company("Dave's Plumbing")
    assert not scoring.looks_like_limited_company("Unlimited Gym")
    assert not scoring.looks_like_limited_company(None)


def test_score_from_a_row_infers_the_channel_from_the_contact_email(monkeypatch):
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    row = {"website_status": "owned_site", "site_score": 3, "contact_email": "o@x.test",
           "google_reviews_count": 3, "business_name": "X"}
    assert scoring.lead_score_for_row(row) == 8
    row["contact_email"] = ""
    assert scoring.lead_score_for_row(row) == 6


def test_default_backlog_cap_is_two_days_of_sends(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_BACKLOG_MAX", 0)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 15)
    assert config.design_backlog_cap() == 30
    monkeypatch.setattr(config, "DESIGN_BACKLOG_MAX", 7)
    assert config.design_backlog_cap() == 7
