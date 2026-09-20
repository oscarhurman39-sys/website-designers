"""DesignAgent's two new gates: a business whose existing site already scores
as healthy is parked before any repo or Vercel project is created, and no
more previews are built once the 'designed' queue holds a couple of days of
sends (previews expire after PREVIEW_TTL_DAYS, so over-building only makes
previews that are torn down unsent)."""
from __future__ import annotations

import pytest

import config
from agents import design_agent
from utils import db


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    monkeypatch.setattr(config, "UNSPLASH_ACCESS_KEY", "")
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example.test")
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    monkeypatch.setattr(design_agent, "_design_next_attempt", {})
    monkeypatch.setattr(design_agent, "_design_failures", {})
    monkeypatch.setattr(design_agent, "_backlog_full_announced", False)
    db.init_db()


def _lead(name: str, site_score=None, email: str = "owner@x.test", status: str = "researched",
          lead_score=None, website_status: str = "owned_site") -> int:
    lead_id = db.insert_lead(name, "plumber", "Horsham", status=status)
    db.update_lead_fields(
        lead_id, contact_email=email, website_status=website_status, site_score=site_score,
        lead_score=lead_score, website_url="https://x.test",
    )
    return lead_id


def _last_note(lead_id: int) -> str:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT notes FROM state_history WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lead_id,)
        ).fetchone()
    return row["notes"] or ""


def test_healthy_site_is_parked_before_any_deploy(monkeypatch):
    lead_id = _lead("Fine Plumbing", site_score=9)

    def never(*a, **k):
        raise AssertionError("deploy path must not run for a business with a healthy site")
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", never)
    monkeypatch.setattr(design_agent.vercel_api, "deploy_files", never)

    assert design_agent.process_lead(db.get_lead(lead_id)) is None
    assert db.get_lead(lead_id)["status"] == "lost"
    assert "scored 9/10" in _last_note(lead_id) and "not a target" in _last_note(lead_id)
    assert db.get_website_by_lead(lead_id) is None


@pytest.mark.parametrize("site_score", [3, None])
def test_poor_or_unaudited_site_still_reaches_the_deploy_path(monkeypatch, site_score):
    lead_id = _lead("Tired Plumbing", site_score=site_score)
    reached = []

    def stop_here(*a, **k):
        reached.append(True)
        raise RuntimeError("stop before any network")
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files", stop_here)

    with pytest.raises(RuntimeError):
        design_agent.process_lead(db.get_lead(lead_id))
    assert reached and db.get_lead(lead_id)["status"] == "researched"


def test_gate_is_off_when_the_threshold_is_zero(monkeypatch):
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 0)
    assert design_agent.park_reason(db.get_lead(_lead("Fine Plumbing", site_score=10))) is None


def test_no_email_is_still_the_first_reason_to_park():
    assert design_agent.park_reason(db.get_lead(_lead("Silent", site_score=1, email=""))).startswith("No contact email")


def test_run_builds_the_best_leads_first_and_stops_at_the_backlog_cap(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_BACKLOG_MAX", 2)
    _lead("Already Built", status="designed")
    _lead("Low", site_score=7, lead_score=4)
    _lead("High", site_score=1, lead_score=8)
    _lead("Mid", site_score=4, lead_score=6)
    built: list[str] = []

    def fake_process(lead):
        built.append(lead["business_name"])
        db.update_lead_status(lead["id"], "designed", notes="built")
    monkeypatch.setattr(design_agent, "process_lead", fake_process)

    design_agent.run()
    assert built == ["High"]  # one already waiting + one built = the cap of two
    design_agent.run()
    assert built == ["High"]  # still full: nothing more is built
    monkeypatch.setattr(config, "DESIGN_BACKLOG_MAX", 4)
    design_agent.run()
    assert built == ["High", "Mid", "Low"]


def test_run_parks_non_targets_even_when_the_backlog_is_full(monkeypatch):
    monkeypatch.setattr(config, "DESIGN_BACKLOG_MAX", 1)
    _lead("Already Built", status="designed")
    good = _lead("Fine Plumbing", site_score=9, lead_score=1)
    no_email = _lead("Silent Plumbing", site_score=2, email="", lead_score=8)

    def never(lead):
        raise AssertionError("nothing may be built while the backlog is full")
    monkeypatch.setattr(design_agent, "process_lead", never)

    design_agent.run()
    assert db.get_lead(good)["status"] == "lost"
    assert db.get_lead(no_email)["status"] == "lost"
