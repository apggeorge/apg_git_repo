# policy_browser.py
import json
import os
from glob import glob
from typing import Any, Dict, List, Optional, Tuple
import streamlit as st

# ---------- Config ----------
DEFAULT_DIR = os.path.join("data", "airline_policies")

TITLE_MAP = {
    "involuntary_refund": "Involuntary Refund",
    "involuntary_reissue": "Involuntary Reissue",
    "voluntary_refund": "Voluntary Refund",
    "medical_refund": "Medical Refund",
    "name_change": "Name Change / Correction",
    "group_booking": "Group Booking",
    "infant_policy": "Infant & Child Policy",
    "baggage_policy": "Baggage Policy",
    "seat_request_policy": "Seat Requests & Special Services",
    "short_term_cancellation_policy": "Short-Term Cancellation (Void) Policy",
}

PREFERRED_ORDER = list(TITLE_MAP.keys())

SCHEMA_HINT = {
    "top_level_required": [
        "airline_name", "iata_code", "plating_carrier", "official_website",
        "policies", "agency_exclusion_list", "endorsement_codes",
        "support_contacts"
    ],
    "policies_required": PREFERRED_ORDER,
    "endorsement_subkeys": [
        "involuntary_refund_code",
        "involuntary_reissue_code",
        "medical_refund_code",
    ],
}

# ---------- Helpers ----------
def titleize_policy_key(key: str) -> str:
    return TITLE_MAP.get(key, key.replace("_", " ").title())

