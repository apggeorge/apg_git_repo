import streamlit as st
import os, json, re
from datetime import datetime
from pathlib import Path

# ---------- Configuration ----------
BASE_STORAGE_DIR = os.environ.get("APG_STORAGE_DIR", os.path.expanduser("~/apg_hub/storage"))
SUBMISSIONS_DIR = os.path.join(BASE_STORAGE_DIR, "submissions")
SCREENSHOTS_DIR = os.path.join(BASE_STORAGE_DIR, "screenshots")
Path(SUBMISSIONS_DIR).mkdir(parents=True, exist_ok=True)
Path(SCREENSHOTS_DIR).mkdir(parents=True, exist_ok=True)

st.set_page_config(page_title="Inside Sales Dashboard", layout="wide")
st.title("📊 Inside Sales Dashboard")
st.caption(f"Data folder: {SUBMISSIONS_DIR}")

# ---------- Helpers ----------
ROUTE_LABELS = {
    "refund_reissue": "Refund / Reissue",
    "general_inquiry": "General Inquiry",
    "airline_policy_lookup": "Airline Policy Lookup",
}

def pretty_service_type(s: str | None) -> str:
    if not s:
        return ""
    return s.replace("_", " ").title()

def normalize_agency(name: str | None) -> str:
    return (name or "").strip().title()

def split_mmdd_hhmm(submitted_at: str | None):
    """Input format in logs is MMDD-HHMMAM/PM. Return (MMDD, HHMM)."""
    if not submitted_at or "-" not in submitted_at:
        return ("", "")
    mmdd, hhmm_ampm = submitted_at.split("-", 1)
    hhmm = re.sub(r"(AM|PM)$", "", hhmm_ampm, flags=re.IGNORECASE)
    return (mmdd, hhmm)

def load_submissions(folder: str):
    rows = []
    for fn in sorted(os.listdir(folder)):
        if not fn.endswith(".json"): continue
        fp = os.path.join(folder, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                s = json.load(f)
                s["_file_path"] = fp
                rows.append(s)
        except Exception:
            continue
    return rows

# ---------- Load ----------
subs = load_submissions(SUBMISSIONS_DIR)

# Sort by ISO timestamp if present, else by submitted_at string
subs.sort(key=lambda r: r.get("submitted_at_iso") or r.get("submitted_at", ""), reverse=True)

# ---------- Sidebar Filters ----------
st.sidebar.header("Filters")
route_opts = sorted({r.get("route", "") for r in subs if r.get("route")})
route_filter = st.sidebar.multiselect("Form Type", [ROUTE_LABELS.get(x, x.title().replace("_"," ")) for x in route_opts])

agency_search = st.sidebar.text_input("Search Agency (contains)")
airline_search = st.sidebar.text_input("Search Airline/Plating (contains)")

# ---------- Build table ----------
table = []
for s in subs:
    route_key = s.get("route", "")
    route_label = ROUTE_LABELS.get(route_key, route_key.title().replace("_", " "))
    agency = normalize_agency(s.get("agency_name"))
    mmdd, hhmm = split_mmdd_hhmm(s.get("submitted_at"))

    # Build display name per spec: Form Type - Agency Name - MMDD - HHMM
    display_name = f"{route_label} - {agency or '—'} - {mmdd or '—'} - {hhmm or '—'}"

    # Apply filters
    if route_filter and route_label not in route_filter:
        continue
    if agency_search and agency_search.lower() not in (agency or '').lower():
        continue
    # airline search checks airline label or plating code
    airline_label = s.get("airline") or ""
    plating = s.get("plating_code") or s.get("ticket_prefix") or ""
    if airline_search and airline_search.lower() not in (airline_label + " " + str(plating)).lower():
        continue

    table.append({
        "Display Name": display_name,
        "Case ID": s.get("service_case_id", ""),
        "Form Type": route_label,
        "Agency": agency,
        "Airline": airline_label,
        "Plating": plating,
        "Service Type": pretty_service_type(s.get("service_request_type")),
        "Submitted (local)": s.get("submitted_at", ""),
        "Submitted (ISO)": s.get("submitted_at_iso", ""),
        "Email": s.get("email", ""),
        "Comments": s.get("comments", ""),
        "Attachment MIME": s.get("attachment_mime", ""),
        "Attachment Path": s.get("saved_file_path", ""),
    })

# ---------- Render ----------
if not table:
    st.info("No submissions match your filters." if (subs and (route_filter or agency_search or airline_search)) else "No submissions found.")
else:
    st.dataframe(table, use_container_width=True, hide_index=True)

    # Details panel
    st.subheader("Details")
    case_ids = [row["Case ID"] for row in table if row.get("Case ID")]
    if case_ids:
        sel = st.selectbox("Select a Case ID", case_ids)
        detail = next((s for s in subs if s.get("service_case_id") == sel), None)
        if detail:
            st.json(detail)
            # Show hint to locate attachment locally
            att = detail.get("saved_file_path")
            if att and os.path.exists(att):
                st.success(f"Attachment available at: {att}")
            elif att:
                st.warning(f"Attachment path recorded, but file not found at: {att}")
