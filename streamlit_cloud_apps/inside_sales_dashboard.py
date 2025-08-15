# 📊 INSIDE SALES DASHBOARD (inside_sales_dashboard.py)
import streamlit as st
import json, re
from datetime import datetime, timezone
from typing import Dict, Any, List

# ---------- Resilient import for shared storage ----------
try:
    from streamlit_cloud_apps.apg_storage import storage
except ModuleNotFoundError:
    import os, sys
    sys.path.append(os.path.dirname(__file__))  # allow sibling import
    from apg_storage import storage

# ---------- Urgency keywords ----------
URGENCY_KEYWORDS = [
    "urgent", "critical", "asap", "immediately",
    "emergency", "priority", "important",
    "expedite", "rush", "high priority",
]
TRIGGER_RE = re.compile(r"\b(" + "|".join(map(re.escape, URGENCY_KEYWORDS)) + r")\b", re.IGNORECASE)

# ---------- Page config ----------
st.set_page_config(page_title="APG Inside Sales Dashboard", layout="wide")
st.markdown("<h1 style='text-align:center;'>📥 Inside Sales Dashboard</h1>", unsafe_allow_html=True)

# small polish for expanders + toggle spacing
st.markdown("""
<style>
details.st-expander {
  border:1px solid #e5e7eb; border-radius:10px; background:#fff; margin-bottom:8px;
}
details.st-expander > summary {
  padding:10px 14px; list-style:none; cursor:pointer;
}
/* keep any checkbox/toggle labels tight */
div[data-testid="stCheckbox"] label p { margin-bottom: 0; }
</style>
""", unsafe_allow_html=True)

