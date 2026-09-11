"""The preview screenshot cache is keyed by lead id, so a new build must
bypass it and it must never hold a Vercel login page.

2026-09-11: after the database reset restarted ids at 1, leads 11-56
inherited July test images from pipeline/screenshots/ and eight cold emails
went out with a "Log in to Vercel" picture (one with another business's
site) embedded as the prospect's "preview".
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from agents import design_agent
from utils import screenshot


@pytest.fixture
def shots_dir(tmp_path, monkeypatch):
    directory = tmp_path / "screenshots"
    directory.mkdir()
    monkeypatch.setattr(screenshot, "SCREENSHOTS_DIR", directory)
    return directory


def test_cached_file_is_reused_by_default(shots_dir, monkeypatch):
    (shots_dir / "7.png").write_bytes(b"old")
    capture = Mock()
    monkeypatch.setattr(screenshot, "_capture_sync", capture)

    assert screenshot.capture_screenshot_sync("https://x.vercel.app", 7) == shots_dir / "7.png"

    capture.assert_not_called()


def test_fresh_build_drops_the_cached_file_first(shots_dir, monkeypatch):
    (shots_dir / "7.png").write_bytes(b"old")

    def capture(url: str, out_path: Path) -> None:
        assert not out_path.exists(), "stale file should be gone before the browser runs"
        out_path.write_bytes(b"new")

    monkeypatch.setattr(screenshot, "_capture_sync", capture)

    path = screenshot.capture_screenshot_sync("https://x.vercel.app", 7, fresh=True)

    assert path.read_bytes() == b"new"


class _FakePage:
    def __init__(self, title: str):
        self._title = title
        self.saved: list[str] = []

    def set_extra_http_headers(self, headers):
        pass

    def goto(self, url, wait_until=None, timeout=None):
        pass

    def wait_for_timeout(self, ms):
        pass

    def title(self):
        return self._title

    def content(self):
        return "<html><body>body</body></html>"

    def evaluate(self, js):
        pass

    def screenshot(self, path, full_page, type):
        Path(path).write_bytes(b"png")
        self.saved.append(path)


class _FakeBrowser:
    def __init__(self, page: _FakePage):
        self.page = page

    def new_page(self, viewport):
        return self.page

    def close(self):
        pass


class _FakePlaywright:
    def __init__(self, page: _FakePage):
        self.chromium = Mock(launch=Mock(return_value=_FakeBrowser(page)))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_login_wall_is_never_saved(shots_dir, monkeypatch):
    page = _FakePage("Log in to Vercel")
    monkeypatch.setattr(screenshot, "sync_playwright", lambda: _FakePlaywright(page))

    with pytest.raises(RuntimeError, match="login wall"):
        screenshot._capture_sync("https://x.vercel.app", shots_dir / "9.png")

    assert page.saved == []
    assert not (shots_dir / "9.png").exists()


def test_real_page_is_saved(shots_dir, monkeypatch):
    page = _FakePage("Joe's Cafe - Trusted Local Cafe")
    monkeypatch.setattr(screenshot, "sync_playwright", lambda: _FakePlaywright(page))

    screenshot._capture_sync("https://x.vercel.app", shots_dir / "9.png")

    assert (shots_dir / "9.png").read_bytes() == b"png"


def test_new_build_asks_for_a_fresh_screenshot(monkeypatch):
    """_process_lead_impl on a lead with no website row must not reuse
    whatever pipeline/screenshots/<id>.png an earlier database left behind."""
    lead = {"id": 43, "business_name": "Manchester Landscaping", "niche": "landscaper",
            "location": "Manchester", "contact_email": "info@example.com"}
    monkeypatch.setattr(design_agent, "available_niches", lambda: {"landscaper", "default"})
    monkeypatch.setattr(design_agent.db, "get_website_by_lead", Mock(side_effect=[None, {"id": 1}]))
    monkeypatch.setattr(design_agent, "build_site_files", lambda lead: {"index.html": "<html></html>"})
    monkeypatch.setattr(design_agent.github_api, "create_repo_with_files",
                        Mock(return_value=(object(), "https://github.com/x/r", "x/r")))
    monkeypatch.setattr(design_agent.vercel_api, "deploy_files",
                        Mock(return_value={"deployment_id": "dpl_1", "url": "https://x.vercel.app", "ready_state": "READY"}))
    monkeypatch.setattr(design_agent, "_validate_deployment_url", lambda deployment: deployment["url"])
    capture = Mock(return_value=("", ""))
    monkeypatch.setattr(design_agent, "_capture_and_publish_screenshot", capture)
    monkeypatch.setattr(design_agent.db, "insert_website", Mock(return_value=1))
    monkeypatch.setattr(design_agent.db, "update_lead_status", Mock())

    design_agent._process_lead_impl(lead)

    capture.assert_called_once_with(43, "https://x.vercel.app", fresh=True)
