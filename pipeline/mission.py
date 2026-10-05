"""Print the MissionAgent's assessment: where the line stands against
MISSION_DAILY_SENDS and what is holding it down, worst first.

    python pipeline/mission.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents import mission_agent  # noqa: E402

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(mission_agent.format_report(mission_agent.assess()))
