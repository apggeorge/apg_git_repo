# 📊 INSIDE SALES DASHBOARD (inside_sales_dashboard.py)
import streamlit as st
import json, os, re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

# ---------- Page config ----------
st.set_page_config(page_title="APG Inside Sales Dashboard", layout="wide")
st.markdown("<h1 style='text-align:center;'>📥 Inside Sales Dashboard</h1>", unsafe_allow_html=True)

# ---------- Storage configuration (must match your form app) ----------
BASE_STORAGE_DIR = os.environ.get("APG_STORAGE_DIR", "/app/storage")
SUBMISSIONS_DIR = os.path.join(BASE_STORAGE_DIR, "submissions")
os.makedirs(SUBMISSIONS_DIR, exist_ok=True)

# ---------- UI helpers ----------
TRIGGER_WORDS = [
    r"\burgent\b", r"\basap\b", r"\bimmediately\b", r"\bemergency\b",
    r"\bhelp\b", r"\bcritical\b", r"\bpriority\b", r"\bnow\b"
]
TRIGGER_RE = re.compile("|".join(TRIGGER_WORDS), re.IGNORECASE)

REQUEST_TYPE_TITLE_MAP = {
    # Keys from refund/reissue submissions (your SERVICE_TYPE_KEYS)
    "involuntary_refund": "Involuntary Refund",
    "involuntary_reissue": "Involuntary Reissue",
    "medical_refund": "Medical Refund",
    "voluntary_refund": "Voluntary Refund",
    # Policy lookups and other routes (fallbacks)
    "airline_policy_lookup": "Airline Policy Lookup",
    "general_inquiry": "General Inquiry",
    "groups": "Groups",
}

ROUTE_FRIENDLY = {
    "refund_reissue": "Refunds / Reissues",
    "general_inquiry": "General Inquiries",
    "airline_policy_lookup": "Policy Lookup",
    "groups": "Groups",
}

STATUS_COLORS = {
    "open": "#f59e0b",        # amber
    "completed": "#10b981",   # emerald
}

def pretty_request_title(item: Dict[str, Any]) -> str:
    # Prefer the explicit service_request_type (normalized) if present
    key = item.get("service_request_type")
    if key and key in REQUEST_TYPE_TITLE_MAP:
        return REQUEST_TYPE_TITLE_MAP[key]

    # Otherwise deduce from route or special ids
    route = item.get("route")
    if route == "refund_reissue":
        # Map a few likely keys; fallback label if missing
        return REQUEST_TYPE_TITLE_MAP.get(key, "Refund/Reissue")
    if route in REQUEST_TYPE_TITLE_MAP:
        return REQUEST_TYPE_TITLE_MAP[route]
    return "Request"

def parse_when(item: Dict[str, Any]) -> datetime:
    # Prefer ISO for proper sorting; fallback to local string
    iso = item.get("submitted_at_iso")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except Exception:
            pass
    # Fallback if only "submitted_at" exists (e.g., "0814-0933AM")
    s = item.get("submitted_at")
    if s:
        try:
            # naive best effort: monthday-hourminAMPM (no year) → assume this year
            now = datetime.now()
            dt = datetime.strptime(s, "%m%d-%I%M%p")
            return dt.replace(year=now.year)
        except Exception:
            pass
    # As a last resort: put it at epoch to not crash sorting
    return datetime(1970, 1, 1, tzinfo=timezone.utc)

