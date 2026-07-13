"""Single-shot manual test run: no loop, no rate limits, just watch it work.

Takes the oldest lead with status 'new' and runs it through LeadAgent ->
DesignAgent -> SalesAgent, once, printing every step so you can see exactly
what happened. This is for manual testing/demoing -- for the real always-on
orchestrator (60s cadence, rate-limited sending, inbox polling, human-in-
the-loop console), use `main.py` instead.

Run with (either works -- Python puts this script's own directory on
sys.path regardless of your current working directory, which is how the
bare `import config` / `from agents import ...` below resolve either way):

    python quick_run.py                    # from inside pipeline/
    py pipeline\\quick_run.py                # from the repo root, Windows
    python pipeline/quick_run.py           # from the repo root, macOS/Linux

Needs a populated .env at the repo root first (see .env.example).
"""
from __future__ import annotations

import config
from agents import design_agent, lead_agent, sales_agent
from utils import db


def _print_step(label: str) -> None:
    print(f"\n{'=' * 60}\n{label}\n{'=' * 60}")


def main() -> None:
    config.validate()
    db.init_db()

    candidates = (
        db.list_leads_by_status_priority("designed")
        + db.list_leads_by_status_priority("researched")
        + db.list_leads_by_status_priority("new")
    )
    if not candidates:
        print(
            "No runnable leads found. Add one first:\n"
            "  - via the dashboard (streamlit run dashboard.py -> 'Add a lead manually')\n"
            "  - via a CSV: python -m agents.lead_agent path/to/leads.csv\n"
            "  - directly: python -c \"from utils import db; db.init_db(); "
            "db.insert_lead('Acme Cafe', 'cafe', 'Springfield')\""
        )
        return

    lead = candidates[0]
    print(
        f"Processing lead {lead['id']}: {lead['business_name']} "
        f"({lead['niche']}, {lead['location']}) from status '{lead['status']}'"
    )

    # --- Step 1: LeadAgent ---------------------------------------------------
    if lead["status"] == "new":
        _print_step("Step 1/3: LeadAgent -- researching contact info")
        try:
            lead_agent.research_lead(lead)
        except Exception as exc:  # noqa: BLE001 - print clearly, never crash silently
            print(f"LeadAgent FAILED: {exc}")
            return
    else:
        _print_step(f"Step 1/3: LeadAgent -- skipped because lead is already '{lead['status']}'")

    lead = db.get_lead(lead["id"])
    print(f"-> status: {lead['status']}")
    if lead["contact_email"]:
        print(f"-> contact email: {lead['contact_email']}")
    if lead["status"] != "researched":
        print(f"Stopping here -- lead did not reach 'researched' (got {lead['status']!r}).")
        print("Check pipeline/leads.db's state_history table for the reason (usually: no "
              "website or no email found).")
        return

    # --- Step 2: DesignAgent --------------------------------------------------
    website = db.get_website_by_lead(lead["id"])
    if lead["status"] == "researched":
        _print_step("Step 2/3: DesignAgent -- building + deploying preview")
        try:
            website = design_agent.process_lead(lead)
        except Exception as exc:  # noqa: BLE001
            print(f"DesignAgent FAILED: {exc}")
            return
    else:
        _print_step(f"Step 2/3: DesignAgent -- skipped because lead is already '{lead['status']}'")

    lead = db.get_lead(lead["id"])
    print(f"-> status: {lead['status']}")
    if website:
        print(f"-> preview: {website['preview_url']}")
        print(f"-> GitHub repo: {website['repo_url']}")
        if website.get("screenshot_url"):
            print(f"-> screenshot: {website['screenshot_url']}")
    if lead["status"] != "designed":
        print(f"Stopping here -- lead did not reach 'designed' (got {lead['status']!r}).")
        return

    # --- Step 3: SalesAgent ----------------------------------------------------
    _print_step("Step 3/3: SalesAgent -- drafting + sending cold email")
    try:
        sent = sales_agent.send_cold_email(lead)
    except Exception as exc:  # noqa: BLE001
        print(f"SalesAgent FAILED: {exc}")
        return

    lead = db.get_lead(lead["id"])
    print(f"-> status: {lead['status']}, email sent: {sent}")

    if config.ENABLE_LIVE_SEND:
        print("\nDone. Check your inbox.")
    else:
        print("\nDone. Dry run completed; no real email was sent.")


if __name__ == "__main__":
    main()
