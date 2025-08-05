import streamlit as st
from datetime import datetime, timedelta
import json
import os
import re
from PIL import Image
import pytesseract
import io
from dateutil.parser import parse as date_parse

st.set_page_config(page_title="Inside Sales Agent Form", layout="centered")
st.title("🛫 Inside Sales Request Form")
st.markdown("Please fill out this quick form to get immediate policy and code information.")

# ------------------ FORM START ------------------ #
with st.form("agent_form", clear_on_submit=False):
    ticket_number = st.text_input("🎫 Ticket Number")

    service_type_labels = {
        "Involuntary Refund": "involuntary_refund",
        "Involuntary Reissue": "involuntary_reissue",
        "Voluntary Refund": "voluntary_refund",
        "Medical Refund": "medical_refund",
        "Name Change": "name_change",
        "Group Booking": "group_booking",
        "Infant Policy": "infant_policy",
        "Baggage Policy": "baggage_policy",
        "Seat Request Policy": "seat_request_policy",
        "Short Term Cancellation Policy": "short_term_cancellation_policy"
    }
    selected_label = st.selectbox("🛠️ Service Request Type", list(service_type_labels.keys()))
    service_request_type = service_type_labels[selected_label]

    airline_record_locator = st.text_input("📄 Airline Record Locator Number")
    iata_agent_number = st.text_input("🏢 IATA Agent Number (clear number if not available)")
    flight_date = st.date_input("🗕️ Flight Departure Date")
    flight_time = st.time_input("🕓 Flight Departure Time")
    full_pnr = st.file_uploader("📎 Full PNR Screenshot (Required for refund/reissue)", type=["png", "jpg", "jpeg", "pdf"])
    email = st.text_input("📧 Email")
    comments = st.text_area("💬 Comments (optional)")
    submitted = st.form_submit_button("🚀 Submit")

# ------------------ WAIVER DETECTION BLOCK ------------------ #

def detect_waiver_signature(text):
    patterns = [
        r'/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,10}/E',             # Sabre
        r'RF-[A-Z0-9]{4,10}',                                # Amadeus
        r'WAIVER[:\s]+[A-Z0-9]{3,15}',                      # Travelport
        r'ENDORSEMENT[:\s]+RF-[A-Z0-9]{3,15}'               # Travelport extended
    ]
    for pattern in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

