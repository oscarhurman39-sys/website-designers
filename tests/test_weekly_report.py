"""`run.py report --weekly`: the per-week funnel and the lead-quality mix."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import config
import report
from utils import db

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
NOW_S = NOW.strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    db.init_db()


def _lead(days_ago: float, email: str = "o@x.test") -> int:
    lead_id = db.insert_lead(f"Biz {days_ago}", "plumber", "Town", status="new")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE leads SET created_at = datetime(?, ?), contact_email = ? WHERE id = ?",
            (NOW_S, f"-{days_ago} days", email, lead_id),
        )
    return lead_id


def _history(lead_id: int, from_state: str, to_state: str, days_ago: float) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO state_history (lead_id, from_state, to_state, notes, timestamp) "
            "VALUES (?, ?, ?, '', datetime(?, ?))",
            (lead_id, from_state, to_state, NOW_S, f"-{days_ago} days"),
        )


def _email(lead_id: int, direction: str, classification: str, days_ago: float) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO email_threads (lead_id, direction, classification, timestamp) VALUES (?, ?, ?, datetime(?, ?))",
            (lead_id, direction, classification, NOW_S, f"-{days_ago} days"),
        )


def test_weekly_funnel_buckets_by_seven_day_window():
    a = _lead(1)
    _lead(2, email="")
    c = _lead(9)
    _history(a, "researched", "designed", 1)
    _history(c, "researched", "designed", 9)
    _email(a, "outbound", "", 1)
    _email(a, "outbound", "follow_up", 0.5)
    _email(c, "outbound", "", 8)
    _email(a, "inbound", "positive", 0.2)
    _email(c, "inbound", "out_of_office", 8)  # an auto-reply is not a reply
    with db.get_connection() as conn:
        conn.execute("INSERT INTO clicks (lead_id, timestamp) VALUES (?, datetime(?, '-1 days'))", (a, NOW_S))
        conn.execute("UPDATE leads SET status = 'won', won_amount = 589 WHERE id = ?", (a,))
    _history(a, "payment_sent", "won", 0.1)

    this_week, last_week = db.weekly_funnel(weeks=2, now=NOW)
    assert (this_week["week_start"], this_week["week_end"]) == ("2026-09-13", "2026-09-20")
    assert [this_week[k] for k in ("sourced", "mailable", "designed", "cold_emails", "follow_ups",
                                   "replies", "clicks", "won", "revenue")] == [2, 1, 1, 1, 1, 1, 1, 1, 589]
    assert [last_week[k] for k in ("sourced", "designed", "cold_emails", "replies", "won", "revenue")] == [1, 1, 1, 0, 0, 0]


def test_lead_quality_mix_buckets_by_existing_web_presence():
    def mk(status: str, website_status: str, site_score):
        lead_id = db.insert_lead("B", "plumber", "T", status=status)
        db.update_lead_fields(lead_id, website_status=website_status, site_score=site_score)

    mk("designed", "owned_site", 9)
    mk("designed", "owned_site", 2)
    mk("researched", "owned_site", None)
    mk("emailed", "owned_site", 5)
    mk("lost", "none", 0)
    mk("designed", "platform_only", 0)
    mix = {row["bucket"]: row for row in db.lead_quality_mix()}
    assert mix["good site"]["to_send"] == 1
    assert mix["very poor site"]["to_send"] == 1
    assert mix["unscored"]["to_build"] == 1
    assert mix["poor site"]["emailed"] == 1
    assert mix["no website"]["lost"] == 1
    assert mix["platform only"]["to_send"] == 1


def test_report_cli_prints_both_tables(capsys):
    _lead(1)
    assert report.main(["--weekly", "--weeks", "2"]) == 0
    out = capsys.readouterr().out
    assert "WEEKLY FUNNEL" in out and "LEAD QUALITY MIX" in out
    assert report.main([]) == 0
    assert "niche" in capsys.readouterr().out
