# policy_browser.py
import json
import os
from glob import glob
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

# ---------- Config ----------
DEFAULT_DIR = os.path.join("apg_hub", "data", "airline_policies")  # adjust if needed

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
    "policies_required": PREFERRED_ORDER,  # you can trim or change this if some are optional
    "endorsement_subkeys": [
        "involuntary_refund_code",
        "involuntary_reissue_code",
        "medical_refund_code",
    ],
}

# ---------- Small helpers ----------
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
    # Normalize common bullet characters and tidy whitespace
    out = text.replace("\r\n", "\n").replace("•", "-").replace("\u2022", "-")
    out = out.replace("→", "→")  # keep arrows; placeholder for future transforms
    return out.strip()

def validate_policy(doc: Dict[str, Any]) -> List[str]:
    errs = []
    # top-level presence
    for k in SCHEMA_HINT["top_level_required"]:
        if k not in doc:
            errs.append(f"Missing top-level key: `{k}`")

    policies = doc.get("policies", {})
    if not isinstance(policies, dict):
        errs.append("`policies` must be an object")
        return errs

    # required policy keys (treat as warnings if you prefer)
    for k in SCHEMA_HINT["policies_required"]:
        if k not in policies:
            errs.append(f"Missing policy key: `{k}`")

    # endorsement codes structure
    enc = doc.get("endorsement_codes", {})
    if not isinstance(enc, dict):
        errs.append("`endorsement_codes` must be an object")
    else:
        for sub in SCHEMA_HINT["endorsement_subkeys"]:
            if sub not in enc:
                errs.append(f"Missing endorsement subkey: `{sub}`")

    # spot common empties
    if isinstance(enc, dict):
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
        if doc.get("iata_code"): sub.append(f"**IATA:** {doc['iata_code']}")
        if doc.get("plating_carrier"): sub.append(f"**Plating Carrier:** {doc['plating_carrier']}")
        st.markdown(" • ".join(sub) if sub else "")
        if doc.get("official_website"):
            st.markdown(f"[Official Website]({doc['official_website']})")
    with col2:
        excluded = (doc.get("agency_exclusion_list") or {}).get("excluded_agencies", [])
        st.metric("Excluded Agencies", len(excluded) if isinstance(excluded, list) else 0)

def render_policies(policies: Dict[str, Any]):
    # ordered display: our preferred order first, then any extras
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
        rows.append({
            "Type": titleize_policy_key(label),
            "Codes": ", ".join(v) if isinstance(v, list) and v else "—"
        })
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
            # fallback raw view
            st.code(json.dumps(cfg, indent=2), language="json")

def render_support_contacts(contacts: Dict[str, Any]):
    st.markdown("### 🧰 Internal Support Contacts")
    if not isinstance(contacts, dict) or not contacts:
        st.info("No internal contacts listed.")
        return
    # Do not expose in emails; this is only for reviewers
    for k, v in contacts.items():
        st.markdown(f"- **{k.replace('_', ' ').title()}**: {v}")

def list_policy_files(base_dir: str) -> List[str]:
    return sorted(glob(os.path.join(base_dir, "*.json")))

# ---------- UI ----------
st.set_page_config(page_title="Airline Policy Browser", layout="wide")
st.title("🧭 Airline Policy Browser")

with st.sidebar:
    st.header("Settings")
    base_dir = st.text_input("Policies directory", value=DEFAULT_DIR)
    files = list_policy_files(base_dir)
    st.caption(f"Found {len(files)} JSON file(s).")

    # derive code list from filename (e.g., 275.json -> 275)
    options = []
    code_map = {}
    for path in files:
        name = os.path.basename(path)
        code = os.path.splitext(name)[0]
        options.append(f"{code}  —  {name}")
        code_map[f"{code}  —  {name}"] = path

    chosen = st.selectbox("Select plating carrier file", options) if options else None
    search = st.text_input("Filter by airline name/code (within file contents)", value="").strip().lower()
    show_raw = st.checkbox("Show raw JSON at bottom", value=False)
    st.markdown("---")
    st.caption("Tip: Put this app at repo root. Adjust path if your layout differs.")

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

# Optional content search filter
if search:
    hay = json.dumps(doc).lower()
    if search not in hay:
        st.info(f"No match for '{search}' in this file. Clear the filter or pick another file.")
        st.stop()

# Validation summary
errors = validate_policy(doc)
if errors:
    with st.expander(f"⚠️ {len(errors)} issue(s) detected – click to review", expanded=True):
        for e in errors:
            st.markdown(f"- {e}")
else:
    st.success("Schema looks good.")

# Render content
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

# Agency exclusions (for reviewers)
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
