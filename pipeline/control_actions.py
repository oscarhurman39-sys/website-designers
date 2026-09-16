"""Mutating StarNet control verbs for the website sales pipeline.

Every command is deliberately narrow: explicit lead IDs only where a lead is
changed, required agent attribution, JSON output, and one local file lock.
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Iterator

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

from utils import db  # noqa: E402

PAUSE_FLAG = PIPELINE_DIR / ".paused"
LOCK_PATH = PIPELINE_DIR / ".control-actions.lock"
CONTROL_LOG = PIPELINE_DIR / "logs" / "control.log"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _audit(agent_id: str, action: str, reason: str, lead_id: int | None = None) -> None:
    # One line per control action. Deliberately NOT state_history: the board
    # reads each lead's latest state_history row as its stage clock, so a
    # pipeline-wide pause logged per lead would reset every age to "now" and
    # blind stalled-work detection. Per-lead verbs still get their
    # state_history row from the db helper; this log is the cross-verb trail.
    CONTROL_LOG.parent.mkdir(parents=True, exist_ok=True)
    lead = f" lead={lead_id}" if lead_id is not None else ""
    with CONTROL_LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{_stamp()}] agent={agent_id} action={action}{lead} reason={reason}\n")


class ControlRefused(RuntimeError):
    pass


@contextmanager
def control_lock() -> Iterator[None]:
    try:
        handle = LOCK_PATH.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ControlRefused(f"control action already running: {LOCK_PATH}") from exc
    try:
        handle.write("exclusive control action lock\n")
        handle.close()
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def _require_agent_id(agent_id: str | None) -> str:
    value = (agent_id or "").strip()
    if not value:
        raise ControlRefused("--agent-id is required")
    return value


def _require_reason(reason: str | None) -> str:
    value = (reason or "").strip()
    if not value:
        raise ControlRefused("--reason is required")
    return value


def _paused_by() -> str | None:
    if not PAUSE_FLAG.exists():
        return None
    for line in PAUSE_FLAG.read_text(encoding="utf-8").splitlines():
        if line.startswith("agent_id="):
            return line[len("agent_id="):].strip() or None
    return None


def _pause(agent_id: str, reason: str) -> dict[str, Any]:
    was_paused = PAUSE_FLAG.exists()
    PAUSE_FLAG.write_text(f"agent_id={agent_id}\nreason={reason}\npaused_at={_stamp()}\n", encoding="utf-8")
    _audit(agent_id, "pause", reason)
    return {"ok": True, "action": "pause", "paused_before": was_paused, "paused_after": True, "agent_id": agent_id, "reason": reason}


def _resume(agent_id: str, reason: str) -> dict[str, Any]:
    was_paused = PAUSE_FLAG.exists()
    paused_by = _paused_by()
    if was_paused:
        PAUSE_FLAG.unlink()
    _audit(agent_id, "resume", reason)
    return {"ok": True, "action": "resume", "paused_before": was_paused, "paused_after": False, "paused_by": paused_by, "agent_id": agent_id, "reason": reason}


def _hold(lead_id: int, agent_id: str, reason: str) -> dict[str, Any]:
    result = db.hold_lead(lead_id, agent_id=agent_id, reason=reason)
    if not result.get("ok"):
        raise ControlRefused(str(result.get("reason") or "hold refused"))
    _audit(agent_id, "hold", reason, lead_id)
    return {"ok": True, "action": "hold", **result}


def _release(lead_id: int, agent_id: str, reason: str) -> dict[str, Any]:
    result = db.release_lead(lead_id, agent_id=agent_id, reason=reason)
    if not result.get("ok"):
        raise ControlRefused(str(result.get("reason") or "release refused"))
    _audit(agent_id, "release", reason, lead_id)
    return {"ok": True, "action": "release", **result}


def _close_silent(lead_id: int, agent_id: str, reason: str) -> dict[str, Any]:
    result = db.close_silent_lead(lead_id, agent_id=agent_id, reason=reason)
    if not result.get("ok"):
        raise ControlRefused(str(result.get("reason") or "close-silent refused"))
    _audit(agent_id, "close-silent", reason, lead_id)
    return {"ok": True, "action": "close-silent", **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mutating StarNet control verbs")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("pause", "resume"):
        p = sub.add_parser(name)
        p.add_argument("--agent-id", required=True)
        p.add_argument("--reason", required=True)
    for name in ("hold", "release", "close-silent"):
        p = sub.add_parser(name)
        p.add_argument("lead_id", type=int)
        p.add_argument("--agent-id", required=True)
        p.add_argument("--reason", default=name)

    try:
        args = parser.parse_args(argv)
        agent_id = _require_agent_id(args.agent_id)
        reason = _require_reason(args.reason)
        with control_lock():
            if args.command == "pause":
                payload = _pause(agent_id, reason)
            elif args.command == "resume":
                payload = _resume(agent_id, reason)
            elif args.command == "hold":
                payload = _hold(args.lead_id, agent_id, reason)
            elif args.command == "release":
                payload = _release(args.lead_id, agent_id, reason)
            else:
                payload = _close_silent(args.lead_id, agent_id, reason)
        print(json.dumps(payload, sort_keys=True))
        return 0
    except ControlRefused as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
