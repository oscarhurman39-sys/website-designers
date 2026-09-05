"""Unit tests for preview expiry (utils/teardown.py) and test-lead cleanup
(cleanup_tests.py) against a throwaway SQLite DB. Every Vercel/GitHub
call is monkeypatched -- nothing here touches a real API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

import cleanup_tests
import config
import main
from utils import db, github_api, teardown, vercel_api


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """Point every db.* call at a fresh SQLite file for this test only."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    monkeypatch.setattr(config, "PREVIEW_TTL_DAYS", 7)


def _days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _make_lead(
    status: str,
    business_name: str = "Joes Cafe",
    site_age_days: float = 10,
    email_age_days: float | None = None,
    contact_email: str = "owner@example.com",
    transferred: bool = False,
) -> int:
    """Insert a lead + website row whose created_at is backdated by
    `site_age_days`, optionally with an outbound email `email_age_days` old.
    Raw SQL for the backdating is fine here: the schema defaults created_at
    to now and nothing in db.py needs to set it explicitly in production."""
    lead_id = db.insert_lead(business_name, "cafe", "Springfield", status=status)
    db.update_lead_fields(lead_id, contact_email=contact_email)
    website_id = db.insert_website(
        lead_id=lead_id,
        template_niche="cafe",
        repo_url=f"https://github.com/me/{business_name.lower()}-preview-{lead_id}",
        repo_full_name=f"me/{business_name.lower()}-preview-{lead_id}",
        preview_url=f"https://{business_name.lower()}-preview-{lead_id}.vercel.app",
        vercel_project_id=f"dpl_{lead_id}",
    )
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE websites SET created_at = ?, transferred = ? WHERE id = ?",
            (_days_ago(site_age_days), int(transferred), website_id),
        )
    if email_age_days is not None:
        thread_id = db.insert_email_thread(
            lead_id, "outbound", "Your new site", "body", "me@example.com", contact_email, message_id=f"<m{lead_id}>"
        )
        with db.get_connection() as conn:
            conn.execute("UPDATE email_threads SET timestamp = ? WHERE id = ?", (_days_ago(email_age_days), thread_id))
    return lead_id


def _selected_lead_ids() -> set[int]:
    return {row["lead_id"] for row in db.list_expired_previews(config.PREVIEW_TTL_DAYS)}


# --- Selection -----------------------------------------------------------------

def test_only_dead_end_statuses_are_selected(tmp_db):
    expected = set()
    for status in db.ALLOWED_STATUSES:
        lead_id = _make_lead(status, site_age_days=10)
        if status in db.PREVIEW_EXPIRABLE_STATUSES:
            expected.add(lead_id)

    assert len(expected) == 4  # emailed / lost / bounced / unsubscribed
    assert _selected_lead_ids() == expected
    # Live conversations, paying clients and not-yet-emailed leads are never candidates.
    for status in ("designed", "replied", "negotiating", "payment_sent", "won", "new", "researched"):
        assert status not in db.PREVIEW_EXPIRABLE_STATUSES


def test_previews_younger_than_ttl_are_not_selected(tmp_db):
    fresh = _make_lead("emailed", site_age_days=3)
    stale = _make_lead("emailed", site_age_days=8)
    assert _selected_lead_ids() == {stale}
    assert fresh not in _selected_lead_ids()


def test_recent_outbound_email_defers_expiry(tmp_db):
    # Site built 10 days ago but the cold email advertising it only went
    # out 2 days ago -- the 7-day promise runs from the email, not the build.
    deferred = _make_lead("emailed", site_age_days=10, email_age_days=2)
    due = _make_lead("emailed", site_age_days=10, email_age_days=9)
    assert _selected_lead_ids() == {due}
    assert deferred not in _selected_lead_ids()


def test_transferred_and_already_torn_down_are_skipped(tmp_db):
    transferred = _make_lead("emailed", transferred=True)
    torn_down = _make_lead("emailed")
    db.mark_website_torn_down(db.get_website_by_lead(torn_down)["id"])
    candidate = _make_lead("emailed")
    assert _selected_lead_ids() == {candidate}
    assert transferred not in _selected_lead_ids()


# --- expire_previews --------------------------------------------------------------

@pytest.fixture
def fake_apis(monkeypatch):
    delete_project = Mock()
    delete_repo = Mock()
    monkeypatch.setattr(vercel_api, "delete_project", delete_project)
    monkeypatch.setattr(github_api, "delete_repo", delete_repo)
    return delete_project, delete_repo


