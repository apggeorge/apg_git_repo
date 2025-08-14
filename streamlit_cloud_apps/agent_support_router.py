# 📄 AGENT SUPPORT ROUTER (agent_support_router.py)
import streamlit as st
import json, os, re, io
from datetime import datetime, timezone
from PIL import Image
import pytesseract
from pathlib import Path

from streamlit_cloud_apps.apg_storage import storage

# =========================
# Page config + Header
# =========================
st.set_page_config(page_title="APG Agency Support Requests", layout="centered")
st.markdown(
    "<h1 style='text-align: center;'>🧭 APG Agency Support Request</h1>",
    unsafe_allow_html=True
)

# =========================
# Repo + Data Paths
# =========================
# repo root is one level up from streamlit_cloud_apps/
REPO_ROOT = Path(__file__).resolve().parent.parent  # => apg_git_repo/

def _pick_existing(*cands) -> str:
    """Return the first existing path from candidates; otherwise first candidate."""
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0]

# Data locations (new canonical paths with fallbacks)
POLICY_DIR = _pick_existing(
    str(REPO_ROOT / "airline_policies"),
    "data/airline_policies",
)
ELIGIBLE_CODE_PATH = _pick_existing(
    str(REPO_ROOT / "eligibility" / "eligible_4_digit_codes.json"),
    str(REPO_ROOT / "reuseable_code" / "internal_code" / "eligible_4_digit_codes.json"),
)
AIRLINE_LIST_PATH = _pick_existing(
    str(REPO_ROOT / "eligibility" / "eligible_airline_names.json"),
    str(REPO_ROOT / "reuseable_code" / "internal_code" / "eligible_airline_names.json"),
)

# =========================
# Global CSS (wrapping)
# =========================
st.markdown("""
<style>
/* Soft card for policy blocks */
.wrap-policy {
  border: 1px solid #e5e7eb;
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px;
  overflow-x: hidden;
  box-sizing: border-box;
  max-width: 100%;
}
/* Ensure wrapping in both <pre> and <code> inside the card */
.wrap-policy pre,
.wrap-policy code {
  display: block;
  margin: 0;
  white-space: pre-wrap !important;
  overflow-wrap: anywhere !important;
  word-break: break-word !important;
  tab-size: 2;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono","Courier New", monospace;
  font-size: 0.92rem;
  line-height: 1.4;
}
/* Wrap Streamlit code/markdown blocks app-wide (service case id, etc.) */
div[data-testid="stCodeBlock"] pre,
div[data-testid="stMarkdownContainer"] pre,
div[data-testid="stMarkdownContainer"] code,
pre,
code {
  white-space: pre-wrap !important;
  overflow-wrap: anywhere !important;
  word-break: break-word !important;
}
</style>
""", unsafe_allow_html=True)

# =========================
# Helpers
# =========================
SERVICE_TYPES = [
    "Involuntary Refund",
    "Involuntary Reissue",
    "Medical Refund",
    "Voluntary Refund",
]
SERVICE_TYPE_KEYS = {
    "Involuntary Refund": "involuntary_refund",
    "Involuntary Reissue": "involuntary_reissue",
    "Medical Refund": "medical_refund",
    "Voluntary Refund": "voluntary_refund",
}

WAIVER_PATTERNS = [
    r"/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,10}/E",  # Sabre
    r"RF-[A-Z0-9]{4,10}",                   # Amadeus
    r"WAIVER[:\s]+[A-Z0-9]{3,15}",          # Travelport
    r"ENDORSEMENT[:\s]+RF-[A-Z0-9]{3,15}",  # Travelport extended
]

def _html_escape(s: str) -> str:
    s = s or ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def load_eligible_codes(path: str) -> set[str]:
    """
    Accepts JSON as either:
      - ["2757", "2758", ...] OR
      - {"codes": [...]}      OR
      - {"2757": true, ...}   (keys used)
    Returns a set of zero-padded 4-digit strings.
    """
    raw = load_json(path, default=[]) or []
    if isinstance(raw, dict):
        raw = raw.get("codes", list(raw.keys()))
    codes = set()
    for item in raw:
        s = re.sub(r"\D", "", str(item))  # digits only
        if s:
            codes.add(s.zfill(4))
    return codes

def airline_name_to_plating_code(name_line: str) -> str | None:
    if not name_line:
        return None
    m = re.search(r"\((\d{3})\)", name_line)
    return m.group(1) if m else None

def detect_waiver_signature(text: str) -> bool:
    for p in WAIVER_PATTERNS:
        if re.search(p, text or "", re.IGNORECASE):
            return True
    return False

