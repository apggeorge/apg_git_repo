# 📄 AGENT SUPPORT ROUTER (agent_support_router.py)
import streamlit as st
import json, os, re, io
from datetime import datetime, timezone
from PIL import Image
import pytesseract

# ---- Storage configuration (APG_STORAGE_DIR) ----
BASE_STORAGE_DIR = os.environ.get("APG_STORAGE_DIR", os.path.expanduser("~/apg_hub/storage"))
SUBMISSIONS_DIR = os.path.join(BASE_STORAGE_DIR, "submissions")
SCREENSHOTS_DIR = os.path.join(BASE_STORAGE_DIR, "screenshots")
os.makedirs(SUBMISSIONS_DIR, exist_ok=True)
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

st.markdown("""
<style>
/* Make ALL st.code blocks wrap instead of overflowing */
div[data-testid="stCodeBlock"] pre {
  white-space: pre-wrap !important;   /* respect newlines, allow wrapping */
  overflow-wrap: anywhere !important; /* wrap long tokens like 13-digit numbers */
  word-break: break-word !important;
}
div[data-testid="stCodeBlock"] {
  overflow-x: visible !important;     /* no horizontal scrollbar/bleed */
}
</style>
""", unsafe_allow_html=True)

# -------------------- Helpers -------------------- #
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

ELIGIBLE_CODE_PATH = "reuseable_code/internal_code/eligible_4_digit_codes.json"
AIRLINE_LIST_PATH = "reuseable_code/internal_code/eligible_airline_names.json"
POLICY_DIR = "data/airline_policies"

WAIVER_PATTERNS = [
    r"/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,10}/E",  # Sabre
    r"RF-[A-Z0-9]{4,10}",                   # Amadeus
    r"WAIVER[:\s]+[A-Z0-9]{3,15}",          # Travelport
    r"ENDORSEMENT[:\s]+RF-[A-Z0-9]{3,15}",  # Travelport extended
]

def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


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
    """Ensure excluded agencies render as a clean list of names.
    Accepts list or string; splits comma-delimited singletons."""
    if not excl:
        return []
    if isinstance(excl, list):
        # Handle one long comma-joined string inside a list
        if len(excl) == 1 and isinstance(excl[0], str) and "," in excl[0]:
            return [x.strip() for x in excl[0].split(",") if x.strip()]
        return [str(x).strip() for x in excl if str(x).strip()]
    if isinstance(excl, str):
        return [x.strip() for x in excl.split(",") if x.strip()]
    return [str(excl).strip()]  # fallback


def render_deadlines(deadline_data: dict | None):
    """Render deadlines / fare-rule timing info for a given service type.
    - If an eligible rebooking window is present, show the nice sentence.
    - Otherwise, list any key/value fields present so nothing gets lost.
    """
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

# ---- Persisted selection for support type ----
if "support_type" not in st.session_state:
    st.session_state.support_type = None

def _set_type(val: str):
    st.session_state.support_type = val

st.markdown("<h3 style='text-align: center;'>How can we help you?</h3>", unsafe_allow_html=True)

# --- Button styles (uniform + light blue) ---
st.markdown("""
<style>
div.stButton > button {
    height: 48px !important;
    width: 100% !important;              /* fill the column = same width */
    background-color: #BFE6FF !important; /* light blue */
    color: #0F172A !important;            /* dark text */
    font-weight: 600 !important;
    border: 1px solid #93C8E8 !important; /* slightly darker blue border */
    border-radius: 10px !important;
}
div.stButton > button:hover {
    background-color: #9DD7FF !important; /* hover light blue */
    color: #0F172A !important;
}
</style>
""", unsafe_allow_html=True)

# --- Three equal columns with real spacing ---
c1, c2, c3 = st.columns(3, gap="large")  # gap prevents crowding/overlap

with c1:
    st.button("💵 Refunds / Reissues",
              on_click=_set_type, args=("Refunds / Reissues",),
              key="btn_refund", use_container_width=True)

