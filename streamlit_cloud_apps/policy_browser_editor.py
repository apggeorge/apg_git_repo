"""
Streamlit editor for airline policy JSON files.

Drop this file into your repo (e.g., apg_git_repo/streamlit_cloud_apps/) and run:

    streamlit run policy_browser_editor.py

It reuses your viewer flow but adds:
- Edit mode with field-level editors for policies, endorsements, deadlines, contacts, and exclusions
- Schema validation (uses your validate_policy rules)
- Diff preview vs. on-disk file
- Safe save to _drafts/ (default), or backup+overwrite original
- Download edited JSON or a unified .patch

Notes:
- In Streamlit Cloud, direct writes persist only for the session. Use Download or connect a PR step for lasting changes.
- You can also import the editor functions into your existing policy_browser.py and gate with a checkbox.
"""
from __future__ import annotations

import json
import os
import difflib
from glob import glob
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime, timezone
import base64, requests, re

import pandas as pd
import streamlit as st

# === GitHub helpers (place after imports) ===
def _gh_headers():
    return {
        "Authorization": f"Bearer {st.secrets['github']['token']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

def _slug(s: str, fallback="anon"):
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or fallback

def gh_put_file(owner: str, repo: str, branch: str, path: str,
                content_bytes: bytes, message: str):
    """Create/update a single file via GitHub Contents API."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"

    # Check if it exists to capture sha
    sha = None
    r_get = requests.get(url, headers=_gh_headers(), params={"ref": branch})
    if r_get.status_code == 200:
        sha = r_get.json().get("sha")

    payload = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    r_put = requests.put(url, headers=_gh_headers(), json=payload)
    if r_put.status_code not in (200, 201):
        raise RuntimeError(f"GitHub write failed: {r_put.status_code} {r_put.text}")
    return r_put.json()

# =========================
# Paths / Constants
# =========================
REPO_ROOT = Path(__file__).resolve().parent.parent  # => apg_git_repo/
DEFAULT_DIR = str(REPO_ROOT / "airline_policies")
DRAFTS_DIR = Path(DEFAULT_DIR) / "_drafts"
HISTORY_DIR = Path(DEFAULT_DIR) / "_history"

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
        "support_contacts",
    ],
    "policies_required": PREFERRED_ORDER,
    "endorsement_subkeys": [
        "involuntary_refund_code",
        "involuntary_reissue_code",
        "medical_refund_code",
    ],
}

# =========================
# Helpers
# =========================
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
    return (
        text.replace("\r\n", "\n")
            .replace("•", "-")
            .replace("\u2022", "-")
            .strip()
    )


def validate_policy(doc: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
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


def list_policy_files(base_dir: str) -> List[str]:
    return sorted(glob(os.path.join(base_dir, "*.json")))


def ensure_schema_defaults(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc.setdefault("airline_name", "")
    doc.setdefault("iata_code", "")
    doc.setdefault("plating_carrier", "")
    doc.setdefault("official_website", "")

    doc.setdefault("policies", {})
    for k in PREFERRED_ORDER:
        doc["policies"].setdefault(k, "")

    # Allow custom policy keys (kept as-is)

    # Endorsements: ensure required keys exist, enforce list[str]
    enc = doc.setdefault("endorsement_codes", {})
    for sub in SCHEMA_HINT["endorsement_subkeys"]:
        enc.setdefault(sub, [])
    for k, v in list(enc.items()):
        if isinstance(v, str):
            enc[k] = [s.strip() for s in v.split(",") if s.strip()]
        elif isinstance(v, list):
            enc[k] = [str(x).strip() for x in v if str(x).strip()]
        else:
            enc[k] = []

    # Agency exclusions wrapper
    ael = doc.setdefault("agency_exclusion_list", {})
    excl = ael.setdefault("excluded_agencies", [])
    if isinstance(excl, str):
        ael["excluded_agencies"] = [s.strip() for s in excl.split(",") if s.strip()]

    # Support contacts
    doc.setdefault("support_contacts", {})

    # Optional deadlines stays as-is, but must be dict if present
    if doc.get("policy_deadlines") is None:
        pass

    # Non-breaking metadata
    meta = doc.setdefault("_meta", {})
    meta.setdefault("last_opened_utc", datetime.now(timezone.utc).isoformat())
    return doc


def to_pretty_json(d: Dict[str, Any]) -> str:
    return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=False)


def diff_strings(a: str, b: str, a_name: str, b_name: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            a.splitlines(),
            b.splitlines(),
            fromfile=a_name,
            tofile=b_name,
            lineterm="",
        )
    )

# =========================
# UI Components
# =========================

def header_view(doc: Dict[str, Any]):
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.markdown(f"## {doc.get('airline_name', 'Unknown Airline')}")
        sub = []
        if doc.get("iata_code"):
            sub.append(f"**IATA:** {doc['iata_code']}")
        if doc.get("plating_carrier"):
            sub.append(f"**Plating:** {doc['plating_carrier']}")
        st.markdown(" • ".join(sub) if sub else "")
        if doc.get("official_website"):
            st.markdown(f"[Official Website]({doc['official_website']})")
    with col2:
        excluded = (doc.get("agency_exclusion_list") or {}).get("excluded_agencies", [])
        st.metric("Excluded Agencies", len(excluded) if isinstance(excluded, list) else 0)
    with col3:
        st.metric("Policy Sections", len(doc.get("policies", {})))


def policies_editor(policies: Dict[str, Any]) -> Dict[str, Any]:
    st.markdown("### 📘 Policies")
    new_policies = dict(policies)

    # Ensure preferred keys are first
    ordered_keys = PREFERRED_ORDER + [k for k in new_policies.keys() if k not in PREFERRED_ORDER]
    seen: set[str] = set()

    for key in ordered_keys:
        if key in seen:
            continue
        seen.add(key)
        with st.expander(f"✏️ {titleize_policy_key(key)}", expanded=False):
            val = st.text_area(
                f"{key}",
                value=new_policies.get(key, ""),
                height=200,
                key=f"pol_{key}",
            )
            new_policies[key] = val

    st.divider()
    with st.expander("➕ Add custom policy section", expanded=False):
        new_key = st.text_input("New policy key (snake_case recommended)", key="add_pol_key")
        if st.button("Add section", type="secondary", key="btn_add_pol"):
            if new_key and new_key not in new_policies:
                new_policies[new_key] = ""
                st.success(f"Added section `{new_key}`. Scroll up to edit.")
            else:
                st.warning("Provide a unique key.")

    return new_policies


def endorsements_editor(enc: Dict[str, Any]) -> Dict[str, Any]:
    st.markdown("### 🏷️ Endorsement Codes")

    # Flatten to a table for editing
    rows = []
    for k, v in enc.items():
        rows.append({"key": k, "codes": ", ".join(v) if isinstance(v, list) else (v or "")})
    df = pd.DataFrame(rows or [{"key": "involuntary_refund_code", "codes": ""}])

    edited = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "key": st.column_config.TextColumn("Key"),
            "codes": st.column_config.TextColumn("Codes (comma-separated)"),
        },
        key="endorsement_editor",
    )

    out: Dict[str, List[str]] = {}
    for _, row in edited.iterrows():
        key = str(row.get("key", "")).strip()
        codes_raw = str(row.get("codes", ""))
        if not key:
            continue
        codes = [c.strip() for c in codes_raw.split(",") if c.strip()]
        out[key] = codes

    # Ensure required keys exist
    for req in SCHEMA_HINT["endorsement_subkeys"]:
        out.setdefault(req, [])
    return out


def deadlines_editor(deadlines: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    st.markdown("### ⏱️ Policy Deadlines (raw JSON)")
    text = "" if not deadlines else to_pretty_json(deadlines)
    edited_text = st.text_area("deadlines_json", value=text, height=200)
    if not edited_text.strip():
        return None
    try:
        parsed = json.loads(edited_text)
        if not isinstance(parsed, dict):
            st.error("`policy_deadlines` must be a JSON object (dictionary). Keeping original value.")
            return deadlines
        return parsed
    except Exception as e:
        st.error(f"Invalid JSON for deadlines: {e}")
        return deadlines


def contacts_editor(contacts: Dict[str, Any]) -> Dict[str, Any]:
    st.markdown("### 🧰 Internal Support Contacts")
    rows = [{"role": k, "value": v} for k, v in (contacts or {}).items()]
    df = pd.DataFrame(rows or [{"role": "Inside Sales", "value": "emails or slack"}])
    edited = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        column_config={
            "role": st.column_config.TextColumn("Role"),
            "value": st.column_config.TextColumn("Value (email, phone, notes)"),
        },
        key="contacts_editor",
    )
    out: Dict[str, str] = {}
    for _, row in edited.iterrows():
        role = str(row.get("role", "")).strip()
        value = str(row.get("value", "")).strip()
        if role and value:
            out[role] = value
    return out


def exclusions_editor(ael: Dict[str, Any]) -> Dict[str, Any]:
    st.markdown("### 🚫 Agency Exclusions")
    excl = (ael or {}).get("excluded_agencies", [])
    df = pd.DataFrame({"IATA/ARC": excl or []})
    edited = st.data_editor(
        df,
        use_container_width=True,
        num_rows="dynamic",
        key="exclusions_editor",
    )
    out_list = [str(x).strip() for x in edited["IATA/ARC"].tolist() if str(x).strip()]
    return {"excluded_agencies": out_list}


# =========================
# App
# =========================
st.set_page_config(page_title="Airline Policy Editor", layout="wide")
st.title("🧭 Airline Policy Editor (with validation & diff)")

with st.sidebar:
    st.header("Source")
    base_dir = DEFAULT_DIR
    st.caption(f"Browsing: {os.path.abspath(base_dir)}")
    files = list_policy_files(base_dir)
    st.caption(f"Found {len(files)} JSON file(s).")
    options = [f"{os.path.splitext(os.path.basename(p))[0]}  —  {os.path.basename(p)}" for p in files]
    code_map = {opt: path for opt, path in zip(options, files)}
    chosen = st.selectbox("Select plating carrier file", options) if options else None

    st.markdown("---")
    st.header("Editor Settings")
    editor_name = st.text_input("Your name (for audit)")
    save_mode = st.radio("Save target", ["Drafts (_drafts/)", "Backup + Overwrite original"], index=0)
    show_raw = st.checkbox("Show raw JSON preview", value=False)

if not files:
    st.warning("No JSON files found. Check the directory path.")
    st.stop()

path = code_map.get(chosen) if chosen else None
if not path:
    st.info("Select a file from the sidebar to begin.")
    st.stop()

orig_doc, err = load_json(path)
if err or not isinstance(orig_doc, dict):
    st.error(f"Failed to load JSON: {err or 'not an object'}")
    st.stop()

orig_doc = ensure_schema_defaults(orig_doc)
header_view(orig_doc)
st.markdown("---")

# Search (client-side) in this file
with st.expander("🔎 Search within file", expanded=False):
    q = st.text_input("Find (case-insensitive)", value="").strip().lower()
    if q:
        hay = json.dumps(orig_doc).lower()
        if q in hay:
            st.success("Found matches in JSON (expand Raw preview to inspect).")
        else:
            st.info("No matches in this file.")

# EDIT MODE
st.subheader("Edit mode")
st.caption("Make changes below. Normalization (bullets, CRLF) is applied on save to policy text blocks.")

edited = dict(orig_doc)
edited["policies"] = policies_editor(edited.get("policies", {}))
st.markdown("---")
edited["endorsement_codes"] = endorsements_editor(edited.get("endorsement_codes", {}))
st.markdown("---")
# Deadlines remain optional
edited["policy_deadlines"] = deadlines_editor(edited.get("policy_deadlines"))
st.markdown("---")
edited["support_contacts"] = contacts_editor(edited.get("support_contacts", {}))
st.markdown("---")
edited["agency_exclusion_list"] = exclusions_editor(edited.get("agency_exclusion_list", {}))

# Normalize policy markdown text areas now (preview and save consistently)
for k, v in list(edited.get("policies", {}).items()):
    edited["policies"][k] = normalize_md(v)

# Audit metadata (does not affect schema validation)
meta = edited.setdefault("_meta", {})
if editor_name:
    meta["last_edited_by"] = editor_name
meta["last_edited_utc"] = datetime.now(timezone.utc).isoformat()

# VALIDATION
st.markdown("---")
errors = validate_policy(edited)
if errors:
    with st.expander(f"⚠️ {len(errors)} issue(s) detected – click to review", expanded=True):
        for e in errors:
            st.markdown(f"- {e}")
else:
    st.success("Schema looks good.")

# DIFF & RAW
orig_txt = to_pretty_json(orig_doc)
new_txt = to_pretty_json(edited)
d = diff_strings(orig_txt, new_txt, os.path.basename(path)+" (original)", os.path.basename(path)+" (edited)")
with st.expander("🧩 Diff vs. original (unified)", expanded=False):
    st.code(d or "(no changes)", language="diff")

if show_raw:
    st.markdown("---")
    st.subheader("Raw JSON (edited)")
    st.code(new_txt, language="json")

# === SAVE / DOWNLOAD (CTA-first) ===
st.markdown("---")
cta_col, json_col, patch_col = st.columns([2, 1, 1])

with cta_col:
    st.caption("Primary action")
    if st.button("✅ Submit Modifications", type="primary", use_container_width=True):
        try:
            owner  = st.secrets["github"]["owner"]
            repo   = st.secrets["github"]["repo_suggestions"]
            branch = st.secrets["github"]["default_branch"]

            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            base = os.path.basename(path).rsplit(".json", 1)[0]
            editor  = _slug(editor_name, "anon")
            plating = _slug(str(edited.get("plating_carrier", "")), "unk")
            airline = _slug(str(edited.get("airline_name", "")), "airline")

            suggestion = {
                "type": "policy_suggestion",
                "source_file": os.path.basename(path),
                "airline_name": edited.get("airline_name"),
                "iata_code": edited.get("iata_code"),
                "plating_carrier": edited.get("plating_carrier"),
                "official_website": edited.get("official_website"),
                "editor": editor_name or "anonymous",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "changed_sections": {
                    "policies_changed": [k for k, v in edited.get("policies", {}).items()
                                         if v != (orig_doc.get("policies", {}).get(k, ""))],
                    "endorsements_changed": (edited.get("endorsement_codes") != orig_doc.get("endorsement_codes")),
                    "deadlines_changed": (edited.get("policy_deadlines") or {}) != (orig_doc.get("policy_deadlines") or {}),
                    "contacts_changed": (edited.get("support_contacts") or {}) != (orig_doc.get("support_contacts") or {}),
                    "exclusions_changed": ((edited.get("agency_exclusion_list") or {}).get("excluded_agencies", [])) !=
                                          ((orig_doc.get("agency_exclusion_list") or {}).get("excluded_agencies", [])),
                },
                "diff_unified": d,                  # your unified diff
                "edited_json": json.loads(new_txt), # parsed JSON
            }

            gh_path = f"suggestions/{plating}/{ts[:4]}/{plating}-{airline}__{ts}__{editor}.suggestion.json"
            message = f"policy suggestion: {base} by {editor_name or 'anonymous'} @ {ts}"
            gh_put_file(owner, repo, branch, gh_path,
                        json.dumps(suggestion, indent=2, ensure_ascii=False).encode("utf-8"),
                        message)

            st.success(f"Submitted to GitHub → {owner}/{repo}@{branch}:{gh_path}")
            st.download_button(
                "⬇️ Download the same suggestion bundle",
                data=json.dumps(suggestion, indent=2, ensure_ascii=False),
                file_name=os.path.basename(gh_path),
                mime="application/json",
                use_container_width=True,
            )
        except Exception as e:
            st.error(f"GitHub submission failed: {e}")

with json_col:
    st.download_button(
        "Download edited JSON",
        data=new_txt,
        file_name=os.path.basename(path),
        mime="application/json",
        use_container_width=True,
    )

with patch_col:
    st.download_button(
        "Download .patch",
        data=d or "",
        file_name=os.path.basename(path).replace(".json", ".patch"),
        mime="text/x-diff",
        disabled=not bool(d),
        use_container_width=True,
    )

# Optional: keep local saves but de-emphasize them
with st.expander("Advanced / other save options", expanded=False):
    a1, a2 = st.columns(2)
    with a1:
        if st.button("Save to _drafts/"):
            DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            fname = os.path.basename(path)
            out_path = DRAFTS_DIR / f"{fname.rsplit('.json',1)[0]}__{ts}.json"
            out_path.write_text(new_txt, encoding="utf-8")
            st.success(f"Draft saved: {out_path}")

    with a2:
        if st.button("Backup & overwrite original"):
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            bak = HISTORY_DIR / f"{os.path.basename(path)}.{ts}.bak.json"
            bak.write_text(orig_txt, encoding="utf-8")
            Path(path).write_text(new_txt, encoding="utf-8")
            st.success(f"Backed up → {bak.name} and wrote changes to {path}")