def normalize_excluded(excl):
    """Ensure excluded agencies render as a clean list of names."""
    if not excl:
        return []
    if isinstance(excl, list):
        if len(excl) == 1 and isinstance(excl[0], str) and "," in excl[0]:
            return [x.strip() for x in excl[0].split(",") if x.strip()]
        return [str(x).strip() for x in excl if str(x).strip()]
    if isinstance(excl, str):
        return [x.strip() for x in excl.split(",") if x.strip()]
    return [str(excl).strip()]  # fallback

def render_deadlines(deadline_data: dict | None):
    if not deadline_data:
        st.markdown("—")
        return
    d = deadline_data.get("eligible_rebooking_range_deadline")
    if d and all(k in d for k in ("before_original_departure", "after_original_departure")):
        st.markdown(
            f"Rebooking allowed from **{d['before_original_departure']} days before** "
            f"to **{d['after_original_departure']} days after** original departure."
        )
        rest = {k: v for k, v in deadline_data.items() if k != "eligible_rebooking_range_deadline"}
    else:
        rest = dict(deadline_data)
    if rest:
        def pretty(s): return s.replace("_", " ").capitalize()
        st.markdown("**Additional timing rules on file:**")
        for k, v in rest.items():
            if isinstance(v, dict):
                inner = ", ".join([f"{pretty(ik)}: {iv}" for ik, iv in v.items()])
                st.markdown(f"- {pretty(k)} — {inner}")
            else:
                st.markdown(f"- {pretty(k)}: {v}")
    else:
        if not d:
            st.markdown("—")

# =========================
# Top-level selection (Dropdown) — single source of truth
# =========================
st.markdown("<h3 style='text-align: center;'>How can we help you?</h3>", unsafe_allow_html=True)

PLACEHOLDER = "— Select a request type —"
_core = ["Airline Policies", "General Inquiries", "Groups", "Refunds / Reissues"]
options = [PLACEHOLDER] + sorted(_core, key=str.casefold)

choice = st.selectbox(
    "Select a request type",
    options,
    index=0,                              # always start on placeholder
    key="support_type_select",            # widget manages its own state
    label_visibility="collapsed",
)

if choice == PLACEHOLDER:
    st.stop()                             # render nothing below until chosen

support_type = choice                     # <-- use this variable below