# ------------------ LOGIC ------------------ #
if submitted:
    # 1a. Ticket validation
    if not re.fullmatch(r"\d{13}", ticket_number):
        st.error("❌ Ticket Number must be exactly 13 digits — no dashes, letters, or symbols allowed.")
        st.stop()

    # 1b. Eligibility check
    eligible_path = "reuseable_code/internal_code/eligible_4_digit_codes.json"
    try:
        with open(eligible_path, "r") as f:
            eligible_codes = json.load(f)
    except FileNotFoundError:
        st.error("❌ Eligibility rules file not found.")
        st.stop()

    ticket_prefix = ticket_number[:4]
    if ticket_prefix not in eligible_codes:
        st.error(f"❌ This ticket is not eligible for support — APG does not currently service the country of origin associated with carrier code `{ticket_prefix}`.")
        st.stop()

    # 2. Validate record locator
    if not re.fullmatch(r"[A-Za-z0-9]{6}", airline_record_locator):
        st.error("❌ Airline Record Locator must be exactly 6 letters and/or numbers — no symbols allowed.")
        st.stop()

    # 3. Required fields
    required_fields = [ticket_number, service_request_type, airline_record_locator, iata_agent_number, email]
    if not all(required_fields):
        st.error("⚠️ Please fill in all required fields.")
        st.stop()

    # 4. Require screenshot for refund/reissue
    if service_request_type in ["involuntary_refund", "involuntary_reissue", "medical_refund"] and not full_pnr:
        st.error("📎 PNR screenshot is required for refund or reissue-related requests.")
        st.stop()

    # 5. Load policy
    plating_code = ticket_number[:3]
    submission_time = datetime.now().strftime("%m%d-%I%M%p")
    service_case_id = f"{plating_code}-{service_request_type}-{submission_time}"

    policy_file = f"data/airline_policies/{plating_code}.json"
    if not os.path.exists(policy_file):
        st.error(f"No policy found for carrier code `{plating_code}`.")
        st.stop()

    with open(policy_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    excluded = data.get("agency_exclusion_list", {}).get("excluded_agencies", [])

    # OCR if refund/reissue and file present
    waiver_present = False
    ocr_text = ""
    if service_request_type in ["involuntary_refund", "involuntary_reissue", "medical_refund"] and full_pnr:
        file_bytes = full_pnr.read()
        full_pnr.seek(0)
        image = Image.open(io.BytesIO(file_bytes))
        ocr_text = pytesseract.image_to_string(image)

        waiver_present = detect_waiver_signature(ocr_text)

        # Save screenshot
        os.makedirs("logs/screenshots", exist_ok=True)
        ext = os.path.splitext(full_pnr.name)[-1]
        with open(f"logs/screenshots/{service_case_id}{ext}", "wb") as out_file:
            out_file.write(file_bytes)

    # SECTION 1 – Service Case
    st.subheader("📌 Section 1: Service Case #")
    st.code(service_case_id)

    # SECTION 2 – Disclaimer + Exclusions
    st.subheader("⚠️ Section 2: Disclaimer")
    st.markdown("Please review the fare rules, or you could be debited.")
    st.markdown(f"**Excluded Agencies:** `{', '.join(excluded) if excluded else 'None on file'}`")

    # SECTION 3 – Policy
    st.subheader("📋 Section 3: Matching Policy Information")
    policy_text = data.get("policies", {}).get(service_request_type, "No policy information found.")
    st.code(policy_text)

    # SECTION 4 – Endorsement Code (if waiver present)
    st.subheader("🔖 Section 4: ENDO Code")
    endo_codes = data.get("endorsement_codes", {}).get(f"{service_request_type}_code", [])
    if waiver_present:
        st.markdown(f"`{', '.join(endo_codes) if endo_codes else '—'}`")
    else:
        st.markdown("— (No valid waiver code detected in uploaded PNR)")

    # SECTION 5 – Deadlines
    st.subheader("⏰ Section 5: Deadline Information")
    deadline_data = data.get("policy_deadlines", {}).get(service_request_type, {})
    if "eligible_rebooking_range_deadline" in deadline_data:
        d = deadline_data["eligible_rebooking_range_deadline"]
        st.markdown(f"Rebooking allowed from **{d['before_original_departure']} days before** to **{d['after_original_departure']} days after** original departure.")
    else:
        st.markdown("—")

    # SECTION 6 – PNR Metadata (optional)
    def convert_time_to_minutes(tstr):
        match = re.match(r'(\d{1,4})([AP])', tstr.upper())
        if not match:
            return None
        raw, period = match.groups()
        raw = raw.zfill(4)
        hour = int(raw[:-2])
        minute = int(raw[-2:])
        if period == 'P' and hour != 12: hour += 12
        if period == 'A' and hour == 12: hour = 0
        return hour * 60 + minute

    def extract_pnr_fields(ocr_text, today=None):
        today = today or datetime.now()
        results = {}
        status_codes = re.findall(r'\b(HK\d?|HX\d?|TK\d?|SC|NN/\w+|SS\d?)\b', ocr_text)
        results['status_codes'] = list(set(status_codes))
        issued_match = re.search(r'ISSUED[:\s]*(\d{1,2}[A-Z]{3}\d{2,4})', ocr_text)
        if issued_match:
            try:
                issued_date = date_parse(issued_match.group(1), dayfirst=True)
                results['ticket_issue_date'] = issued_date.strftime('%Y-%m-%d')
            except:
                results['ticket_issue_date'] = issued_match.group(1)
        flight_times = re.findall(r'\b(\d{3,4}[AP])\b', ocr_text)
        try:
            if len(flight_times) >= 4:
                t1 = convert_time_to_minutes(flight_times[1])
                t2 = convert_time_to_minutes(flight_times[2])
                layover_min = t2 - t1
                if layover_min < 0: layover_min += 1440
                results['time_between_flights'] = f"{layover_min//60}h {layover_min%60}m"
        except:
            results['time_between_flights'] = None
        return results

    if ocr_text:
        st.subheader("📄 Section 6: Parsed PNR Metadata")
        parsed = extract_pnr_fields(ocr_text)
        for k, v in parsed.items():
            label = k.replace("_", " ").title()
            st.markdown(f"**{label}:** {v if v else '—'}")

    # SECTION 7 – Internal Logging
    os.makedirs("logs/submissions", exist_ok=True)
    log_entry = {
        "service_case_id": service_case_id,
        "ticket_number": ticket_number,
        "ticket_prefix": ticket_prefix,
        "service_request_type": service_request_type,
        "airline_record_locator": airline_record_locator,
        "iata_agent_number": iata_agent_number,
        "flight_date": str(flight_date),
        "flight_time": str(flight_time),
        "email": email,
        "excluded_agencies": excluded,
        "endorsement_code": endo_codes if waiver_present else [],
        "waiver_detected": waiver_present,
        "ocr_text": ocr_text[:2000]
    }

    if ocr_text:
        log_entry["pnr_metadata"] = parsed

    with open(f"logs/submissions/{service_case_id}.json", "w") as f:
        json.dump(log_entry, f, indent=2)