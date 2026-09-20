"""The sourcing rotation cursor must carry over between runs.

The starting bucket used to be the UTC day number, so it advanced by ONE
bucket per day. With 8 niches x 44 towns = 352 buckets and a request budget
that only reaches a few dozen per run, every run of every day re-walked the
same front of the list: the same handful of towns were mined out, the rest of
England was never searched, the send queue collapsed into ~13 niche+town
buckets and OUTREACH_COOLDOWN_DAYS then throttled sending to almost nothing.

These tests pin the fix: the cursor is stored in the database, advances by the
number of buckets a run actually visited, wraps at the end of the grid, and
never takes the run down with it when the database is unavailable.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
from agents import sourcing_agent
from utils import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    path = tmp_path / "rotation.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    db.init_db(str(path))
    return str(path)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(sourcing_agent, "_skip_reason", lambda place: None)
    monkeypatch.setattr(sourcing_agent, "_within_exclusion_zone", lambda c: False)
    monkeypatch.setattr(sourcing_agent, "_already_in_db", lambda c: False)
    monkeypatch.setattr(sourcing_agent, "_to_candidate",
                        lambda place, niche, location: SimpleNamespace(
                            business_name=place["name"], niche=niche, location=location))
    monkeypatch.setattr(config, "SOURCING_PER_BUCKET", 1)
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Crawley", "Leeds", "York", "Bath"])


def _api(monkeypatch, visited: list | None = None):
    def fake_iter_places(query):
        if visited is not None:
            visited.append(query)
        yield {"id": query, "name": query.split(" in ")[-1]}
    monkeypatch.setattr(sourcing_agent, "_iter_places", fake_iter_places)


def test_state_store_round_trips(temp_db):
    assert db.get_state("nothing.here") is None
    assert db.get_state("nothing.here", "fallback") == "fallback"
    db.set_state("k", "1")
    assert db.get_state("k") == "1"
    db.set_state("k", "2")  # upsert, not a second row
    assert db.get_state("k") == "2"


def test_the_next_run_starts_where_the_last_one_stopped(temp_db, monkeypatch):
    """The whole point: two short runs must cover four towns, not two."""
    _api(monkeypatch)
    first = [c.business_name for c in sourcing_agent.iter_candidates(limit=2)]
    assert first == ["Crawley", "Leeds"]
    assert db.get_state(sourcing_agent._ROTATION_STATE_KEY) == "2"

    second = [c.business_name for c in sourcing_agent.iter_candidates(limit=2)]
    assert second == ["York", "Bath"]


def test_the_cursor_wraps_at_the_end_of_the_grid(temp_db, monkeypatch):
    _api(monkeypatch)
    db.set_state(sourcing_agent._ROTATION_STATE_KEY, "3")
    got = [c.business_name for c in sourcing_agent.iter_candidates(limit=2)]
    assert got == ["Bath", "Crawley"]
    assert db.get_state(sourcing_agent._ROTATION_STATE_KEY) == "1"


def test_a_full_sweep_leaves_the_cursor_at_the_start(temp_db, monkeypatch):
    _api(monkeypatch)
    got = [c.business_name for c in sourcing_agent.iter_candidates()]
    assert got == ["Crawley", "Leeds", "York", "Bath"]
    assert db.get_state(sourcing_agent._ROTATION_STATE_KEY) == "0"


def test_an_exhausted_request_budget_still_advances_the_cursor(temp_db, monkeypatch):
    """A run that finds nothing new must still move on, or the next run
    re-searches the same mined-out towns and the grid never advances."""
    visited: list[str] = []

    def fake_iter_places(query):
        visited.append(query)
        if len(visited) >= 2:
            raise sourcing_agent.PlacesBudgetExhausted("request budget spent")
        return iter(())

    monkeypatch.setattr(sourcing_agent, "_iter_places", fake_iter_places)
    assert list(sourcing_agent.iter_candidates()) == []
    assert visited == ["plumber in Crawley", "plumber in Leeds"]
    assert db.get_state(sourcing_agent._ROTATION_STATE_KEY) == "2"


def test_a_corrupt_cursor_starts_from_the_first_bucket(temp_db, monkeypatch):
    _api(monkeypatch)
    db.set_state(sourcing_agent._ROTATION_STATE_KEY, "not-a-number")
    got = [c.business_name for c in sourcing_agent.iter_candidates(limit=1)]
    assert got == ["Crawley"]


def test_an_unreachable_database_does_not_stop_sourcing(tmp_path, monkeypatch):
    """No pipeline_state table (an old DB mid-migration): the run still
    yields leads, it just cannot remember where it got to."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "empty.db"))
    _api(monkeypatch)
    got = [c.business_name for c in sourcing_agent.iter_candidates(limit=2)]
    assert got == ["Crawley", "Leeds"]
