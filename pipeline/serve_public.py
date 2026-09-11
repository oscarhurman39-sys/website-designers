"""Bring PUBLIC_BASE_URL up, or report honestly why it is not up.

    python run.py serve-public            # ensure it is running, then verify
    python run.py serve-public --status   # report only; starts nothing

Every cold email carries an unsubscribe link, a click-tracking link and a
Stripe checkout that all resolve against PUBLIC_BASE_URL. Those links are dead
whenever the local webhook server or the ngrok tunnel is down, so this needs to
be true before a send and stay true afterwards.

Why this exists when start_public.bat already does it:

  - `start_public.bat` is not safe to run twice. A second webhook server cannot
    bind port 5000 and a second ngrok cannot claim a static domain already in
    use, so a well-meaning second launch leaves two broken consoles behind.
    This module checks first and does nothing when things are already up.
  - The .bat prints "Started" whether or not either process actually came up.
    This one waits for /health to answer through the public URL and tells you
    the truth, which matters when an agent is reading the output.
  - The .bat hard-codes the ngrok domain, so it silently tunnels the wrong host
    if PUBLIC_BASE_URL is ever changed. The domain here is derived from .env.

Processes are launched in their own console windows, exactly as the .bat does,
so they outlive whatever started them and can be closed by hand.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import config

PIPELINE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent
WEBHOOK_PORT = 5000
LOCAL_HEALTH_URL = f"http://127.0.0.1:{WEBHOOK_PORT}/health"
# ngrok's own local dashboard. If it answers, an ngrok process is already up,
# and it reports which public URL that process actually claimed.
NGROK_API_URL = "http://127.0.0.1:4040/api/tunnels"
STARTUP_TIMEOUT_SECONDS = 45
POLL_SECONDS = 1.5


def _get_json(url: str, timeout: float = 3.0):
    import requests

    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def local_server_up() -> bool:
    body = _get_json(LOCAL_HEALTH_URL)
    return isinstance(body, dict) and body.get("ok") is True


def public_url_up() -> bool:
    body = _get_json(f"{config.PUBLIC_BASE_URL}/health", timeout=8.0)
    return isinstance(body, dict) and body.get("ok") is True


def ngrok_tunnels() -> Optional[list[str]]:
    """Public URLs ngrok is currently serving, or None when ngrok is not running."""
    body = _get_json(NGROK_API_URL)
    if not isinstance(body, dict):
        return None
    return [t.get("public_url", "") for t in body.get("tunnels", []) if isinstance(t, dict)]


def ngrok_domain() -> str:
    """The host ngrok must claim, taken from PUBLIC_BASE_URL so the two cannot drift."""
    return urlparse(config.PUBLIC_BASE_URL).hostname or ""


def _spawn(args: list[str], cwd: Path) -> None:
    """Launch a long-running process in its own console so it survives this one."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, cwd=str(cwd), creationflags=flags, close_fds=True)


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_SECONDS)
    return predicate()


def status_lines() -> tuple[bool, list[str]]:
    """(everything_up, human-readable lines). Starts nothing."""
    domain = ngrok_domain()
    local = local_server_up()
    tunnels = ngrok_tunnels()
    public = public_url_up()

    lines = [
        f"webhook server (127.0.0.1:{WEBHOOK_PORT}): " + ("UP" if local else "down"),
    ]
    if tunnels is None:
        lines.append("ngrok: not running")
    elif not tunnels:
        lines.append("ngrok: running but serving no tunnel")
    else:
        matched = any(domain and domain in url for url in tunnels)
        lines.append(
            f"ngrok: running, serving {', '.join(tunnels)}"
            + ("" if matched else f" -- which does NOT match PUBLIC_BASE_URL ({domain})")
        )
    lines.append(f"public URL ({config.PUBLIC_BASE_URL}/health): " + ("UP" if public else "unreachable"))
    if not public:
        lines.append("  -> every unsubscribe, click and payment link in a sent email is currently dead")
    return (local and public), lines


def ensure_up() -> int:
    """Start whatever is missing, then verify. Returns a process exit code."""
    domain = ngrok_domain()
    if not domain or "localhost" in domain or "127.0.0.1" in domain:
        print(f"PUBLIC_BASE_URL is {config.PUBLIC_BASE_URL!r}, which is not a public host. Nothing to serve.")
        return 1

    if local_server_up() and public_url_up():
        print("Already up. Nothing to do.")
        for line in status_lines()[1]:
            print(f"  {line}")
        return 0

    if not local_server_up():
        print(f"Starting webhook server on port {WEBHOOK_PORT} ...")
        _spawn([sys.executable, str(PIPELINE_DIR / "webhook_server.py")], PIPELINE_DIR)
        if not _wait_for(local_server_up, STARTUP_TIMEOUT_SECONDS):
            print(
                "FAILED: the webhook server did not answer /health in "
                f"{STARTUP_TIMEOUT_SECONDS}s. Check its console window for the error "
                "(a missing .env value stops it at startup)."
            )
            return 1
        print("  webhook server is up.")

    tunnels = ngrok_tunnels()
    if tunnels is None:
        print(f"Starting ngrok tunnel for {domain} ...")
        _spawn(["ngrok", "http", f"--url={domain}", str(WEBHOOK_PORT)], REPO_ROOT)
    elif not any(domain in url for url in tunnels):
        # A second ngrok cannot claim a domain the first one holds, so spawning
        # one here would just fail in a console nobody is watching.
        print(
            f"FAILED: ngrok is already running but serving {', '.join(tunnels) or 'nothing'}, "
            f"not {domain}. Close that ngrok window and run this again."
        )
        return 1

    if not _wait_for(public_url_up, STARTUP_TIMEOUT_SECONDS):
        print(
            f"FAILED: {config.PUBLIC_BASE_URL}/health did not answer in {STARTUP_TIMEOUT_SECONDS}s. "
            "The webhook server is up locally, so this is the tunnel. Check the ngrok window "
            "(an expired free session or a domain already claimed elsewhere both look like this)."
        )
        return 1

    print("Public URL is live.")
    for line in status_lines()[1]:
        print(f"  {line}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="report only; start nothing")
    args = parser.parse_args(argv)

    if args.status:
        ok, lines = status_lines()
        for line in lines:
            print(line)
        return 0 if ok else 1
    return ensure_up()


if __name__ == "__main__":
    raise SystemExit(main())
