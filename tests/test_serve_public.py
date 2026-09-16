"""serve_public.py: the command that brings PUBLIC_BASE_URL up.

It exists so an agent (or a tired operator) can call it repeatedly without
breaking anything and can trust what it prints. Nothing here touches the
network or spawns a process -- every probe and the spawn itself is stubbed."""
from __future__ import annotations

import pytest

import config
import serve_public


@pytest.fixture
def env(monkeypatch):
    """Public URL configured, nothing running, spawning stubbed out."""
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://tunnel.example.test")
    spawned: list[list[str]] = []
    monkeypatch.setattr(serve_public, "_spawn", lambda args, cwd: spawned.append(args))
    # Make the bounded waits instant so a test never sleeps.
    monkeypatch.setattr(serve_public, "POLL_SECONDS", 0)
    monkeypatch.setattr(serve_public, "STARTUP_TIMEOUT_SECONDS", 0)
    return spawned


def _state(monkeypatch, *, local: bool, public: bool, tunnels):
    monkeypatch.setattr(serve_public, "local_server_up", lambda: local)
    monkeypatch.setattr(serve_public, "public_url_up", lambda: public)
    monkeypatch.setattr(serve_public, "ngrok_tunnels", lambda: tunnels)


def test_domain_comes_from_public_base_url_not_a_hardcoded_string(monkeypatch):
    """start_public.bat hard-codes the domain and silently tunnels the wrong
    host when PUBLIC_BASE_URL changes. This must follow the config."""
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://moved-to-a-new-host.ngrok-free.dev")
    assert serve_public.ngrok_domain() == "moved-to-a-new-host.ngrok-free.dev"


def test_already_up_starts_nothing(env, monkeypatch, capsys):
    _state(monkeypatch, local=True, public=True, tunnels=["https://tunnel.example.test"])
    assert serve_public.ensure_up() == 0
    assert env == []  # nothing spawned
    assert "Already up" in capsys.readouterr().out


def test_starts_both_when_nothing_is_running(env, monkeypatch):
    """Each process only comes up once its own spawn has happened, which is
    what makes this a real ordering check rather than a canned sequence."""
    up = {"local": False, "public": False}
    monkeypatch.setattr(serve_public, "local_server_up", lambda: up["local"])
    monkeypatch.setattr(serve_public, "public_url_up", lambda: up["public"])
    monkeypatch.setattr(serve_public, "ngrok_tunnels", lambda: None)

    def spawn(args, cwd):
        env.append(args)
        if args[0] == "ngrok":
            up["public"] = True
        else:
            up["local"] = True

    monkeypatch.setattr(serve_public, "_spawn", spawn)

    assert serve_public.ensure_up() == 0
    assert len(env) == 2
    assert env[0][-1].endswith("webhook_server.py")  # local server first
    assert env[1][0] == "ngrok" and "--url=tunnel.example.test" in env[1]


def test_only_starts_the_tunnel_when_the_local_server_is_already_up(env, monkeypatch):
    public = {"v": False}
    monkeypatch.setattr(serve_public, "local_server_up", lambda: True)
    monkeypatch.setattr(serve_public, "public_url_up", lambda: public["v"])
    monkeypatch.setattr(serve_public, "ngrok_tunnels", lambda: None)

    def spawn(args, cwd):
        env.append(args)
        public["v"] = True

    monkeypatch.setattr(serve_public, "_spawn", spawn)
    assert serve_public.ensure_up() == 0
    assert len(env) == 1 and env[0][0] == "ngrok"


def test_refuses_to_spawn_a_second_ngrok_over_a_claimed_domain(env, monkeypatch, capsys):
    """A second ngrok cannot claim a static domain the first one holds, so
    spawning one would only fail in a console nobody is watching."""
    _state(monkeypatch, local=True, public=False, tunnels=["https://someone-elses-domain.ngrok-free.dev"])
    assert serve_public.ensure_up() == 1
    assert env == []
    out = capsys.readouterr().out
    assert "already running" in out and "Close that ngrok window" in out


def test_refuses_a_local_public_base_url(env, monkeypatch, capsys):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:5000")
    assert serve_public.ensure_up() == 1
    assert env == []
    assert "not a public host" in capsys.readouterr().out


def test_reports_failure_when_the_tunnel_never_answers(env, monkeypatch, capsys):
    _state(monkeypatch, local=True, public=False, tunnels=None)
    assert serve_public.ensure_up() == 1
    assert len(env) == 1  # it did try
    assert "did not answer" in capsys.readouterr().out


def test_status_never_starts_anything_and_exits_nonzero_when_down(env, monkeypatch, capsys):
    _state(monkeypatch, local=False, public=False, tunnels=None)
    assert serve_public.main(["--status"]) == 1
    assert env == []
    out = capsys.readouterr().out
    assert "webhook server (127.0.0.1:5000): down" in out
    assert "ngrok: not running" in out
    assert "link in a sent email is currently dead" in out


def test_status_flags_a_tunnel_pointing_at_the_wrong_host(env, monkeypatch, capsys):
    _state(monkeypatch, local=True, public=False, tunnels=["https://stale-host.ngrok-free.dev"])
    serve_public.main(["--status"])
    assert "does NOT match PUBLIC_BASE_URL" in capsys.readouterr().out


def test_health_probes_reject_a_200_that_is_not_our_server(monkeypatch):
    """ngrok's offline page and any captive portal answer 200 with HTML."""
    monkeypatch.setattr(serve_public, "_get_json", lambda url, timeout=3.0: "<html>ngrok</html>")
    assert serve_public.local_server_up() is False
    assert serve_public.public_url_up() is False
    monkeypatch.setattr(serve_public, "_get_json", lambda url, timeout=3.0: {"ok": True})
    assert serve_public.local_server_up() is True
