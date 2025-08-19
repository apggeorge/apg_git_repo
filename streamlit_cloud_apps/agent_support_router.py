# 📄 AGENT SUPPORT ROUTER (agent_support_router.py)
import streamlit as st
import json, os, re, io, shutil, textwrap
from datetime import datetime, timezone
from PIL import Image
import pytesseract
from pathlib import Path

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
REPO_ROOT = Path(__file__).resolve().parent.parent  # => apg_git_repo/

def _pick_existing(*cands) -> str:
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0]

POLICY_DIR = _pick_existing(str(REPO_ROOT / "airline_policies"), "data/airline_policies")
ELIGIBLE_CODE_PATH = _pick_existing(
    str(REPO_ROOT / "eligibility" / "eligible_4_digit_codes.json"),
    str(REPO_ROOT / "reuseable_code" / "internal_code" / "eligible_4_digit_codes.json"),
)
AIRLINE_LIST_PATH = _pick_existing(
    str(REPO_ROOT / "eligibility" / "eligible_airline_names.json"),
    str(REPO_ROOT / "reuseable_code" / "internal_code" / "eligible_airline_names.json"),
)
CONTACTS_FILE = _pick_existing(
    str(REPO_ROOT / "airline_contacts" / "contacts.json"),
    str(REPO_ROOT / "data" / "meta" / "airline_contacts.json"),
    "airline_contacts/contacts.json",
    "data/meta/airline_contacts.json",
)

# =========================
# Email service (resilient import)
# =========================
INSIDE_SALES_FROM = os.environ.get("APG_INSIDE_SALES_FROM", "inside.sales@apg.example")

# Dynamic loader to avoid Pylance missing-import warnings and support multiple locations
import importlib, sys as _sys
_gmail_send = None
_gmail_search = None

def _try_load(mod_path: str):
    try:
        mod = importlib.import_module(mod_path)
        send = getattr(mod, "send_email", None)
        search = getattr(mod, "search_messages_by_subject", None)
        return send, search
    except Exception:
        return None, None

# 1) Real path in your repo
_gmail_send, _gmail_search = _try_load("reuseable_code.external_code.gmail_api")

# 2) Legacy/alternate path (if you ever move it back under services/)
if _gmail_send is None:
    _gmail_send, _gmail_search = _try_load("services.email_api.gmail_api")

# 3) Last-chance: add project roots to sys.path then retry
if _gmail_send is None:
    PROJECT_ROOT = REPO_ROOT.parent
    _sys.path.extend([
        str(PROJECT_ROOT),
        str(PROJECT_ROOT / "reuseable_code"),
        str(REPO_ROOT / "reuseable_code"),
    ])
    _gmail_send, _gmail_search = _try_load("reuseable_code.external_code.gmail_api")

