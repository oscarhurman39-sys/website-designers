from pathlib import Path
from unittest.mock import Mock

import pytest

from pipeline import reviewed_batch


def test_pause_required(monkeypatch, tmp_path):
    monkeypatch.setattr(reviewed_batch, "PAUSE_FLAG", tmp_path / ".paused")
    with pytest.raises(reviewed_batch.BatchError, match="not paused"):
        reviewed_batch.prepare([1])


def test_lock_cleanup(monkeypatch, tmp_path):
    lock = tmp_path / "batch.lock"
    monkeypatch.setattr(reviewed_batch, "LOCK_PATH", lock)
    with reviewed_batch.batch_lock():
        assert lock.exists()
    assert not lock.exists()


def test_prepare_only_explicit_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(reviewed_batch, "PAUSE_FLAG", tmp_path / ".paused")
    reviewed_batch.PAUSE_FLAG.touch()
    leads = {1: {"id": 1, "status": "designed"}, 2: {"id": 2, "status": "designed"}}
    seen = []
    monkeypatch.setattr(reviewed_batch.db, "get_lead", lambda i: leads[i])
    monkeypatch.setattr(reviewed_batch.db, "get_website_by_lead", lambda i: {"preview_url": "https://x"})
    monkeypatch.setattr(reviewed_batch.design_agent, "process_lead", lambda lead: seen.append(lead["id"]) or {})
    assert reviewed_batch.prepare([1]) == 1
    assert seen == []


def test_send_health_and_selected(monkeypatch, tmp_path):
    monkeypatch.setattr(reviewed_batch, "PAUSE_FLAG", tmp_path / ".paused")
    reviewed_batch.PAUSE_FLAG.touch()
    monkeypatch.setattr(reviewed_batch.config, "ENABLE_LIVE_SEND", True)
    lead = {"id": 1, "status": "designed", "contact_email": "a@x", "unsubscribed": 0}
    monkeypatch.setattr(reviewed_batch.db, "get_lead", lambda i: lead if i == 1 else None)
    monkeypatch.setattr(reviewed_batch.db, "get_website_by_lead", lambda i: {"preview_url": "https://x"})
    monkeypatch.setattr(reviewed_batch.db, "is_unsubscribed", lambda e: False)
    monkeypatch.setattr(reviewed_batch.sales_agent, "_can_send_now", lambda: True)
    monkeypatch.setattr(reviewed_batch.sales_agent, "_cooldown_blocked_until", lambda l: None)
    monkeypatch.setattr(reviewed_batch.tracker, "public_endpoint_up", lambda: True)
    sent = []
    monkeypatch.setattr(reviewed_batch.sales_agent, "send_cold_email", lambda l: sent.append(l["id"]) or True)
    assert reviewed_batch.send([1], 1) == 1
    assert sent == [1]
