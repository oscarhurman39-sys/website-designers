"""Streamlit dashboard for operators.

Run with: `streamlit run dashboard.py` (from the repo root).

Shows every lead with status/preview link/last email timestamp, lets you
filter by status, drill into a lead's email thread, retry failed leads, pause/
resume the main.py loop (via a shared flag file), and export the leads
table to CSV.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# dashboard.py lives at the repo root, but config.py / utils / agents are
# flat, non-package modules under pipeline/ (main.py, webhook_server.py,
# etc. all import them the same bare way, assuming pipeline/ is on
# sys.path). Add pipeline/ to sys.path so this file can reuse them as-is.
_PIPELINE_DIR = Path(__file__).resolve().parent / "pipeline"
sys.path.insert(0, str(_PIPELINE_DIR))

import config  # noqa: E402
import scheduler  # noqa: E402
from agents import design_agent, lead_agent, sales_agent  # noqa: E402
from utils import db, tracer  # noqa: E402

PAUSE_FLAG = _PIPELINE_DIR / ".paused"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

st.set_page_config(page_title="Cold Email Sales Pipeline", layout="wide")
db.init_db()  # safe/idempotent if main.py hasn't started the DB yet


def _is_paused() -> bool:
    return PAUSE_FLAG.exists()


def _set_paused(paused: bool) -> None:
    if paused:
        PAUSE_FLAG.touch()
    else:
        PAUSE_FLAG.unlink(missing_ok=True)


st.title("Cold Email Sales Pipeline")


def _log_tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:]) or "(log is empty)"
    except OSError:
        return "(no log available)"


# --- Orchestrator process controls -------------------------------------------
# Start/stop main.py itself, via scheduler.py's PID-file-guarded background
# start -- the same mechanism cron's --check mode uses. A main.py started here
# is headless: automation runs, but the operator console commands (takeover /
# payment ready / transfer) need a real terminal session (see README
# "Deployment" and the limitation note in scheduler.py's docstring).
_orch_pid = scheduler.current_pid()
col_start, col_stop, col_proc = st.columns([1, 1, 4])
with col_start:
    if st.button("Start pipeline", disabled=_orch_pid is not None):
        scheduler.run_check_once()
        time.sleep(2)  # long enough for a misconfigured main.py to die on config.validate()
        if scheduler.current_pid() is None:
            st.error(
                "main.py exited immediately after starting. Last scheduler.log lines:\n\n"
                f"```\n{_log_tail(scheduler.LOG_FILE)}\n```"
            )
        else:
            st.rerun()
with col_stop:
    if st.button("Stop pipeline", disabled=_orch_pid is None):
        if scheduler.stop_main():
            st.rerun()
        else:
            st.error("main.py did not stop within the timeout -- check it manually (see pipeline/.scheduler.pid).")
with col_proc:
    if _orch_pid is not None:
        st.write(f"**Orchestrator:** running (pid {_orch_pid}) -- logs in `pipeline/scheduler.log`")
    else:
        st.write("**Orchestrator:** not running")
st.caption(
    "Started from here, main.py runs headless (exactly like cron's `scheduler.py --check`): "
    "research, design, sending and inbox polling all work, but `takeover` / `payment ready` / "
    "`transfer` need main.py in a real terminal (tmux) instead. If main.py runs under the "
    "foreground supervisor (`python scheduler.py`), stop it from that terminal, not here -- "
    "the supervisor restarts whatever this Stop button kills."
)

st.divider()

# --- Pause/resume controls ---------------------------------------------------
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    if st.button("Pause pipeline", disabled=_is_paused()):
        _set_paused(True)
        st.rerun()
with col2:
    if st.button("Resume pipeline", disabled=not _is_paused()):
        _set_paused(False)
        st.rerun()
with col3:
    st.write("**Loop:** " + ("PAUSED (flag set -- a running main.py idles)" if _is_paused() else "ACTIVE"))

st.divider()

# --- Add a lead manually --------------------------------------------------------
st.subheader("Add a lead manually")
_available_niches = sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir()) if TEMPLATES_DIR.exists() else []
with st.form("add_lead_form", clear_on_submit=True):
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        new_business_name = st.text_input("Business name")
    with col_b:
        new_niche = st.selectbox("Niche", _available_niches) if _available_niches else st.text_input("Niche")
    with col_c:
        new_email = st.text_input("Contact email (optional)")
    if st.form_submit_button("Add lead"):
        if not new_business_name.strip() or not str(new_niche).strip():
            st.error("Business name and niche are required.")
        else:
            new_lead_id = db.insert_lead(new_business_name.strip(), str(new_niche).strip(), location="")
            if new_email.strip():
                # A known email means LeadAgent's job (find one) is already
                # done -- skip straight to 'researched' so DesignAgent picks
                # it up on the next cycle instead of re-scraping for it.
                db.update_lead_fields(new_lead_id, contact_email=new_email.strip())
                db.update_lead_status(new_lead_id, "researched", notes="Manually added via dashboard with known email")
                st.success(f"Added lead {new_lead_id} ({new_business_name}) as 'researched' -- ready for DesignAgent.")
            else:
                st.success(f"Added lead {new_lead_id} ({new_business_name}) as 'new' -- LeadAgent will research it next cycle.")
            st.rerun()

st.divider()

# --- Quick-run: process the next 'new' lead through every agent right now ------
st.subheader("Quick-run next lead")
st.caption(
    "Runs LeadAgent -> DesignAgent -> SalesAgent on the oldest 'new' lead, right now, "
    "in this dashboard process. Sends a real cold email if it gets that far -- this "
    "bypasses the hourly/daily rate-limit gate that main.py's normal loop enforces "
    "(sales_agent._can_send_now), since it's a single deliberate manual action, not "
    "automation. Each step still stops early if the previous one didn't succeed."
)
if st.button("Process next 'new' lead now"):
    new_leads = db.list_leads_by_status("new")
    if not new_leads:
        st.warning("No leads with status 'new' to process.")
    else:
        target = new_leads[0]
        try:
            with st.status(f"Processing lead {target['id']} ({target['business_name']})...", expanded=True) as box:
                st.write("Running LeadAgent...")
                lead_agent.research_lead(target)
                current = db.get_lead(target["id"])
                st.write(f"-> status: {current['status']}")

                if current["status"] == "researched":
                    st.write("Running DesignAgent...")
                    website = design_agent.process_lead(current)
                    current = db.get_lead(target["id"])
                    detail = f", preview: {website['preview_url']}" if website else ""
                    st.write(f"-> status: {current['status']}{detail}")

                    if current["status"] == "designed":
                        st.write("Running SalesAgent...")
                        sent = sales_agent.send_cold_email(current)
                        current = db.get_lead(target["id"])
                        st.write(f"-> status: {current['status']}, email sent: {sent}")

                box.update(label=f"Finished processing lead {target['id']}", state="complete")
        except Exception as exc:  # noqa: BLE001 - surface it in the UI rather than crashing the page
            st.error(f"Quick-run failed partway through: {exc}")
        st.rerun()

st.divider()

# --- Leads table --------------------------------------------------------------
leads = db.list_all_leads()
if not leads:
    st.info("No leads yet. Add one above, drop a CSV into pipeline/leads_inbox/, and start main.py.")
    st.stop()

rows = []
for lead in leads:
    website = db.get_website_by_lead(lead["id"])
    rows.append(
        {
            "id": lead["id"],
            "business_name": lead["business_name"],
            "niche": lead["niche"],
            "status": lead["status"],
            "contact_email": lead["contact_email"],
            "preview_url": website["preview_url"] if website else "",
            "last_email": db.get_last_email_timestamp(lead["id"]) or "",
            "unsubscribed": bool(lead["unsubscribed"]),
        }
    )
df = pd.DataFrame(rows)

status_options = ["(all)"] + sorted(df["status"].unique().tolist())
selected_status = st.selectbox("Filter by status", status_options)
filtered = df if selected_status == "(all)" else df[df["status"] == selected_status]

st.dataframe(
    filtered,
    use_container_width=True,
    column_config={
        "preview_url": st.column_config.LinkColumn("Preview"),
    },
)

st.download_button(
    "Export filtered leads to CSV",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name="leads_export.csv",
    mime="text/csv",
)

st.divider()

# --- Lead detail / email thread ------------------------------------------------
st.subheader("Lead detail")
lead_id = st.selectbox("Select a lead id", filtered["id"].tolist() if not filtered.empty else [])
if lead_id:
    lead = db.get_lead(int(lead_id))
    website = db.get_website_by_lead(int(lead_id))

    st.json(
        {
            "business_name": lead["business_name"],
            "niche": lead["niche"],
            "location": lead["location"],
            "status": lead["status"],
            "pain_point": lead["pain_point"],
            "testimonial": lead["testimonial"],
            "preview_url": website["preview_url"] if website else None,
            "repo_url": website["repo_url"] if website else None,
            "transferred": bool(website["transferred"]) if website else None,
        }
    )

    if lead["status"] == "bounced":
        if st.button(f"Retry bounce for lead {lead_id} (reset to 'researched')"):
            db.update_lead_status(int(lead_id), "researched", notes="Manually retried from dashboard")
            st.success("Lead reset to 'researched'. It will be re-designed/emailed on the next cycle.")
            st.rerun()

    st.write("**Email thread**")
    thread = db.get_email_threads(int(lead_id))
    if not thread:
        st.write("_No emails yet._")
    for msg in thread:
        direction_label = "-> outbound" if msg["direction"] == "outbound" else "<- inbound"
        with st.expander(f"{direction_label} | {msg['timestamp']} | {msg['subject']}"):
            st.write(f"From: {msg['from_addr']}  To: {msg['to_addr']}")
            if msg["classification"]:
                st.write(f"Classification: `{msg['classification']}`")
            st.text(msg["body"])

st.divider()

# --- Agent trace history -----------------------------------------------------
# Local pipeline/traces.json is always written by utils/tracer.py and is the
# only readable source of trace history in this app: the VoltAgent Python
# SDK is write-only (create/update history & events, no list/query
# endpoint), so even in cloud-mirroring mode this file is what we can show
# here -- full trace history for cloud mode lives in the VoltAgent Cloud UI.
st.subheader("Agent trace history")

cloud_enabled = bool(config.VOLTAGENT_PUBLIC_KEY and config.VOLTAGENT_SECRET_KEY)
if cloud_enabled:
    st.caption(
        "VoltAgent Cloud mirroring is enabled -- spans are also sent to "
        f"{config.VOLTAGENT_BASE_URL}. The Python SDK has no read-back API, "
        "so this table (from pipeline/traces.json) is still the local view; "
        "see your VoltAgent Cloud dashboard for the hosted one."
    )
else:
    st.caption(
        "Local tracing only (set VOLTAGENT_PUBLIC_KEY / VOLTAGENT_SECRET_KEY "
        "in .env to also mirror spans to VoltAgent Cloud)."
    )

traces = tracer.load_local_traces()
if not traces:
    st.write("_No agent activity traced yet._")
else:
    trace_rows = []
    for t in traces:
        agent = t["agents"][0] if t.get("agents") else {}
        tool = agent.get("tools", [{}])[0] if agent.get("tools") else {}
        trace_rows.append(
            {
                "start_time": t.get("start_time"),
                "agent_id": t.get("agent_id"),
                "agent_name": agent.get("name"),
                "tool_name": tool.get("name"),
                "status": t.get("status"),
                "input": str(t.get("input"))[:80],
            }
        )
    trace_df = pd.DataFrame(trace_rows).sort_values("start_time", ascending=False)
    st.dataframe(trace_df, use_container_width=True)

    with st.expander("Raw trace JSON (most recent 20)"):
        st.json(list(reversed(traces))[:20])
