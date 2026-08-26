"""
Builds all DataFrames from snapshots and pushes them to Google Sheets.
"""

import re
import json
import unicodedata
from pathlib import Path

import pandas as pd

from tracker.config import get_agent

_AGENT = get_agent()
_NPN = _AGENT["npn"]
_FN = _AGENT["first_name"].lower()
_LN = _AGENT["last_name"].lower()

from tracker.diff import build_all_clients, compute_diff, assign_loss_months
from tracker.ingest import load_all_snapshots
from tracker.sheets import update_sheet


def _excl_name_key(first, last) -> str:
    s = f"{first} {last}".lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


def _load_exclusions() -> list:
    """Clients to drop from everything (e.g. HealthSherpa rows the agent never
    actually sold, confirmed absent from CRM). See data/excluded_clients.json."""
    p = Path(__file__).parent.parent / "data" / "excluded_clients.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _filter_excluded(df: pd.DataFrame, exclusions: list) -> pd.DataFrame:
    """Remove excluded clients. Entries WITH an FFM App ID match by ID only
    (precise — avoids nuking a different person who shares a common name).
    Entries WITHOUT an App ID fall back to name+state."""
    if not exclusions or df.empty:
        return df
    _digits = lambda x: re.sub(r"[^0-9]", "", str(x))
    app_ids = {_digits(e["ffm_app_id"]) for e in exclusions if e.get("ffm_app_id")} - {""}
    name_states = {(_excl_name_key(e["first"], e["last"]), str(e["state"]).upper())
                   for e in exclusions if not e.get("ffm_app_id")}

    def _keep(row) -> bool:
        aid = _digits(row.get("ffm_app_id"))
        if aid and aid in app_ids:
            return False
        key = (_excl_name_key(row.get("first_name", ""), row.get("last_name", "")),
               str(row.get("state") or "").upper())
        return key not in name_states

    return df[df.apply(_keep, axis=1)].copy()

_ALL_CLIENTS_COLS = ["first_name", "last_name", "carrier", "effective_date", "term_date",
                     "status", "state", "ffm_app_id", "net_premium", "applicant_count",
                     "household_size", "subsidy", "months_on_book",
                     "client_since", "cancel_reason", "term_estimated", "phone", "email",
                     "policy_number", "loss_basis"]

_ACTIVE_COLS = ["first_name", "last_name", "carrier", "effective_date",
                "status", "state", "ffm_app_id", "net_premium", "applicant_count", "months_on_book"]

_STATUS_ORDER = ["Effectuated", "PendingEffectuation", "PendingFollowups", "Cancelled", "Terminated"]


def _select(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    return df[[c for c in cols if c in df.columns]]


def _sort_by_date(df: pd.DataFrame) -> pd.DataFrame:
    """Sort ascending by effective_date (oldest first), NaT pushed to end."""
    if df.empty or "effective_date" not in df.columns:
        return df
    return df.sort_values("effective_date", ascending=True, na_position="last").reset_index(drop=True)


def _sort_by_term_date_desc(df: pd.DataFrame) -> pd.DataFrame:
    """Sort descending by term_date (most recent cancellations first), NaT pushed to end."""
    if df.empty or "term_date" not in df.columns:
        return df
    return df.sort_values("term_date", ascending=False, na_position="last").reset_index(drop=True)


def _sort(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "status" in df.columns:
        order_map = {s: i for i, s in enumerate(_STATUS_ORDER)}
        df = df.copy()
        df["_status_rank"] = df["status"].map(order_map).fillna(len(_STATUS_ORDER))
        sort_cols = ["_status_rank"] + (["last_name"] if "last_name" in df.columns else [])
        df = df.sort_values(sort_cols, key=lambda s: s.str.lower() if s.dtype == object else s)
        df = df.drop(columns=["_status_rank"]).reset_index(drop=True)
    return df


def _load_appointments() -> dict:
    """Load state→carrier appointments from config/appointments.yaml."""
    import yaml
    appt_path = Path(__file__).parent.parent / "config" / "appointments.yaml"
    if not appt_path.exists():
        return {}
    try:
        with open(appt_path) as f:
            data = yaml.safe_load(f)
        return data.get("appointments", {})
    except Exception:
        return {}


def _filter_by_appointments(df: pd.DataFrame, appointments: dict) -> pd.DataFrame:
    """Remove rows whose carrier is not in the agent's appointments for their state."""
    if not appointments or df.empty:
        return df
    if "state" not in df.columns or "carrier" not in df.columns:
        return df
    def _is_appointed(row):
        state   = str(row.get("state", "")).strip().upper()
        carrier = str(row.get("carrier", "")).strip().lower()
        if not state or not carrier:
            return True
        keywords = appointments.get(state, [])
        if not keywords:
            return False  # state not in appointments — exclude
        return any(kw.lower() in carrier for kw in keywords)
    return df[df.apply(_is_appointed, axis=1)].copy()


def _build_last_paid(settings: dict) -> dict:
    """Money-backed 'last month paid per client' lookups for loss-dating. Returns
    {"by_policy": {policy: 'YYYY-MM'}, "by_name": {name_key: 'YYYY-MM'},
     "by_carrier_names": {brand: {name_key: 'YYYY-MM'}}} — so a gone client can be matched
    to the month his commission stopped by policy ID (name-independent), then exact name,
    then fuzzy name within the same carrier. Merges persisted 2025 statement records
    (data/commission_2025.json) with the live 2026 Insurance PAYMENTS sheet. Best-effort."""
    import json as _json

    def _key(m):
        x = re.sub(r"\b(family|household)\b", "", str(m), flags=re.I).strip()
        if "," in x:
            last, rest = x.split(",", 1); p = rest.split()
            return re.sub(r"[^a-z]", "", ((p[0] if p else "") + last).lower())
        p = x.split()
        return re.sub(r"[^a-z]", "", ((p[0] + p[-1]) if len(p) >= 2 else x).lower())

    def _polnorm(x):
        v = re.sub(r"[^0-9a-z]", "", str(x).lower())
        return v if len(v) >= 5 else ""

    def _brand(c):
        c = str(c).lower()
        for kw, b in (("ambetter", "ambetter"), ("oscar", "oscar"), ("wellpoint", "anthem"),
                      ("anthem", "anthem"), ("unitedhealth", "uhc"), ("united health", "uhc"),
                      ("uhc", "uhc"), ("cigna", "cigna"), ("molina", "molina"),
                      ("selecthealth", "selecthealth"), ("select health", "selecthealth"),
                      ("blue", "bcbs"), ("bcbs", "bcbs")):
            if kw in c:
                return b
        return re.sub(r"[^a-z]", "", c)[:10] or "other"

    by_policy, by_name, by_carrier = {}, {}, {}

    def _add(nk, pol, carrier, month):
        if not month:
            return
        if nk:
            by_name[nk] = max(by_name.get(nk, ""), month)
            d = by_carrier.setdefault(_brand(carrier), {})
            d[nk] = max(d.get(nk, ""), month)
        pn = _polnorm(pol)
        if pn:
            by_policy[pn] = max(by_policy.get(pn, ""), month)

    # 2025 statements (persisted: {"records": [[name_key, policy_norm, carrier, 'YYYY-MM'], ...]})
    try:
        p25 = Path(__file__).resolve().parent.parent / "data" / "commission_2025.json"
        if p25.exists():
            data = _json.load(open(p25))
            for r in (data.get("records") if isinstance(data, dict) else []):
                if len(r) >= 4:
                    _add(str(r[0]), str(r[1]), str(r[2]), str(r[3]))
    except Exception as e:
        print(f"  (2025 commission history skipped: {type(e).__name__})")

    # 2026 live payments sheet
    try:
        url = settings.get("payments_sheet_url"); imp = settings.get("impersonation_target", "")
        if url:
            from tracker.commissions import parse_payments_sheet
            from tracker.sheets import _open_sheet
            pdf = parse_payments_sheet(_open_sheet(url, imp))
            if pdf is not None and not pdf.empty:
                for _, row in pdf.iterrows():
                    m = pd.to_datetime(row.get("payment_month"), errors="coerce")
                    _add(_key(row.get("member")), row.get("policy_id"),
                         row.get("carrier"), m.strftime("%Y-%m") if pd.notna(m) else "")
    except Exception as e:
        print(f"  (2026 payments money-dating skipped: {type(e).__name__}: {e})")

    return {"by_policy": by_policy, "by_name": by_name, "by_carrier_names": by_carrier}


def _build_supplemental_display(supp: pd.DataFrame) -> pd.DataFrame:
    """Format the normalized supplemental roster for the Supplemental sheet tab:
    friendly headers, active policies first, premium rounded. Commission is
    omitted until the agent provides per-carrier comp rates."""
    if supp is None or supp.empty:
        return pd.DataFrame()
    df = supp.copy()
    df["_active_rank"] = (df["status"] == "Active").map({True: 0, False: 1})
    df = df.sort_values(["_active_rank", "carrier", "last_name", "first_name"],
                        key=lambda s: s.str.lower() if s.dtype == object else s)
    out = pd.DataFrame({
        "First Name":      df["first_name"],
        "Last Name":       df["last_name"],
        "Carrier":         df["carrier"],
        "Policy Number":   df.get("policy_number", ""),
        "Product":         df["product"],
        "Monthly Premium": df["premium"].round(2),
        "Status":          df["status"],
        "Status Detail":   df["status_detail"],
        "Term Date":       pd.to_datetime(df.get("term_date"), errors="coerce"),
        "State":           df["state"],
        "Email":           df["email"],
        "Phone":           df["phone"],
    })
    return out.reset_index(drop=True)


def _build_pastdue_display(pastdue: pd.DataFrame) -> pd.DataFrame:
    """Format the health past-due roster for its sheet tab: friendly headers,
    most overdue first."""
    if pastdue is None or pastdue.empty:
        return pd.DataFrame()
    df = pastdue.copy()
    df["_overdue"] = pd.to_numeric(df.get("days_overdue"), errors="coerce")
    df = df.sort_values(["_overdue", "carrier"], ascending=[False, True], na_position="last")
    _members = pd.to_numeric(df.get("members"), errors="coerce").fillna(1).astype(int)
    out = pd.DataFrame({
        "First Name":   df["first_name"],
        "Last Name":    df["last_name"],
        "Carrier":      df["carrier"],
        "State":        df["state"],
        "Status":       df.get("status"),
        "Members":      _members,
        "Premium":      pd.to_numeric(df["premium"], errors="coerce").round(2),
        "Paid Through": pd.to_datetime(df.get("paid_through"), errors="coerce"),
        "Balance":      pd.to_numeric(df.get("balance"), errors="coerce").round(2),
        "Days Overdue": pd.to_numeric(df.get("days_overdue"), errors="coerce"),
        "Reason":       df["reason"],
        "Phone":        df["phone"],
        "Email":        df["email"],
    })
    return out.reset_index(drop=True)


def _load_followup_due_dates(books_dir: Path = None) -> dict:
    """Map FFM app id (and lowercased name) -> soonest OPEN verification due date,
    read from the HealthSherpa DMI/SVI follow-up exports in followup_books/.
    Only OPEN items (action_needed / insufficient_documentation / processing) carry
    a real due date; completed/expired rows are blank. Returns {"ffm":{}, "name":{}}."""
    base = Path(books_dir) if books_dir else (Path(__file__).resolve().parent.parent / "followup_books")
    by_ffm, by_name = {}, {}
    if not base.exists():
        return {"ffm": by_ffm, "name": by_name}
    OPEN = {"action_needed", "insufficient_documentation", "processing"}
    for fn in ("dmi.csv", "svi.csv"):
        p = base / fn
        if not p.exists():
            continue
        try:
            d = pd.read_csv(p, dtype=str)
        except Exception:
            continue
        cols = {c.strip().lower(): c for c in d.columns}
        status_col = next((cols[k] for k in cols if k.endswith("status")), None)
        due_col    = cols.get("due date")
        ffm_col    = next((cols[k] for k in cols if "ffm" in k), None)
        name_col   = cols.get("client name")
        if not status_col or not due_col:
            continue
        for _, r in d.iterrows():
            if str(r.get(status_col) or "").strip().lower() not in OPEN:
                continue
            due = pd.to_datetime(r.get(due_col), errors="coerce")
            if pd.isna(due):
                continue
            ffm = re.sub(r"\.0$", "", str(r.get(ffm_col) or "").strip()) if ffm_col else ""
            nm  = str(r.get(name_col) or "").strip().lower() if name_col else ""
            if ffm and (ffm not in by_ffm or due < by_ffm[ffm]):
                by_ffm[ffm] = due
            if nm and (nm not in by_name or due < by_name[nm]):
                by_name[nm] = due
    return {"ffm": by_ffm, "name": by_name}


def _build_follow_ups(all_clients: pd.DataFrame) -> pd.DataFrame:
    """HealthSherpa verification follow-ups (DMI = income/coverage match, SVI =
    enrollment verification). 'Expired' = subsidy lost (lost client, for outreach);
    'Open' = still actionable — reach out before it expires. For the Follow-ups tab."""
    if all_clients is None or all_clients.empty:
        return pd.DataFrame()
    df = all_clients.copy()
    # Follow-ups are CURRENT clients only — Ethan must be the agent of record right
    # now. Drop anyone whose AOR moved to another agent or is unassigned (they're no
    # longer his client, even if he originally enrolled them).
    if "policy_aor" in df.columns:
        _aor = df["policy_aor"].fillna("").astype(str)
        _mine = _aor.str.contains(_NPN) | (
            _aor.str.contains(_FN, case=False) & _aor.str.contains(_LN, case=False))
        df = df[_mine].copy()
    for c in ("dmi_outstanding", "dmi_expired", "svi_outstanding", "svi_expired"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0) if c in df.columns else 0
    exp = (df["dmi_expired"] > 0) | (df["svi_expired"] > 0)
    opn = ((df["dmi_outstanding"] > 0) | (df["svi_outstanding"] > 0)) & ~exp
    sub = df[exp | opn].copy()
    if sub.empty:
        return pd.DataFrame()

    def _type(r):
        t = []
        if r["dmi_outstanding"] or r["dmi_expired"]:
            t.append("Income/coverage (DMI)")
        if r["svi_outstanding"] or r["svi_expired"]:
            t.append("Enrollment (SVI)")
        return ", ".join(t)

    is_exp = (sub["dmi_expired"] > 0) | (sub["svi_expired"] > 0)
    out = pd.DataFrame({
        "First Name":   sub["first_name"],
        "Last Name":    sub["last_name"],
        "Carrier":      sub.get("carrier"),
        "State":        sub.get("state"),
        "Follow-up":    sub.apply(_type, axis=1),
        "Status":       ["Expired" if e else "Open" for e in is_exp],
        "Detail":       sub.get("followup_docs", "").astype(str).str.replace("_", " ").str.title(),
        "Phone":        sub.get("phone"),
        "Email":        sub.get("email"),
    })

    # Attach the verification due date (from the DMI/SVI exports) — matched by FFM
    # app id, name as fallback. Open items have one; expired/blank stay empty.
    dd = _load_followup_due_dates()
    def _due(r):
        ffm = re.sub(r"\.0$", "", str(r.get("ffm_app_id") or "").strip())
        d = dd["ffm"].get(ffm) if ffm else None
        if d is None:
            d = dd["name"].get(f"{r.get('first_name','')} {r.get('last_name','')}".strip().lower())
        return d
    _due_ts = pd.to_datetime(sub.apply(_due, axis=1), errors="coerce")
    out["Due Date"] = _due_ts.dt.strftime("%Y-%m-%d").where(_due_ts.notna(), "")

    # Sort by due date: soonest (most urgent) first, undated (expired) last.
    out["_due_sort"] = _due_ts
    return out.sort_values("_due_sort", ascending=True, na_position="last").drop(columns="_due_sort").reset_index(drop=True)


def _alert_new_lapses(all_clients: pd.DataFrame) -> None:
    """Text the agent the moment a client newly drops into Re-Engage (Cancelled/
    Terminated). Diffs the current lapsed set against the last run's saved set so
    each person is alerted exactly once. First run just initializes (no blast)."""
    import json
    import re
    import unicodedata

    if all_clients is None or all_clients.empty or "status" not in all_clients.columns:
        return

    def _key(f, l):
        s = unicodedata.normalize("NFKD", f"{f} {l}").encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z]", "", s)

    _data = Path(__file__).resolve().parent.parent / "data"
    churn = all_clients[all_clients["status"].isin(["Cancelled", "Terminated"])]
    cur = {}
    for _, r in churn.iterrows():
        k = _key(r.get("first_name", ""), r.get("last_name", ""))
        if k:
            cur[k] = f"{r.get('first_name','')} {r.get('last_name','')}".strip().title()

    path = _data / "known_lapsed.json"
    first_run = not path.exists()
    prev = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text())
        except Exception:
            prev = {}

    new_keys = [k for k in cur if k not in prev]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, indent=2))

    if first_run:
        print(f"  Lapse alerts: initialized ({len(cur)} already lapsed — no text sent).")
        return
    if not new_keys:
        return

    names = [cur[k] for k in new_keys]
    print(f"  Lapse alerts: {len(names)} newly dropped → texting")

    cfg = _data / "alert_config.json"
    phone = None
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text())
            phone = c.get("phone") if c.get("lapse_alerts", True) else None
        except Exception:
            phone = None
    if not phone:
        print("  (no alert phone configured — skipping text)")
        return

    shown = names[:12]
    more = f"\n…and {len(names) - 12} more" if len(names) > 12 else ""
    msg = ("🔔 Dropped off your book (now in Re-Engage):\n• "
           + "\n• ".join(shown) + more + "\nReach out to win them back.")
    try:
        from tracker.digest import send_imessage
        send_imessage(msg, phone)
        print(f"  Lapse alert texted to {phone}")
    except Exception as e:
        print(f"  (lapse text failed: {e})")


