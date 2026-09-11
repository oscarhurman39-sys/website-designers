"""Casey Websites control panel: one window, one button per command.

Built into a Desktop .exe by tools/build_control_panel.bat (PyInstaller).
Start/stop/status go through tools/ops.py, which runs the services hidden
and logs to pipeline/logs/, so nothing flashes on screen. Buttons that are
meant to be read (report, preflight, dashboard...) open one console each,
on purpose, because the operator asked to see them.

Only one copy of the panel can run at a time (a lock file with a live PID),
and every background call is spawned with no window, which is what stops
the periodic status check from flickering console windows.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

REPO = Path(r"D:\projects\website-designers")
PY = REPO / "venv" / "Scripts" / "python.exe"
OPS = REPO / "tools" / "ops.py"
LOCK = REPO / "pipeline" / ".run" / "control_panel.pid"

NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def hidden(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command with no console window at all."""
    return subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


def console(args: list[str], title: str, keep_open: bool = True) -> None:
    """Open ONE console window for a command the operator wants to read."""
    cmd = " ".join(f'"{a}"' if " " in a else a for a in args)
    tail = " & echo. & echo [done - press any key to close] & pause >nul" if keep_open else ""
    subprocess.Popen(f'cmd /c "title {title} & {cmd}{tail}"', cwd=str(REPO), creationflags=NEW_CONSOLE)


def pid_alive(pid: int) -> bool:
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return bool(ok) and code.value == 259


def take_single_instance_lock() -> bool:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        other = int(LOCK.read_text().strip())
        if other != os.getpid() and pid_alive(other):
            return False
    except (OSError, ValueError):
        pass
    LOCK.write_text(str(os.getpid()))
    return True


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
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._busy = False

        ttk.Label(self, text="Casey Websites pipeline", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        self.status = ttk.Label(self, text="checking...", font=("Segoe UI", 9))
        self.status.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        groups = [
            ("Services (run hidden, no windows)", [
                ("Start everything", "webhook server + ngrok + pipeline loop, in the background", lambda: self.ops("start")),
                ("Stop everything", "stop all three", lambda: self.ops("stop")),
                ("Restart everything", "stop, then start", lambda: self.ops("restart")),
                ("Show loop log", "last 60 lines of the pipeline log", lambda: console([str(PY), str(OPS), "logs", "loop", "-n", "60"], "loop log")),
            ]),
            ("Look", [
                ("Preflight check", "go-live check, changes nothing", lambda: console([str(PY), "run.py", "preflight"], "preflight")),
                ("Dashboard", "leads and threads in the browser", lambda: console([str(PY), "run.py", "dashboard"], "dashboard", keep_open=False)),
                ("Report", "leads / emailed / replied / won / revenue per niche", lambda: console([str(PY), "run.py", "report"], "report")),
                ("Preview the cold email", "the exact email a lead would get, nothing sent", lambda: console([str(PY), "run.py", "preview-email"], "preview-email")),
            ]),
            ("Leads", [
                ("Source 10 leads (dry run)", "shows what would be added, writes nothing", lambda: console([str(PY), "run.py", "source", "--dry-run", "--limit", "10"], "source dry-run")),
                ("Source 10 leads (real)", "adds 10 businesses to the database", self.source_real),
                ("Teardown dry run", "which expired previews would be removed", lambda: console([str(PY), str(REPO / "pipeline" / "utils" / "teardown.py"), "--dry-run"], "teardown")),
            ]),
            ("Checks", [
                ("Test Slack alert", "proves alerts reach #leads", lambda: console([str(PY), "run.py", "test-alert"], "test-alert")),
                ("Run the tests", "full test suite (a few minutes)", lambda: console([str(PY), "-m", "pytest", "-q", "--basetemp=.pytest_tmp/panel"], "pytest")),
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
        ttk.Label(self, text=f"{REPO}  ·  agents use: python run.py ops start|stop|status", foreground="#888", font=("Segoe UI", 8)).grid(row=row, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.after(300, self.refresh)

    # --- background work, never on the UI thread ------------------------------

    def refresh(self) -> None:
        if not self._busy:
            threading.Thread(target=self._refresh_worker, daemon=True).start()
        self.after(15000, self.refresh)

    def _refresh_worker(self) -> None:
        try:
            out = hidden([str(PY), str(OPS), "status", "--json"], timeout=30).stdout
            s = json.loads(out)
            text = "  |  ".join([
                f"webhook {'UP' if s['webhook'] else 'down'}",
                f"ngrok {'UP' if s['ngrok'] else 'down'}",
                f"loop {'RUNNING' if s['loop'] else 'stopped'}",
                f"public URL {'UP' if s['public_url_reachable'] else 'down'}",
            ])
        except Exception as exc:  # noqa: BLE001
            text = f"status unavailable: {exc}"
        self.after(0, lambda: self.status.config(text=text))

    def ops(self, verb: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.status.config(text=f"{verb}ing services... (hidden, up to a minute)")

        def work() -> None:
            try:
                r = hidden([str(PY), str(OPS), verb], timeout=120)
                summary = (r.stdout or "").strip().splitlines()
                msg = "\n".join(summary[-6:]) or f"{verb}: done"
                ok = r.returncode == 0
            except Exception as exc:  # noqa: BLE001
                msg, ok = f"{verb} failed: {exc}", False
            self._busy = False
            self.after(0, lambda: (messagebox.showinfo if ok else messagebox.showwarning)("Casey Websites", msg))
            self.after(50, self.refresh)

        threading.Thread(target=work, daemon=True).start()

    def source_real(self) -> None:
        if messagebox.askyesno("Source leads", "Add 10 real businesses to the database?\n\nThey will get preview sites built and, if live sending is on, emailed."):
            console([str(PY), "run.py", "source", "--limit", "10"], "source")

    def close(self) -> None:
        try:
            LOCK.unlink()
        except OSError:
            pass
        self.destroy()


if __name__ == "__main__":
    if not REPO.exists() or not PY.exists():
        tk.Tk().withdraw()
        messagebox.showerror("Casey Websites", f"Repo or venv not found under {REPO}")
        sys.exit(1)
    if not take_single_instance_lock():
        tk.Tk().withdraw()
        messagebox.showinfo("Casey Websites", "The control panel is already open.")
        sys.exit(0)
    os.chdir(REPO)
    Panel().mainloop()
