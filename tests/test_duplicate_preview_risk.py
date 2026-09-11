"""Local duplicate-preview warnings for the reviewed operator flow."""
from __future__ import annotations

import pytest

import config
from pipeline import reviewed_batch
from utils import db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "DESIGN_TEMPLATE_STYLE", "modern")
    db.init_db()
    return tmp_path


def _lead(name: str, niche: str = "plumber", location: str = "Crawley, Sussex", status: str = "researched") -> int:
    lead_id = db.insert_lead(name, niche, location, status=status)
    db.update_lead_fields(lead_id, contact_email=f"owner{lead_id}@example.test")
    return lead_id


def _website(lead_id: int, template_niche: str = "plumber", torn_down: bool = False) -> None:
    db.insert_website(
        lead_id=lead_id,
        template_niche=template_niche,
        repo_url=f"https://github.test/repo-{lead_id}",
        repo_full_name=f"casey/repo-{lead_id}",
        preview_url=f"https://preview-{lead_id}.example.test",
        vercel_project_id=f"prj_{lead_id}",
    )
    if torn_down:
        with db.get_connection() as conn:
            conn.execute("UPDATE websites SET torn_down_at = datetime('now') WHERE lead_id = ?", (lead_id,))


def test_duplicate_preview_risk_matches_same_trade_town_and_template(env):
    first = _lead("First Plumbing")
    _website(first)

    second = db.get_lead(_lead("Second Plumbing"))
    risks = db.duplicate_preview_risks(second)

    assert [risk["lead_id"] for risk in risks] == [first]
    assert risks[0]["business_name"] == "First Plumbing"
    assert risks[0]["template_niche"] == "plumber"


def test_duplicate_preview_risk_ignores_other_towns_trades_templates_and_torn_down(env):
    _website(_lead("Maidstone Plumbing", location="Maidstone, Kent"))
    _website(_lead("Crawley Sparks", niche="electrician"), template_niche="electrician")
    _website(_lead("Old Crawley Plumbing"), torn_down=True)
    _website(_lead("Legacy Crawley Plumbing"), template_niche="default")

    risks = db.duplicate_preview_risks(db.get_lead(_lead("Fresh Crawley Plumbing")))

    assert risks == []


def test_reviewed_batch_prepare_warns_before_designing_duplicate(env, monkeypatch, capsys):
    pause_flag = env / ".paused"
    pause_flag.write_text("paused", encoding="utf-8")
    monkeypatch.setattr(reviewed_batch, "PAUSE_FLAG", pause_flag)
    monkeypatch.setattr(reviewed_batch.design_agent, "process_lead", lambda lead: {"preview_url": "https://new.example.test"})

    _website(_lead("First Plumbing", status="designed"))
    second = _lead("Second Plumbing")

    assert reviewed_batch.prepare([second]) == 1

    out = capsys.readouterr().out
    assert "WARNING: duplicate preview risk" in out
    assert "First Plumbing" in out
    assert "plumber in Crawley, Sussex" in out


def test_reviewed_batch_prepare_warns_for_already_designed_duplicate(env, monkeypatch, capsys):
    pause_flag = env / ".paused"
    pause_flag.write_text("paused", encoding="utf-8")
    monkeypatch.setattr(reviewed_batch, "PAUSE_FLAG", pause_flag)

    _website(_lead("First Plumbing", status="designed"))
    second = _lead("Second Plumbing", status="designed")
    _website(second)

    assert reviewed_batch.prepare([second]) == 1

    assert "WARNING: duplicate preview risk" in capsys.readouterr().out