def is_urgent(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(TRIGGER_RE.search(text))

def load_all_submissions() -> List[Dict[str, Any]]:
    out = []
    for name in os.listdir(SUBMISSIONS_DIR):
        if not name.endswith(".json"):
            continue
        path = os.path.join(SUBMISSIONS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Ensure a few standard fields exist
            data["_file"] = path
            # Default status if not present
            data["status"] = data.get("status", "open")
            out.append(data)
        except Exception:
            # Skip corrupt files but continue
            continue
    # Newest first
    out.sort(key=parse_when, reverse=True)
    return out

def save_submission(path: str, payload: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

def header_line(item: Dict[str, Any]) -> str:
    """Format: Request Type — Agency Name — Date — Time"""
    title = pretty_request_title(item)
    agency = item.get("agency_name") or "—"
    # Show local-ish timestamp based on what we have
    dt = parse_when(item)
    date_str = dt.strftime("%Y-%m-%d")
    time_str = dt.strftime("%I:%M%p").lstrip("0")
    return f"{title} — {agency} — {date_str} — {time_str}"

def status_badge(status: str) -> str:
    status = status or "open"
    color = STATUS_COLORS.get(status, "#6b7280")  # gray fallback
    label = "Completed" if status == "completed" else "Open"
    return f"<span style='background:{color};color:white;padding:2px 8px;border-radius:999px;font-size:12px;'>{label}</span>"

def urgent_badge(flagged: bool) -> str:
    if not flagged:
        return ""
    # Yellow badge
    return "<span style='background:#fde68a;color:#7c2d12;padding:2px 8px;border-radius:999px;font-size:12px;margin-left:8px;'>⚠️ Urgent</span>"

# ---------- Sidebar filters ----------
st.sidebar.header("Filters")
route_filter = st.sidebar.multiselect(
    "Route",
    options=["Refunds / Reissues", "General Inquiries", "Policy Lookup", "Groups"],
    default=[]
)
status_filter = st.sidebar.multiselect("Status", options=["open", "completed"], default=["open"])
search_query = st.sidebar.text_input("Search (agency, email, case id, comments)")
st.sidebar.caption("Tip: leave filters blank to show everything.")

# ---------- Load ----------
items = load_all_submissions()

# ---------- Filter ----------
def include_item(it: Dict[str, Any]) -> bool:
    # Route filter
    if route_filter:
        route_label = ROUTE_FRIENDLY.get(it.get("route"), "Other")
        if route_label not in route_filter:
            return False
    # Status filter
    if status_filter and it.get("status", "open") not in status_filter:
        return False
    # Search
    if search_query:
        hay = " ".join([
            it.get("service_case_id", ""),
            it.get("agency_name", ""),
            it.get("email", ""),
            it.get("comments", "") or it.get("comment", ""),
            it.get("airline", ""),
            it.get("plating_code", ""),
            it.get("route", ""),
            it.get("service_request_type", ""),
        ]).lower()
        if search_query.lower() not in hay:
            return False
    return True

filtered = [it for it in items if include_item(it)]

# ---------- Bulk actions row ----------
left, mid, right = st.columns([1, 2, 1])
with left:
    st.write(f"Showing **{len(filtered)}** of **{len(items)}** submissions")
with mid:
    sort_choice = st.selectbox("Sort by", ["Newest first", "Oldest first"], index=0, label_visibility="collapsed")
with right:
    show_json = st.checkbox("Show raw JSON in details", value=False)

if sort_choice == "Oldest first":
    filtered = list(reversed(filtered))

st.markdown("---")

# ---------- Render list ----------
if not filtered:
    st.info("No submissions match your current filters.")
else:
    for idx, it in enumerate(filtered):
        # Determine urgency from comments (handles both 'comments' and 'comment' keys)
        comments_text = it.get("comments") or it.get("comment") or ""
        urgent = is_urgent(comments_text)

        header = header_line(it)
        status_html = status_badge(it.get("status", "open"))
        urgent_html = urgent_badge(urgent)

        # Top row: Gmail-style header line with badges
        st.markdown(
            f"""
            <div style="
                display:flex;justify-content:space-between;align-items:center;
                padding:10px 14px;border:1px solid #e5e7eb;border-radius:10px;
                background:#ffffff;margin-bottom:8px;">
              <div style="font-weight:600;">{header}</div>
              <div>{status_html}{urgent_html}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Expand for details
        with st.expander("Show details"):
            # Status controls
            cols = st.columns([1, 1, 6])
            with cols[0]:
                mark_done = st.button("✅ Mark Completed", key=f"done_{idx}", disabled=(it.get("status") == "completed"))
            with cols[1]:
                reopen = st.button("↩️ Reopen", key=f"reopen_{idx}", disabled=(it.get("status") != "completed"))

            # Core metadata table (minimal, readable)
            def field(label, value):
                st.markdown(f"**{label}:** {value if (value not in [None, '']) else '—'}")

            leftc, rightc = st.columns(2)
            with leftc:
                field("Service Case ID", it.get("service_case_id"))
                field("Route", ROUTE_FRIENDLY.get(it.get("route"), it.get("route", "—")))
                field("Request Type", pretty_request_title(it))
                field("Agency Name", it.get("agency_name"))
                field("Agency ID (ARC)", it.get("agency_id"))
                field("Email", it.get("email"))
            with rightc:
                when = parse_when(it)
                field("Submitted (Local-ish)", when.strftime("%Y-%m-%d %I:%M%p").lstrip("0"))
                field("Submitted (ISO)", it.get("submitted_at_iso"))
                field("Plating Code", it.get("plating_code"))
                field("Ticket Number", it.get("ticket_number"))
                field("Record Locator", it.get("airline_record_locator"))
                field("Airline", it.get("airline"))

            # Comments / policy / extras
            st.markdown("**Comment:**")
            st.write(comments_text if comments_text else "—")

            # If your form wrote these fields, show them smartly
            if it.get("excluded_agencies"):
                st.markdown("**Excluded Agencies on File:**")
                st.code(", ".join(it.get("excluded_agencies") or []), language="text")

            if it.get("endorsement_code"):
                st.markdown("**Endorsement/Waiver Codes:**")
                st.code(", ".join(it.get("endorsement_code") or []), language="text")

            if it.get("saved_file_path"):
                st.markdown(f"**Attachment:** `{it.get('saved_file_path')}`")

            if show_json:
                st.markdown("**Raw JSON:**")
                st.code(json.dumps(it, indent=2), language="json")

            # Handle status updates (write back into the same JSON file)
            if mark_done or reopen:
                new_status = "completed" if mark_done else "open"
                it["status"] = new_status
                if new_status == "completed":
                    it["completed_at_iso"] = datetime.now(timezone.utc).isoformat()
                else:
                    it.pop("completed_at_iso", None)
                try:
                    save_submission(it["_file"], it)
                    st.success(f"Status updated to **{new_status}**.")
                except Exception as e:
                    st.error(f"Failed to update status: {e}")

st.markdown("---")
st.caption("💡 Pro tip: use the sidebar filters and search to triage quickly. Urgent language is auto-flagged.")