def load_json(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, str(e)

def normalize_md(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return text.replace("\r\n", "\n").replace("•", "-").replace("\u2022", "-").strip()

def validate_policy(doc: Dict[str, Any]) -> List[str]:
    errs = []
    for k in SCHEMA_HINT["top_level_required"]:
        if k not in doc:
            errs.append(f"Missing top-level key: `{k}`")

    policies = doc.get("policies", {})
    if not isinstance(policies, dict):
        errs.append("`policies` must be an object")
        return errs

    for k in SCHEMA_HINT["policies_required"]:
        if k not in policies:
            errs.append(f"Missing policy key: `{k}`")

    enc = doc.get("endorsement_codes", {})
    if not isinstance(enc, dict):
        errs.append("`endorsement_codes` must be an object")
    else:
        for sub in SCHEMA_HINT["endorsement_subkeys"]:
            if sub not in enc:
                errs.append(f"Missing endorsement subkey: `{sub}`")
        for k, v in enc.items():
            if v in (None, "", []) and k.endswith("_code"):
                errs.append(f"`{k}` is empty")

    deadlines = doc.get("policy_deadlines")
    if deadlines is not None and not isinstance(deadlines, dict):
        errs.append("`policy_deadlines` must be an object if present")

    return errs

def render_header(doc: Dict[str, Any]):
    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(f"## {doc.get('airline_name', 'Unknown Airline')}")
        sub = []
        if doc.get("iata_code"):
            sub.append(f"**IATA:** {doc['iata_code']}")
        if doc.get("plating_carrier"):
            sub.append(f"**Plating Carrier:** {doc['plating_carrier']}")
        st.markdown(" • ".join(sub) if sub else "")
        if doc.get("official_website"):
            st.markdown(f"[Official Website]({doc['official_website']})")
    with col2:
        excluded = (doc.get("agency_exclusion_list") or {}).get("excluded_agencies", [])
        st.metric("Excluded Agencies", len(excluded) if isinstance(excluded, list) else 0)

def render_policies(policies: Dict[str, Any]):
    ordered_keys = PREFERRED_ORDER + [k for k in policies.keys() if k not in PREFERRED_ORDER]
    seen = set()
    for key in ordered_keys:
        if key in policies and key not in seen:
            seen.add(key)
            st.markdown(f"### 📘 {titleize_policy_key(key)}")
            st.markdown(normalize_md(policies.get(key, "")) or "_(no text)_")

def render_endorsements(enc: Dict[str, Any]):
    st.markdown("### 🏷️ Endorsement Codes")
    if not isinstance(enc, dict) or not enc:
        st.info("No endorsement codes provided.")
        return
    rows = []
    for k, v in enc.items():
        label = k.replace("_code", "")
        rows.append({"Type": titleize_policy_key(label), "Codes": ", ".join(v) if v else "—"})
    st.table(rows)

def render_deadlines(deadlines: Dict[str, Any]):
    if not deadlines:
        return
    st.markdown("### ⏱️ Policy Deadlines")
    for policy_key, cfg in deadlines.items():
        st.markdown(f"**{titleize_policy_key(policy_key)}**")
        if isinstance(cfg, dict) and "eligible_rebooking_range_deadline" in cfg:
            rng = cfg["eligible_rebooking_range_deadline"]
            before = rng.get("before_original_departure")
            after = rng.get("after_original_departure")
            if before is not None and after is not None:
                st.markdown(f"- Eligible rebooking window: **{before} days before** to **{after} days after** the original departure.")
            else:
                st.markdown("- Eligible rebooking window: _not fully specified_.")
        else:
            st.code(json.dumps(cfg, indent=2), language="json")

def render_support_contacts(contacts: Dict[str, Any]):
    st.markdown("### 🧰 Internal Support Contacts")
    if not isinstance(contacts, dict) or not contacts:
        st.info("No internal contacts listed.")
        return
    for k, v in contacts.items():
        st.markdown(f"- **{k.replace('_', ' ').title()}**: {v}")

def list_policy_files(base_dir: str) -> List[str]:
    return sorted(glob(os.path.join(base_dir, "*.json")))

# ---------- UI ----------
st.set_page_config(page_title="Airline Policy Browser", layout="wide")
st.title("🧭 Airline Policy Browser")

# Sidebar
with st.sidebar:
    st.header("Settings")
    base_dir = DEFAULT_DIR
    st.caption(f"Browsing: {os.path.abspath(base_dir)}")
    files = list_policy_files(base_dir)
    st.caption(f"Found {len(files)} JSON file(s).")
    options = [f"{os.path.splitext(os.path.basename(p))[0]}  —  {os.path.basename(p)}" for p in files]
    code_map = {opt: path for opt, path in zip(options, files)}
    chosen = st.selectbox("Select plating carrier file", options) if options else None
    search = st.text_input("Filter by airline name/code (within file contents)", value="").strip().lower()
    show_raw = st.checkbox("Show raw JSON at bottom", value=False)

if not files:
    st.warning("No JSON files found. Check the directory path.")
    st.stop()

path = code_map.get(chosen) if chosen else None
if not path:
    st.info("Select a file from the sidebar to begin.")
    st.stop()

doc, err = load_json(path)
if err or not isinstance(doc, dict):
    st.error(f"Failed to load JSON: {err or 'not an object'}")
    st.stop()

if search:
    hay = json.dumps(doc).lower()
    if search not in hay:
        st.info(f"No match for '{search}' in this file.")
        st.stop()

# Validation
errors = validate_policy(doc)
if errors:
    with st.expander(f"⚠️ {len(errors)} issue(s) detected – click to review", expanded=True):
        for e in errors:
            st.markdown(f"- {e}")
else:
    st.success("Schema looks good.")

# Render
render_header(doc)
st.markdown("---")
st.subheader("Policies")
render_policies(doc.get("policies", {}))
st.markdown("---")
render_endorsements(doc.get("endorsement_codes", {}))

deadlines = doc.get("policy_deadlines", {})
if isinstance(deadlines, dict) and deadlines:
    st.markdown("---")
    render_deadlines(deadlines)

contacts = doc.get("support_contacts", {})
if isinstance(contacts, dict) and contacts:
    st.markdown("---")
    render_support_contacts(contacts)

excl = (doc.get("agency_exclusion_list") or {}).get("excluded_agencies", [])
st.markdown("---")
st.markdown("### 🚫 Agency Exclusions")
if isinstance(excl, list) and excl:
    st.write(excl)
else:
    st.caption("No excluded agencies listed.")

if show_raw:
    st.markdown("---")
    st.code(json.dumps(doc, indent=2, ensure_ascii=False), language="json")
