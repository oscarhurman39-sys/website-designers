from __future__ import annotations

from pathlib import Path

import pytest

import config
import preview_audit
from utils import db


@pytest.fixture
def audit_db(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    return tmp_path


def _add_preview(tmp_path: Path, *, complete: bool = True, name: str = "Example Co") -> int:
    lead_id = db.insert_lead(name, "plumber", "Maidstone", status="designed")
    db.update_lead_fields(lead_id, contact_email="owner@example.com" if complete else "")
    screenshot = tmp_path / f"{lead_id}.png"
    if complete:
        screenshot.write_bytes(b"png")
    db.insert_website(
        lead_id, "plumber", "https://github.com/casey/example", "casey/example",
        "https://example.vercel.app", "dpl_example" if complete else "",
        screenshot_path=str(screenshot) if complete else "",
    )
    if not complete:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE websites SET repo_url = ?, preview_url = ?, repo_full_name = ? WHERE lead_id = ?",
                ("", "not-a-url", "", lead_id),
            )
    return lead_id


def test_complete_preview_passes_without_touching_the_db(audit_db):
    lead_id = _add_preview(audit_db)
    result = preview_audit.audit_previews(repo_root=audit_db)
    assert [(item.lead_id, item.ok, item.issues) for item in result] == [(lead_id, True, ())]
    with db.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM state_history").fetchone()[0] == 1


def test_missing_files_urls_and_handoff_data_are_reported(audit_db):
    lead_id = _add_preview(audit_db, complete=False)
    [result] = preview_audit.audit_previews(repo_root=audit_db)
    assert result.lead_id == lead_id
    assert not result.ok
    assert result.issues == (
        "preview URL missing or invalid", "repository URL missing or invalid",
        "screenshot file path missing", "contact email missing",
        "repository handoff name missing", "Vercel project handoff id missing",
    )


def test_audit_honours_requested_limit(audit_db):
    for number in range(3):
        _add_preview(audit_db, name=f"Example {number}")
    assert len(preview_audit.audit_previews(repo_root=audit_db, limit=2)) == 2


@pytest.mark.parametrize("limit", [0, 11])
def test_limit_must_be_between_one_and_ten(audit_db, limit):
    with pytest.raises(ValueError, match="between 1 and 10"):
        preview_audit.audit_previews(repo_root=audit_db, limit=limit)