with c2:
    st.button("📩 General Inquiries",
              on_click=_set_type, args=("General Inquiries",),
              key="btn_general", use_container_width=True)

with c3:
    st.button("📑 Airline Policies",
              on_click=_set_type, args=("Airline Policies",),
              key="btn_policy", use_container_width=True)

# Now use the persisted selection
support_type = st.session_state.support_type

# ==================== 1) REFUNDS / REISSUES ==================== #
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
        agency_id = st.text_input("🏢 Agency ID (ARC/IATA or CLIA Number)")
        agency_name = st.text_input("🏷️ Agency Name")
        full_pnr = st.file_uploader("📎 Full PNR Screenshot (required for refund/reissue)", type=["png", "jpg", "jpeg", "pdf"])
        email = st.text_input("📧 Email Address")
        comments = st.text_area("💬 Comments (optional)")
        submitted = st.form_submit_button("🚀 Submit")

    if submitted:
        if not re.fullmatch(r"\d{13}", ticket_number or ""):
            st.error("❌ Ticket Number must be exactly 13 digits — no dashes, letters, or symbols.")
            st.stop()
        eligible_codes = load_json(ELIGIBLE_CODE_PATH, default={}) or {}
        ticket_prefix = (ticket_number or "")[:4]
        if ticket_prefix not in eligible_codes:
            st.error(
                f"❌ This ticket is not eligible — APG does not currently service the country of origin for carrier code `{ticket_prefix}`."
            )
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
        policy_file = os.path.join(POLICY_DIR, f"{plating_code}.json")
        if not os.path.exists(policy_file):
            st.error(f"❌ No policy found for plating carrier `{plating_code}`.")
            st.stop()
        data = load_json(policy_file, default={}) or {}
        excluded = normalize_excluded(data.get("agency_exclusion_list", {}).get("excluded_agencies", []))
        waiver_present, ocr_text = False, ""
        # Always persist the uploaded file (image or PDF) for future parsing
        saved_file_path = None
        attachment_mime = None
        if full_pnr is not None:
            try:
                file_bytes = full_pnr.read()
                full_pnr.seek(0)
                ext = os.path.splitext(full_pnr.name)[-1] or ""
                saved_file_path = os.path.join(SCREENSHOTS_DIR, f"{service_case_id}{ext}")
                with open(saved_file_path, "wb") as out_file:
                    out_file.write(file_bytes)
                attachment_mime = full_pnr.type

                # OCR only for images
                if full_pnr.type in ("image/png", "image/jpeg", "image/jpg"):
                    img = Image.open(io.BytesIO(file_bytes))
                    ocr_text = pytesseract.image_to_string(img)
                    waiver_present = detect_waiver_signature(ocr_text)
                elif full_pnr.type == "application/pdf":
                    st.info("ℹ️ PDF saved for future parsing. OCR not applied in this flow.")
            except Exception:
                st.warning("⚠️ Unable to process the uploaded file. Proceeding without waiver detection.")
        st.subheader("📌 Service Case #")
        st.code(service_case_id)
        st.subheader("⚠️ Disclaimer & Exclusions")
        st.markdown("Please review fare rules to avoid any ADM")
        st.markdown(f"**Agency Eligibility Exclusions:** `{', '.join(excluded) if excluded else 'None on file'}`")
        # Enable wrapping in st.code boxes
        st.markdown("""
            <style>
            pre {
                white-space: pre-wrap !important;
                word-wrap: break-word !important;
            }
            </style>
        """, unsafe_allow_html=True)

        # ... keep everything above the same ...

        st.subheader("📋 Airline Policy")
        policy_text = (data.get("policies", {}) or {}).get(
            service_request_type,
            "No policy information found."
        )
        st.markdown("<div class='wrap-policy'>", unsafe_allow_html=True)
        st.code(policy_text)
        st.markdown("</div>", unsafe_allow_html=True)

        st.subheader("🔖 Applicable Waiver Codes")
        endo_codes = (data.get("endorsement_codes", {}) or {}).get(f"{service_request_type}_code", [])
        if waiver_present:
            st.markdown(f"`{', '.join(endo_codes) if endo_codes else '—'}`")
        else:
            st.markdown(" No applicable waiver code found ")
        st.subheader("⏰ Policy Deadlines")
        deadline_data = (data.get("policy_deadlines", {}) or {}).get(service_request_type, {})
        render_deadlines(deadline_data)
        submitted_at = datetime.now().strftime("%m%d-%I%M%p")
        log_entry = {
            "service_case_id": service_case_id,
            "route": "refund_reissue",
            "submitted_at": submission_time,          # keep human-readable
            "submitted_at_iso": submitted_at_iso,     # machine-parseable ISO
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
            "saved_file_path": saved_file_path,
        }
        with open(os.path.join(SUBMISSIONS_DIR, f"{service_case_id}.json"), "w", encoding="utf-8") as f:
            json.dump(log_entry, f, indent=2)

