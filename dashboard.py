"""Streamlit dashboard for operators.

Run with: `streamlit run dashboard.py` (from the repo root).

Shows every lead with status/preview link/last email timestamp, lets you
filter by status, drill into a lead's email thread, retry a bounce, pause/
resume the main.py loop (via a shared flag file), and export the leads
table to CSV.
"""
from __future__ import annotations

import sys
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
from utils import db  # noqa: E402

PAUSE_FLAG = _PIPELINE_DIR / ".paused"

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
    st.write("**Status:** " + ("PAUSED" if _is_paused() else "RUNNING"))

st.divider()

# --- Leads table --------------------------------------------------------------
leads = db.list_all_leads()
if not leads:
    st.info("No leads yet. Drop a CSV into pipeline/leads_inbox/ and start main.py.")
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
