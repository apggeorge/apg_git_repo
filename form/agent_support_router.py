# 📄 AGENT SUPPORT ROUTER (agent_support_router.py)
import streamlit as st
import json, os, re, io
from datetime import datetime
from PIL import Image
import pytesseract

st.set_page_config(page_title="Agency Support Request", layout="centered")
st.title("🧭 Agency Support Request")
st.markdown("Select the type of support you need:")

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
    r"RF-[A-Z0-9]{4,10}",                    # Amadeus
    r"WAIVER[:\\s]+[A-Z0-9]{3,15}",          # Travelport
    r"ENDORSEMENT[:\\s]+RF-[A-Z0-9]{3,15}",  # Travelport extended
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


support_type = st.selectbox(
    "How can we help you?",
    ["Refunds / Reissues", "General Inquiries", "Airline Policies"],
)

# ==================== 1) REFUNDS / REISSUES ==================== #
if support_type == "Refunds / Reissues":
    st.header("🛫 Refund / Reissue Request")
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
        policy_file = os.path.join(POLICY_DIR, f"{plating_code}.json")
        if not os.path.exists(policy_file):
            st.error(f"❌ No policy found for plating carrier `{plating_code}`.")
            st.stop()
        data = load_json(policy_file, default={}) or {}
        excluded = normalize_excluded(data.get("agency_exclusion_list", {}).get("excluded_agencies", []))
        waiver_present, ocr_text = False, ""
        if full_pnr is not None and full_pnr.type in ("image/png", "image/jpeg", "image/jpg"):
            try:
                file_bytes = full_pnr.read()
                full_pnr.seek(0)
                img = Image.open(io.BytesIO(file_bytes))
                ocr_text = pytesseract.image_to_string(img)
                waiver_present = detect_waiver_signature(ocr_text)
                os.makedirs("logs/screenshots", exist_ok=True)
                ext = os.path.splitext(full_pnr.name)[-1]
                with open(f"logs/screenshots/{service_case_id}{ext}", "wb") as out_file:
                    out_file.write(file_bytes)
            except Exception:
                st.warning("⚠️ Unable to OCR the uploaded image. Proceeding without waiver detection.")
        elif full_pnr is not None and full_pnr.type == "application/pdf":
            st.info("ℹ️ PDF uploaded — OCR not applied here. (Image uploads support automatic waiver detection.)")
        st.subheader("📌 Section 1: Service Case #")
        st.code(service_case_id)
        st.subheader("⚠️ Section 2: Disclaimer & Exclusions")
        st.markdown("Please review fare rules, or you could be debited.")
        st.markdown(f"**Excluded Agencies:** `{', '.join(excluded) if excluded else 'None on file'}`")
        st.subheader("📋 Section 3: Matching Policy Information")
        policy_text = (data.get("policies", {}) or {}).get(service_request_type, "No policy information found.")
        st.code(policy_text)
        st.subheader("🔖 Section 4: ENDO Code (shown only if waiver detected)")
        endo_codes = (data.get("endorsement_codes", {}) or {}).get(f"{service_request_type}_code", [])
        if waiver_present:
            st.markdown(f"`{', '.join(endo_codes) if endo_codes else '—'}`")
        else:
            st.markdown("— (No valid waiver code detected in uploaded PNR)")
        st.subheader("⏰ Section 5: Deadline Information")
        deadline_data = (data.get("policy_deadlines", {}) or {}).get(service_request_type, {})
        render_deadlines(deadline_data)
        os.makedirs("logs/submissions", exist_ok=True)
        log_entry = {
            "service_case_id": service_case_id,
            "ticket_number": ticket_number,
            "ticket_prefix": ticket_prefix,
            "service_request_type": service_request_type,
            "airline_record_locator": airline_record_locator,
            "agency_id": agency_id,
            "agency_name": agency_name,
            "email": email,
            "excluded_agencies": excluded,
            "endorsement_code": endo_codes if waiver_present else [],
            "waiver_detected": waiver_present,
        }
        with open(f"logs/submissions/{service_case_id}.json", "w", encoding="utf-8") as f:
            json.dump(log_entry, f, indent=2)

# ==================== 2) GENERAL INQUIRIES ==================== #
elif support_type == "General Inquiries":
    st.header("📨 General Inquiry")
    with st.form("general_form"):
        agency_name = st.text_input("🏷️ Agency Name")
        email = st.text_input("📧 Email Address")
        comment = st.text_area("💬 Comment")
        submitted = st.form_submit_button("Submit Inquiry")
    if submitted:
        st.success("✅ Inquiry submitted. Our team will contact you shortly.")

# ==================== 3) AIRLINE POLICIES (lookup only) ==================== #
elif support_type == "Airline Policies":
    st.header("📚 Airline Policy Lookup")
    with st.form("policy_form"):
        agency_name = st.text_input("🏷️ Agency Name")
        airline_list = load_json(AIRLINE_LIST_PATH, default=[]) or []
        if not airline_list:
            st.error("❌ Airline list not found.")
        airline = st.selectbox("🛫 Airline", airline_list)
        policy_service_label = st.selectbox("🛠️ Support Request Type", SERVICE_TYPES)
        submitted = st.form_submit_button("🔍 Lookup")
    if submitted and airline:
        code = airline_name_to_plating_code(airline)
        if not code:
            st.error("❌ Could not determine plating carrier for the selected airline.")
        else:
            policy_path = os.path.join(POLICY_DIR, f"{code}.json")
            if not os.path.exists(policy_path):
                st.error(f"❌ No policy file found for plating carrier `{code}`.")
            else:
                pdata = load_json(policy_path, default={}) or {}
                st.subheader("📋 Policy")
                key = SERVICE_TYPE_KEYS[policy_service_label]
                ptext = (pdata.get("policies", {}) or {}).get(key, "No policy available for this request type.")
                st.code(ptext)
                st.subheader("⏰ Deadlines")
                dl = (pdata.get("policy_deadlines", {}) or {}).get(key, {})
                render_deadlines(dl)
                st.subheader("🚫 Excluded Agencies (if any)")
                excl = normalize_excluded((pdata.get("agency_exclusion_list", {}) or {}).get("excluded_agencies", []))
                st.markdown(f"`{', '.join(excl) if excl else 'None on file'}`")