# ==================== 2) GENERAL INQUIRIES ==================== #
elif support_type == "General Inquiries":
    st.markdown(
        "<h2 style='text-align: center;'>📨 General Inquiry</h2>",
        unsafe_allow_html=True
    )
    with st.form("general_form"):
        agency_name = st.text_input("🏷️ Agency Name")
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
            "email": email,
            "comment": comment,
            "submitted_at": gi_time,
            "submitted_at_iso": gi_time_iso,
        }
        with open(os.path.join(SUBMISSIONS_DIR, f"{gi_case_id}.json"), "w", encoding="utf-8") as f:
            json.dump(gi_log, f, indent=2)
        st.success("✅ Inquiry submitted. Our team will contact you shortly.")

# ==================== 3) AIRLINE POLICIES ==================== #
elif support_type == "Airline Policies":
    st.markdown("<h2 style='text-align:center;'>📚 Airline Policy Lookup</h2>", unsafe_allow_html=True)

    with st.form("policy_form"):
        agency_name = st.text_input("🏷️ Agency Name")

        # Load mapping: { "018": "Juneyao Airlines", ... }
        airlines_map_raw = load_json(AIRLINE_LIST_PATH, default={}) or {}
        if not isinstance(airlines_map_raw, dict):
            st.error("❌ Airline list must be a mapping of plating code → airline name.")
            st.stop()

        # Normalize: zero-pad codes, strip whitespace
        airlines_map = {str(k).zfill(3): str(v).strip() for k, v in airlines_map_raw.items()}

        # Sorted options → "Name (code)"
        options = sorted([(name, code) for code, name in airlines_map.items()],
                         key=lambda x: x[0].lower())
        labels = [f"{name} ({code})" for name, code in options]

        # Default index to 0 so it works on older Streamlit versions
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

        # ✅ Ensure the submit button is inside the form
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

        # --- Render policy text
        st.subheader("📋 Policy")
        ptext = (pdata.get("policies", {}) or {}).get(key, "No policy available for this request type.")
        st.markdown("<div class='wrap-policy'>", unsafe_allow_html=True)
        st.code(ptext)
        st.markdown("</div>", unsafe_allow_html=True)

        # --- Deadlines
        st.subheader("⏰ Policy Deadlines")
        dl = (pdata.get("policy_deadlines", {}) or {}).get(key, {})
        render_deadlines(dl)

        # --- Excluded agencies
        st.subheader("🚫 Excluded Agencies (if any)")
        excl = normalize_excluded((pdata.get("agency_exclusion_list", {}) or {}).get("excluded_agencies", []))
        st.markdown(f"`{', '.join(excl) if excl else 'None on file'}`")

        # --- Log
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
        with open(os.path.join(SUBMISSIONS_DIR, f"{pol_case_id}.json"), "w", encoding="utf-8") as f:
            json.dump(pol_log, f, indent=2)