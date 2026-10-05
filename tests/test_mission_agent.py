"""MissionAgent tests: the assessment names the stage holding the daily
target down, the report says which setting to change, and missing preview
screenshots are retaken. Throwaway SQLite file, no network, no browser."""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import config
from agents import mission_agent
from utils import db, screenshot, tracker


@pytest.fixture
def mission(monkeypatch, tmp_path):
    """A line that could carry 50 a day: every setting generous."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    monkeypatch.setattr(config, "MISSION_DAILY_SENDS", 50)
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    monkeypatch.setattr(tracker, "public_endpoint_up", lambda: True)
    monkeypatch.setattr(config, "FOLLOW_UP_ENABLED", False)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 60)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY_PER_ACCOUNT", 20)
    monkeypatch.setattr(config, "EMAIL_ACCOUNTS", [config.EMAIL_ACCOUNTS[0]] * 3)
    monkeypatch.setattr(config, "SEND_WINDOW_START_HOUR", 8)
    monkeypatch.setattr(config, "SEND_WINDOW_END_HOUR", 22)
    monkeypatch.setattr(config, "SOURCING_ENABLED", True)
    monkeypatch.setattr(config, "SOURCING_DAILY_LIMIT", 400)
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", [f"Town {i}" for i in range(300)])
    monkeypatch.setattr(config, "OUTREACH_COOLDOWN_DAYS", 5)
    monkeypatch.setattr(config, "DESIGN_BACKLOG_MAX", 100)
    monkeypatch.setattr(config, "PREVIEW_TTL_DAYS", 3)
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)


def _stages(state: dict) -> list[str]:
    return [blocker.stage for blocker in state["blockers"]]


def test_a_line_sized_for_the_mission_has_no_blockers(mission):
    state = mission_agent.assess()

    assert state["target"] == 50
    assert state["blockers"] == []
    assert "nothing in the settings holds the mission down" in mission_agent.format_report(state)


def test_the_tightest_stage_is_listed_first_with_its_fix(mission, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 15)
    monkeypatch.setattr(config, "SOURCING_DAILY_LIMIT", 12)  # x 25% default yield = 3 a day

    state = mission_agent.assess()

    assert _stages(state)[:2] == ["sourcing", "send cap"]
    assert state["blockers"][0].limit == 3
    report = mission_agent.format_report(state)
    assert "set SOURCING_DAILY_LIMIT=200" in report
    assert "SITE_SCORE_GOOD_AT=0" in report
    assert "set EMAIL_MAX_PER_DAY=50" in report


def test_follow_ups_halve_what_the_cap_carries(mission, monkeypatch):
    monkeypatch.setattr(config, "FOLLOW_UP_ENABLED", True)

    state = mission_agent.assess()

    cap = next(b for b in state["blockers"] if b.stage == "send cap")
    assert cap.limit == 30
    assert "set EMAIL_MAX_PER_DAY=100" in cap.fix


def test_one_mailbox_carrying_fifty_is_called_out(mission, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ACCOUNTS", [config.EMAIL_ACCOUNTS[0]])
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY_PER_ACCOUNT", 60)

    mailboxes_blocker = next(b for b in mission_agent.assess()["blockers"] if b.stage == "mailboxes")

    assert "spam" in mailboxes_blocker.problem
    assert "2 mailboxes" in mailboxes_blocker.fix


def test_line_stoppers_sort_above_everything(mission, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 15)
    monkeypatch.setattr(tracker, "public_endpoint_up", lambda: False)

    state = mission_agent.assess()

    assert _stages(state)[0] == "send"
    assert "stops the line" in mission_agent.format_report(state)


def test_a_closed_gate_is_fixed_before_the_sourcing_limit(mission, monkeypatch):
    monkeypatch.setattr(mission_agent, "sourcing_yield", lambda: 0.02)

    sourcing = next(b for b in mission_agent.assess()["blockers"] if b.stage == "sourcing")

    assert sourcing.fix.startswith("set SITE_SCORE_GOOD_AT=0 first")


def test_failing_builds_stop_the_line(mission):
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Springfield", status="researched")
    db.log_state_history(lead_id, "researched", "researched",
                         notes="Design failed: 403 Client Error: Forbidden for url: https://api.vercel.com/v13/deployments")

    design = mission_agent.assess()["blockers"][0]

    assert design.stage == "design" and design.limit is None
    assert "403 Client Error" in design.problem and "VERCEL_TOKEN" in design.fix


def test_cooldown_and_vercel_limits_are_measured(mission, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", [f"Town {i}" for i in range(44)])
    monkeypatch.setattr(config, "PREVIEW_TTL_DAYS", 7)

    blockers = {b.stage: b for b in mission_agent.assess()["blockers"]}

    assert blockers["cooldown"].limit == 8        # 44 towns / 5 days
    assert blockers["preview hosting"].limit == 28  # 200 projects / 7 days


def test_cold_sends_are_counted_without_follow_ups(mission):
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Springfield", status="emailed")
    db.insert_email_thread(lead_id=lead_id, direction="outbound", subject="s", body="b",
                           from_addr="a@x.test", to_addr="b@y.test")
    db.insert_email_thread(lead_id=lead_id, direction="outbound", subject="s", body="b",
                           from_addr="a@x.test", to_addr="b@y.test", classification="follow_up")

    state = mission_agent.assess()

    assert state["cold_today"] == 1
    assert state["sends_today"] == 2


def test_missing_screenshots_are_retaken_for_waiting_previews(mission, monkeypatch):
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Springfield", status="designed")
    db.insert_website(lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name="o/r",
                      preview_url="https://joes.example")
    captured: list[str] = []

    def capture(url: str, out_path: Path) -> None:
        captured.append(url)
        Image.new("RGB", (1280, 4000), "white").save(out_path, "PNG")

    monkeypatch.setattr(screenshot, "_capture_sync", capture)

    assert mission_agent.assess()["queued_without_screenshot"] == 1
    assert mission_agent.heal_screenshots() == 1
    assert captured == ["https://joes.example"]
    assert mission_agent.assess()["queued_without_screenshot"] == 0


def test_email_image_is_a_small_jpeg_of_the_top_of_the_page(mission):
    source = screenshot.screenshot_path(7)
    Image.new("RGB", (1280, 6000), "navy").save(source, "PNG")

    small = screenshot.email_image(7)

    assert small is not None and small.suffix == ".jpg"
    with Image.open(small) as image:
        assert image.size == (640, 960)
    screenshot.invalidate(7)
    assert not small.exists() and screenshot.email_image(7) is None


def test_run_is_quiet_without_a_mission(mission, monkeypatch):
    monkeypatch.setattr(config, "MISSION_DAILY_SENDS", 0)
    monkeypatch.setattr(mission_agent, "_last_run_at", None)
    from agents import sales_agent

    monkeypatch.setattr(sales_agent, "_slack_notify", lambda text: pytest.fail("no brief without a mission"))

    mission_agent.run()
