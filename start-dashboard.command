#!/usr/bin/env bash
# Double-click launcher for the pipeline dashboard (macOS; on Linux run it
# from a terminal or point a .desktop launcher's Exec= at it).
#
# Does the whole setup itself, and is safe to run repeatedly -- every step is
# skipped if it's already done, so after the first run this is just a fast
# launch.
#
# First run on macOS: right-click -> Open (Gatekeeper), and if needed:
#   chmod +x start-dashboard.command
set -u
cd "$(dirname "$0")" || exit 1
VENV_PY="venv/bin/python"

die() { printf '\n  %s\n\n' "$1"; read -r -p "Press Return to close..." _; exit 1; }

# --- Find a usable Python ----------------------------------------------------
BOOT_PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then BOOT_PY="$candidate"; break; fi
done
[ -n "$BOOT_PY" ] || die "Python 3 isn't installed, or isn't on your PATH. Install it from https://www.python.org/downloads/ and run this again."

# --- Create the virtual environment (first run only) -------------------------
if [ ! -x "$VENV_PY" ]; then
    echo "[setup] Creating the virtual environment..."
    "$BOOT_PY" -m venv venv || die "Could not create the virtual environment. The output above says why."
fi

# --- Install dependencies (only when something is missing) -------------------
# streamlit is the canary: if it imports, a previous run already completed the
# install, so skip it and launch straight away.
if ! "$VENV_PY" -c "import streamlit" >/dev/null 2>&1; then
    echo "[setup] Installing dependencies -- this takes a few minutes the first time..."
    "$VENV_PY" -m pip install --quiet --upgrade pip
    "$VENV_PY" -m pip install -r requirements.txt \
        || die "Dependency install failed. The output above says why -- a common cause is a full disk; a few GB need to be free."
    echo "[setup] Downloading the browser used for preview screenshots..."
    "$VENV_PY" -m playwright install chromium
fi

# --- Create .env from the template (first run only) --------------------------
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[setup] Created .env from the template -- it needs your real keys."
fi

# --- Check the config --------------------------------------------------------
if ! "$VENV_PY" pipeline/config.py; then
    die "Fill in the missing values listed above in .env, then run this again."
fi

# --- Launch ------------------------------------------------------------------
echo
echo "[ok] Starting the dashboard. Your browser should open automatically."
echo "     Leave this window open -- closing it stops the dashboard."
echo
exec "$VENV_PY" -m streamlit run dashboard.py