def _upload_summary(all_clients, pastdue, snapshot_dir, today=None) -> None:
    """After a new HealthSherpa upload, text the agent a summary: new policies/
    members signed, and clients newly fallen off since the last upload split into
    Cancelled (→ Re-Engage), Behind on payment, and Taken by another agent.
    Only fires when the HealthSherpa snapshot actually changed (a real upload)."""
    import glob
    import hashlib
    import json
    import re
    import unicodedata

    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    _data = Path(__file__).resolve().parent.parent / "data"
    _data.mkdir(parents=True, exist_ok=True)

    # Only run on a genuinely new HealthSherpa upload (snapshot content changed).
    hs = sorted(glob.glob(str(Path(snapshot_dir) / "*healthsherpa*.parquet")))
    if not hs:
        return
    # Hash the STABLE input upload, NOT the rebuilt parquet. Re-ingesting the
    # same HealthSherpa export writes a byte-different parquet (metadata/row
    # order), so hashing the parquet re-fired the "new upload" text on every
    # re-run — the double-text on 2026-08-01. The input CSV is the upload's
    # identity: same export = same bytes = same hash = text correctly suppressed.
    _input_hs = Path(__file__).resolve().parent.parent / "input" / "healthsherpa.csv"
    _hsrc = _input_hs if _input_hs.exists() else Path(hs[-1])
    h = hashlib.md5(_hsrc.read_bytes()).hexdigest()
    marker = _data / "last_upload_hash.txt"
    if marker.exists() and marker.read_text().strip() == h:
        # NEVER exit silently — a quiet return here is indistinguishable from a
        # lost text (bit us twice when a ghost process consumed the marker).
        print("  Upload summary: no new HealthSherpa upload since last text — nothing to send.")
        return

    NPN = _NPN
    def _is_e(v):
        v = str(v or "").lower()
        return _LN in v and _FN in v
    def _ever_mine(r):
        # Ethan enrolled/submitted it → his NPN in npn_used, or he's the submitting
        # agent. Gates AOR-taken to clients he actually had (Ethan 2026-08-15).
        if NPN and str(NPN) in str(r.get("npn_used", "") or ""):
            return True
        return _is_e(r.get("submitting_agent_name", ""))
    # NEVER-MINE GUARD, authoritative here (Ethan 2026-08-25, corrected): a foreign policy_aor
    # is only a real loss if the AOR was EVER his. Upstream re-stamps cancel_reason to "AOR
    # taken — {agent}" for every foreign-AOR row (report.py ~L1452), wiping any earlier label,
    # so the text-builder recomputes "was the AOR ever his" from the snapshots itself. A client
    # whose policy_aor was foreign in EVERY snapshot was never his — no one took them over and
    # he'd never be paid, so they are NOT a loss (claim or no claim). A client whose AOR named
    # HIM in some snapshot and is foreign now IS a genuine steal (e.g. Laikka Batiste).
    def _aor_is_his_us(a):
        al = str(a or "").lower()
        return (str(NPN) in str(a)) or (_LN in al and _FN in al)
    _his_ids_us, _his_names_us = set(), set()
    for _sp in hs:                            # ALL HS snapshots
        try:
            _pdf = pd.read_parquet(_sp)
        except Exception:
            continue
        _plc = {c.lower(): c for c in _pdf.columns}
        if "policy_aor" not in _plc:
            continue
        _hu = _pdf[_plc["policy_aor"]].apply(_aor_is_his_us)
        if "ffm_app_id" in _plc:
            for _v in _pdf.loc[_hu, _plc["ffm_app_id"]].astype(str):
                _v = re.sub(r"\.0$", "", _v.strip())
                if _v and _v.lower() != "nan":
                    _his_ids_us.add(_v)
        if "first_name" in _plc and "last_name" in _plc:
            _his_names_us.update(zip(
                _pdf.loc[_hu, _plc["first_name"]].astype(str).str.strip().str.lower(),
                _pdf.loc[_hu, _plc["last_name"]].astype(str).str.strip().str.lower()))
    _have_hist = bool(_his_ids_us or _his_names_us)

    def _ever_his_aor(r):
        if not _have_hist:
            return True                       # scan failed → NEVER suppress a steal
        _pid = re.sub(r"\.0$", "", str(r.get("ffm_app_id") or "").strip())
        if _pid and _pid.lower() != "nan" and _pid in _his_ids_us:
            return True
        return (str(r.get("first_name") or "").strip().lower(),
                str(r.get("last_name") or "").strip().lower()) in _his_names_us

    def _foreign_aor_v(a):
        al = str(a or "").strip().lower()
        return al not in ("", "none", "nan") and str(NPN) not in str(a) and not _is_e(a)

    def _is_never_mine(r):
        # foreign AOR now + his NPN on the record + the AOR was NEVER his in any snapshot.
        return (_foreign_aor_v(r.get("policy_aor")) and _ever_mine(r)
                and not _ever_his_aor(r))
    def _key(f, l):
        s = unicodedata.normalize("NFKD", f"{f} {l}").encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z]", "", s)
    def _disp(f, l):
        return f"{f} {l}".strip().title()
    def _as_bool(v):
        # Robust truthiness for term_estimated (may arrive as a real bool, a
        # "True"/"False" string, or NaN). See [[sheets-bool-string-gotcha]].
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "t")
        try:
            if pd.isna(v):
                return False
        except (TypeError, ValueError):
            pass
        return bool(v)

    lost, vexp, aor, pol, polmem, polname = {}, {}, {}, set(), {}, {}
    lost_term = {}     # name_key -> real loss date (term_date), for freshness trim
    lost_estimated = {}  # name_key -> was the term date estimated/unconfirmed
    lost_basis = {}    # name_key -> how the loss date was derived (diff.assign_loss_months):
                       # "" / "commission" = confirmed; "sync" / "active" = inferred-recency
    active_mine = {}   # currently active AND credited to the agent — win-back proof
    taken_pids = set()  # policy ids whose AOR is another agent — never a NEW SALE of his
    for _, r in all_clients.iterrows():
        f, l = r.get("first_name", ""), r.get("last_name", "")
        k = _key(f, l)
        if not k:
            continue
        _never_mine = False
        st = str(r.get("status") or "")
        _reason = str(r.get("cancel_reason") or "")
        if st in ("Cancelled", "Terminated"):
            # Expired DMI/SVI verification ≠ cancelled: coverage is usually still
            # active with a termination date pending, so the client is SAVEABLE
            # (Ahmed Elzubair 2026-07-10 — Effectuated + paid, terming 7/31).
            if _is_never_mine(r) or "never mine" in _reason.lower():
                # Foreign AOR since the client's first appearance — he never held them,
                # so it isn't a loss. Drop from active silently: no loss bucket, and no
                # new-sale credit below. Checked by recomputed seen-before (not just the
                # reason, which upstream re-stamps to "AOR taken — {agent}"). (Ethan
                # 2026-08-25 — John MacDonald/Cynthia Crowe.)
                _never_mine = True
            elif "Verification expired" in _reason:
                vexp[k] = _disp(f, l)
            elif "AOR taken" in _reason:
                # taken clients are now reclassified Terminated, but they belong
                # in the "taken by another agent" bucket, not generic "lost"
                aor[k] = _disp(f, l)
            else:
                lost[k] = _disp(f, l)
                lost_term[k] = pd.to_datetime(r.get("term_date"), errors="coerce")
                lost_estimated[k] = _as_bool(r.get("term_estimated"))
                lost_basis[k] = str(r.get("loss_basis") or "").strip().lower()
        if st in ("Effectuated", "PendingEffectuation", "PendingFollowups"):
            _a = r.get("policy_aor")
            a = "" if pd.isna(_a) else str(_a)
            # A missing AOR is unknown, NOT another agent — "nan"/"none" text
            # slipping through here caused false "taken" alerts (2026-07-06).
            _foreign = a.strip().lower() not in ("", "none", "nan") and NPN not in a and not _is_e(a)
            if _foreign and _ever_mine(r) and not _is_never_mine(r):
                aor[k] = _disp(f, l)          # taken from me — I enrolled AND held them before
            elif _foreign and _ever_mine(r):
                _never_mine = True            # claimed someone else's app OR brand-new foreign →
                                              # never his; drop, don't credit as a sale (2026-08-25)
            elif _foreign:
                pass                          # foreign AOR but never mine → not my client,
                                              # don't report as a loss (Ethan's rule 2026-08-15)
            else:
                active_mine[k] = _disp(f, l)
        pid = re.sub(r"\.0$", "", str(r.get("ffm_app_id") or "").strip())
        if pid and pid.lower() != "nan":
            pol.add(pid)
            polname.setdefault(pid, _disp(f, l))   # first name seen on the policy (primary)
            if k in aor or _never_mine:   # taken, or never his → not a new sale of his
                taken_pids.add(pid)
            # NaN is truthy, so `int(nan or 1)` raises ValueError and kills the
            # whole summary — treat missing applicant_count as 1 explicitly.
            _n = pd.to_numeric(r.get("applicant_count"), errors="coerce")
            polmem[pid] = 1 if pd.isna(_n) else max(int(_n), 1)

    pdue = {}
    if pastdue is not None and not pastdue.empty:
        for _, r in pastdue.iterrows():
            k = _key(r.get("first_name", ""), r.get("last_name", ""))
            if k:
                pdue[k] = _disp(r.get("first_name", ""), r.get("last_name", ""))

    # RACE GUARD (Jessica Austin / Patricia Williams false-loss ×3, 2026-07-15):
    # the merged roster can momentarily lag a win-back / plan-change while ingest
    # settles, so a client who is truly active leaks into the lost/taken buckets
    # for one run. Re-read the freshest HealthSherpa snapshot straight from disk —
    # if a person has ANY current row that is active AND credited to us, they can
    # never be reported lost or taken. Ground truth wins over a stale in-memory build.
    _active_now = set()
    try:
        _snap = pd.read_parquet(hs[-1])
        for _, r in _snap.iterrows():
            if str(r.get("status") or "") not in ("Effectuated", "PendingEffectuation", "PendingFollowups"):
                continue
            _a = str(r.get("policy_aor") or "")
            if NPN in _a or _is_e(_a):
                _active_now.add(_key(r.get("first_name", ""), r.get("last_name", "")))
    except Exception:
        pass
    # Guard ONLY the lost/taken buckets. Verification-expired (vexp) clients are
    # active-and-mine BY DESIGN — active coverage with a term pending for missing
    # docs — and that "⚠️ still active, get docs in" alert must still fire, so vexp
    # is deliberately NOT guarded.
    if _active_now:
        _guarded = [n for k, n in {**lost, **aor}.items() if k in _active_now]
        if _guarded:
            print(f"  Upload summary: race guard kept {len(_guarded)} active-and-mine "
                  f"client(s) out of lost/taken: {', '.join(sorted(set(_guarded)))}")
        lost = {k: v for k, v in lost.items() if k not in _active_now}
        aor = {k: v for k, v in aor.items() if k not in _active_now}

    def _load(name):
        p = _data / name
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    base_lost, base_aor, base_pd, base_pol = (_load("known_lapsed.json"), _load("known_aor.json"),
                                              _load("known_pastdue.json"), _load("known_policies.json"))
    first_run = base_pol is None
    # Win-backs already announced, so a recovered client isn't re-celebrated on
    # every future upload (Takeyta Young fired 3x). Same once-only pattern as
    # aor_alerted.json. Cleared below if they lapse again, so a genuine re-loss →
    # re-win can announce afresh. (Ethan 2026-07-25)
    base_wb = set(_load("winback_alerted.json") or [])

    # Day-total baselines: freeze the book as it stood at the START OF TODAY (opening
    # policies + the open lost/taken/past-due sets). Every text that day counts new
    # sales AND new losses from this opening, so multiple same-day pulls show the
    # running DAY total, not that pull's delta. Resets automatically at midnight (new
    # date → new opening book). The per-run known_*.json baselines still update every
    # run, so cross-day de-dup is unaffected. (Ethan 2026-08-13.)
    _day_file = _data / "day_baseline.json"
    _today_str = today.strftime("%Y-%m-%d")
    try:
        _db = json.loads(_day_file.read_text())
    except Exception:
        _db = {}
    if _db.get("date") == _today_str:            # later pull today → keep this morning's opening
        day_start_pol = set(_db.get("policies") or [])
        day_lost = _db.get("lost", base_lost)
        day_aor  = _db.get("aor", base_aor)
        day_pd   = _db.get("pastdue", base_pd)
    else:                                         # first pull today → opening = end of last run
        day_start_pol = set(base_pol or [])
        day_lost, day_aor, day_pd = base_lost, base_aor, base_pd

    def _new(cur, base):
        return [] if base is None else [v for k, v in cur.items() if k not in base]
    # Only surface cancellations whose REAL loss date is recent (≤45 days) as
    # fresh Re-Engage leads. A client only now dropping off the book but whose
    # money/exchange-sync dates the loss months ago (e.g. Carol Walker, lapsed
    # Jan) is still baselined (so she never re-alerts) and still counts in churn
    # by her real month — she's just not a "call today" lead. (Ethan 2026-07-23)
    _FRESH_LOST_DAYS = 45
    def _fresh_lost(k):
        # A client is a FRESH "call today" Re-Engage lead ONLY if the cancel is
        # CONFIRMED (a real HealthSherpa/carrier term date, not an estimate) AND
        # recent (<=45d). Estimated or undated cancels are still baselined and
        # counted in churn by their real month — they're just not surfaced as
        # fresh leads. This stops (a) an old cancel that briefly oscillated
        # (false win-back → re-loss) from faking a new loss, and (b) the
        # estimated-term flood. Undated now defaults to STALE, not fresh.
        # (Ethan 2026-08-01 — Jan cancels re-texted as "Lost 7")
        if lost_estimated.get(k):
            return False
        # An INFERRED loss date — exchange-sync (last_ede_sync, which advances to
        # ~now on every re-sync) or last-active snapshot — marks WHEN we noticed
        # the drop, not when it happened, so a months-old lapse can masquerade as
        # brand new. These stay baselined and still count in churn by their frozen
        # month; they just never surface as fresh "call today" Re-Engage leads.
        # (Ethan 2026-08-01 — freeze-basis gap; 67 sync-dated losses were leaking.)
        if lost_basis.get(k) in ("sync", "active"):
            return False
        td = lost_term.get(k)
        if td is None or pd.isna(td):
            return False   # no confirmed cancel date → not a fresh lead
        # Bound both ends: a FUTURE-dated term (days < 0) is a scheduled/pending
        # cancel, not a loss that already happened, so it is not a fresh lead yet.
        _days = (today - td).days
        return 0 <= _days <= _FRESH_LOST_DAYS
    _new_lost_keys = [] if day_lost is None else [k for k in lost if k not in day_lost]
    new_lost   = [lost[k] for k in _new_lost_keys if _fresh_lost(k)]
    stale_lost = [lost[k] for k in _new_lost_keys if not _fresh_lost(k)]
    # Expired verifications share the known_lapsed baseline so a client already
    # texted under either label never re-alerts when they move between buckets.
    new_vexp = _new(vexp, day_lost)
    new_aor = _new(aor, day_aor)
    new_pd = _new(pdue, day_pd)
    # Win-backs: was lost/taken at the last text, now active AND his again.
    # (Ethan 2026-07-08: "if I ever get a person back that was lost or win them
    # back from an AOR I want you to include that in the text".)
    # A client lost/taken AGAIN drops out of the "already announced" set, so if
    # they come back later they can be celebrated afresh.
    base_wb -= set(lost) | set(aor)
    won_lost_keys = [k for k in (base_lost or {})
                     if k not in lost and k in active_mine and k not in base_wb]
    won_aor_keys = [k for k in (base_aor or {})
                    if k not in aor and k in active_mine and k not in base_wb]
    won_lost = [base_lost[k] for k in won_lost_keys]
    won_aor = [base_aor[k] for k in won_aor_keys]
    # day-total: new since today's opening, EXCLUDING policies taken by another agent
    # (an old app that only just reappeared under a foreign AOR is not a new sale).
    new_pol = [p for p in pol if p not in day_start_pol and p not in taken_pids]
    new_pol_n = 0 if first_run else len(new_pol)
    new_mem = 0 if first_run else sum(polmem.get(p, 1) for p in new_pol)

    # State writes are deferred until the text is actually SENT (or knowingly
    # skipped). Writing them first is how texts got lost: any crash or failed
    # send after the marker write "consumed" the upload event, and the next
    # run's gate stayed silent forever.
    def _save_state():
        (_data / "known_lapsed.json").write_text(json.dumps({**vexp, **lost}, indent=2))
        (_data / "known_aor.json").write_text(json.dumps(aor, indent=2))
        (_data / "known_pastdue.json").write_text(json.dumps(pdue, indent=2))
        (_data / "known_policies.json").write_text(json.dumps(sorted(pol), indent=2))
        # Persist today's opening book so later same-day pulls keep counting from the
        # start of the day (idempotent: same sets re-written on same-day runs).
        _day_file.write_text(json.dumps({
            "date": _today_str, "policies": sorted(day_start_pol),
            "lost": day_lost or {}, "aor": day_aor or {}, "pastdue": day_pd or {},
        }, indent=1))
        (_data / "winback_alerted.json").write_text(
            json.dumps(sorted(base_wb | set(won_lost_keys) | set(won_aor_keys)), indent=1))
        marker.write_text(h)

    if first_run:
        _save_state()
        print("  Upload summary: baselines initialized (no text on first run).")
        return

    def _fmt(names):
        return ", ".join(names[:6]) + (f" +{len(names) - 6} more" if len(names) > 6 else "")
    d = today.strftime("%b %d")
    _new_names = [polname[p] for p in new_pol if polname.get(p)]
    _signed = f"✅ Signed: {new_pol_n} new policies / {new_mem} members"
    if _new_names:
        _signed += f":\n • {_fmt(_new_names)}"
    lines = [f"HealthSherpa updated · {d}", _signed]
    total = len(new_lost) + len(new_pd) + len(new_aor)
    if total == 0:
        if not new_vexp and not stale_lost:
            lines.append("⬇️ Lost 0 clients — all clear.")
    else:
        lines.append(f"⬇️ Lost {total} clients:")
        if new_lost:
            lines.append(f" • Cancelled (→ Re-Engage): {_fmt(new_lost)}")
        if new_pd:
            lines.append(f" • Behind on payment: {_fmt(new_pd)}")
        if new_aor:
            lines.append(f" • Taken by another agent: {_fmt(new_aor)}")
    if stale_lost:
        lines.append(f"🗂️ +{len(stale_lost)} older lapse(s) fell off the book, dated to "
                     f"their real month (counted in churn, not fresh Re-Engage leads).")
    if new_vexp:
        lines.append(f"⚠️ Verification expired — still active but will be termed "
                     f"unless docs go in: {_fmt(new_vexp)}")
    if won_lost:
        lines.append(f"🎉 Won back (were cancelled): {_fmt(won_lost)}")
    if won_aor:
        lines.append(f"🏆 Won back from another agent: {_fmt(won_aor)}")
    msg = "\n".join(lines)
    print("  Upload summary:\n    " + msg.replace("\n", "\n    "))

    cfg = _data / "alert_config.json"
    phone, enabled = None, True
    if cfg.exists():
        try:
            c = json.loads(cfg.read_text())
            phone = c.get("phone")
            enabled = c.get("upload_summary", c.get("lapse_alerts", True))
        except Exception:
            pass

    if not enabled or not phone:
        # Deliberately not texting → the event is handled; advance state.
        _save_state()
        print("  (upload summary text disabled or no phone configured — not texted)")
        return

    import time as _time
    sent = False
    for attempt in (1, 2):
        try:
            from tracker.digest import send_imessage
            send_imessage(msg, phone)
            sent = True
            break
        except Exception as e:
            print(f"  !! upload summary text attempt {attempt} failed: {e}")
            if attempt == 1:
                _time.sleep(3)
    if sent:
        _save_state()
        print(f"  Upload summary texted to {phone}")
    else:
        print("  !! TEXT NOT SENT — baselines/marker left unchanged, next report run will retry.")


