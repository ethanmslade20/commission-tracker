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

from tracker.diff import build_all_clients, compute_diff
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
                     "status", "state", "ffm_app_id", "net_premium", "applicant_count", "months_on_book",
                     "client_since", "cancel_reason", "term_estimated", "phone", "email"]

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
    h = hashlib.md5(Path(hs[-1]).read_bytes()).hexdigest()
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
    def _key(f, l):
        s = unicodedata.normalize("NFKD", f"{f} {l}").encode("ascii", "ignore").decode().lower()
        return re.sub(r"[^a-z]", "", s)
    def _disp(f, l):
        return f"{f} {l}".strip().title()

    lost, vexp, aor, pol, polmem = {}, {}, {}, set(), {}
    active_mine = {}   # currently active AND credited to the agent — win-back proof
    for _, r in all_clients.iterrows():
        f, l = r.get("first_name", ""), r.get("last_name", "")
        k = _key(f, l)
        if not k:
            continue
        st = str(r.get("status") or "")
        if st in ("Cancelled", "Terminated"):
            # Expired DMI/SVI verification ≠ cancelled: coverage is usually still
            # active with a termination date pending, so the client is SAVEABLE
            # (Ahmed Elzubair 2026-07-10 — Effectuated + paid, terming 7/31).
            if "Verification expired" in str(r.get("cancel_reason") or ""):
                vexp[k] = _disp(f, l)
            else:
                lost[k] = _disp(f, l)
        if st in ("Effectuated", "PendingEffectuation", "PendingFollowups"):
            _a = r.get("policy_aor")
            a = "" if pd.isna(_a) else str(_a)
            # A missing AOR is unknown, NOT another agent — "nan"/"none" text
            # slipping through here caused false "taken" alerts (2026-07-06).
            if a.strip().lower() not in ("", "none", "nan") and NPN not in a and not _is_e(a):
                aor[k] = _disp(f, l)
            else:
                active_mine[k] = _disp(f, l)
        pid = re.sub(r"\.0$", "", str(r.get("ffm_app_id") or "").strip())
        if pid and pid.lower() != "nan":
            pol.add(pid)
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

    def _load(name):
        p = _data / name
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    base_lost, base_aor, base_pd, base_pol = (_load("known_lapsed.json"), _load("known_aor.json"),
                                              _load("known_pastdue.json"), _load("known_policies.json"))
    first_run = base_pol is None

    def _new(cur, base):
        return [] if base is None else [v for k, v in cur.items() if k not in base]
    new_lost = _new(lost, base_lost)
    # Expired verifications share the known_lapsed baseline so a client already
    # texted under either label never re-alerts when they move between buckets.
    new_vexp = _new(vexp, base_lost)
    new_aor = _new(aor, base_aor)
    new_pd = _new(pdue, base_pd)
    # Win-backs: was lost/taken at the last text, now active AND his again.
    # (Ethan 2026-07-08: "if I ever get a person back that was lost or win them
    # back from an AOR I want you to include that in the text".)
    won_lost = [v for k, v in (base_lost or {}).items()
                if k not in lost and k in active_mine]
    won_aor = [v for k, v in (base_aor or {}).items()
               if k not in aor and k in active_mine]
    base_pol_set = set(base_pol or [])
    new_pol = [p for p in pol if p not in base_pol_set]
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
        marker.write_text(h)

    if first_run:
        _save_state()
        print("  Upload summary: baselines initialized (no text on first run).")
        return

    def _fmt(names):
        return ", ".join(names[:6]) + (f" +{len(names) - 6} more" if len(names) > 6 else "")
    d = today.strftime("%b %d")
    lines = [f"HealthSherpa updated · {d}",
             f"✅ Signed: {new_pol_n} new policies / {new_mem} members"]
    total = len(new_lost) + len(new_pd) + len(new_aor)
    if total == 0:
        if not new_vexp:
            lines.append("⬇️ Lost 0 clients — all clear.")
    else:
        lines.append(f"⬇️ Lost {total} clients:")
        if new_lost:
            lines.append(f" • Cancelled (→ Re-Engage): {_fmt(new_lost)}")
        if new_pd:
            lines.append(f" • Behind on payment: {_fmt(new_pd)}")
        if new_aor:
            lines.append(f" • Taken by another agent: {_fmt(new_aor)}")
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

    # Mark confirmed-AOR-changed clients whose HealthSherpa policy_aor field still
    # lags the exchange (e.g. Tammy Bennett -> Albert Rincon). Stamping policy_aor
    # here means EVERY AOR filter treats them as another agent's — including the
    # cloud app, which reads this written value and can't see data/aor_changed.json
    # (gitignored). 'Marketplace disconnected' clients are NOT on the list, so they
    # keep policy_aor=Ethan and are left alone.
    try:
        from tracker.commissions import aor_changed_keys
        _chg = aor_changed_keys()
        if _chg and "policy_aor" in all_clients.columns:
            import re as _re_aor
            def _ck(r):
                l = _re_aor.sub(r"[^a-z]", "", str(r.get("last_name", "")).lower())
                f = _re_aor.sub(r"[^a-z]", "", str(r.get("first_name", "")).lower())
                return l + f
            _m = all_clients.apply(lambda r: _ck(r) in _chg, axis=1)
            if _m.any():
                all_clients.loc[_m, "policy_aor"] = "AOR changed (another agent)"
                print(f"  AOR-changed override: marked {int(_m.sum())} client(s) as another agent's")
    except Exception as _e:
        print(f"  (AOR-changed override skipped: {_e})")

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
                                        apply_uhc_truth, apply_anthem_truth)

    # A malformed carrier file (changed export format, wrong download) must
    # never kill the whole report — skip that carrier loudly and keep going.
    def _apply_truth(fn, label, fmt):
        nonlocal all_clients
        try:
            all_clients, _s = fn(all_clients)
            if _s.get("applied"):
                print(f"  {label} portal truth: " + fmt(_s))
        except Exception as e:
            print(f"  !! {label} book SKIPPED — {type(e).__name__}: {e}")
            print(f"     Check carrier_books/ for a bad/changed {label} export; "
                  f"book statuses for {label} were left as-is this run.")

    _apply_truth(apply_ambetter_truth, "Ambetter",
                 lambda s: f"+{s['added_from_portal']} added, "
                           f"{s['cancelled_termed'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected)")
    _apply_truth(apply_oscar_truth, "Oscar",
                 lambda s: f"+{s['added_from_portal']} added, "
                           f"{s['cancelled_inactive'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected)")
    _apply_truth(apply_uhc_truth, "UHC",
                 lambda s: f"+{s['added_policies']} added, "
                           f"{s['cancelled_lapsed'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected)")
    _apply_truth(apply_anthem_truth, "Anthem",
                 lambda s: f"+{s['added_policies']} added, "
                           f"{s['cancelled_lapsed'] + s['cancelled_dropped']} marked cancelled "
                           f"({s['protected_new_sales']} new sales protected)")

    # HealthSherpa verification truth: an EXPIRED DMI/SVI follow-up means the
    # subsidy / eligibility is lost, so the client is effectively gone. Mark them
    # Cancelled so they drop off active + past-due and flow into Re-Engage outreach.
    if not all_clients.empty:
        def _numcol(name):
            if name in all_clients.columns:
                return pd.to_numeric(all_clients[name], errors="coerce").fillna(0)
            return pd.Series(0.0, index=all_clients.index)
        _exp = (_numcol("dmi_expired") > 0) | (_numcol("svi_expired") > 0)
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
                return pd.Timestamp(fs + "-01")
            except Exception:
                pass
        return pd.Timestamp(_earliest_month + "-01")

    if not all_clients.empty:
        all_clients["client_since"] = all_clients.apply(_tenure_start, axis=1)
        _cs = pd.to_datetime(all_clients["client_since"], errors="coerce")
        all_clients["months_on_book"] = ((_latest_y - _cs.dt.year) * 12
                                         + (_latest_m - _cs.dt.month) + 1).clip(lower=1)

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

        # When the AOR change registered (best proxy = last Marketplace sync).
        # For AOR-taken clients this date becomes their Term Date.
        if "last_ede_sync" in all_clients.columns:
            _sync = pd.to_datetime(all_clients["last_ede_sync"], errors="coerce")
        else:
            _sync = pd.Series(pd.NaT, index=all_clients.index)

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

    # HealthSherpa verification follow-ups (open = save the subsidy; expired = lost).
    follow_ups = _build_follow_ups(all_clients)
    if follow_ups is not None and not follow_ups.empty:
        print(f"  Follow-ups: {len(follow_ups)} clients "
              f"({(follow_ups['Status'] == 'Open').sum()} open, "
              f"{(follow_ups['Status'] == 'Expired').sum()} expired)")

    # AOR Defense: the scraped at-risk list merged with the book — split into
    # Taken (another agent filed an AOR change — fight these) vs Disconnected
    # (usually still ours; just needs a Reconnect). Texts on NEWLY-taken only.
    aor_defense = None
    try:
        from tracker.aor_defense import build_aor_defense, alert_new_aor_changes
        aor_defense = build_aor_defense()
        if aor_defense is not None and not aor_defense.empty:
            _t = int((aor_defense["Type"] == "Taken").sum())
            _d = int((aor_defense["Type"] == "Disconnected").sum())
            _open = int(((aor_defense["Type"] == "Taken") & (aor_defense["Handled"] == "")).sum())
            print(f"  AOR Defense: {len(aor_defense)} at-risk ({_t} taken / {_d} disconnected · {_open} taken still open)")
            alert_new_aor_changes(aor_defense)
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
