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


# --- Pre-launch reset: teardown --all and cleanup_tests --reset ------------------


def test_find_test_leads_matches_the_testville_marker_location(tmp_db, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_EMAIL", "")
    fake = db.insert_lead("Surrey Auto", "vehicle-repair", "Testville", status="designed")
    db.insert_lead("Surrey Auto", "vehicle-repair", "Oxted", status="designed")
    assert {lead["id"] for lead in cleanup_tests.find_test_leads()} == {fake}


def test_teardown_all_takes_every_deployed_preview_but_never_a_transferred_one(tmp_db, fake_apis):
    delete_project, delete_repo = fake_apis
    fresh = _make_lead("designed", site_age_days=0.1)
    old = _make_lead("lost", site_age_days=30, email_age_days=30)
    handed_over = _make_lead("emailed", business_name="Handed Over", transferred=True)

    assert {row["lead_id"] for row in db.list_live_previews()} == {fresh, old}
    assert teardown.expire_previews(dry_run=True, all_previews=True) == 0
    delete_project.assert_not_called()

    assert teardown.expire_previews(all_previews=True) == 2
    assert delete_project.call_count == 2
    assert delete_repo.call_count == 2
    assert db.list_live_previews() == []
    assert db.get_website_by_lead(handed_over)["torn_down_at"] is None
    assert db.get_lead(fresh)["status"] == "designed"  # housekeeping never moves the funnel


def test_teardown_all_refuses_when_any_lead_has_paid(tmp_db, fake_apis):
    delete_project, _ = fake_apis
    _make_lead("designed")
    _make_lead("won", business_name="Paying Client")
    assert teardown.expire_previews(all_previews=True) == 0
    delete_project.assert_not_called()


def test_reset_refuses_while_previews_are_deployed_or_data_looks_real(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup_tests, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    lead = _make_lead("designed")

    assert cleanup_tests.reset_prelaunch() is None  # a preview is still deployed
    assert db.get_lead(lead) is not None

    db.mark_website_torn_down(db.get_website_by_lead(lead)["id"])
    db.mark_unsubscribed(lead, "owner@example.com")
    assert cleanup_tests.reset_prelaunch() is None  # a suppression entry exists
    assert db.get_lead(lead) is not None
    assert not (tmp_path / "archive").exists()


def test_reset_archives_the_database_and_starts_empty(tmp_db, monkeypatch, tmp_path):
    import sqlite3

    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(cleanup_tests, "ARCHIVE_DIR", archive_dir)
    traces = tmp_path / "traces.json"
    traces.write_text("[]")
    monkeypatch.setattr(config, "TRACES_PATH", str(traces))
    lead = _make_lead("emailed", email_age_days=1)
    db.mark_website_torn_down(db.get_website_by_lead(lead)["id"])

    assert cleanup_tests.reset_prelaunch(dry_run=True) is None
    assert db.get_lead(lead) is not None

    archive = cleanup_tests.reset_prelaunch()
    assert archive is not None and archive.exists()
    assert db.get_lead(lead) is None
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 0
    copied = sqlite3.connect(archive)
    try:
        assert copied.execute("SELECT COUNT(*) FROM leads").fetchone()[0] == 1
    finally:
        copied.close()
    assert not traces.exists()
    assert (archive_dir / archive.name.replace("leads-", "traces-").replace(".db", ".json")).exists()


# --- A token without delete_repo must not stall the cleanup ----------------------


def test_teardown_records_the_preview_as_gone_when_the_repo_cannot_be_deleted(
    tmp_db, fake_apis, monkeypatch, tmp_path
):
    """A 403 means the token lacks delete_repo: retrying can never work, and
    the Vercel project is already deleted, so the preview really is dead."""
    delete_project, delete_repo = fake_apis
    monkeypatch.setattr(teardown, "ARCHIVE_DIR", tmp_path / "archive")
    lead = _make_lead("emailed", email_age_days=30)
    repo = db.get_website_by_lead(lead)["repo_full_name"]
    delete_repo.side_effect = github_api.RepoDeleteForbidden(repo)

    assert teardown.expire_previews() == 1
    delete_project.assert_called_once()
    assert db.get_website_by_lead(lead)["torn_down_at"] is not None  # not retried forever
    assert db.list_live_previews() == []                            # and the reset is unblocked

    listed = sorted((tmp_path / "archive").glob("orphan-repos-*.txt"))
    assert len(listed) == 1
    assert listed[0].read_text(encoding="utf-8").strip() == repo
    with db.get_connection() as conn:
        notes = conn.execute(
            "SELECT notes FROM state_history WHERE lead_id = ? ORDER BY id DESC LIMIT 1", (lead,)
        ).fetchone()["notes"]
    assert "left in place" in notes


def test_a_transient_github_failure_is_still_retried_next_pass(tmp_db, fake_apis):
    _, delete_repo = fake_apis
    delete_repo.side_effect = RuntimeError("502 Bad Gateway")
    lead = _make_lead("emailed", email_age_days=30)

    assert teardown.expire_previews() == 0
    assert db.get_website_by_lead(lead)["torn_down_at"] is None
    assert {row["lead_id"] for row in db.list_expired_previews(config.PREVIEW_TTL_DAYS)} == {lead}


def test_cleanup_tests_deletes_the_lead_even_when_its_repo_survives(tmp_db, fake_apis, monkeypatch, tmp_path):
    delete_project, delete_repo = fake_apis
    monkeypatch.setattr(teardown, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setattr(config, "ADMIN_EMAIL", "")
    delete_repo.side_effect = github_api.RepoDeleteForbidden("me/test-business-preview-1")
    lead = _make_lead("designed", business_name="Test Business")

    assert cleanup_tests.cleanup() == 1
    assert db.get_lead(lead) is None
    assert delete_project.call_count == 1
    assert sorted((tmp_path / "archive").glob("orphan-repos-*.txt"))


def test_delete_listed_repos_clears_the_saved_list_and_reports_a_still_bad_token(fake_apis, tmp_path):
    _, delete_repo = fake_apis
    listing = tmp_path / "orphan-repos.txt"
    listing.write_text("me/one\n\nme/two\n", encoding="utf-8")  # blank lines ignored

    assert teardown.delete_listed_repos(listing, dry_run=True) == 0
    delete_repo.assert_not_called()

    assert teardown.delete_listed_repos(listing) == 2
    assert [call.args[0] for call in delete_repo.call_args_list] == ["me/one", "me/two"]

    delete_repo.reset_mock()
    delete_repo.side_effect = github_api.RepoDeleteForbidden("me/one")
    assert teardown.delete_listed_repos(listing) == 0  # scope still missing: nothing silently "succeeds"