def _person_key_series(df):
    """Person key aligned to df.index that GROUPS one person's multiple rows but
    SEPARATES same-name DIFFERENT people by subscriber id. A name gets its sid
    appended only when >=2 distinct non-blank subscriber ids appear among that
    name's active rows (a genuine collision — e.g. two real 'Rhonda Walker'
    Ambetter policies). Prevents the plan-switch + AOR rules from merging same-
    name strangers and wrongly terminating one. (Ethan 2026-07-24)"""
    _ACT_PK = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    fn = df.get("first_name", pd.Series("", index=df.index)).fillna("").astype(str).str.lower().str.replace(r"[^a-z]", "", regex=True)
    ln = df.get("last_name", pd.Series("", index=df.index)).fillna("").astype(str).str.lower().str.replace(r"[^a-z]", "", regex=True)
    nm = fn + "|" + ln
    if "ffm_subscriber_id" in df.columns:
        sid = df["ffm_subscriber_id"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    else:
        sid = pd.Series("", index=df.index)
    act = df["status"].isin(_ACT_PK) if "status" in df.columns else pd.Series(True, index=df.index)
    _c = pd.DataFrame({"nm": nm, "sid": sid, "act": act})
    _n = _c[_c["act"] & (_c["sid"] != "")].groupby("nm")["sid"].nunique()
    colliding = set(_n[_n > 1].index)
    return nm.where(~(nm.isin(colliding) & (sid != "")), nm + "|" + sid)


def run_report(settings: dict) -> None:
    # ONE report at a time, process-wide. Concurrent runs (launchd watcher vs a
    # manual run) once raced on the upload-summary marker and silently ate the
    # text alert (the "ghost process", Jun 29). Non-blocking lock: second caller
    # prints and exits instead of double-writing the sheet.
    import fcntl
    _lock_path = Path(__file__).resolve().parent.parent / "data" / ".report.lock"
    _lock_path.parent.mkdir(parents=True, exist_ok=True)
    _lock_f = open(_lock_path, "w")
    try:
        fcntl.flock(_lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("!! Another report run is already in progress — skipping this one "
              "(it would race on the sheet + alert baselines). Try again in ~3 min.")
        return
    _lock_f.write(str(pd.Timestamp.now()))
    _lock_f.flush()

    snapshot_dir = Path(settings["snapshot_dir"])
    months = load_all_snapshots(snapshot_dir)

    if not months:
        print("No snapshots found. Run `track ingest` first.")
        return

    # Drop excluded clients (never-sold HealthSherpa noise) from every snapshot
    # so they vanish from the book, dashboard, Re-Engage, AND daily tracker.
    _exclusions = _load_exclusions()
    if _exclusions:
        _before = sum(len(d) for d in months.values())
        months = {m: _filter_excluded(d, _exclusions) for m, d in months.items()}
        _removed = _before - sum(len(d) for d in months.values())
        print(f"  Excluded clients: removed {_removed} row(s) across snapshots ({len(_exclusions)} on the list)")

    sorted_months = sorted(months.keys())
    latest_month = sorted_months[-1]
    prior_month = sorted_months[-2] if len(sorted_months) >= 2 else None

    print(f"Building report. Latest month: {latest_month}")

    appointments = _load_appointments()
    all_clients  = build_all_clients(months)

    # Carriers Ethan never counts as his book (e.g. Florida Blue / BCBS-FL). Matched on
    # the raw carrier name as a case-insensitive substring so "florida blue" drops only
    # the FL Blue entities, not other Blue Cross plans. Same rule runs on the agent site.
    _excl_carriers = [str(x).strip().lower() for x in (settings.get("excluded_carriers") or [])
                      if str(x).strip()]
    if _excl_carriers and "carrier" in all_clients.columns:
        _cl = all_clients["carrier"].astype(str).str.lower()
        _drop = _cl.apply(lambda c: any(x in c for x in _excl_carriers))
        if int(_drop.sum()):
            print(f"  Excluded {int(_drop.sum())} clients on non-counted carriers "
                  f"({', '.join(_excl_carriers)})")
        all_clients = all_clients[~_drop].copy()

    # Mark confirmed-AOR-changed clients whose HealthSherpa policy_aor field still
    # lags the exchange (e.g. Tammy Bennett -> Albert Rincon). Stamping policy_aor
    # here means EVERY AOR filter treats them as another agent's — including the
    # cloud app, which reads this written value and can't see data/aor_changed.json
    # (gitignored). 'Marketplace disconnected' clients are NOT on the list, so they
    # keep policy_aor=Ethan and are left alone.
    try:
        from tracker.commissions import aor_changed_keys, aor_changed_agents
        _chg = aor_changed_keys()
        _chg_agents = aor_changed_agents()
        if _chg and "policy_aor" in all_clients.columns:
            import re as _re_aor
            def _ck(r):
                l = _re_aor.sub(r"[^a-z]", "", str(r.get("last_name", "")).lower())
                f = _re_aor.sub(r"[^a-z]", "", str(r.get("first_name", "")).lower())
                return l + f
            _keys = all_clients.apply(_ck, axis=1)
            _m = _keys.isin(_chg)
            if _m.any():
                # Stamp the actual taking agent when we know it ("Yitzchak Nassy
                # (NPN: 16663886)") so the reason reads "AOR taken — Yitzchak
                # Nassy", not a generic label; fall back to generic otherwise.
                all_clients.loc[_m, "policy_aor"] = _keys[_m].map(_chg_agents).fillna(
                    "AOR changed (another agent)")
                # ALSO force them Cancelled so they leave the active count. "All Active"
                # is filtered by STATUS only (line ~1231), NOT by policy_aor, and the
                # AOR-at-risk block below only Cancels clients on HealthSherpa's at-risk
                # export. So a live-verified AOR-take that the export misses (e.g. Samuel
                # Riley, Katie Johnson) would otherwise linger in the active count with
                # just a stamped policy_aor. Carrier-truth skips already-Cancelled rows
                # (it only reconciles status.isin(_ACTIVE)), so this sticks; and the book
                # race guard already excludes aor_changed_keys() from re-activation.
                if "cancel_reason" not in all_clients.columns:
                    all_clients["cancel_reason"] = ""
                all_clients.loc[_m, "status"] = "Cancelled"
                all_clients.loc[_m, "cancel_reason"] = "AOR taken"
                print(f"  AOR-changed override: marked {int(_m.sum())} client(s) as "
                      f"another agent's (Cancelled, out of active count)")
    except Exception as _e:
        print(f"  (AOR-changed override skipped: {_e})")

    # Manually-verified TERMINATIONS (data/manual_lost.json): clients confirmed
    # terminated via live HealthSherpa that the exports miss — they dropped off the
    # carrier book entirely AND still read Effectuated in the (stale) HS export, so
    # neither carrier-truth nor the export catches them. Force them Cancelled so they
    # leave the active book, with a real term date. Curated + additive, like
    # aor_changed above; distinct from it because these are genuine LAPSES (still
    # your AOR), not agent-switches. (Ethan 2026-08-01 absent-set audit.)
    try:
        _mlp = Path(__file__).resolve().parent.parent / "data" / "manual_lost.json"
        if _mlp.exists() and {"first_name", "last_name"}.issubset(all_clients.columns):
            _ml = json.loads(_mlp.read_text())
            _mlk = {re.sub(r"[^a-z]", "", (str(i.get("last", "")) + str(i.get("first", ""))).lower()): i
                    for i in _ml}
            def _mk(r):
                return re.sub(r"[^a-z]", "", (str(r.get("last_name", "")) + str(r.get("first_name", ""))).lower())
            _mmask = all_clients.apply(lambda r: _mk(r) in _mlk, axis=1)
            if _mmask.any():
                for _i in all_clients.index[_mmask]:
                    _it = _mlk[_mk(all_clients.loc[_i])]
                    all_clients.at[_i, "status"] = "Cancelled"
                    all_clients.at[_i, "cancel_reason"] = "Lapsed — verified terminated (live HS)"
                    _td = pd.to_datetime(_it.get("term_date"), errors="coerce")
                    if pd.notna(_td) and "term_date" in all_clients.columns:
                        all_clients.at[_i, "term_date"] = _td
                    if "term_estimated" in all_clients.columns:
                        all_clients.at[_i, "term_estimated"] = False
                    # Keep loss_basis EMPTY (NOT "sync"/"active") so these DO surface on
                    # Re-Engage as call-backs: both the upload-text gate (~L613) and the
                    # dashboard Re-Engage page EXCLUDE loss_basis in ("sync","active").
                    # assign_loss_months later also sets "" for these real-term rows;
                    # setting "" here keeps the intent right if this block ever moves.
                    if "loss_basis" in all_clients.columns:
                        all_clients.at[_i, "loss_basis"] = ""
                print(f"  Manual-lost override: marked {int(_mmask.sum())} verified-terminated client(s) Cancelled")
    except Exception as _e:
        print(f"  (manual-lost override skipped: {_e})")

    # AOR-taken = a client Ethan SIGNED UP (his NPN in npn_used, or he submitted it) whose
    # agent-of-record now shows ANOTHER agent → taken from him, marked Cancelled so he drops
    # out of the active book. Ethan's rule (2026-08-15, locked in): he does NOT need to have
    # been paid, and it does NOT depend on HealthSherpa's at-risk flag — "as long as I was
    # shown as the agent and later another agent took over, it counts." The appointed
    # state+carrier gate is applied by _filter_by_appointments just below (non-appointed
    # rows are dropped entirely, so only appointed steals survive). Poaching flips policy_aor
    # but leaves npn_used = his, so this reliably catches steals while ignoring never-mine
    # noise. (The AOR at-risk scrape still feeds the AOR Defense tab via aor_defense.py; it's
    # just no longer the gate for the active-count drop.)
    if "policy_aor" in all_clients.columns:
        try:
            def _foreign_aor(a):
                al = str(a or "").lower()
                if not al.strip() or "none" in al:
                    return False
                if _NPN and str(_NPN) in str(a):
                    return False
                if _LN in al and _FN in al:
                    return False
                return True

            _mine = pd.Series(False, index=all_clients.index)
            if "npn_used" in all_clients.columns:
                _mine |= all_clients["npn_used"].astype(str).str.contains(str(_NPN), na=False, regex=False)
            if "submitting_agent_name" in all_clients.columns:
                _sub = all_clients["submitting_agent_name"].astype(str).str.lower()
                _mine |= (_sub.str.contains(_FN, na=False, regex=False)
                          & _sub.str.contains(_LN, na=False, regex=False))

            _taken = all_clients["policy_aor"].apply(_foreign_aor) & _mine

            # NEVER-MINE GUARD (Ethan 2026-08-25, corrected): npn_used = his NPN does NOT prove
            # he ever HELD the client. The ONLY reliable test of a real steal vs. a never-his
            # client is: was the AOR EVER his in any snapshot? He can CLAIM an application (which
            # stamps his NPN on it) and never be the agent of record — a claim earns nothing. But
            # he can also claim one, briefly BE the AOR, then lose it — a genuine steal (e.g.
            # Laikka Batiste). So "claimed" is NOT the discriminator; AOR history is.
            #   real steal = foreign AOR now  AND  policy_aor named HIM in some snapshot
            #   never mine = foreign AOR now  AND  policy_aor was foreign in EVERY snapshot
            # (John MacDonald / Cynthia Crowe: claimed, foreign from first sight, never his.)
            def _aor_is_his(a):
                al = str(a or "").lower()
                return (str(_NPN) in str(a)) or (_LN in al and _FN in al)
            _his_ids, _his_names = set(), set()
            for _sp in sorted(Path(snapshot_dir).glob("*healthsherpa*.parquet")):
                try:
                    _pdf = pd.read_parquet(_sp)
                except Exception:
                    continue
                _plc = {c.lower(): c for c in _pdf.columns}
                if "policy_aor" not in _plc:
                    continue
                _hu = _pdf[_plc["policy_aor"]].apply(_aor_is_his)
                if "ffm_app_id" in _plc:
                    for _v in _pdf.loc[_hu, _plc["ffm_app_id"]].astype(str):
                        _v = re.sub(r"\.0$", "", _v.strip())
                        if _v and _v.lower() != "nan":
                            _his_ids.add(_v)
                if "first_name" in _plc and "last_name" in _plc:
                    _his_names.update(zip(
                        _pdf.loc[_hu, _plc["first_name"]].astype(str).str.strip().str.lower(),
                        _pdf.loc[_hu, _plc["last_name"]].astype(str).str.strip().str.lower()))

            def _ever_his_aor(r) -> bool:
                _pid = re.sub(r"\.0$", "", str(r.get("ffm_app_id") or "").strip())
                if _pid and _pid.lower() != "nan" and _pid in _his_ids:
                    return True
                return (str(r.get("first_name") or "").strip().lower(),
                        str(r.get("last_name") or "").strip().lower()) in _his_names

            # SAFETY: if the "ever his" scan found nothing (unreadable snapshots), do NOT
            # suppress anything — treat every taken client as a real steal. Never let a scan
            # failure mass-hide genuine steals.
            if not _his_ids and not _his_names:
                _held = pd.Series(True, index=all_clients.index)
            elif len(all_clients):
                _held = all_clients.apply(_ever_his_aor, axis=1)
            else:
                _held = pd.Series(False, index=all_clients.index)
            _taken_real  = _taken & _held
            _taken_never = _taken & ~_held

            if "cancel_reason" not in all_clients.columns:
                all_clients["cancel_reason"] = ""
            _active_sts = all_clients["status"].isin(
                ["Effectuated", "PendingEffectuation", "PendingFollowups"])
            _newly = int((_taken_real & _active_sts).sum())    # active-count impact (excl. already-gone)
            _never = int(_taken_never.sum())
            all_clients.loc[_taken_real, "status"] = "Cancelled"
            all_clients.loc[_taken_real, "cancel_reason"] = "AOR taken"
            # Never-mine (foreign AOR that was NEVER credited to his NPN in any snapshot): he
            # never held them, so they are not his active clients, not a loss, and not "taken
            # from him". REMOVE from the book — otherwise the downstream re-stamp (~L1452)
            # relabels them "AOR taken — {agent}" and surfaces them on Re-Engage / AOR Defense
            # and in the text. Safe: _filter_by_appointments drops rows right below.
            if _taken_never.any():
                all_clients = all_clients[~_taken_never].reset_index(drop=True)
            if _newly:
                print(f"  AOR-taken (signed by me + foreign AOR; appointed gate applied next): "
                      f"marked {_newly} active client(s) Cancelled")
            if _never:
                print(f"  Never-mine (foreign AOR, never credited to my NPN in any snapshot): "
                      f"removed {_never} client(s) from the book (not a loss, not reported)")
        except Exception as _e:
            print(f"  (AOR-taken rule skipped: {_e})")

    before_ct    = len(all_clients)
    all_clients  = _filter_by_appointments(all_clients, appointments)
    filtered_ct  = before_ct - len(all_clients)
    if filtered_ct:
        print(f"  Appointment filter: removed {filtered_ct} non-appointed carrier/state rows")

    # Carrier-portal truth (Ambetter): the portal is the source of truth for who
    # is active. Drops policies missing from the portal (unless coverage hasn't
    # started yet) and adds portal business the tracker lacks. Daily tracker is
    # built from `months` separately, so sale timing stays HealthSherpa-driven.
    from tracker.carrier_truth import (apply_ambetter_truth, apply_oscar_truth,
                                        apply_uhc_truth, apply_anthem_truth,
                                        apply_cigna_truth)

    # A malformed carrier file (changed export format, wrong download) must
    # never kill the whole report — skip that carrier loudly and keep going.
    # PARTIAL-BOOK GUARD (Ethan 2026-07-24): a well-formed but SHORT export (a
    # filtered download, a paging cutoff, a missing plan year) must not lapse
    # real active clients just for being absent from it. If a book would cancel
    # an abnormal share of the carrier's active clients purely for absence
    # (cancelled_dropped), reject the whole book this run. Measured normal
    # absent-drop rates are ~8-14% per carrier, so 30% is a safe "this file is
    # broken, not real churn" line.
    _ACT_ST = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    _DROP_REJECT_RATE, _DROP_REJECT_FLOOR = 0.30, 15

    def _apply_truth(fn, label, fmt, carrier_kw):
        nonlocal all_clients
        before = all_clients
        try:
            after, _s = fn(before)
        except Exception as e:
            print(f"  !! {label} book SKIPPED — {type(e).__name__}: {e}")
            print(f"     Check carrier_books/ for a bad/changed {label} export; "
                  f"book statuses for {label} were left as-is this run.")
            return
        if not _s.get("applied"):
            return
        _act_before = int((before.get("carrier", pd.Series("", index=before.index))
                             .astype(str).str.contains(carrier_kw, case=False, na=False, regex=True)
                           & before.get("status", pd.Series("", index=before.index)).isin(_ACT_ST)).sum())
        _dropped = int(_s.get("cancelled_dropped", 0))
        if _act_before >= _DROP_REJECT_FLOOR and _dropped > _DROP_REJECT_RATE * _act_before:
            print(f"  !! {label} portal truth REJECTED — would cancel {_dropped}/{_act_before} active "
                  f"clients as 'absent from portal' ({_dropped / _act_before:.0%} > "
                  f"{_DROP_REJECT_RATE:.0%}). That signals a partial/short {label} export, not real "
                  f"churn — re-download the full {label} book. Statuses left as-is this run.")
            return  # discard `after`, keep the pre-call book
        all_clients = after
        print(f"  {label} portal truth: " + fmt(_s))

    _apply_truth(apply_ambetter_truth, "Ambetter",
                 lambda s: f"+{s['added_from_portal']} added, "
                           f"{s['cancelled_termed'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected, {s.get('absent_kept', 0)} kept: absent from book, not cancelled)", "ambetter")
    _apply_truth(apply_oscar_truth, "Oscar",
                 lambda s: f"+{s['added_from_portal']} added, "
                           f"{s['cancelled_inactive'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected, {s.get('absent_kept', 0)} kept: absent from book, not cancelled)", "oscar")
    _apply_truth(apply_uhc_truth, "UHC",
                 lambda s: f"+{s['added_policies']} added, "
                           f"{s['cancelled_lapsed'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected, {s.get('absent_kept', 0)} kept: absent from book, not cancelled)", "united|uhc")
    _apply_truth(apply_anthem_truth, "Anthem",
                 lambda s: f"+{s['added_policies']} added, "
                           f"{s['cancelled_lapsed'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected, {s.get('absent_kept', 0)} kept: absent from book, not cancelled)", "anthem")
    _apply_truth(apply_cigna_truth, "Cigna",
                 lambda s: f"+{s['added_from_portal']} added, "
                           f"{s['cancelled_inactive'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected, {s.get('absent_kept', 0)} kept: absent from book, not cancelled)", "cigna")

    # HealthSherpa verification truth: an EXPIRED DMI/SVI follow-up means the
    # subsidy / eligibility is lost, so the client is effectively gone. Mark them
    # Cancelled so they drop off active + past-due and flow into Re-Engage outreach.
    if not all_clients.empty:
        def _numcol(name):
            if name in all_clients.columns:
                return pd.to_numeric(all_clients[name], errors="coerce").fillna(0)
            return pd.Series(0.0, index=all_clients.index)
        # ...but NOT brand-new business whose coverage hasn't started yet (future
        # effective date). A pre-effective new enrollment with an expired DMI/SVI just
        # owes a document — it belongs on the Follow-ups list, not marked lost.
        _eff = (pd.to_datetime(all_clients["effective_date"], errors="coerce")
                if "effective_date" in all_clients.columns
                else pd.Series(pd.NaT, index=all_clients.index))
        _today = pd.Timestamp.today().normalize()
        _exp = ((_numcol("dmi_expired") > 0) | (_numcol("svi_expired") > 0)) & ~(_eff > _today)
        if _exp.any():
            if "cancel_reason" not in all_clients.columns:
                all_clients["cancel_reason"] = ""
            all_clients.loc[_exp, "status"] = "Cancelled"
            all_clients.loc[_exp, "cancel_reason"] = "Verification expired — subsidy lost"
            print(f"  Follow-up truth: {int(_exp.sum())} clients with an expired "
                  f"verification marked Cancelled (subsidy lost)")

    # Canonical carrier names (merge "United Healthcare"/"UnitedHealthcare", the
    # several Molina forms, "U of U"→University of Utah) so reporting doesn't
    # split one carrier across spellings. Done AFTER carrier-truth matching.
    if not all_clients.empty and "carrier" in all_clients.columns:
        from tracker.carriers import normalize_carrier_series
        all_clients["carrier"] = normalize_carrier_series(all_clients["carrier"])

    # One client, one active policy: collapse plan switches (a person showing more
    # than one active policy — e.g. Ambetter → UnitedHealthcare, or a duplicate
    # add). Keep the newest by effective date; term the older ones as "Plan
    # switch", flagged estimated so a switch isn't miscounted as a real loss and is
    # excluded from Re-Engage.
    if not all_clients.empty and "status" in all_clients.columns:
        _ACT = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
        if "cancel_reason" not in all_clients.columns:
            all_clients["cancel_reason"] = ""
        if "term_estimated" not in all_clients.columns:
            all_clients["term_estimated"] = False
        _pk = _person_key_series(all_clients)   # groups a person's rows, splits same-name strangers by sid
        _eff_ps = pd.to_datetime(all_clients.get("effective_date"), errors="coerce")
        _actmask = all_clients["status"].isin(_ACT)
        _n_switch = 0
        for _k, _cnt in _pk[_actmask].value_counts().items():
            if _cnt < 2 or not _k.strip("|"):
                continue
            _idxs = list(all_clients.index[_actmask & (_pk == _k)])
            _grp_eff = _eff_ps.loc[_idxs]
            _newest = _grp_eff.idxmax() if _grp_eff.notna().any() else _idxs[0]
            for _i in _idxs:
                if _i == _newest:
                    continue
                all_clients.at[_i, "status"] = "Terminated"
                all_clients.at[_i, "cancel_reason"] = "Plan switch"
                all_clients.at[_i, "term_estimated"] = True
                if "term_date" in all_clients.columns and pd.isna(
                        pd.to_datetime(all_clients.at[_i, "term_date"], errors="coerce")):
                    all_clients.at[_i, "term_date"] = _grp_eff.loc[_newest]
                _n_switch += 1
        if _n_switch:
            print(f"  Plan-switch cleanup: collapsed {_n_switch} older duplicate active policy(ies)")

    # DUPLICATE-PERSON COLLAPSE (double-count fix, Ethan 2026-08-02). The sid-aware
    # _person_key_series above SPLITS one person into separate keys whenever they hold
    # >=2 active rows with distinct subscriber ids — right for same-name STRANGERS,
    # wrong for ONE person who switched carriers (old brand lingers, or carrier-truth
    # re-added a stale portal policy whose plan-year term date hasn't caught up) or has
    # a duplicate same-carrier enrollment (two app_ids/sids, same plan). A person holds
    # only ONE active major-medical marketplace plan, so within a single name+state keep
    # the current row (latest current_effective) and term the rest "Plan switch".
    # SAFETY: two SAME-carrier rows with DIFFERENT eff dates and NO shared id
    # (app_id/sid/email/phone) are left alone — preserves the same-name-collision guard.
    if not all_clients.empty and "status" in all_clients.columns:
        _ACT_DC = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
        _nm_dc = all_clients.apply(
            lambda r: re.sub(r"[^a-z]", "", f"{r.get('first_name','')}{r.get('last_name','')}".lower()), axis=1)
        _st_dc = all_clients.get("state", pd.Series("", index=all_clients.index)).fillna("").astype(str).str.lower().str.strip()
        _key_dc = _nm_dc + "@" + _st_dc
        _am_dc = all_clients["status"].isin(_ACT_DC)
        _eff_dc = pd.to_datetime(all_clients.get("effective_date"), errors="coerce")
        _cur_dc = pd.to_datetime(all_clients.get("current_effective"), errors="coerce")
        _cur_dc = _cur_dc.where(_cur_dc.notna(), _eff_dc)
        _car_dc = all_clients.get("carrier", pd.Series("", index=all_clients.index)).astype(str).str.lower()
        _sid_dc = all_clients.get("ffm_subscriber_id", pd.Series("", index=all_clients.index)).astype(str).str.replace(r"[^0-9]", "", regex=True)
        _app_dc = all_clients.get("ffm_app_id", pd.Series("", index=all_clients.index)).astype(str).str.replace(r"[^0-9]", "", regex=True)
        _em_dc = all_clients.get("email", pd.Series("", index=all_clients.index)).fillna("").astype(str).str.lower().str.strip()
        _ph_dc = all_clients.get("phone", pd.Series("", index=all_clients.index)).astype(str).str.replace(r"[^0-9]", "", regex=True).str[-10:]

        def _same_person_dc(i, j):
            if _app_dc[i] and _app_dc[i] == _app_dc[j]: return True    # same FFM application
            if _sid_dc[i] and _sid_dc[i] == _sid_dc[j]: return True    # same subscriber id
            if _em_dc[i] and _em_dc[i] == _em_dc[j]:    return True
            if _ph_dc[i] and _ph_dc[i] == _ph_dc[j]:    return True
            if _car_dc[i] != _car_dc[j]:                return True    # cross-carrier switch
            return bool(_eff_dc[i] == _eff_dc[j])                      # same carrier: dup only if identical eff

        _n_dc = 0
        for _kv, _cnt in _key_dc[_am_dc].value_counts().items():
            if _cnt < 2 or _kv.startswith("@"):
                continue
            _idxs = list(all_clients.index[_am_dc & (_key_dc == _kv)])
            _clusters = []
            for _i in _idxs:
                for _cl in _clusters:
                    if any(_same_person_dc(_i, _j) for _j in _cl):
                        _cl.append(_i); break
                else:
                    _clusters.append([_i])
            for _cl in _clusters:
                if len(_cl) < 2:
                    continue
                _newest = max(_cl, key=lambda k: (pd.notna(_cur_dc[k]),
                                                  _cur_dc[k] if pd.notna(_cur_dc[k]) else pd.Timestamp.min))
                for _i in _cl:
                    if _i == _newest:
                        continue
                    all_clients.at[_i, "status"] = "Terminated"
                    all_clients.at[_i, "cancel_reason"] = "Plan switch"
                    all_clients.at[_i, "term_estimated"] = True
                    if "term_date" in all_clients.columns and pd.isna(
                            pd.to_datetime(all_clients.at[_i, "term_date"], errors="coerce")):
                        all_clients.at[_i, "term_date"] = _cur_dc[_newest]
                    _n_dc += 1
        if _n_dc:
            print(f"  Duplicate-person collapse: termed {_n_dc} redundant active policy(ies) "
                  f"(cross-carrier switch / same-carrier duplicate the sid-split pass missed)")

    # STATE-EXCHANGE RE-ACTIVATION (Ethan 2026-08-01). A client currently enrolled
    # in a state-based-marketplace book (Georgia Access / Get Covered IL — source=
    # access, "Report a Change" = active) has active OFF-FFM coverage that never
    # appears in an FFM carrier portal book. build_all_clients collapses such a
    # person to source="healthsherpa" whenever they ALSO carry an old HS row, so
    # carrier-truth's access-skip (_ffm_mask) can't protect them: carrier-truth
    # matches their OLD, termed FFM policy in the carrier export and cancels the
    # whole person (e.g. Calvin Copeland/Oscar, Lavelva Ellerson/Ambetter). Restore
    # anyone the access book still lists ACTIVE — but ONLY carrier-truth lapses
    # (Cancelled with a still-blank cancel_reason at this point). AOR-taken,
    # verification-expired, manual-lost and plan-switch all stamp a cancel_reason
    # above, so they're excluded and never restored. Read the RAW input books
    # (dry_run, no write) so the ingest HS-dedup can't hide the evidence. Runs after
    # every cancel rule and before loss-dating, so it's the final word on status and
    # restored clients get no loss date. Only flips existing rows (never adds), so it
    # cannot double-count.
    try:
        from tracker.config import load_carrier_configs, load_full_carrier_config
        from tracker.ingest import ingest_file as _ingest_acc, detect_source as _detect_acc
        _acc_sc = load_carrier_configs(settings["carrier_config_path"])
        _acc_fc = load_full_carrier_config(settings["carrier_config_path"])
        _ACC_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
        _acc_keys = set()
        for _bp in sorted(Path(settings["input_dir"]).glob("*.csv")):
            if _detect_acc(_bp.name, _acc_sc) != "access":
                continue
            _, _adf = _ingest_acc(_bp, _acc_sc, snapshot_dir, dry_run=True, full_config=_acc_fc)
            if _adf is not None and {"name_key", "status"}.issubset(_adf.columns):
                _acc_keys |= set(_adf.loc[_adf["status"].isin(_ACC_ACTIVE), "name_key"].dropna())
        if _acc_keys and not all_clients.empty and "name_key" in all_clients.columns:
            _acc_blank = (all_clients.get("cancel_reason", pd.Series("", index=all_clients.index))
                          .fillna("").astype(str).str.strip() == "")
            # The book is PER-POLICY, so a plan-switcher can hold an active plan AND
            # an old lapsed one. Restoring the lapsed row for someone who is ALREADY
            # active would create a duplicate active policy (double-count). So only
            # restore people who are currently FULLY lost (no active row), and flip at
            # most ONE row each.
            _acc_active_now = set(all_clients.loc[
                all_clients["status"].isin(list(_ACC_ACTIVE)), "name_key"].dropna())
            _acc_cand = (all_clients["status"].isin(["Cancelled", "Terminated"])
                         & _acc_blank
                         & all_clients["name_key"].isin(_acc_keys)
                         & ~all_clients["name_key"].isin(_acc_active_now))
            _acc_idx = all_clients.index[_acc_cand]
            if len(_acc_idx):
                _acc_first = all_clients.loc[_acc_idx].drop_duplicates("name_key").index
                all_clients.loc[_acc_first, "status"] = "Effectuated"
                all_clients.loc[_acc_first, "term_date"] = pd.NaT
                if "term_estimated" in all_clients.columns:
                    all_clients.loc[_acc_first, "term_estimated"] = False
                print(f"  State-exchange re-activation: restored {len(_acc_first)} "
                      f"GA/IL-enrolled client(s) wrongly lapsed by carrier-truth")
    except Exception as _e:
        print(f"  (state-exchange re-activation skipped: {_e})")

    # Loss dating: every gone client (AOR-taken, verification-expired, undated
    # HealthSherpa cancellation) that carries no cancel date gets one. We date it to
    # the month his COMMISSION on them stopped (money doesn't lie) — falling back to
    # the exchange sync date, then the last month a snapshot showed them active.
    # Without this they'd be counted active-forever in the month-over-month engine
    # and never register as a loss, understating churn and overstating LTV. Runs last,
    # after every status rule, so "who's gone" is final.
    _last_paid = _build_last_paid(settings)
    all_clients = assign_loss_months(all_clients, last_paid=_last_paid)

    # Tenure = how long the client has been on YOUR book, NOT the policy's
    # coverage age. The policy's effective_date can predate the relationship by
    # years (inherited / agent-of-record transfers start as far back as 2018).
    # Tenure start, best source first:
    #   1. broker_effective_date — the carrier's "broker of record since" date
    #      (authoritative; Ambetter provides it for the whole book)
    #   2. first_seen — first month the client appears in our HealthSherpa data
    #   3. earliest snapshot month — floor for portal-only business with neither
    _earliest_month = min(months.keys())
    _latest_month   = max(months.keys())
    _latest_y, _latest_m = int(_latest_month[:4]), int(_latest_month[5:7])

    def _tenure_start(row):
        """The date the client became OURS (broker-of-record / first seen)."""
        bed = row.get("broker_effective_date")
        if pd.notna(bed):
            return pd.Timestamp(bed)
        fs = row.get("first_seen")
        if isinstance(fs, str) and fs:
            try:
                start = pd.Timestamp(fs + "-01")
            except Exception:
                start = None
            if start is not None:
                # first_seen is month-granular; if they signed during that same
                # month, the submission date gives the real day.
                sub = pd.to_datetime(row.get("submission_date"), errors="coerce")
                if pd.notna(sub) and (sub.year, sub.month) == (start.year, start.month):
                    return sub.normalize()
                return start
        return pd.Timestamp(_earliest_month + "-01")

    if not all_clients.empty:
        all_clients["client_since"] = all_clients.apply(_tenure_start, axis=1)
        _cs = pd.to_datetime(all_clients["client_since"], errors="coerce")
        # COMPLETED months since client_since (day-aware) — "4" used to mean
        # "their 4th calendar month", which read as double the real tenure
        # (Ethan 2026-07-10: "why does it say 4 months when May was not 4
        # months ago"). A brand-new client is 0 (i.e. under a month).
        _now = pd.Timestamp.today().normalize()
        all_clients["months_on_book"] = ((_now.year - _cs.dt.year) * 12
                                         + (_now.month - _cs.dt.month)
                                         - (_now.day < _cs.dt.day).astype(int)).clip(lower=0)

    # Cancellation reason for the Re-Engage view: use HealthSherpa's own notes
    # ("Canceled at member's request" etc.) when present, else a derived
    # "Lapsed — <carrier>" for carrier-truth lapses.
    if not all_clients.empty:
        _churn = all_clients["status"].isin(["Cancelled", "Terminated"])
        # Preserve any reason already set upstream (e.g. "Verification expired").
        _existing = (all_clients["cancel_reason"].fillna("").astype(str).str.strip()
                     if "cancel_reason" in all_clients.columns
                     else pd.Series("", index=all_clients.index))
        _notes = (all_clients["cancel_notes"].fillna("").astype(str).str.strip()
                  if "cancel_notes" in all_clients.columns
                  else pd.Series("", index=all_clients.index))
        _notes = _notes.replace({"nan": "", "-": "", "None": ""})
        _derived = "Lapsed — " + all_clients["carrier"].astype(str)

        # AOR-taken: the current agent of record is someone other than Ethan
        # (NPN 21457938). These clients usually still have ACTIVE coverage — they
        # just moved to another agent — so flag them distinctly for win-back.
        if "policy_aor" in all_clients.columns:
            _aor = all_clients["policy_aor"].fillna("").astype(str)
            _aor_name = _aor.str.replace(r"\s*\(NPN.*$", "", regex=True).str.strip()
            _aor_taken = ((_aor.str.strip() != "")
                          & ~_aor.str.contains("None", case=False, na=False)
                          & ~_aor.str.contains(_NPN, na=False)
                          & ~_aor.str.contains(_FN, case=False, na=False)
                          & (_aor_name != ""))
        else:
            _aor_name  = pd.Series("", index=all_clients.index)
            _aor_taken = pd.Series(False, index=all_clients.index)

        # ACCURACY (Ethan 2026-07-13, chose "pull them out as taken"): a client
        # whose CURRENT agent-of-record is another agent is not part of the
        # active book — even if the policy is still Effectuated, someone else
        # gets paid. Reclassify every taken client as churned so they drop out
        # of active counts / KPIs and flow to Re-Engage, exactly matching the
        # authoritative AOR Defense list. (all_clients is already ownership-
        # filtered, so a foreign policy_aor here means "I enrolled them and lost
        # the AOR" — never a never-mine client.) The real takeover date set
        # below places each in the correct month for trends.
        #
        # MARKETPLACE WINS: mark taken at the PERSON level — if ANY of a client's
        # rows shows a foreign marketplace AOR, the whole client is taken. Carrier
        # truth can add a second row stamped with MY name from the carrier book
        # (which lags the marketplace), and that must not mask the steal (Ethan
        # 2026-07-13: Kristen Southern — marketplace shows David Raigoza, BCBS
        # book still shows me; she IS taken. "Marketplace wins, period.")
        _pk = _person_key_series(all_clients)   # groups a person's rows, splits same-name strangers by sid
        _taken_people = set(_pk[_aor_taken])
        _who_by_person = {}
        for _pkv, _nm in zip(_pk[_aor_taken], _aor_name[_aor_taken]):
            _who_by_person.setdefault(_pkv, _nm)
        _aor_taken = _pk.isin(_taken_people)                     # propagate to all rows
        _aor_name = _pk.map(_who_by_person).where(_aor_taken, _aor_name)  # carry agent name
        _newly_taken = _aor_taken & ~all_clients["status"].isin(["Cancelled", "Terminated"])
        all_clients.loc[_newly_taken, "status"] = "Terminated"
        _churn = all_clients["status"].isin(["Cancelled", "Terminated"])

        # When the AOR change registered. This date becomes the Term Date, which
        # drives Re-Engage's "lost N days ago". It MUST match how AOR Defense
        # dates each steal — that page uses the HealthSherpa AOR-at-risk scrape's
        # `last_synced` (the day the change was detected). Using last_ede_sync
        # (the last data refresh, ~always 2-3 days ago) made Re-Engage show every
        # taken client as "lost 3 days ago" and disagree with AOR Defense
        # (Ethan 2026-07-13: Aritha Woods — AOR Defense 90 days, Re-Engage 3).
        import json as _json
        _risk_path = Path(__file__).resolve().parent.parent / "data" / "aor_at_risk.json"
        _steal_by_xid, _steal_by_name = {}, {}
        try:
            for _e in _json.loads(_risk_path.read_text()):
                _ls = pd.to_datetime(_e.get("last_synced", ""), errors="coerce")
                if pd.isna(_ls):
                    continue
                _xid = str(_e.get("exchange_id", "")).strip()
                if _xid:
                    _steal_by_xid[_xid] = _ls
                _pn = str(_e.get("name", "")).split()
                if _pn:
                    _steal_by_name[re.sub(r"[^a-z]", "", (_pn[0] + _pn[-1]).lower())] = _ls
        except Exception:
            pass

        def _steal_date(row):
            _x = re.sub(r"\.0$", "", str(row.get("ffm_app_id", "")).strip())
            if _x in _steal_by_xid:
                return _steal_by_xid[_x]
            _p = f"{row.get('first_name','')} {row.get('last_name','')}".split()
            if _p:
                _k = re.sub(r"[^a-z]", "", (_p[0] + _p[-1]).lower())
                if _k in _steal_by_name:
                    return _steal_by_name[_k]
            return pd.NaT

        _ede = (pd.to_datetime(all_clients["last_ede_sync"], errors="coerce")
                if "last_ede_sync" in all_clients.columns
                else pd.Series(pd.NaT, index=all_clients.index))
        # scrape's detection date is primary; last_ede_sync only as a fallback
        # for AOR clients the scrape never listed (known_aor-only) — matching
        # AOR Defense, which also falls back to last_ede_sync for those.
        _sync = all_clients.apply(_steal_date, axis=1).fillna(_ede)

        _keep_existing = _existing.str.contains("Verification expired", na=False)
        all_clients["cancel_reason"] = ""
        all_clients.loc[_churn, "cancel_reason"] = _notes.where(_notes != "", _derived)[_churn]
        # AOR-taken takes precedence (most actionable), except where a verification
        # expiry was already recorded. (No date in the reason — it goes in Term Date.)
        _aor_rows = _churn & _aor_taken & ~_keep_existing
        all_clients.loc[_aor_rows, "cancel_reason"] = ("AOR taken — " + _aor_name)[_aor_rows]
        # The AOR date IS the term date — the real day they left the book, not the
        # carrier-truth detection date. Mark it non-estimated.
        if "term_date" not in all_clients.columns:
            all_clients["term_date"] = pd.NaT
        _aor_dated = _aor_rows & _sync.notna()
        all_clients.loc[_aor_dated, "term_date"] = _sync[_aor_dated]
        if "term_estimated" in all_clients.columns:
            all_clients.loc[_aor_dated, "term_estimated"] = False
        # Restore preserved upstream reasons.
        all_clients.loc[_keep_existing, "cancel_reason"] = _existing[_keep_existing]

    # Compute diff to identify missing clients (those who dropped off last month)
    if prior_month:
        diff = compute_diff(months[prior_month], months[latest_month])
        missing_df = diff["missing"]
        print(f"  Comparing {prior_month} → {latest_month}: "
              f"{len(diff['new'])} new, {len(missing_df)} missing, {len(diff['stayed'])} stayed")
    else:
        missing_df = pd.DataFrame()
        print("  Only one month of data.")

    # Manually-added clients (data/manual_clients.json): active clients Ethan is the
    # agent of record for but who are in NO export — plans "not submitted through
    # HealthSherpa" (AOR transfers). They can't come through ingest, so inject them
    # here as Effectuated — AFTER every filter/override/re-activation and right before
    # the active split — so nothing drops them. Skips any name_key already present (no
    # double-count once they show up in a real export; delete them from the JSON then).
    # Guarded so a bad file can never break the report.
    try:
        import json as _json_mc
        from tracker.ingest import normalize_name
        _mcp = Path(__file__).resolve().parent.parent / "data" / "manual_clients.json"
        if _mcp.exists() and "status" in all_clients.columns and not all_clients.empty:
            _mc = _json_mc.loads(_mcp.read_text())
            _ACT_MC = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
            _idx_by_key = {}
            if "name_key" in all_clients.columns:
                for _i2, _k2 in all_clients["name_key"].items():
                    _idx_by_key.setdefault(str(_k2), _i2)
            _latest = max(months.keys())
            _added = _react = _already = 0
            _mrows = []
            for _c in _mc:
                _f = str(_c.get("first", "")).strip(); _l = str(_c.get("last", "")).strip()
                if not (_f and _l):
                    continue
                _nk = normalize_name(f"{_f} {_l}")
                _eff = pd.to_datetime(_c.get("effective"), errors="coerce")
                _i = _idx_by_key.get(_nk)
                if _i is not None:
                    # Already in the roster (from an earlier month). If it got dropped to
                    # Cancelled/Terminated (they fell off the export), re-activate it; if it's
                    # already active, leave it (no double-count).
                    if str(all_clients.at[_i, "status"]) in _ACT_MC:
                        _already += 1
                    else:
                        all_clients.at[_i, "status"] = "Effectuated"
                        if "cancel_reason" in all_clients.columns: all_clients.at[_i, "cancel_reason"] = ""
                        if "cancel_notes" in all_clients.columns: all_clients.at[_i, "cancel_notes"] = "manual: active in HealthSherpa (AOR Ethan)"
                        if "term_date" in all_clients.columns: all_clients.at[_i, "term_date"] = pd.NaT
                        if "last_seen" in all_clients.columns: all_clients.at[_i, "last_seen"] = _latest
                        if "source" in all_clients.columns: all_clients.at[_i, "source"] = "manual"
                        _react += 1
                    continue
                _mob = None
                if pd.notna(_eff):
                    _mob = max(0, (int(_latest[:4]) - _eff.year) * 12 + (int(_latest[5:7]) - _eff.month))
                _mrows.append({
                    "name_key": _nk, "client_key": "", "first_name": _f, "last_name": _l,
                    "carrier": str(_c.get("carrier", "")).strip(),
                    "effective_date": _eff, "current_effective": _eff, "term_date": pd.NaT,
                    "status": "Effectuated", "state": str(_c.get("state", "")).strip().upper(),
                    "ffm_app_id": "", "ffm_subscriber_id": "", "email": "", "phone": "",
                    "cancel_reason": "", "cancel_notes": "",
                    "net_premium": float(_c.get("net_premium", 0) or 0),
                    "applicant_count": float(_c.get("members", 1) or 1),
                    "first_seen": (_eff.strftime("%Y-%m") if pd.notna(_eff) else _latest),
                    "last_seen": _latest, "last_active": _latest, "months_on_book": _mob,
                    "dmi_outstanding": False, "dmi_expired": False, "svi_outstanding": False,
                    "svi_expired": False, "followup_docs": "",
                    "policy_aor": "Ethan Slade (NPN: 21457938)", "last_ede_sync": "",
                    "policy_number": "", "submission_date": "", "source": "manual",
                })
                _added += 1
            if _mrows:
                _mdf = pd.DataFrame(_mrows).reindex(columns=all_clients.columns)
                all_clients = pd.concat([all_clients, _mdf], ignore_index=True)
            print(f"  Manual-add: {_added} injected, {_react} re-activated, {_already} already-active "
                  f"(AOR clients not in current export)")
    except Exception as _e:
        print(f"  (manual-add injection skipped: {_e})")

    # All Active: Effectuated, PendingEffectuation, or PendingFollowups
    # Must match _ACTIVE_STATUSES in dashboard.py so member counts agree.
    active_pending = all_clients[
        all_clients["status"].isin(["Effectuated", "PendingEffectuation", "PendingFollowups"])
    ].copy() if "status" in all_clients.columns else pd.DataFrame()

    # All Missing/Cancelled: Cancelled/Terminated + clients who dropped off (missing diff)
    cancelled = all_clients[
        all_clients["status"].isin(["Cancelled", "Terminated"])
    ].copy() if "status" in all_clients.columns else pd.DataFrame()

    _active_statuses = {"Effectuated", "PendingEffectuation", "PendingFollowups"}

    if not missing_df.empty:
        missing_df = _filter_by_appointments(missing_df, appointments)
        existing_keys = set(cancelled["name_key"].dropna()) if "name_key" in cancelled.columns else set()
        extra = missing_df[
            ~missing_df.get("name_key", pd.Series(dtype=str)).isin(existing_keys)
        ].copy()
        # Anyone who dropped off the export is treated as Cancelled regardless of
        # their last known status (covers Pending clients who never effectuated)
        if "status" in extra.columns:
            extra.loc[extra["status"].isin(_active_statuses), "status"] = "Cancelled"
        cancelled_missing = pd.concat([cancelled, extra], ignore_index=True)
    else:
        cancelled_missing = cancelled

    sheet_url = settings.get("sheet_url", "")
    if not sheet_url:
        print("No sheet_url in config/settings.yaml.")
        return

    impersonation_target = settings.get("impersonation_target", "")
    if not impersonation_target:
        print("No impersonation_target in config/settings.yaml.")
        return

    # Daily tracker should only count carriers/states the agent is appointed
    # with (cancellations still count for the day they were submitted, but
    # non-appointed business is excluded entirely).
    months_appointed = {
        m: _filter_by_appointments(df, appointments) for m, df in months.items()
    }

    # Supplemental / ancillary book (dental, vision, STM, accident, …) across
    # carriers. Premium only for now — commission rates TBD.
    from tracker.supplemental import load_supplemental
    supp = load_supplemental()
    supp_display = _build_supplemental_display(supp)
    if not supp_display.empty:
        print(f"  Supplemental book: {len(supp_display)} policies "
              f"({(supp['status'] == 'Active').sum()} active)")

    # Health-plan policies behind on payment (Ambetter paid-through passed /
    # Oscar balance owed) — active but in grace, savable with a payment call.
    from tracker.pastdue import load_health_pastdue
    pastdue = load_health_pastdue()
    pastdue_display = _build_pastdue_display(pastdue)
    if not pastdue_display.empty:
        print(f"  Health past-due: {len(pastdue_display)} active policies behind on payment")

    # Commission gaps: active clients with no / stopped commission payments,
    # by reading the actual payments sheet and reconciling against the book.
    commission_gaps = None
    ambetter_disputes = None
    _payments = None
    _pay_url = settings.get("payments_sheet_url")
    if _pay_url:
        try:
            from tracker.commissions import parse_payments_sheet, build_gaps
            from tracker.sheets import _open_sheet
            _payments = parse_payments_sheet(_open_sheet(_pay_url, impersonation_target))
            # Gaps = clients Ethan is the agent for but isn't paid on. Exclude any
            # whose AOR moved to another agent (he's correctly unpaid — not a
            # dispute). Blank AOR kept (could be his). Person dedup keeps the rest.
            _gap_active = active_pending
            if "policy_aor" in active_pending.columns:
                _a = active_pending["policy_aor"].fillna("").astype(str)
                _not_mine = (_a.str.strip().ne("") & ~_a.str.contains("None")
                             & ~_a.str.contains(_NPN)
                             & ~(_a.str.contains(_FN, case=False) & _a.str.contains(_LN, case=False)))
                _gap_active = active_pending[~_not_mine]
            # Also drop confirmed-AOR-changed clients whose policy_aor field lags.
            from tracker.commissions import drop_aor_changed
            _gap_active = drop_aor_changed(_gap_active)
            commission_gaps = build_gaps(_gap_active, _payments)
            # Policy-number cross-reference: flag who was truly never paid (carrier
            # policy # never appears on a statement) vs paid under a different member.
            from tracker.commissions import audit_gaps
            _books = str(Path(__file__).resolve().parent.parent / "carrier_books")
            commission_gaps = audit_gaps(commission_gaps, _payments, _books)
            # NOTE: "Too new" rows are kept IN this tab (clearly labeled) on purpose —
            # the cloud app reads the label back from here to hide them from the page.
            # Removing them here would strip the label and make them reappear blank.
            if commission_gaps is not None and not commission_gaps.empty:
                _disp = (commission_gaps["Dispute"] == "✅ Dispute").sum() if "Dispute" in commission_gaps.columns else 0
                print(f"  Commission gaps: {len(commission_gaps)} active clients with a payment gap "
                      f"({(commission_gaps['Gap'] == 'Never paid').sum()} never paid, "
                      f"{(commission_gaps['Gap'] == 'Stopped').sum()} stopped) · "
                      f"{_disp} policy-verified disputes")

            # Ambetter disputes: cross-reference the carrier's own export (Eligible
            # for Commission = Yes + member paid-through current) against actual
            # payments — "carrier says owed, but I was never paid."
            _amb_book = Path(__file__).resolve().parent.parent / "carrier_books" / "ambetter.csv"
            if _amb_book.exists():
                from tracker.carrier_status import (parse_ambetter_export,
                                                    classify_ambetter, dispute_display)
                _amb = parse_ambetter_export(str(_amb_book))
                _clf = classify_ambetter(_amb, _payments, book=active_pending)
                ambetter_disputes = dispute_display(_clf)
                if ambetter_disputes is not None and not ambetter_disputes.empty:
                    print(f"  Ambetter disputes: {len(ambetter_disputes)} policies the carrier "
                          f"confirms owed but show no payment")
        except Exception as e:
            print(f"  (commission gaps / Ambetter disputes skipped: {e})")

    # Fill missing carrier policy IDs. HealthSherpa's issuer_assigned_policy_id
    # covers only part of the book — the carrier portals (Ambetter/Oscar/Anthem)
    # and the commission statements know the rest. Matched per-carrier by name
    # so a same-name client can't inherit another carrier's number. Priority:
    # HealthSherpa (kept) > carrier book > statement policy id.
    if not all_clients.empty and "policy_number" in all_clients.columns:
        try:
            from tracker.commissions import (carrier_policy_map, _route_carrier,
                                             _norm_id, _person_key)
            _pmap = carrier_policy_map(str(Path(__file__).resolve().parent.parent / "carrier_books"))
            _stmt = {}
            if _payments is not None and len(_payments):
                for _, pr in _payments.iterrows():
                    _rt = _route_carrier(pr.get("carrier"))
                    _pid = str(pr.get("policy_id") or "").strip()
                    if _rt and _pid:
                        _stmt.setdefault((_rt, pr.get("name_key")), _pid)
            _filled_book = _filled_stmt = 0
            for _i, _r in all_clients.iterrows():
                _cur = str(_r.get("policy_number") or "").strip()
                if _cur and _cur.lower() not in ("nan", "none"):
                    continue
                _rt = _route_carrier(_r.get("carrier"))
                if not _rt:
                    continue
                _f, _l = str(_r.get("first_name") or ""), str(_r.get("last_name") or "")
                _pid = _pmap.get(_rt, {}).get(_norm_id(_l + _f)[:12])
                if _pid:
                    _filled_book += 1
                else:
                    _pid = _stmt.get((_rt, _person_key(_f, _l)))
                    if _pid:
                        _filled_stmt += 1
                if _pid:
                    all_clients.at[_i, "policy_number"] = _pid
            if _filled_book or _filled_stmt:
                print(f"  Policy IDs: filled {_filled_book} from carrier books, "
                      f"{_filled_stmt} from commission statements")
        except Exception as e:
            print(f"  (policy-id fill skipped: {e})")

    # HealthSherpa verification follow-ups (open = save the subsidy; expired = lost).
    follow_ups = _build_follow_ups(all_clients)
    if follow_ups is not None and not follow_ups.empty:
        print(f"  Follow-ups: {len(follow_ups)} clients "
              f"({(follow_ups['Status'] == 'Open').sum()} open, "
              f"{(follow_ups['Status'] == 'Expired').sum()} expired)")

    # BOOK-LEVEL RACE GUARD (#1, Ethan 2026-07-24): the fresh HealthSherpa snapshot
    # is ground truth for who is active-and-ours RIGHT NOW. A client that snapshot
    # shows Effectuated/Pending with policy_aor = us CANNOT be an "AOR taken" loss —
    # that flag came from a stale/lagging signal (a won-back client whose export row
    # still names the old agent, an aor_changed.json entry not yet cleared, a
    # transient build race). Revert those to active in the BOOK itself, before it is
    # pushed — the old race guard only scrubbed the text alert, after the push.
    # SCOPED to "AOR taken" only: carrier-truth lapses and verification-expired are
    # authoritative real losses and are left alone; hand-confirmed steals
    # (data/aor_changed.json) are also left alone (the agent verified those).
    try:
        import glob as _glob_rg
        import re as _re_rg
        _hs_snaps = sorted(_glob_rg.glob(str(Path(settings["snapshot_dir"]) / "*healthsherpa*.parquet")))
        if _hs_snaps and "cancel_reason" in all_clients.columns and not all_clients.empty:
            def _lf_rg(f, l):
                return _re_rg.sub(r"[^a-z]", "", str(l).lower()) + _re_rg.sub(r"[^a-z]", "", str(f).lower())
            _snap_rg = pd.read_parquet(_hs_snaps[-1])
            _mine_now, _status_now = set(), {}
            for _, _r in _snap_rg.iterrows():
                if str(_r.get("status") or "") not in ("Effectuated", "PendingEffectuation", "PendingFollowups"):
                    continue
                _aor = str(_r.get("policy_aor") or "")
                if _NPN in _aor or (_LN in _aor.lower() and _FN in _aor.lower()):
                    _k = _lf_rg(_r.get("first_name", ""), _r.get("last_name", ""))
                    _mine_now.add(_k)
                    _status_now.setdefault(_k, str(_r.get("status")))
            try:
                from tracker.commissions import aor_changed_keys
                _confirmed = set(aor_changed_keys())
            except Exception:
                _confirmed = set()
            if _mine_now:
                _rk = all_clients.apply(lambda r: _lf_rg(r.get("first_name", ""), r.get("last_name", "")), axis=1)
                _reason = all_clients["cancel_reason"].fillna("").astype(str)
                _protect = (all_clients["status"].isin(["Cancelled", "Terminated"])
                            & _reason.str.startswith("AOR taken")
                            & _rk.isin(_mine_now) & ~_rk.isin(_confirmed))
                if _protect.any():
                    for _i in all_clients.index[_protect]:
                        all_clients.at[_i, "status"] = _status_now.get(_rk.at[_i], "Effectuated")
                        all_clients.at[_i, "cancel_reason"] = ""
                        if "term_date" in all_clients.columns:
                            all_clients.at[_i, "term_date"] = pd.NaT
                    _rn = sorted({f"{all_clients.at[_i, 'first_name']} {all_clients.at[_i, 'last_name']}".strip()
                                  for _i in all_clients.index[_protect]})
                    print(f"  Book race guard: restored {int(_protect.sum())} 'AOR taken' client(s) the "
                          f"fresh HealthSherpa snapshot shows active-and-ours "
                          f"({', '.join(_rn[:8])}{'…' if len(_rn) > 8 else ''})")
    except Exception as _e:
        print(f"  (book race guard skipped: {_e})")

    # AOR Defense: the scraped at-risk list merged with the book — split into
    # Taken (another agent filed an AOR change — fight these) vs Disconnected
    # (usually still ours; just needs a Reconnect). Texts on NEWLY-taken only.
    aor_defense = None
    try:
        from tracker.aor_defense import (build_aor_defense, alert_new_aor_changes,
                                         build_silent_dropoffs)
        import re as _re_sd
        def _fl_key(name):
            p = str(name).split()
            return _re_sd.sub(r"[^a-z]", "", (p[0] + p[-1]).lower()) if p else ""
        aor_defense = build_aor_defense(appointments=appointments)

        # Silent drop-offs: clients who were ACTIVE in the last HS export, then
        # vanished from it and got carrier-truth-lapsed — the hidden-AOR pattern
        # (Roderick Bell). The scrape can't see them (no HS row left), so flag
        # them as "Suspected" on AOR Defense for a quick verify instead of
        # letting them become silent "Lapsed" write-offs.
        try:
            _susp = build_silent_dropoffs(all_clients, months)
            if _susp is not None and not _susp.empty:
                if aor_defense is not None and not aor_defense.empty:
                    _have = set(aor_defense["Client"].apply(_fl_key))
                    _susp = _susp[~_susp["Client"].apply(_fl_key).isin(_have)]
                if not _susp.empty:
                    aor_defense = (pd.concat([aor_defense, _susp], ignore_index=True)
                                   if aor_defense is not None and not aor_defense.empty
                                   else _susp.reset_index(drop=True))
                    print(f"  Silent drop-off watch: {len(_susp)} active client(s) vanished "
                          f"from HS — flagged Suspected AOR ({', '.join(_susp['Client'].head(6))}"
                          f"{'…' if len(_susp) > 6 else ''})")
        except Exception as _e:
            print(f"  (silent drop-off watch skipped: {_e})")

        if aor_defense is not None and not aor_defense.empty:
            _t = int((aor_defense["Type"] == "Taken").sum())
            _d = int((aor_defense["Type"] == "Disconnected").sum())
            _s = int((aor_defense["Type"] == "Suspected").sum())
            _open = int(((aor_defense["Type"] == "Taken") & (aor_defense["Handled"] == "")).sum())
            print(f"  AOR Defense: {len(aor_defense)} at-risk ({_t} taken / {_d} disconnected / "
                  f"{_s} suspected · {_open} taken still open)")
            # Texts fire on confirmed steals only — never on unverified "Suspected".
            alert_new_aor_changes(aor_defense[aor_defense["Type"] == "Taken"])
    except Exception as e:
        print(f"  (AOR defense skipped: {e})")

    # Data freshness — when each source file was last pulled (shown in Settings).
    freshness = None
    try:
        from tracker.freshness import build_freshness
        freshness = build_freshness()
    except Exception as e:
        print(f"  (freshness skipped: {e})")

    print("Pushing to Google Sheets...")
    update_sheet(
        sheet_url=sheet_url,
        impersonation_target=impersonation_target,
        tab_names=settings["tabs"],
        all_clients=_sort(_select(all_clients, _ALL_CLIENTS_COLS)),
        active_pending_df=_sort_by_date(_select(active_pending, _ACTIVE_COLS)),
        cancelled_missing_df=_sort_by_term_date_desc(_select(cancelled_missing, _ALL_CLIENTS_COLS)),
        months=months_appointed,
        supplemental_df=supp_display,
        health_pastdue_df=pastdue_display,
        commission_gaps_df=commission_gaps,
        ambetter_disputes_df=ambetter_disputes,
        follow_ups_df=follow_ups,
        aor_defense_df=aor_defense,
        freshness_df=freshness,
    )

    # On a new HealthSherpa upload, text the agent the summary: new sales + who
    # newly fell off (cancelled / behind on payment / taken by another agent).
    try:
        _upload_summary(all_clients, pastdue, settings["snapshot_dir"])
    except Exception as e:
        print(f"  (upload summary step skipped: {e})")

    print("Done.")
