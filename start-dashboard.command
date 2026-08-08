#!/usr/bin/env bash
# Double-click launcher for the pipeline dashboard (macOS; on Linux run it
# from a terminal or wire it to a .desktop entry). Starts the dashboard and
# opens it in your browser; use its Start pipeline button from there.
#
# First run on macOS: right-click -> Open (Gatekeeper), and if needed:
#   chmod +x start-dashboard.command
cd "$(dirname "$0")" || exit 1
[ -f venv/bin/activate ] && source venv/bin/activate
exec python3 -m streamlit run dashboard.py
