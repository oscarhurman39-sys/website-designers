"""The username parsed from a paying customer's reply is invited to their
repo as an admin and the customer is told the site is theirs. Before this,
"GitHub: none - never used it" stored the username "none" and the handover
invited that account; "I don't use GitHub - is there another way?" stored
"is". A name is now only stored when it parses as a real handle AND GitHub
confirms the account exists; everything else goes to a human.
"""
from __future__ import annotations

import pytest

import config
from agents import sales_agent
from utils import db


@pytest.mark.parametrize("reply", [
    "I don't use GitHub - is there another way to get the files?",
    "GitHub: none - never used it",
    "github username: n/a",
    "GitHub: I don't have an account yet",
    "I don't have a GitHub account, can you just email the files?",
    "My github is not set up yet",
    "What's GitHub? github.com/yourname means nothing to me",
])
def test_ordinary_english_is_not_a_username(reply):
    assert sales_agent.extract_github_username(reply) is None


@pytest.mark.parametrize("reply, expected", [
    ("My github is oscar-hurman", "oscar-hurman"),
    ("github.com/realuser99 thanks", "realuser99"),
    ("GitHub username: acme-plumbing-2026", "acme-plumbing-2026"),
])
def test_real_handles_are_still_parsed(reply, expected):
    assert sales_agent.extract_github_username(reply) == expected


@pytest.fixture
def won_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    db.init_db()
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Maidstone, Kent", status="won")
    return db.get_lead(lead_id)


def test_a_name_that_is_not_a_github_account_is_never_stored(won_lead, monkeypatch):
    checked: list[str] = []
    monkeypatch.setattr(sales_agent.github_api, "user_exists", lambda name: checked.append(name) or False)

    assert sales_agent._maybe_capture_github_username(won_lead, "github username: acmeplumb") is False

    assert checked == ["acmeplumb"]
    assert db.get_lead(won_lead["id"])["github_username"] is None


def test_an_unverifiable_name_is_not_stored_either(won_lead, monkeypatch):
    def api_down(name):
        raise RuntimeError("GitHub API unreachable")
    monkeypatch.setattr(sales_agent.github_api, "user_exists", api_down)

    assert sales_agent._maybe_capture_github_username(won_lead, "my github is oscar-hurman") is False
    assert db.get_lead(won_lead["id"])["github_username"] is None


def test_a_confirmed_account_is_stored_once(won_lead, monkeypatch):
    checked: list[str] = []
    monkeypatch.setattr(sales_agent.github_api, "user_exists", lambda name: checked.append(name) or True)

    assert sales_agent._maybe_capture_github_username(won_lead, "my github is oscar-hurman") is True
    assert db.get_lead(won_lead["id"])["github_username"] == "oscar-hurman"
    assert checked == ["oscar-hurman"]

    # A later reply never overwrites a stored name.
    later = db.get_lead(won_lead["id"])
    assert sales_agent._maybe_capture_github_username(later, "github.com/someone-else") is False
    assert db.get_lead(won_lead["id"])["github_username"] == "oscar-hurman"
