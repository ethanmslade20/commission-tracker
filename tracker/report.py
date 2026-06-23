"""
Builds all DataFrames from snapshots and pushes them to Google Sheets.
"""

import re
import json
import unicodedata
from pathlib import Path

import pandas as pd

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
                     "client_since", "cancel_reason", "term_estimated"]

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


def run_report(settings: dict) -> None:
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
    all_clients, _amb = apply_ambetter_truth(all_clients)
    if _amb.get("applied"):
        print(f"  Ambetter portal truth: +{_amb['added_from_portal']} added, "
              f"{_amb['cancelled_termed'] + _amb['cancelled_dropped']} marked cancelled "
              f"({_amb['protected_new_sales']} new sales protected)")
    all_clients, _osc = apply_oscar_truth(all_clients)
    if _osc.get("applied"):
        print(f"  Oscar portal truth: +{_osc['added_from_portal']} added, "
              f"{_osc['cancelled_inactive'] + _osc['cancelled_dropped']} marked cancelled "
              f"({_osc['protected_new_sales']} new sales protected)")
    all_clients, _uhc = apply_uhc_truth(all_clients)
    if _uhc.get("applied"):
        print(f"  UHC portal truth: +{_uhc['added_policies']} added, "
              f"{_uhc['cancelled_lapsed'] + _uhc['cancelled_dropped']} marked cancelled "
              f"({_uhc['protected_new_sales']} new sales protected)")
    all_clients, _ant = apply_anthem_truth(all_clients)
    if _ant.get("applied"):
        print(f"  Anthem portal truth: +{_ant['added_policies']} added, "
              f"{_ant['cancelled_lapsed'] + _ant['cancelled_dropped']} marked cancelled "
              f"({_ant['protected_new_sales']} new sales protected)")

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
        _notes = (all_clients["cancel_notes"].fillna("").astype(str).str.strip()
                  if "cancel_notes" in all_clients.columns
                  else pd.Series("", index=all_clients.index))
        _notes = _notes.replace({"nan": "", "-": "", "None": ""})
        _derived = "Lapsed — " + all_clients["carrier"].astype(str)
        all_clients["cancel_reason"] = ""
        all_clients.loc[_churn, "cancel_reason"] = _notes.where(_notes != "", _derived)[_churn]

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
    _pay_url = settings.get("payments_sheet_url")
    if _pay_url:
        try:
            from tracker.commissions import parse_payments_sheet, build_gaps
            from tracker.sheets import _open_sheet
            _payments = parse_payments_sheet(_open_sheet(_pay_url, impersonation_target))
            commission_gaps = build_gaps(active_pending, _payments)
            if commission_gaps is not None and not commission_gaps.empty:
                print(f"  Commission gaps: {len(commission_gaps)} active clients with a payment gap "
                      f"({(commission_gaps['Gap'] == 'Never paid').sum()} never paid, "
                      f"{(commission_gaps['Gap'] == 'Stopped').sum()} stopped)")
        except Exception as e:
            print(f"  (commission gaps skipped: {e})")

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
    )

    # Text the agent any clients who newly dropped into Re-Engage this run.
    try:
        _alert_new_lapses(all_clients)
    except Exception as e:
        print(f"  (lapse alert step skipped: {e})")

    print("Done.")
