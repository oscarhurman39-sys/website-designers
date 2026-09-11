"""Headless service manager for the pipeline: no windows, no prompts, JSON out.

    python run.py ops start            # webhook server + ngrok + pipeline loop, all hidden
    python run.py ops stop             # stop the three cleanly (by PID file)
    python run.py ops restart
    python run.py ops status [--json]  # what is up; exit 0 only if everything is
    python run.py ops logs [name]      # tail pipeline/logs/<name>.log (webhook|ngrok|loop)

This is the entry point StarNet agents and the desktop app both use. Every
service runs detached with no console window and logs to pipeline/logs/, so
nothing pops up on the Commander's screen and a run never depends on a
terminal staying open. `start` is idempotent: it only launches what is
missing, and refuses to launch a second copy of anything.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "pipeline"
LOGS = PIPELINE / "logs"
RUN = PIPELINE / ".run"            # one PID file per service
PY = REPO / "venv" / "Scripts" / "python.exe"
WEBHOOK_PORT = 5000
NGROK_API = 4040

SERVICES = {
    # name: (command, cwd, port that proves it is up or None)
    "webhook": ([str(PY), str(PIPELINE / "webhook_server.py")], PIPELINE, WEBHOOK_PORT),
    "ngrok": (None, REPO, NGROK_API),          # command built at runtime from PUBLIC_BASE_URL
    "loop": ([str(PY), str(PIPELINE / "main.py")], PIPELINE, None),
}

_HIDDEN = 0
if sys.platform == "win32":
    # CREATE_NO_WINDOW only: the service keeps a *hidden* console that its own
    # child processes inherit. DETACHED_PROCESS gave it no console at all, so
    # every helper it spawned (Flask reloader, main.py workers) popped up a
    # visible black window on the Commander's screen.
    _HIDDEN = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


# --- helpers --------------------------------------------------------------------

def _public_domain() -> str:
    sys.path.insert(0, str(PIPELINE))
    import config  # noqa: E402
    host = config.PUBLIC_BASE_URL.replace("https://", "").replace("http://", "").split("/")[0]
    return "" if not host or "localhost" in host or "127.0.0.1" in host else host


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pid_file(name: str) -> Path:
    return RUN / f"{name}.pid"


def _read_pid(name: str) -> Optional[int]:
    try:
        return int(_pid_file(name).read_text().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=_HIDDEN)
    else:
        os.kill(pid, 15)


def _public_url_up() -> bool:
    # requests ships its own CA bundle (certifi); this Python's stdlib SSL
    # store rejects ngrok's chain with "certificate has expired".
    import requests
    sys.path.insert(0, str(PIPELINE))
    import config  # noqa: E402
    try:
        r = requests.get(config.PUBLIC_BASE_URL + "/health", headers={"ngrok-skip-browser-warning": "1"}, timeout=8)
        return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


def _ngrok_serving(domain: str) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{NGROK_API}/api/tunnels", timeout=3) as r:
            data = json.load(r)
        return any(domain in (t.get("public_url") or "") for t in data.get("tunnels", []))
    except Exception:  # noqa: BLE001
        return False


# --- status ---------------------------------------------------------------------

def status() -> dict:
    domain = _public_domain()
    webhook_up = port_open(WEBHOOK_PORT)
    ngrok_up = port_open(NGROK_API) and (not domain or _ngrok_serving(domain))
    loop_up = pid_alive(_read_pid("loop"))
    public_up = bool(domain) and webhook_up and ngrok_up and _public_url_up()
    return {
        "webhook": webhook_up,
        "ngrok": ngrok_up,
        "loop": loop_up,
        "public_url_reachable": public_up,
        "public_domain": domain,
        "all_up": webhook_up and ngrok_up and loop_up and public_up,
        "pids": {n: _read_pid(n) for n in SERVICES},
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _print_status(s: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(s, indent=1))
        return
    for name in ("webhook", "ngrok", "loop"):
        print(f"{name:<8} {'UP' if s[name] else 'down':<5} pid={s['pids'].get(name) or '-'}")
    print(f"public   {'UP' if s['public_url_reachable'] else 'down'}  {s['public_domain'] or '(no public domain configured)'}")
    print("ALL UP" if s["all_up"] else "NOT ALL UP")


# --- start / stop ---------------------------------------------------------------

def _spawn(name: str, cmd: list[str], cwd: Path) -> int:
    LOGS.mkdir(exist_ok=True)
    RUN.mkdir(exist_ok=True)
    log = open(LOGS / f"{name}.log", "a", buffering=1)
    log.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ops start: {' '.join(cmd)}\n")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            creationflags=_HIDDEN, close_fds=True)
    _pid_file(name).write_text(str(proc.pid))
    # The pipeline's own scheduler PID file, so `scheduler.py --check` and the
    # dashboard agree with us about whether the loop is running.
    if name == "loop":
        (PIPELINE / ".scheduler.pid").write_text(str(proc.pid))
    return proc.pid


def _wait(predicate, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(1)
    return predicate()


def start() -> int:
    domain = _public_domain()
    started, problems = [], []

    if not port_open(WEBHOOK_PORT):
        cmd, cwd, _ = SERVICES["webhook"]
        _spawn("webhook", cmd, cwd); started.append("webhook")
        if not _wait(lambda: port_open(WEBHOOK_PORT), 25):
            problems.append("webhook server did not open port 5000 (see pipeline/logs/webhook.log)")

    if domain:
        if not port_open(NGROK_API):
            _spawn("ngrok", ["ngrok", "http", f"--url={domain}", str(WEBHOOK_PORT), "--log=stdout"], REPO); started.append("ngrok")
            if not _wait(lambda: _ngrok_serving(domain), 25):
                problems.append("ngrok did not come up serving the public domain (see pipeline/logs/ngrok.log)")
        elif not _ngrok_serving(domain):
            problems.append(f"an ngrok is already running but not serving {domain}; stop it and run ops start again")
    else:
        problems.append("PUBLIC_BASE_URL is not a public host; ngrok not started")

    if not pid_alive(_read_pid("loop")):
        cmd, cwd, _ = SERVICES["loop"]
        _spawn("loop", cmd, cwd); started.append("loop")
        if not _wait(lambda: pid_alive(_read_pid("loop")), 5):
            problems.append("pipeline loop exited immediately (see pipeline/logs/loop.log)")

    if domain and not problems:
        # ngrok registers the tunnel a few seconds before the edge routes it.
        _wait(_public_url_up, 40)

    s = status()
    print(f"started: {', '.join(started) or 'nothing (already up)'}")
    for p in problems:
        print(f"PROBLEM: {p}")
    _print_status(s, False)
    return 0 if s["all_up"] and not problems else 1


def stop() -> int:
    stopped = []
    for name in ("loop", "ngrok", "webhook"):
        pid = _read_pid(name)
        if pid_alive(pid):
            _kill(pid); stopped.append(f"{name}({pid})")
        try:
            _pid_file(name).unlink()
        except OSError:
            pass
    # Anything we did not start ourselves but that holds our ports (e.g. a
    # start_all.bat window): find it by port and stop it too.
    if sys.platform == "win32":
        ps = (f"Get-NetTCPConnection -LocalPort {WEBHOOK_PORT},{NGROK_API} -State Listen -ErrorAction SilentlyContinue "
              "| Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, creationflags=_HIDDEN)
    for f in (PIPELINE / ".scheduler.pid",):
        try:
            f.unlink()
        except OSError:
            pass
    print(f"stopped: {', '.join(stopped) or 'nothing was running under ops'}")
    _print_status(status(), False)
    return 0


def logs(name: str, lines: int) -> int:
    path = LOGS / f"{name}.log"
    if not path.exists():
        print(f"no log yet: {path}")
        return 1
    tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    print("\n".join(tail))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start"); sub.add_parser("stop"); sub.add_parser("restart")
    st = sub.add_parser("status"); st.add_argument("--json", action="store_true")
    lg = sub.add_parser("logs"); lg.add_argument("name", nargs="?", default="loop", choices=sorted(SERVICES)); lg.add_argument("-n", type=int, default=40)
    args = parser.parse_args(argv)
    if args.cmd == "start":
        return start()
    if args.cmd == "stop":
        return stop()
    if args.cmd == "restart":
        stop(); return start()
    if args.cmd == "status":
        s = status(); _print_status(s, args.json); return 0 if s["all_up"] else 1
    if args.cmd == "logs":
        return logs(args.name, args.n)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
