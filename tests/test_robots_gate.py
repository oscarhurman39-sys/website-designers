"""The robots.txt gate must fail open only when robots.txt really is
unreachable -- not whenever a WAF dislikes the user agent.

RobotFileParser.read() fetches with urllib's own `Python-urllib/3.x` user
agent and swallows HTTPError internally: 401, 403 and every 5xx came back as
"disallowed" while the caller's `except Exception: return True` never ran. 45
of the 164 leads sourced by 2026-09-20 died on "Could not fetch website", and
this was a large part of it.
"""
from __future__ import annotations

import requests

from agents import lead_agent

URL = "https://acme.test/about"


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code, self.text = status_code, text


def _robots(monkeypatch, resp, calls: list | None = None):
    def fake_get(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        if isinstance(resp, Exception):
            raise resp
        return resp
    monkeypatch.setattr(lead_agent.requests, "get", fake_get)


def test_robots_is_fetched_as_this_pipeline_not_as_python_urllib(monkeypatch):
    calls: list = []
    _robots(monkeypatch, _Resp(200, "User-agent: *\nAllow: /\n"), calls)
    assert lead_agent._robots_allows(URL) is True
    url, kwargs = calls[0]
    assert url == "https://acme.test/robots.txt"
    assert kwargs["headers"] == lead_agent.REQUEST_HEADERS
    assert kwargs["timeout"] == lead_agent.REQUEST_TIMEOUT


def test_an_explicit_disallow_is_obeyed(monkeypatch):
    _robots(monkeypatch, _Resp(200, "User-agent: *\nDisallow: /about\n"))
    assert lead_agent._robots_allows(URL) is False


def test_a_missing_robots_file_allows_the_fetch(monkeypatch):
    _robots(monkeypatch, _Resp(404))
    assert lead_agent._robots_allows(URL) is True


def test_unauthorized_and_forbidden_mean_disallow(monkeypatch):
    for code in (401, 403):
        _robots(monkeypatch, _Resp(code))
        assert lead_agent._robots_allows(URL) is False, code


def test_a_server_error_means_disallow(monkeypatch):
    _robots(monkeypatch, _Resp(503))
    assert lead_agent._robots_allows(URL) is False


def test_a_timeout_fails_open(monkeypatch):
    """The one case the old docstring actually described."""
    _robots(monkeypatch, requests.Timeout("too slow"))
    assert lead_agent._robots_allows(URL) is True


def test_a_broken_robots_file_fails_open(monkeypatch):
    _robots(monkeypatch, _Resp(200, "\x00\x01 not really robots syntax"))
    assert lead_agent._robots_allows(URL) is True