# ---------- UI helpers ----------
REQUEST_TYPE_TITLE_MAP = {
    "involuntary_refund": "Involuntary Refund",
    "involuntary_reissue": "Involuntary Reissue",
    "medical_refund": "Medical Refund",
    "voluntary_refund": "Voluntary Refund",
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

def pretty_request_title(item: Dict[str, Any]) -> str:
    key = item.get("service_request_type")
    if key and key in REQUEST_TYPE_TITLE_MAP:
        return REQUEST_TYPE_TITLE_MAP[key]
    route = item.get("route")
    if route == "refund_reissue":
        return REQUEST_TYPE_TITLE_MAP.get(key, "Refund/Reissue")
    if route in REQUEST_TYPE_TITLE_MAP:
        return REQUEST_TYPE_TITLE_MAP[route]
    return "Request"

def parse_when(item: Dict[str, Any]) -> datetime:
    iso = item.get("submitted_at_iso")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00"))
        except Exception:
            pass
    s = item.get("submitted_at")
    if s:
        try:
            now = datetime.now()
            dt = datetime.strptime(s, "%m%d-%I%M%p")
            return dt.replace(year=now.year)
        except Exception:
            pass
    return datetime(1970, 1, 1, tzinfo=timezone.utc)

def is_urgent(text: str) -> bool:
    return bool(TRIGGER_RE.search(text or ""))

# ---------- Storage reading (index-aware) ----------
INDEX_KEY = "submissions/_index.json"
SUBMISSIONS_DIR = "submissions/"

def _fetch_from_index() -> List[Dict[str, Any]]:
    try:
        index = storage.read_json(INDEX_KEY)
    except Exception:
        return []
    if not isinstance(index, list) or not index:
        return []
    out: List[Dict[str, Any]] = []
    for entry in index:
        try:
            if isinstance(entry, dict) and "key" in entry:
                key = entry["key"]
                data = storage.read_json(key)
                data["_key"] = key
                for k in ("service_case_id", "route", "service_request_type",
                          "agency_name", "email", "plating_code", "submitted_at_iso",
                          "attachment_key", "attachment_url"):
                    if k not in data and k in entry:
                        data[k] = entry[k]
                out.append(data)
            elif isinstance(entry, str):
                key = entry
                data = storage.read_json(key)
                data["_key"] = key
                out.append(data)
        except Exception:
            continue
    return out

def load_all_submissions() -> List[Dict[str, Any]]:
    items = _fetch_from_index()
    if not items:
        try:
            items = storage.list_json(SUBMISSIONS_DIR)
        except Exception:
            items = []
    for it in items:
        it["status"] = it.get("status", "open")
    items.sort(key=parse_when, reverse=True)
    return items

def save_submission(key: str, payload: Dict[str, Any]) -> None:
    storage.write_json(key, payload)

def header_line(item: Dict[str, Any]) -> str:
    title = pretty_request_title(item)
    agency = item.get("agency_name") or "—"
    dt = parse_when(item)
    return f"{title} — {agency} — {dt.strftime('%Y-%m-%d %I:%M%p').lstrip('0')}"

# ---------- Sidebar filters ----------
st.sidebar.header("Filters")
route_filter = st.sidebar.multiselect("Route", options=list(ROUTE_FRIENDLY.values()), default=[])
status_filter = st.sidebar.multiselect("Status", options=["open", "completed"], default=["open"])
search_query = st.sidebar.text_input("Search (agency, email, case id, comments)")
st.sidebar.caption("Tip: leave filters blank to show everything.")

# ---------- Load ----------
items = load_all_submissions()

# ---------- Filter ----------
def include_item(it: Dict[str, Any]) -> bool:
    if route_filter and ROUTE_FRIENDLY.get(it.get("route"), "Other") not in route_filter:
        return False
    if status_filter and it.get("status", "open") not in status_filter:
        return False
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

# ---------- Render ----------
if not filtered:
    st.info("No submissions match your current filters.")
else:
    for idx, it in enumerate(filtered):
        comments_text = it.get("comments") or it.get("comment") or ""
        urgent = is_urgent(comments_text)
        urgent_tag = "  🟡 Caution" if urgent else ""
        badge = "🟢 Completed" if it.get("status") == "completed" else "🟠 Open"

        with st.expander(f"{header_line(it)}   — {badge}{urgent_tag}", expanded=urgent):
            # --- Completed toggle LEFT-ALIGNED (auto-save) ---
            # lives directly under the expander header, aligned with the content
            state_key = f"_last_status_{idx}"
            last_status = st.session_state.get(state_key, it.get("status", "open"))
            completed_now = st.toggle(
                "Completed",
                value=(last_status == "completed"),
                key=f"completed_toggle_{idx}",
                help="Flip to mark this ticket completed / reopen",
            )
            new_status = "completed" if completed_now else "open"
            if new_status != last_status:
                it["status"] = new_status
                if new_status == "completed":
                    it["completed_at_iso"] = datetime.now(timezone.utc).isoformat()
                else:
                    it.pop("completed_at_iso", None)
                try:
                    key = it.get("_key") or it.get("storage_key")
                    if not key:
                        raise RuntimeError("Missing storage key for update.")
                    save_submission(key, it)
                    st.session_state[state_key] = new_status
                    st.success(f"Status updated to **{new_status}**.")
                except Exception as e:
                    st.error(f"Failed to update status: {e}")

            # --- Core metadata ---
            def field(label, value):
                st.markdown(f"**{label}:** {value if value not in [None, ''] else '—'}")

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

            st.markdown("**Comment:**")
            st.write(comments_text if comments_text else "—")

            if it.get("excluded_agencies"):
                st.markdown("**Excluded Agencies on File:**")
                st.code(", ".join(it.get("excluded_agencies") or []), language="text")

            if it.get("endorsement_code"):
                st.markdown("**Endorsement/Waiver Codes:**")
                st.code(", ".join(it.get("endorsement_code") or []), language="text")

            if it.get("attachment_url"):
                st.markdown(f"**Attachment:** [Open attachment]({it['attachment_url']})")
            elif it.get("attachment_key"):
                st.markdown(f"**Attachment key:** `{it['attachment_key']}`")

            if st.checkbox("Show raw JSON", key=f"showjson_{idx}"):
                st.code(json.dumps(it, indent=2), language="json")

st.markdown("---")
st.caption("💡 Pro tip: urgent keywords in comments trigger a yellow caution icon and auto-open the case.")
