"""Bounded, operator-selected batch preparation and sending.

This module deliberately never discovers leads: every operation is limited to
IDs supplied on the command line.  The normal loop must be paused first.
"""
from __future__ import annotations

import argparse
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config
from agents import design_agent, lead_agent, sales_agent
from utils import db, tracker

PIPELINE_DIR = Path(__file__).resolve().parent
PAUSE_FLAG = PIPELINE_DIR / ".paused"
LOCK_PATH = PIPELINE_DIR / ".reviewed-batch.lock"


class BatchError(RuntimeError):
    pass


@contextmanager
def batch_lock() -> Iterator[None]:
    try:
        handle = LOCK_PATH.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise BatchError(f"reviewed batch already running: {LOCK_PATH}") from exc
    try:
        handle.write("exclusive reviewed-batch lock\n")
        handle.close()
        yield
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def _require_paused() -> None:
    if not PAUSE_FLAG.exists():
        raise BatchError("main pipeline is not paused; create pipeline/.paused first")


def _selected(ids: list[int]) -> list[dict]:
    if not ids:
        raise BatchError("at least one --lead ID is required")
    result = []
    for lead_id in ids:
        lead = db.get_lead(lead_id)
        if lead is None:
            raise BatchError(f"lead {lead_id} does not exist")
        result.append(lead)
    return result


def _warn_duplicate_preview_risk(lead: dict) -> None:
    """Operator warning before one town/trade/template bucket is reused."""
    for risk in db.duplicate_preview_risks(lead):
        print(
            "[reviewed-batch] WARNING: duplicate preview risk before lead "
            f"{lead['id']}: lead {risk['lead_id']} ({risk['business_name']}) already used "
            f"{risk['template_niche']} for {risk['niche']} in {risk['location']}"
        )


def prepare(ids: list[int]) -> int:
    _require_paused()
    leads = _selected(ids)
    prepared = 0
    for lead in leads:
        current = db.get_lead(lead["id"])
        if current["status"] == "new":
            lead_agent.research_lead(current)
            current = db.get_lead(lead["id"])
        if current["status"] == "researched":
            _warn_duplicate_preview_risk(current)
            if design_agent.process_lead(current) is None:
                raise BatchError(f"lead {lead['id']} could not be designed")
            prepared += 1
        elif current["status"] == "designed":
            _warn_duplicate_preview_risk(current)
            prepared += 1
        else:
            raise BatchError(f"lead {lead['id']} is {current['status']!r}, not new/researched/designed")
    return prepared


def _sendable(lead: dict) -> bool:
    email = (lead.get("contact_email") or "").strip()
    return (
        lead.get("status") == "designed"
        and bool(email)
        and not lead.get("unsubscribed")
        and not db.is_unsubscribed(email)
        and db.get_website_by_lead(lead["id"]) is not None
    )


def send(ids: list[int], max_seconds: int = 600) -> int:
    _require_paused()
    if not config.ENABLE_LIVE_SEND:
        raise BatchError("--send requires ENABLE_LIVE_SEND=true")
    if max_seconds <= 0:
        raise BatchError("--max-seconds must be positive")
    leads = _selected(ids)
    deadline = time.monotonic() + max_seconds
    sent = 0
    for lead_id in ids:
        while not sales_agent._can_send_now():
            if time.monotonic() >= deadline:
                return sent
            time.sleep(min(1.0, max(0.01, deadline - time.monotonic())))
        lead = db.get_lead(lead_id)
        if lead is None or not _sendable(lead):
            continue
        if sales_agent._cooldown_blocked_until(lead):
            continue
        if not tracker.public_endpoint_up():
            raise BatchError(f"public health check failed before lead {lead_id}; stopped fail-closed")
        if time.monotonic() >= deadline:
            return sent
        if sales_agent.send_cold_email(lead):
            sent += 1
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded reviewed lead batch")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--send", action="store_true")
    parser.add_argument("--lead", action="append", type=int, required=True)
    parser.add_argument("--max-seconds", type=int, default=600)
    args = parser.parse_args(argv)
    try:
        with batch_lock():
            count = prepare(args.lead) if args.prepare else send(args.lead, args.max_seconds)
        print(f"reviewed-batch: {'prepared' if args.prepare else 'sent'} {count}")
        return 0
    except BatchError as exc:
        print(f"reviewed-batch: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
