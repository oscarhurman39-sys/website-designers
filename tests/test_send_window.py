"""Cold email leaves only in local business hours. A cold email that lands
at 01:00 is deleted as spam; the first live day would have started sending
at 00:05 UTC the moment the daily caps reset, because nothing stopped it."""
from __future__ import annotations

from datetime import datetime, timezone

import config
from agents import sales_agent


def _pin(monkeypatch, start=8, end=18, days=(0, 1, 2, 3, 4)):
    monkeypatch.setattr(config, "SEND_WINDOW_START_HOUR", start)
    monkeypatch.setattr(config, "SEND_WINDOW_END_HOUR", end)
    monkeypatch.setattr(config, "SEND_WINDOW_DAYS", frozenset(days))


def test_weekday_office_hours_are_inside(monkeypatch):
    _pin(monkeypatch)
    assert sales_agent._inside_send_window(datetime(2026, 9, 9, 10, 30))   # Wednesday
    assert sales_agent._inside_send_window(datetime(2026, 9, 9, 8, 0))     # opens at start
    assert not sales_agent._inside_send_window(datetime(2026, 9, 9, 18, 0))  # closes at end


def test_night_and_weekend_are_outside(monkeypatch):
    _pin(monkeypatch)
    assert not sales_agent._inside_send_window(datetime(2026, 9, 10, 1, 5))    # 01:05 Thursday
    assert not sales_agent._inside_send_window(datetime(2026, 9, 12, 11, 0))   # Saturday
    assert not sales_agent._inside_send_window(datetime(2026, 9, 13, 11, 0))   # Sunday


def test_can_send_now_holds_outside_the_window_even_when_caps_allow(monkeypatch):
    _pin(monkeypatch)
    monkeypatch.setattr(sales_agent, "_next_send_allowed_at", datetime(2000, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(sales_agent.db, "emails_sent_last_hour", lambda: 0)
    monkeypatch.setattr(sales_agent.db, "emails_sent_today", lambda: 0)
    monkeypatch.setattr(sales_agent.mailboxes, "any_account_under_cap", lambda: True)
    monkeypatch.setattr(sales_agent, "_inside_send_window", lambda local_now: False)
    assert sales_agent._can_send_now() is False
    monkeypatch.setattr(sales_agent, "_inside_send_window", lambda local_now: True)
    assert sales_agent._can_send_now() is True


def test_window_can_be_opened_fully_from_env(monkeypatch):
    _pin(monkeypatch, start=0, end=24, days=range(7))
    assert sales_agent._inside_send_window(datetime(2026, 9, 13, 3, 0))  # Sunday 03:00
