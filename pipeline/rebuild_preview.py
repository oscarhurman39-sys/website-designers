"""Re-render and redeploy one lead's preview in place.

    python run.py rebuild <lead_id>

Use it after dropping photos/logo into pipeline/assets/<lead_id>/ by hand
(files that arrived over WhatsApp, a phone call, etc. -- see
docs/PHOTOS_AND_LOGO_PLAYBOOK.md) or after a template change you want a
specific prospect to see. Same repo, same Vercel project, same URL; the
lead's status is untouched and nothing is emailed.
"""
from __future__ import annotations

import sys
import argparse
import json
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402
from agents import design_agent  # noqa: E402
from utils import assets, db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-render and redeploy one lead's preview in place")
    parser.add_argument("lead_id", type=int)
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args()
    agent_id = (args.agent_id or "").strip()
    if not agent_id:
        print(json.dumps({"ok": False, "error": "--agent-id is required"}, sort_keys=True))
        return 1
    config.validate()
    lead_id = args.lead_id
    lead = db.get_lead(lead_id)
    if lead is None:
        print(json.dumps({"ok": False, "error": f"No lead with id {lead_id}"}, sort_keys=True))
        return 1
    print(f"[rebuild] Lead {lead_id} ({lead['business_name']}), assets on file: {assets.summary(lead_id)}")
    website = design_agent.rebuild_preview(lead)
    if website is None:
        print(json.dumps({"ok": False, "error": "This lead has no preview site yet", "lead_id": lead_id}, sort_keys=True))
        return 1
    db.log_state_history(lead_id, lead.get("status"), lead.get("status") or "designed", "rebuild", agent_id=agent_id)
    print(json.dumps({"ok": True, "action": "rebuild", "agent_id": agent_id, "lead_id": lead_id, "preview_url": website.get("preview_url")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
