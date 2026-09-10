"""Casey Websites control panel: one window, one button per command.

Built into a Desktop .exe by tools/build_control_panel.bat (PyInstaller).
Every button runs the same command you would type in a terminal, in its own
console window, from the repo root, so nothing here can do anything the
documented commands can't. The status row polls whether the webhook server,
the ngrok tunnel and the pipeline loop are actually running.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

REPO = Path(r"D:\projects\website-designers")
PY = REPO / "venv" / "Scripts" / "python.exe"
PID_FILE = REPO / "pipeline" / ".scheduler.pid"
NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)


def run(args: list[str], title: str, keep_open: bool = True) -> None:
    """Open a console window and run the command from the repo root."""
    if not PY.exists():
        messagebox.showerror("Casey Websites", f"venv python not found:\n{PY}")
        return
    cmd = " ".join(f'"{a}"' if " " in a else a for a in args)
    tail = " & echo. & echo [done - press any key to close] & pause >nul" if keep_open else ""
    subprocess.Popen(f'cmd /c "title {title} & {cmd}{tail}"', cwd=str(REPO), creationflags=NEW_CONSOLE)


def run_py(script_args: list[str], title: str, keep_open: bool = True) -> None:
    run([str(PY), *script_args], title, keep_open)


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_alive(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return False
    return str(pid) in out


def loop_running() -> bool:
    try:
        return pid_alive(int(PID_FILE.read_text().strip()))
    except (OSError, ValueError):
        return False


class Panel(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Casey Websites")
        self.resizable(False, False)
        self.configure(padx=16, pady=12)
        try:
            self.iconbitmap(default=str(REPO / "tools" / "casey.ico"))
        except tk.TclError:
            pass

        ttk.Label(self, text="Casey Websites pipeline", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        self.status = ttk.Label(self, text="checking...", font=("Segoe UI", 9))
        self.status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        groups = [
            ("Start / stop", [
                ("Start everything", "webhook server + ngrok + pipeline loop", lambda: run(["cmd", "/c", str(REPO / "start_all.bat")], "start_all", keep_open=False)),
                ("Stop everything", "close the pipeline, webhook server and ngrok", self.stop_all),
                ("Preflight check", "go-live check, changes nothing", lambda: run_py(["run.py", "preflight"], "preflight")),
            ]),
            ("Look", [
                ("Dashboard", "leads and threads in the browser", lambda: run_py(["run.py", "dashboard"], "dashboard", keep_open=False)),
                ("Report", "leads / emailed / replied / won / revenue per niche", lambda: run_py(["run.py", "report"], "report")),
                ("Preview the cold email", "the exact email a lead would get, nothing sent", lambda: run_py(["run.py", "preview-email"], "preview-email")),
                ("Render sample sites", "every niche as screenshots in pipeline\\out", lambda: run_py([str(REPO / "pipeline" / "render_preview.py")], "render")),
            ]),
            ("Leads", [
                ("Source 10 leads (dry run)", "shows what would be added, writes nothing", lambda: run_py(["run.py", "source", "--dry-run", "--limit", "10"], "source dry-run")),
                ("Source 10 leads (real)", "adds 10 businesses to the database", self.source_real),
                ("Teardown dry run", "which expired previews would be removed", lambda: run_py([str(REPO / "pipeline" / "utils" / "teardown.py"), "--dry-run"], "teardown")),
            ]),
            ("Checks", [
                ("Test Slack alert", "proves alerts reach #leads", lambda: run_py(["run.py", "test-alert"], "test-alert")),
                ("Public URL status", "is the ngrok tunnel reachable", lambda: run_py(["run.py", "serve-public", "--status"], "serve-public")),
                ("Run the tests", "full test suite (a few minutes)", lambda: run_py(["-m", "pytest", "-q", "--basetemp=.pytest_tmp/panel"], "pytest")),
                ("Open .env", "settings and switches in Notepad", lambda: subprocess.Popen(["notepad.exe", str(REPO / ".env")])),
            ]),
        ]
        row = 2
        for heading, buttons in groups:
            ttk.Label(self, text=heading, font=("Segoe UI", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))
            row += 1
            for label, hint, fn in buttons:
                ttk.Button(self, text=label, width=26, command=fn).grid(row=row, column=0, sticky="w", pady=1)
                ttk.Label(self, text=hint, foreground="#555").grid(row=row, column=1, sticky="w", padx=(10, 0))
                row += 1
        ttk.Label(self, text=str(REPO), foreground="#888", font=("Segoe UI", 8)).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.after(200, self.refresh)

    def refresh(self) -> None:
        parts = [
            f"webhook server {'UP' if port_open(5000) else 'down'}",
            f"ngrok {'UP' if port_open(4040) else 'down'}",
            f"pipeline loop {'RUNNING' if loop_running() else 'stopped'}",
        ]
        self.status.config(text="  |  ".join(parts))
        self.after(5000, self.refresh)

    def source_real(self) -> None:
        if messagebox.askyesno("Source leads", "Add 10 real businesses to the database?\n\nThey will get preview sites built and, if live sending is on, emailed."):
            run_py(["run.py", "source", "--limit", "10"], "source")

    def stop_all(self) -> None:
        if not messagebox.askyesno("Stop everything", "Stop the pipeline loop, webhook server and ngrok?"):
            return
        ps = (
            "Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force; "
            "foreach ($p in Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'website-designers' -and ($_.CommandLine -match 'main.py|scheduler.py|webhook_server.py') }) { Stop-Process -Id $p.ProcessId -Force }"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
        self.refresh()


if __name__ == "__main__":
    if not REPO.exists():
        tk.Tk().withdraw()
        messagebox.showerror("Casey Websites", f"Repo not found at {REPO}")
        sys.exit(1)
    os.chdir(REPO)
    Panel().mainloop()