# =========================
# Global CSS (wrapping)
# =========================
st.markdown("""
<style>
.wrap-policy {
  border: 1px solid #e5e7eb;
  background: #f8fafc;
  border-radius: 10px;
  padding: 12px;
  overflow-x: hidden;
  box-sizing: border-box;
  max-width: 100%;
}
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

# Airlines requiring manual HQ approval (no waiver codes)
MANUAL_APPROVAL_CODES = {"141": "Flydubai", "188": "Air Cambodia", "239": "Air Mauritius"}

WAIVER_PATTERNS = [
    r"/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,10}/E",  # Sabre
    r"RF-[A-Z0-9]{4,10}",                   # Amadeus / generic
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
    raw = load_json(path, default=[]) or []
    if isinstance(raw, dict):
        raw = raw.get("codes", list(raw.keys()))
    codes = set()
    for item in raw:
        s = re.sub(r"\D", "", str(item))
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
    if not excl:
        return []
    if isinstance(excl, list):
        if len(excl) == 1 and isinstance(excl[0], str) and "," in excl[0]:
            return [x.strip() for x in excl[0].split(",") if x.strip()]
        return [str(x).strip() for x in excl if str(x).strip()]
    if isinstance(excl, str):
        return [x.strip() for x in excl.split(",") if x.strip()]
    return [str(excl).strip()]

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

# ---- Index helpers ----
def _index_add(dir_key: str, meta: dict):
    if hasattr(storage, "upsert_index_item"):
        storage.upsert_index_item(dir_key, meta)
        return
    index_key = f"{dir_key.rstrip('/')}/_index.json"
    try:
        lst = storage.read_json(index_key)
        if not isinstance(lst, list):
            lst = []
    except Exception:
        lst = []
    k = meta.get("key")
    if k and k not in lst:
        lst.append(k)
        storage.write_json(index_key, lst)

# ---- Email logging helpers ----
def _email_index_add(case_id: str, meta: dict):
    dir_key = f"emails/{case_id}"
    if hasattr(storage, "upsert_index_item"):
        storage.upsert_index_item(dir_key, meta)
        return
    index_key = f"{dir_key}/_index.json"
    try:
        lst = storage.read_json(index_key)
        if not isinstance(lst, list):
            lst = []
    except Exception:
        lst = []
    k = meta.get("key")
    if k and k not in lst:
        lst.append(k)
        storage.write_json(index_key, lst)

def _log_sent_email(case_id: str, payload: dict):
    ts = datetime.now(timezone.utc).isoformat()
    key = f"emails/{case_id}/{ts}.json"
    payload = dict(payload or {})
    payload.update({"key": key, "logged_at": ts})
    storage.write_json(key, payload)
    _email_index_add(case_id, {"key": key})
    return key

# =========================
# OCR + GDS detection + PNR parsing helpers
# =========================
GDS_SABRE_HINTS = ("WETR*", "PCC:", "/DC", "PLT", "FCI")
GDS_AMADEUS_HINTS = ("TST/", "FA PAX", "NONEND", "INVOL", "FE PAX")
GDS_TRAVELPORT_HINTS = ("WAIVER:", "ENDORSEMENT:", "WORLDSPAN", "GALILEO", "APOLLO")

MONTH_MAP = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
             "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def detect_gds(text: str) -> str:
    t = (text or "").upper()
    if any(h in t for h in GDS_SABRE_HINTS): return "sabre"
    if any(h in t for h in GDS_AMADEUS_HINTS): return "amadeus"
    if any(h in t for h in GDS_TRAVELPORT_HINTS): return "travelport"
    if re.search(r"/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,12}/E", t): return "sabre"
    if re.search(r"NONEND|RF-[A-Z0-9]{3,}", t): return "amadeus"
    return "unknown"

def _parse_issued_date_token(tok: str):
    tok = tok.strip().upper()
    m = re.match(r"(\d{1,2})([A-Z]{3})(\d{2,4})$", tok)
    if not m: return None
    d, mon, y = m.groups()
    year = int(y) if len(y)==4 else 2000 + int(y)
    try:
        return datetime(year, MONTH_MAP[mon], int(d))
    except Exception:
        return None

def _time_token_to_minutes(t: str):
    if not t: return None
    t = t.strip().upper()
    m = re.match(r"(\d{1,4})([AP])", t)
    if not m: return None
    raw, ap = m.groups()
    raw = raw.zfill(4)
    hh, mm = int(raw[:-2]), int(raw[-2:])
    if ap == "P" and hh != 12: hh += 12
    if ap == "A" and hh == 12: hh = 0
    return hh*60 + mm

def _to_minutes_any(tok: str):
    if not tok: return None
    t = tok.strip().upper()
    m = re.match(r"(\d{3,4})([AP])$", t)  # 0350P
    if m:
        h = int(m.group(1)[:-2] or "0"); mmin = int(m.group(1)[-2:])
        if m.group(2) == "P" and h != 12: h += 12
        if m.group(2) == "A" and h == 12: h = 0
        return h*60 + mmin
    m = re.match(r"^([01]\d|2[0-3])([0-5]\d)(?:\+1)?$", t)  # 1550 or 0015+1
    if m:
        return int(m.group(1))*60 + int(m.group(2))
    return None

def _minutes_to_hm(m):
    if m is None: return None
    m = int(m)
    if m < 0: m = (m + 1440) % 1440
    return f"{m//60}h {m%60}m"

def extract_common_signatures(text: str):
    t = (text or "")
    sigs = []
    sigs += re.findall(r"/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,12}/E", t, flags=re.I)           # Sabre
    sigs += re.findall(r"(?:NONEND[^\n]*?WAIVER[^\n]*?[A-Z0-9]{3,})", t, flags=re.I)   # Amadeus
    sigs += re.findall(r"RF-[A-Z0-9]{3,12}", t, flags=re.I)                            # RF- reasons
    sigs += re.findall(r"WAIVER[:\s]+[A-Z0-9]{3,15}", t, flags=re.I)                   # Travelport
    return sorted(set(sigs))

def extract_status_codes(text: str):
    pat = r"\b(HK\d?|HX\d?|TK\d?|SC|HS\/HK\d?|HK\/HX\d?|NN\/\w+|SS\d?)\b"
    return sorted(set(re.findall(pat, text or "", flags=re.I)))

def extract_ticket_issue_date(text: str):
    m = re.search(r"ISSUED[:\s]*(\d{1,2}[A-Z]{3}\d{2,4})", text or "", flags=re.I)
    if not m:
        m = re.search(r"\bDT(\d{1,2}[A-Z]{3}\d{2,4})\b", text or "", flags=re.I)  # Amadeus FA/DT
    if not m: return None, None
    raw = m.group(1).upper()
    dt = _parse_issued_date_token(raw)
    return (dt, raw)

def extract_pnr_locator(text: str):
    m = re.search(r"\bPNR[:\s]*([A-Z0-9]{5,6})\b", text or "", flags=re.I)
    if m: return m.group(1).upper()
    m = re.search(r"\bLOCATOR[:\s-]*([A-Z0-9]{6})\b", text or "", flags=re.I)
    return m.group(1).upper() if m else None

def extract_ticket_number(text: str):
    m = re.search(r"\bTKT[:\s-]*?(\d{10,14})\b", text or "", flags=re.I)
    if m: return m.group(1)
    m = re.search(r"\bFA\s+PAX\s+(\d{10,14})\b", text or "", flags=re.I)  # Amadeus
    return m.group(1) if m else None

def extract_sabre_segments(text: str):
    segs = []
    pat = re.compile(
        r"^(?:\s*\d+|AS)\s+([A-Z0-9]{2})\s+([A-Z0-9]+)\s+(\d{1,2}[A-Z]{3})\s+([A-Z]{3})([A-Z]{3})\s+([A-Z/]{2,6}\d?)\s+(\d{3,4}[AP])\s+(\d{3,4}[AP])",
        re.I | re.M
    )
    for car, flt, dt, o, d, stat, dep, arr in pat.findall(text or ""):
        segs.append({
            "car":car.upper(),"flt":flt.upper(),"date":dt.upper(),
            "orig":o.upper(),"dest":d.upper(),"stat":stat.upper(),
            "dep":dep.upper(),"arr":arr.upper()
        })
    return segs

SEG_ROW_24H = re.compile(
    r"^\s*\d+\s+([A-Z0-9]{2})\s+([A-Z0-9]+)\s+[A-Z]?\s+(\d{1,2}[A-Z]{3})\s+([A-Z]{3})\s*([A-Z]{3})\s+([A-Z]{2}\d?)\s+(\d{4}(?:\+1)?)\s+(\d{4}(?:\+1)?)",
    re.I | re.M
)
SEG_ROW_12H = re.compile(
    r"^\s*(?:\d+|0?\d)\s+([A-Z0-9]{2})\s+([A-Z0-9]+)\s+[A-Z]?\s+(\d{1,2}[A-Z]{3})\s+([A-Z]{3})\s*([A-Z]{3})\s+([A-Z/]{2,6}\d?)\s+(\d{3,4}[AP])\s+(\d{3,4}[AP])",
    re.I | re.M
)

def extract_generic_segments(text: str):
    segs = []
    idx = 0
    for r in SEG_ROW_24H.findall(text or ""):
        car, flt, dt, o, d, stat, dep, arr = [x.upper() for x in r]
        segs.append({"idx": idx, "car":car,"flt":flt,"date":dt,"orig":o,"dest":d,"stat":stat,"dep":dep,"arr":arr}); idx+=1
    for r in SEG_ROW_12H.findall(text or ""):
        car, flt, dt, o, d, stat, dep, arr = [x.upper() for x in r]
        segs.append({"idx": idx, "car":car,"flt":flt,"date":dt,"orig":o,"dest":d,"stat":stat,"dep":dep,"arr":arr}); idx+=1
    return segs

CXL = re.compile(r"^(HX|UN|UC|US|NO)\d*$", re.I)
OK  = re.compile(r"^(HK|OK|HS|TKOK)\d*$", re.I)

def extract_sabre_schedule_change(text: str):
    t = text or ""
    sc = re.search(
        r"^\s*SC\s+([A-Z0-9]{2})\s+([A-Z0-9]+)\s+(\d{1,2}[A-Z]{3})\s+([A-Z]{3})([A-Z]{3})\s+[A-Z/]{2,6}\d?\s+(\d{3,4}[AP])\s+(\d{3,4}[AP])",
        t, flags=re.I | re.M
    )
    if not sc: return None
    car, flt, dt, o, d, old_dep, old_arr = [g.upper() for g in sc.groups()]
    segs = extract_sabre_segments(t)
    new = next((s for s in segs if s["date"]==dt and s["orig"]==o and s["dest"]==d), None)
    if not new:
        return {"date":dt,"orig":o,"dest":d,"old_dep":old_dep,"old_arr":old_arr}

    old_dep_m = _time_token_to_minutes(old_dep)
    old_arr_m = _time_token_to_minutes(old_arr)
    new_dep_m = _time_token_to_minutes(new["dep"])
    new_arr_m = _time_token_to_minutes(new["arr"])
    dep_delta = (new_dep_m - old_dep_m) if (new_dep_m is not None and old_dep_m is not None) else None
    arr_delta = (new_arr_m - old_arr_m) if (new_arr_m is not None and old_arr_m is not None) else None
    if dep_delta is not None and dep_delta < -720: dep_delta += 1440
    if arr_delta is not None and arr_delta < -720: arr_delta += 1440

    return {
        "date": dt, "orig": o, "dest": d,
        "old_dep": old_dep, "old_arr": old_arr,
        "new_dep": new["dep"], "new_arr": new["arr"],
        "delta_dep_min": dep_delta, "delta_arr_min": arr_delta,
        "max_delta_min": max([x for x in (dep_delta, arr_delta) if x is not None], default=None),
        "method": "sabre_sc_line"
    }

def compute_schedule_change_from_segments(segs):
    best = None
    key_to_old = {}
    key_to_new = {}
    for s in segs:
        key = (s.get("date"), s.get("orig"), s.get("dest"))
        if not key[0] or not key[1] or not key[2]:
            continue
        if CXL.match(s.get("stat","")):
            key_to_old.setdefault(key, []).append(s)
        elif OK.match(s.get("stat","")):
            key_to_new.setdefault(key, []).append(s)

    for key in set(key_to_old) & set(key_to_new):
        for old in key_to_old[key]:
            for new in key_to_new[key]:
                od = _to_minutes_any(old["dep"]); nd = _to_minutes_any(new["dep"])
                oa = _to_minutes_any(old["arr"]); na = _to_minutes_any(new["arr"])
                if None in (od, nd, oa, na):
                    continue
                dep_delta = nd - od
                arr_delta = na - oa
                if dep_delta < -720: dep_delta += 1440
                if arr_delta < -720: arr_delta += 1440
                cand = {
                    "date": key[0], "orig": key[1], "dest": key[2],
                    "old_dep": old["dep"], "old_arr": old["arr"],
                    "new_dep": new["dep"], "new_arr": new["arr"],
                    "delta_dep_min": dep_delta, "delta_arr_min": arr_delta,
                    "max_delta_min": max(dep_delta, arr_delta),
                    "method": "pair_old_new_segments"
                }
                if not best or cand["max_delta_min"] > best["max_delta_min"]:
                    best = cand
    return best

def compute_layover_minutes(segments):
    if not segments or len(segments) < 2: return None
    a = _to_minutes_any(segments[0]["arr"])
    d = _to_minutes_any(segments[1]["dep"])
    if a is None or d is None: return None
    lay = d - a
    if lay < 0: lay += 1440
    return lay

# ---- Sign-off + Issue detection (cross-GDS) ----
RE_REASON_CODES = re.compile(r"\bRF-[A-Z0-9]{3,12}\b", re.I)
ISSUE_PATTERNS = [
    ("schedule_change",  re.compile(r"\b(SC(HD)?\s?CHG|SKCHG|SCHG|SCHEDULE\s*CHANGE|RETIMED?|RE-TIME)\b", re.I)),
    ("cancellation",     re.compile(r"\b(CXL|CANCEL(L|ED|ATION)?|FLT\s*CNL|NO-OP)\b", re.I)),
    ("delay",            re.compile(r"\b(DELAY|DLAY|RETARD)\b", re.I)),
    ("denied_boarding",  re.compile(r"\b(DENIED\s*BOARDING|OVERSALE|OVERSOLD|DB|INVOL\s*DNB)\b", re.I)),
    ("misconnect",       re.compile(r"\b(MISCONNECT|MISCONX|MISCNX)\b", re.I)),
    ("weather",          re.compile(r"\b(WEATHER|WX)\b", re.I)),
    ("maintenance",      re.compile(r"\b(MX|MAINT(ENANCE)?)\b", re.I)),
    ("atc",              re.compile(r"\b(ATC|AIR\s*TRAFFIC\s*CONTROL)\b", re.I)),
    ("security",         re.compile(r"\b(SECURITY|SEC)\b", re.I)),
    ("involuntary",      re.compile(r"\b(INVOL(UNTARY)?|IRROPS?|IROP)\b", re.I)),
]

def detect_airline_signoff(text: str, signatures: list[str]) -> bool:
    if signatures: return True
    t = (text or "").upper()
    if re.search(r"/DC[A-Z0-9]{2,3}\*[A-Z0-9]{4,12}/E", t): return True
    if re.search(r"\bWAIVER[:\s]+[A-Z0-9]{3,}\b", t): return True
    if re.search(r"\b(ENDORSEMENT|AUTH(?:ORIZATION| CODE)?|APPR(?:OVAL)?)\b", t): return True
    return False

def detect_issue_tokens(text: str) -> tuple[list[str], str | None]:
    tokens, best = [], None
    for label, rx in ISSUE_PATTERNS:
        if rx.search(text or ""):
            tokens.append(label)
            if best is None:
                best = label
    return sorted(set(tokens)), best

def extract_reason_codes(text: str) -> list[str]:
    return sorted(set(RE_REASON_CODES.findall(text or "")))

def parse_pnr_text(text: str) -> dict:
    gds = detect_gds(text)
    out = {
        "gds": gds,
        "pnr": extract_pnr_locator(text),
        "ticket_number": extract_ticket_number(text),
        "status_codes": extract_status_codes(text),
        "endorsement_signatures": extract_common_signatures(text),
        "issue_date_raw": None,
        "issue_date_iso": None,
        "time_since_issue_days": None,
        "time_until_expiration_days": None,
        "schedule_change": None,
        "layover_minutes": None,
        "segments": [],
        "airline_signed_off": False,
        "issue_tokens": [],
        "issue_label": None,
        "reason_codes": [],
    }

    issued_dt, issued_raw = extract_ticket_issue_date(text)
    if issued_dt:
        out["issue_date_raw"] = issued_raw
        out["issue_date_iso"] = issued_dt.strftime("%Y-%m-%d")
        out["time_since_issue_days"] = (datetime.now() - issued_dt).days
        exp_days = (issued_dt.replace(year=issued_dt.year+1) - datetime.now()).days
        out["time_until_expiration_days"] = max(exp_days, 0)

    if gds == "sabre":
        segs = extract_sabre_segments(text)
        sc = extract_sabre_schedule_change(text) or compute_schedule_change_from_segments(segs)
    else:
        segs = extract_generic_segments(text)
        sc = compute_schedule_change_from_segments(segs)

    out["segments"] = segs
    out["layover_minutes"] = compute_layover_minutes(segs)
    out["schedule_change"] = sc
    out["inv_ref_eligibility_minutes"] = (sc or {}).get("max_delta_min")
    out["inv_ref_eligibility_3h"] = (out["inv_ref_eligibility_minutes"] is not None
                                     and out["inv_ref_eligibility_minutes"] >= 180)

    out["airline_signed_off"] = detect_airline_signoff(text, out["endorsement_signatures"])
    toks, best = detect_issue_tokens(text)
    out["issue_tokens"], out["issue_label"] = toks, best
    out["reason_codes"] = extract_reason_codes(text)
    return out

# ======= quiet OCR helper =======
def ocr_image_bytes(file_bytes: bytes):
    """Return (text, status) where status in {'ok','tesseract_missing','error'}."""
    try:
        if not shutil.which("tesseract"):
            return ("", "tesseract_missing")
        img = Image.open(io.BytesIO(file_bytes)).convert("L")
        txt = pytesseract.image_to_string(img)
        return (txt or "", "ok")
    except Exception:
        return ("", "error")

# =========================
# Storage + Contacts + Email wrappers
# =========================
# resilient import for apg_storage
try:
    from streamlit_cloud_apps.apg_storage import storage
except ModuleNotFoundError:
    import os as _os, sys as _sys
    _sys.path.append(_os.path.dirname(__file__))  # allow sibling import
    from apg_storage import storage

_AIRLINE_CONTACTS = load_json(CONTACTS_FILE, default={}) or {}

def get_airline_contacts(plating_code: str) -> dict:
    rec = _AIRLINE_CONTACTS.get(str(plating_code)) or {}
    to_list = rec.get("to") or []
    cc_list = rec.get("cc") or []
    return {"to": to_list, "cc": cc_list}

# ---- email sending wrappers ----

def _send_email_via_api(to: list[str] | str, subject: str, text: str, html: str | None = None,
                        attachments: list[dict] | None = None, thread_id: str | None = None,
                        sender: str | None = None) -> dict:
    """Thin wrapper around your gmail_api. Always logs to storage as well."""
    to_list = to if isinstance(to, list) else [to]
    payload = {
        "from": sender or INSIDE_SALES_FROM,
        "to": to_list,
        "subject": subject,
        "text": text,
        "html": html,
        "attachments": attachments or [],
        "thread_id": thread_id,
    }
    resp = {"sent": False}
    if _gmail_send:
        try:
            r = _gmail_send(
                to=to_list, subject=subject, text=text, html=html,
                attachments=attachments, thread_id=thread_id, sender=sender or INSIDE_SALES_FROM
            )
            resp = {"sent": True, **(r or {})}
        except Exception as e:
            resp = {"sent": False, "error": str(e)}
    log_key = _log_sent_email(payload.get("thread_id") or subject, {**payload, **resp})
    resp["log_key"] = log_key
    return resp

# =========================
# Top-level selection (Dropdown)
# =========================
st.markdown("<h3 style='text-align: center;'>How can we help you?</h3>", unsafe_allow_html=True)

PLACEHOLDER = "— Select a request type —"
_core = ["Airline Policies", "General Inquiries", "Groups", "Refunds / Reissues"]
options = [PLACEHOLDER] + sorted(_core, key=str.casefold)

choice = st.selectbox(
    "Select a request type",
    options,
    index=0,
    key="support_type_select",
    label_visibility="collapsed",
)
if choice == PLACEHOLDER:
    st.stop()
support_type = choice

# ======= bottom bar helper (shown after any submission) =======
def render_additional_bar(suffix: str):
    st.markdown("<hr style='margin-top:1.25rem;margin-bottom:0.75rem;'>", unsafe_allow_html=True)
    st.markdown("<div style='text-align:center; font-weight:600;'>Additional Support Requests?</div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,1,1])
    with col2:
        if st.button("➕ Start another request", key=f"new_req_{suffix}"):
            st.session_state["support_type_select"] = PLACEHOLDER
            st.rerun()

# ======= small sanitizers =======
clean_digits = lambda s: re.sub(r"\D", "", (s or "").strip())
trim_nospace = lambda s: re.sub(r"\s+", "", (s or "").strip())

# =========================
# 1) REFUNDS / REISSUES
# =========================
if support_type == "Refunds / Reissues":
    st.markdown("<h2 style='text-align: center;'>🛫 Refund / Reissue Submission</h2>", unsafe_allow_html=True)
    with st.form("refund_form", clear_on_submit=False):
        ticket_number_in = st.text_input("🎫 Airline Ticket Number")
        service_type_label = st.selectbox("🛠️ Service Request Type", SERVICE_TYPES)
        service_request_type = SERVICE_TYPE_KEYS[service_type_label]
        airline_record_locator_in = st.text_input("📄 Airline Record Locator Number")
        agency_id_in = st.text_input("🏢 Agency ID (ARC Number)")
        agency_name = st.text_input("🏷️ Agency Name")
        full_pnr = st.file_uploader("📎 Full PNR Screenshot (required for refund/reissue)", type=["png", "jpg", "jpeg", "pdf"])
        email_in = st.text_input("📧 Email Address")
        comments = st.text_area("💬 Comments (optional)")
        submitted = st.form_submit_button("🚀 Submit")

    if submitted:
        # ------- sanitize inputs (trailing/inner spaces, case) -------
        ticket_number = clean_digits(ticket_number_in)
        airline_record_locator = trim_nospace(airline_record_locator_in).upper()
        agency_id = trim_nospace(agency_id_in)
        email = (email_in or "").strip().lower()

        # ------- Basic field checks -------
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

        # Attachment + OCR + parsing (quiet)
        waiver_present = False
        attachment_key = None
        attachment_url = None
        attachment_mime = None
        parsed_meta = {}
        ocr_status = None

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
                    ocr_text, ocr_status = ocr_image_bytes(file_bytes)
                    if ocr_status == "ok":
                        parsed_meta = parse_pnr_text(ocr_text)
                        waiver_present = bool(parsed_meta.get("endorsement_signatures"))
                elif full_pnr.type == "application/pdf":
                    ocr_status = "skipped_pdf"
            except Exception:
                ocr_status = "save_or_ocr_error"
                parsed_meta = {}

        # PUBLIC-FACING OUTPUT (no raw metadata)
        st.subheader("📌 Service Case ID")
        st.code(service_case_id)

        st.subheader("📋 Airline Policy")
        policy_text = (data.get("policies", {}) or {}).get(service_request_type, "No policy information found.")
        st.markdown(f"<div class='wrap-policy'><pre>{_html_escape(policy_text)}</pre></div>", unsafe_allow_html=True)

        # ---- Waiver / Manual approval block ----
        st.subheader("🔖 Applicable Waiver Codes")
        manual_required = plating_code in MANUAL_APPROVAL_CODES
        endo_codes = (data.get("endorsement_codes", {}) or {}).get(f"{service_request_type}_code", [])
        inv_ref_3h = bool(parsed_meta.get("inv_ref_eligibility_3h")) if parsed_meta else False
        airline_signed_off = bool(parsed_meta.get("airline_signed_off")) if parsed_meta else False
        justification_detected = airline_signed_off or inv_ref_3h or ("involuntary" in (parsed_meta.get("issue_tokens") or []))

        waiver_ui_text = None
        if manual_required:
            waiver_ui_text = "**Manual approval required by airline HQ** — your request has been forwarded. No waiver code will be provided at this stage."
            st.markdown(waiver_ui_text)
        elif justification_detected and endo_codes:
            waiver_ui_text = f"`{', '.join(endo_codes)}`"
            st.markdown(waiver_ui_text)
        elif justification_detected and not endo_codes:
            waiver_ui_text = "Triggers detected for manual approval. No pre-filled waiver code on file."
            st.markdown(waiver_ui_text)
        else:
            waiver_ui_text = "Waiver Code is not required."
            st.markdown(waiver_ui_text)

        st.subheader("⚠️ Disclaimer & Exclusions")
        st.markdown("Please review fare rules to avoid any ADMs ")
        st.markdown(f"**Agency Eligibility Exclusions:** `{', '.join(excluded) if excluded else 'None on file'}`")

        # Persist submission (with full metadata)
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
            "waiver_ui_text": waiver_ui_text,
            "manual_approval_required": manual_required,
            "attachment_mime": attachment_mime,
            "attachment_key": attachment_key,
            "attachment_url": attachment_url,
            "ocr_status": ocr_status,
            "pnr_metadata": parsed_meta or {}
        }
        log_key = f"submissions/{service_case_id}.json"
        log_entry["storage_key"] = log_key
        storage.write_json(log_key, log_entry)

        _index_add("submissions", {
            "key": log_key,
            "service_case_id": service_case_id,
            "route": "refund_reissue",
            "agency_name": agency_name,
            "email": email,
            "plating_code": plating_code,
            "service_request_type": service_request_type,
            "submitted_at_iso": submitted_at_iso,
            "attachment_key": attachment_key,
            "attachment_url": attachment_url,
        })

        # ========= EMAIL ACTIONS =========
        # Helper to build a compact text summary
        def _case_text_summary():
            lines = [
                f"Service Case ID: {service_case_id}",
                f"Carrier Code: {plating_code} ({MANUAL_APPROVAL_CODES.get(plating_code, '')})",
                f"Service Type: {service_type_label} [{service_request_type}]",
                f"Agency: {agency_name} (ARC {agency_id})",
                f"Agent Email: {email}",
                f"Ticket: {ticket_number}",
                f"Record Locator: {airline_record_locator}",
            ]
            if attachment_url:
                lines.append(f"PNR Screenshot: {attachment_url}")
            if parsed_meta:
                sc = parsed_meta.get("schedule_change") or {}
                if sc:
                    lines.append(
                        "Schedule Change: " +
                        f"{sc.get('orig','')}→{sc.get('dest','')} on {sc.get('date','')} | "
                        f"Δdep={_minutes_to_hm(sc.get('delta_dep_min'))}, Δarr={_minutes_to_hm(sc.get('delta_arr_min'))}"
                    )
                if parsed_meta.get("endorsement_signatures"):
                    lines.append("Detected Signatures: " + ", ".join(parsed_meta.get("endorsement_signatures")))
                if parsed_meta.get("issue_tokens"):
                    lines.append("Issue Tokens: " + ", ".join(parsed_meta.get("issue_tokens")))
            lines.append("")
            lines.append("Policy Excerpt:\n" + textwrap.shorten(policy_text.replace("\n", " "), width=800, placeholder=" …"))
            if waiver_ui_text:
                lines.append("")
                lines.append("Waiver / Approval Status: " + re.sub(r"<.*?>", "", waiver_ui_text))
            return "\n".join(lines)

        # 1) Airline Manual Approval email (for 141/188/239)
        if manual_required:
            contacts = get_airline_contacts(plating_code)
            airline_to = contacts.get("to") or [INSIDE_SALES_FROM]  # safe fallback
            airline_cc = contacts.get("cc") or []
            subj_airline = f"[APG Manual Approval] {service_case_id} — {plating_code} {service_type_label} for {agency_name}"
            body_text = _case_text_summary()
            attachments = []
            if attachment_key and attachment_mime:
                try:
                    # If your gmail_api supports raw-bytes attachments
                    file_bytes = storage.read_bytes(attachment_key)
                    attachments = [{"filename": os.path.basename(attachment_key), "mime": attachment_mime, "bytes": file_bytes}]
                except Exception:
                    attachments = []
            _send_email_via_api(
                to=airline_to + airline_cc,
                subject=subj_airline,
                text=body_text,
                html=None,
                attachments=attachments,
                thread_id=service_case_id,
                sender=INSIDE_SALES_FROM,
            )

        # 2) Agent confirmation email (always)
        subj_agent = f"[APG Confirmation] {service_case_id} — {plating_code} {service_type_label}"
        _send_email_via_api(
            to=email,
            subject=subj_agent,
            text=_case_text_summary(),
            html=None,
            attachments=None,
            thread_id=service_case_id,
            sender=INSIDE_SALES_FROM,
        )

        # ========= INLINE EMAIL TOOLS (compose + thread) =========
        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("✉️ Email Tools")
        with st.form("compose_agent_email"):
            default_subject = f"[APG] {service_case_id} — Update"
            c_subject = st.text_input("Subject", value=default_subject)
            c_body = st.text_area("Message", value="Hi there,\n\nFollowing up on your case above.\n\n— APG Inside Sales")
            send_click = st.form_submit_button("Send Email to Agent")
        if send_click:
            resp = _send_email_via_api(
                to=email,
                subject=c_subject,
                text=c_body,
                html=None,
                attachments=None,
                thread_id=service_case_id,
                sender=INSIDE_SALES_FROM,
            )
            if resp.get("sent"):
                st.success("✅ Email sent to agent and logged.")
            else:
                st.warning("Email logged locally, but sending via API may have failed.")

        if manual_required:
            with st.form("compose_airline_email"):
                default_subject2 = f"[APG Manual Approval] {service_case_id} — {plating_code} {service_type_label}"
                c2_subject = st.text_input("Subject (Airline)", value=default_subject2)
                c2_body = st.text_area("Message (Airline)", value=_case_text_summary())
                send_click2 = st.form_submit_button("Resend Manual Approval to Airline")
            if send_click2:
                contacts = get_airline_contacts(plating_code)
                airline_to = contacts.get("to") or [INSIDE_SALES_FROM]
                airline_cc = contacts.get("cc") or []
                _resp2 = _send_email_via_api(
                    to=airline_to + airline_cc,
                    subject=c2_subject,
                    text=c2_body,
                    html=None,
                    attachments=None,
                    thread_id=service_case_id,
                    sender=INSIDE_SALES_FROM,
                )
                if _resp2.get("sent"):
                    st.success("✅ Manual approval email sent to airline and logged.")
                else:
                    st.warning("Airline email logged locally, but sending via API may have failed.")

        # Thread viewer / refresh
        st.markdown("<h4>📬 Email Thread</h4>", unsafe_allow_html=True)
        if st.button("🔄 Refresh thread", key="refresh_thread"):
            # If your gmail API supports searching by subject or custom header
            msgs = []
            if _gmail_search:
                try:
                    msgs = _gmail_search(subject=service_case_id) or []
                except Exception:
                    msgs = []
            # Persist a snapshot for quick viewing
            snap_key = f"emails/{service_case_id}/thread_snapshot.json"
            storage.write_json(snap_key, {"refreshed_at": datetime.now(timezone.utc).isoformat(), "messages": msgs})
            _email_index_add(service_case_id, {"key": snap_key})
            st.experimental_rerun()

        # Display last 10 logged emails / snapshot
        try:
            idx = storage.read_json(f"emails/{service_case_id}/_index.json")
            if isinstance(idx, list):
                idx = [x for x in idx if x.endswith(".json")]
                idx = sorted(idx)[-10:]
                for k in idx:
                    try:
                        item = storage.read_json(k)
                        subject = item.get("subject") or k.split("/")[-1]
                        to_list = item.get("to")
                        st.markdown(f"**{subject}**")
                        st.caption(f"To: {', '.join(to_list) if isinstance(to_list, list) else to_list}")
                        snippet = (item.get("text") or "").strip().splitlines()
                        st.write("\n".join(snippet[:6]) + ("\n…" if len(snippet) > 6 else ""))
                        st.markdown("<hr>", unsafe_allow_html=True)
                    except Exception:
                        continue
        except Exception:
            pass

        # Optional tiny debug hint (toggle with APG_DEBUG_UI=1)
        if os.environ.get("APG_DEBUG_UI") == "1":
            st.caption(f"Debug: OCR={ocr_status}; signed_off={airline_signed_off}; 3h_elig={inv_ref_3h}; manual={manual_required}")

        render_additional_bar("refund")

# =========================
# 2) GENERAL INQUIRIES
# =========================
elif support_type == "General Inquiries":
    st.markdown("<h2 style='text-align: center;'>📨 General Inquiry</h2>", unsafe_allow_html=True)
    with st.form("general_form"):
        agency_name = st.text_input("🏷️ Agency Name")
        agency_id_in = st.text_input("🏢 Agency ID (ARC Number)")
        email_in = st.text_input("📧 Email Address")
        comment = st.text_area("💬 Comment")
        submitted = st.form_submit_button("Submit Inquiry")

    if submitted:
        agency_id = trim_nospace(agency_id_in)
        email = (email_in or "").strip().lower()
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

        _index_add("submissions", {
            "key": gi_key,
            "service_case_id": gi_case_id,
            "route": "general_inquiry",
            "agency_name": agency_name,
            "email": email,
            "submitted_at_iso": gi_time_iso,
        })

        # Confirmation email
        _send_email_via_api(
            to=email,
            subject=f"[APG Confirmation] {gi_case_id} — General Inquiry",
            text=f"We received your inquiry. Case: {gi_case_id}\n\n{comment}",
            html=None,
            attachments=None,
            thread_id=gi_case_id,
            sender=INSIDE_SALES_FROM,
        )

        st.success("✅ Inquiry submitted. Our team will contact you shortly.")
        render_additional_bar("general")

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
        options = sorted([(name, code) for code, name in airlines_map.items()], key=lambda x: x[0].lower())
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

        _index_add("submissions", {
            "key": pol_key,
            "service_case_id": pol_case_id,
            "route": "airline_policy_lookup",
            "agency_name": agency_name,
            "airline": airline_name,
            "plating_code": code,
            "service_request_type": key,
            "submitted_at_iso": pol_time_iso,
        })

        render_additional_bar("policies")

# =========================
# 4) GROUPS (same as General Inquiries)
# =========================
elif support_type == "Groups":
    st.markdown("<h2 style='text-align: center;'>👥 Groups Inquiry</h2>", unsafe_allow_html=True)
    with st.form("groups_form"):
        agency_name = st.text_input("🏷️ Agency Name")
        agency_id_in = st.text_input("🏢 Agency ID (ARC Number)")
        email_in = st.text_input("📧 Email Address")
        comment = st.text_area("💬 Group Request / Notes")
        submitted = st.form_submit_button("Submit Groups Request")

    if submitted:
        agency_id = trim_nospace(agency_id_in)
        email = (email_in or "").strip().lower()
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

        _index_add("submissions", {
            "key": grp_key,
            "service_case_id": grp_case_id,
            "route": "groups",
            "agency_name": agency_name,
            "email": email,
            "submitted_at_iso": grp_time_iso,
        })

        # Confirmation email
        _send_email_via_api(
            to=email,
            subject=f"[APG Confirmation] {grp_case_id} — Groups",
            text=f"We received your groups request. Case: {grp_case_id}\n\n{comment}",
            html=None,
            attachments=None,
            thread_id=grp_case_id,
            sender=INSIDE_SALES_FROM,
        )

        st.success("✅ Groups request submitted. Our team will contact you shortly.")
        render_additional_bar("groups")