# =========================
# 1) REFUNDS / REISSUES
# =========================
if support_type == "Refunds / Reissues":
    st.markdown(
        "<h2 style='text-align: center;'>🛫 Refund / Reissue Submission</h2>",
        unsafe_allow_html=True
    )
    with st.form("refund_form", clear_on_submit=False):
        ticket_number = st.text_input("🎫 Airline Ticket Number")
        service_type_label = st.selectbox("🛠️ Service Request Type", SERVICE_TYPES)
        service_request_type = SERVICE_TYPE_KEYS[service_type_label]
        airline_record_locator = st.text_input("📄 Airline Record Locator Number")
        agency_id = st.text_input("🏢 Agency ID (ARC Number)")
        agency_name = st.text_input("🏷️ Agency Name")
        full_pnr = st.file_uploader(
            "📎 Full PNR Screenshot (required for refund/reissue)",
            type=["png", "jpg", "jpeg", "pdf"]
        )
        email = st.text_input("📧 Email Address")
        comments = st.text_area("💬 Comments (optional)")
        submitted = st.form_submit_button("🚀 Submit")

    if submitted:
        # Basic field checks
        if not re.fullmatch(r"\d{13}", ticket_number or ""):
            st.error("❌ Ticket Number must be exactly 13 digits — no dashes, letters, or symbols.")
            st.stop()

        eligible_codes = load_eligible_codes(ELIGIBLE_CODE_PATH)
        ticket_prefix = (ticket_number or "")[:4]
        if ticket_prefix not in eligible_codes:
            st.error(f"❌ This ticket is not eligible — APG does not currently service the country of origin for carrier code `{ticket_prefix}`.")
            st.caption(f"Debug: using {ELIGIBLE_CODE_PATH}; {len(eligible_codes)} codes loaded.")
            st.stop()

        if not re.fullmatch(r"[A-Za-z0-9]{6}", airline_record_locator or ""):
            st.error("❌ Record Locator must be exactly 6 letters/numbers — no symbols.")
            st.stop()

        required_fields = [ticket_number, service_request_type, airline_record_locator, agency_id, email]
        if not all(required_fields):
            st.error("⚠️ Please fill in all required fields.")
            st.stop()

        if service_request_type in ["involuntary_refund", "involuntary_reissue", "medical_refund", "voluntary_refund"] and not full_pnr:
            st.error("📎 PNR screenshot is required for refund or reissue-related requests.")
            st.stop()

        plating_code = ticket_number[:3]
        submission_time = datetime.now().strftime("%m%d-%I%M%p")
        service_case_id = f"{plating_code}-{agency_id}-{submission_time}"
        submitted_at_iso = datetime.now(timezone.utc).isoformat()

        # Load policy
        policy_file = os.path.join(POLICY_DIR, f"{plating_code}.json")
        if not os.path.exists(policy_file):
            st.error(f"❌ No policy found for plating carrier `{plating_code}`.")
            st.stop()
        data = load_json(policy_file, default={}) or {}
        excluded = normalize_excluded(data.get("agency_exclusion_list", {}).get("excluded_agencies", []))

        # Handle attachment + waiver detection
        waiver_present = False
        attachment_key = None
        attachment_url = None
        attachment_mime = None

        if full_pnr is not None:
            try:
                file_bytes = full_pnr.read()
                full_pnr.seek(0)
                ext = os.path.splitext(full_pnr.name)[-1] or ""
                attachment_key = f"screenshots/{service_case_id}{ext}"
                storage.save_bytes(attachment_key, file_bytes, content_type=full_pnr.type)
                attachment_mime = full_pnr.type
                attachment_url = storage.url(attachment_key)

                if full_pnr.type in ("image/png", "image/jpeg", "image/jpg"):
                    img = Image.open(io.BytesIO(file_bytes))
                    ocr_text = pytesseract.image_to_string(img)
                    waiver_present = detect_waiver_signature(ocr_text)
                elif full_pnr.type == "application/pdf":
                    st.info("ℹ️ PDF saved for future parsing. OCR not applied in this flow.")
            except Exception:
                st.warning("⚠️ Unable to process the uploaded file. Proceeding without waiver detection.")

        # Display results
        st.subheader("📌 Service Case ID")
        st.code(service_case_id)

        st.subheader("📋 Airline Policy")
        policy_text = (data.get("policies", {}) or {}).get(
            service_request_type,
            "No policy information found."
        )
        st.markdown(f"<div class='wrap-policy'><pre>{_html_escape(policy_text)}</pre></div>", unsafe_allow_html=True)

        st.subheader("🔖 Applicable Waiver Codes")
        endo_codes = (data.get("endorsement_codes", {}) or {}).get(f"{service_request_type}_code", [])
        if waiver_present:
            st.markdown(f"`{', '.join(endo_codes) if endo_codes else '—'}`")
        else:
            st.markdown(" Waiver Code is not required. ")

        st.subheader("⚠️ Disclaimer & Exclusions")
        st.markdown("Please review fare rules to avoid any ADMs ")
        st.markdown(f"**Agency Eligibility Exclusions:** `{', '.join(excluded) if excluded else 'None on file'}`")

        # Persist submission (shared storage)
        log_entry = {
            "service_case_id": service_case_id,
            "route": "refund_reissue",
            "submitted_at": submission_time,
            "submitted_at_iso": submitted_at_iso,
            "ticket_number": ticket_number,
            "ticket_prefix": ticket_prefix,
            "plating_code": plating_code,
            "service_request_type": service_request_type,
            "airline_record_locator": airline_record_locator,
            "agency_id": agency_id,
            "agency_name": agency_name,
            "email": email,
            "comments": comments,
            "excluded_agencies": excluded,
            "endorsement_code": endo_codes if waiver_present else [],
            "waiver_detected": waiver_present,
            "attachment_mime": attachment_mime,
            # Shared storage references:
            "attachment_key": attachment_key,
            "attachment_url": attachment_url,
        }
        log_key = f"submissions/{service_case_id}.json"
        log_entry["storage_key"] = log_key
        storage.write_json(log_key, log_entry)

# =========================
# 2) GENERAL INQUIRIES
# =========================
elif support_type == "General Inquiries":
    st.markdown(
        "<h2 style='text-align: center;'>📨 General Inquiry</h2>",
        unsafe_allow_html=True
    )
    with st.form("general_form"):
        agency_name = st.text_input("🏷️ Agency Name")
        agency_id = st.text_input("🏢 Agency ID (ARC Number)")
        email = st.text_input("📧 Email Address")
        comment = st.text_area("💬 Comment")
        submitted = st.form_submit_button("Submit Inquiry")

    if submitted:
        gi_time = datetime.now().strftime("%m%d-%I%M%p")
        gi_time_iso = datetime.now(timezone.utc).isoformat()
        gi_case_id = f"GEN-{gi_time}"
        gi_log = {
            "service_case_id": gi_case_id,
            "route": "general_inquiry",
            "agency_name": agency_name,
            "agency_id": agency_id,
            "email": email,
            "comment": comment,
            "submitted_at": gi_time,
            "submitted_at_iso": gi_time_iso,
        }
        gi_key = f"submissions/{gi_case_id}.json"
        gi_log["storage_key"] = gi_key
        storage.write_json(gi_key, gi_log)
        st.success("✅ Inquiry submitted. Our team will contact you shortly.")

