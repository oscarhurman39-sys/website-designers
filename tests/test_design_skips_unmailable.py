"""A lead with no contact email must not cost a GitHub repo and a Vercel
project: SalesAgent could never send it, so DesignAgent marks it lost up
front instead of building a preview nobody will see."""
from __future__ import annotations

import pytest

import config
from agents import design_agent
from utils import db


@pytest.fixture(autouse=True)
def _modern(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")


def test_unmailable_lead_is_lost_without_a_deploy(monkeypatch):
    db.init_db()
    lead_id = db.insert_lead("No Email Plumbing", "plumber", "Horsham", status="researched")

    def never(*a, **k):
        raise AssertionError("deploy path must not run for a lead with no email")
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", never)
    monkeypatch.setattr(design_agent.vercel_api, "deploy_files", never)

    assert design_agent.process_lead(db.get_lead(lead_id)) is None
    lead = db.get_lead(lead_id)
    assert lead["status"] == "lost"
    assert db.get_website_by_lead(lead_id) is None


def test_mailable_lead_still_reaches_the_deploy_path(monkeypatch):
    db.init_db()
    lead_id = db.insert_lead("Has Email Plumbing", "plumber", "Horsham", status="researched")
    db.update_lead_fields(lead_id, contact_email="owner@hasemail.test")
    reached = []
    def stop_here(*a, **k):
        reached.append(True)
        raise RuntimeError("stop before any network")
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", stop_here)

    with pytest.raises(RuntimeError):
        design_agent.process_lead(db.get_lead(lead_id))
    assert reached and db.get_lead(lead_id)["status"] == "researched"
