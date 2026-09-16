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
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))

import config  # noqa: E402
from agents import design_agent  # noqa: E402
from utils import assets, db  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("Usage: python run.py rebuild <lead_id>")
        return 1
    config.validate()
    lead_id = int(sys.argv[1])
    lead = db.get_lead(lead_id)
    if lead is None:
        print(f"No lead with id {lead_id}")
        return 1
    print(f"[rebuild] Lead {lead_id} ({lead['business_name']}), assets on file: {assets.summary(lead_id)}")
    website = design_agent.rebuild_preview(lead)
    if website is None:
        print("[rebuild] This lead has no preview site yet -- it will be built by the normal design step.")
        return 1
    print(f"[rebuild] Live: {website['preview_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