# =========================
# 3) AIRLINE POLICIES
# =========================
elif support_type == "Airline Policies":
    st.markdown("<h2 style='text-align:center;'>📚 Airline Policy Lookup</h2>", unsafe_allow_html=True)

    with st.form("policy_form"):
        agency_name = st.text_input("🏷️ Agency Name")

        airlines_map_raw = load_json(AIRLINE_LIST_PATH, default={}) or {}
        if not isinstance(airlines_map_raw, dict):
            st.error("❌ Airline list must be a mapping of plating code → airline name.")
            st.stop()

        airlines_map = {str(k).zfill(3): str(v).strip() for k, v in airlines_map_raw.items()}
        options = sorted([(name, code) for code, name in airlines_map.items()],
                         key=lambda x: x[0].lower())
        labels = [f"{name} ({code})" for name, code in options]

        selected_label = st.selectbox("🛫 Airline", labels)
        policy_service_pretty = None
        pretty_to_key = {}

        if selected_label:
            idx = labels.index(selected_label)
            airline_name, code = options[idx]

            policy_path = os.path.join(POLICY_DIR, f"{code}.json")
            if not os.path.exists(policy_path):
                st.error(f"❌ No policy file found for plating carrier `{code}`.")
                st.stop()

            pdata = load_json(policy_path, default={}) or {}
            available_keys = list((pdata.get("policies") or {}).keys())
            if not available_keys:
                st.error("❌ No policies found in this airline file.")
                st.stop()

            key_to_pretty = {k: k.replace("_", " ").title() for k in available_keys}
            pretty_to_key = {v: k for k, v in key_to_pretty.items()}
            pretty_names = sorted(key_to_pretty.values(), key=str.lower)

            policy_service_pretty = st.selectbox("🛠️ Support Request Type", pretty_names)

        submitted = st.form_submit_button("🔍 Lookup")

    if submitted and selected_label and policy_service_pretty:
        idx = labels.index(selected_label)
        airline_name, code = options[idx]

        policy_path = os.path.join(POLICY_DIR, f"{code}.json")
        if not os.path.exists(policy_path):
            st.error(f"❌ No policy file found for plating carrier `{code}`.")
            st.stop()

        pdata = load_json(policy_path, default={}) or {}
        key = pretty_to_key.get(policy_service_pretty)
        if not key:
            st.error("❌ Could not resolve the selected request type.")
            st.stop()

        st.subheader("📋 Airline Policy")
        ptext = (pdata.get("policies", {}) or {}).get(key, "No policy available for this request type.")
        st.markdown(f"<div class='wrap-policy'><pre>{_html_escape(ptext)}</pre></div>", unsafe_allow_html=True)

        st.subheader("🚫 Excluded Agencies (if any)")
        excl = normalize_excluded((pdata.get("agency_exclusion_list", {}) or {}).get("excluded_agencies", []))
        st.markdown(f"`{', '.join(excl) if excl else 'None on file'}`")

        pol_time = datetime.now().strftime("%m%d-%I%M%p")
        pol_time_iso = datetime.now(timezone.utc).isoformat()
        pol_case_id = f"{code}-POL-{pol_time}"
        pol_log = {
            "service_case_id": pol_case_id,
            "route": "airline_policy_lookup",
            "agency_name": agency_name,
            "airline": airline_name,
            "plating_code": code,
            "service_request_type": key,
            "submitted_at": pol_time,
            "submitted_at_iso": pol_time_iso,
            "excluded_agencies": excl
        }
        pol_key = f"submissions/{pol_case_id}.json"
        pol_log["storage_key"] = pol_key
        storage.write_json(pol_key, pol_log)

# =========================
# 4) GROUPS (same as General Inquiries)
# =========================
elif support_type == "Groups":
    st.markdown(
        "<h2 style='text-align: center;'>👥 Groups Inquiry</h2>",
        unsafe_allow_html=True
    )
    with st.form("groups_form"):
        agency_name = st.text_input("🏷️ Agency Name")
        agency_id = st.text_input("🏢 Agency ID (ARC Number)")
        email = st.text_input("📧 Email Address")
        comment = st.text_area("💬 Group Request / Notes")
        submitted = st.form_submit_button("Submit Groups Request")

    if submitted:
        grp_time = datetime.now().strftime("%m%d-%I%M%p")
        grp_time_iso = datetime.now(timezone.utc).isoformat()
        grp_case_id = f"GRP-{grp_time}"
        grp_log = {
            "service_case_id": grp_case_id,
            "route": "groups",
            "agency_name": agency_name,
            "agency_id": agency_id,
            "email": email,
            "comment": comment,
            "submitted_at": grp_time,
            "submitted_at_iso": grp_time_iso,
        }
        grp_key = f"submissions/{grp_case_id}.json"
        grp_log["storage_key"] = grp_key
        storage.write_json(grp_key, grp_log)
        st.success("✅ Groups request submitted. Our team will contact you shortly.")