def test_expire_previews_deletes_records_and_is_idempotent(tmp_db, fake_apis):
    delete_project, delete_repo = fake_apis
    lead_id = _make_lead("emailed", business_name="Joes Cafe")
    keep_id = _make_lead("negotiating", business_name="Hot Lead")

    assert teardown.expire_previews() == 1

    delete_project.assert_called_once_with(github_api.make_repo_name("Joes Cafe", lead_id))
    delete_repo.assert_called_once_with(f"me/joes cafe-preview-{lead_id}")
    website = db.get_website_by_lead(lead_id)
    assert website["torn_down_at"]
    assert db.get_lead(lead_id)["status"] == "emailed"  # status never changes
    with db.get_connection() as conn:
        notes = [r["notes"] for r in conn.execute(
            "SELECT notes FROM state_history WHERE lead_id = ? ORDER BY id", (lead_id,)
        )]
    assert any("preview expired after 7 days" in (n or "") for n in notes)
    assert db.get_website_by_lead(keep_id)["torn_down_at"] is None

    # Second pass finds nothing and makes no API calls.
    assert teardown.expire_previews() == 0
    assert delete_project.call_count == 1
    assert delete_repo.call_count == 1


def test_expire_previews_continues_past_a_failing_row(tmp_db, fake_apis):
    delete_project, delete_repo = fake_apis
    bad = _make_lead("lost", business_name="Flaky", site_age_days=12)
    good = _make_lead("bounced", business_name="Fine", site_age_days=11)

    def fail_on_flaky(project_name: str) -> None:
        if "flaky" in project_name:
            raise RuntimeError("vercel down")

    delete_project.side_effect = fail_on_flaky

    assert teardown.expire_previews() == 1

    assert db.get_website_by_lead(good)["torn_down_at"]
    assert db.get_website_by_lead(bad)["torn_down_at"] is None  # retried next pass
    assert delete_repo.call_count == 1
    assert _selected_lead_ids() == {bad}


def test_expire_previews_dry_run_touches_nothing(tmp_db, fake_apis):
    delete_project, delete_repo = fake_apis
    lead_id = _make_lead("unsubscribed")

    assert teardown.expire_previews(dry_run=True) == 0

    delete_project.assert_not_called()
    delete_repo.assert_not_called()
    assert db.get_website_by_lead(lead_id)["torn_down_at"] is None


def test_main_runs_teardown_at_most_hourly(monkeypatch):
    expire = Mock(return_value=0)
    monkeypatch.setattr(teardown, "expire_previews", expire)
    monkeypatch.setattr(config, "PREVIEW_TEARDOWN_ENABLED", True)
    monkeypatch.setattr(main, "_last_teardown_at", None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["now"])

    main._maybe_expire_previews()
    clock["now"] += 60
    main._maybe_expire_previews()
    assert expire.call_count == 1
    clock["now"] += 3600
    main._maybe_expire_previews()
    assert expire.call_count == 2

    monkeypatch.setattr(config, "PREVIEW_TEARDOWN_ENABLED", False)
    clock["now"] += 7200
    main._maybe_expire_previews()
    assert expire.call_count == 2


# --- cleanup_tests -----------------------------------------------------------------

def test_cleanup_tests_finds_and_removes_only_test_leads(tmp_db, fake_apis, monkeypatch):
    delete_project, delete_repo = fake_apis
    monkeypatch.setattr(config, "ADMIN_EMAIL", "me@example.com")
    test_biz = _make_lead("designed", business_name="Test Business", contact_email="whoever@example.com")
    acme = _make_lead("emailed", business_name="Acme Cafe", email_age_days=1)
    mine = _make_lead("won", business_name="Real Looking Co", contact_email="me@example.com")
    real = _make_lead("emailed", business_name="Real Prospect", email_age_days=1)
    db.log_click(acme)

    assert {l["id"] for l in cleanup_tests.find_test_leads()} == {test_biz, acme, mine}
    assert cleanup_tests.cleanup(dry_run=True) == 0
    delete_project.assert_not_called()

    assert cleanup_tests.cleanup() == 3

    assert delete_project.call_count == 3
    assert delete_repo.call_count == 3
    for lead_id in (test_biz, acme, mine):
        assert db.get_lead(lead_id) is None
        assert db.get_website_by_lead(lead_id) is None
        assert db.get_email_threads(lead_id) == []
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM clicks").fetchone()["n"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS n FROM state_history WHERE lead_id IN (?, ?, ?)", (test_biz, acme, mine)
        ).fetchone()["n"] == 0
    assert db.get_lead(real) is not None
    assert db.get_website_by_lead(real) is not None


def test_cleanup_tests_ignores_blank_admin_email(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAIL", "")
    _make_lead("emailed", business_name="Real Prospect", contact_email="")
    assert cleanup_tests.find_test_leads() == []
