"""
Carrier book-of-business reconciliation (Phase 1).

Cross-references a carrier's own policy export (the source of truth for that
carrier) against what the tracker currently believes is active for that
carrier. Read-only — does not touch snapshots, Sheets, or the live app.

Currently supports Ambetter's "All policies" export (zip or csv).
"""

import re
import glob
import zipfile
import unicodedata
from pathlib import Path

import pandas as pd

from tracker.ingest import load_all_snapshots
from tracker.report import _load_appointments, _filter_by_appointments

_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}


def _clean_id(x) -> str:
    return re.sub(r"[^0-9]", "", str(x))


def _name_key(first, last) -> str:
    s = f"{first} {last}".lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


def _load_carrier_csv(path: Path) -> pd.DataFrame:
    """Load a carrier export from a .zip (first .csv inside) or a .csv."""
    p = Path(path)
    if p.is_dir():
        cands = sorted(
            list(p.glob("*.zip")) + list(p.glob("*.csv")),
            key=lambda f: f.stat().st_mtime,
        )
        if not cands:
            raise FileNotFoundError(f"No .zip or .csv found in {p}")
        p = cands[-1]
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            inner = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not inner:
                raise FileNotFoundError(f"No CSV inside {p}")
            with z.open(inner[0]) as f:
                return pd.read_csv(f)
    return pd.read_csv(p)


def _email(x) -> str:
    x = str(x).strip().lower()
    return x if "@" in x else ""


def _phone(x) -> str:
    d = re.sub(r"[^0-9]", "", str(x))
    return d[-10:] if len(d) >= 10 else ""


def _tracker_active(snapshot_dir: str, carrier_substr: str) -> pd.DataFrame:
    """Tracker's active clients for a carrier (full history, appointment-filtered)."""
    months = load_all_snapshots(Path(snapshot_dir))
    allc = _filter_by_appointments(pd.concat(months.values(), ignore_index=True), _load_appointments())
    t = allc[
        allc["status"].isin(_ACTIVE)
        & allc["carrier"].astype(str).str.contains(carrier_substr, case=False, na=False)
    ].copy()
    t["nm"] = t.apply(lambda r: _name_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    t["em"] = t.get("email", "").apply(_email) if "email" in t.columns else ""
    t["ph"] = t.get("phone", "").apply(_phone) if "phone" in t.columns else ""
    t["eff"] = pd.to_datetime(t.get("effective_date"), errors="coerce")
    return t.sort_values("eff").drop_duplicates("nm", keep="last")


def reconcile_oscar(oscar_path, snapshot_dir="snapshots", out_dir=".", today=None) -> dict:
    """Compare an Oscar book export against the tracker's active Oscar book.
    Matches by name / email / phone (Oscar has no FFM subscriber ID)."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)

    t = _tracker_active(snapshot_dir, "oscar")

    o = _load_carrier_csv(oscar_path)
    o["nm"] = o["Member name"].apply(lambda n: _name_key(*str(n).split(" ", 1)) if " " in str(n) else _name_key(n, ""))
    o["em"] = o["Email"].apply(_email)
    o["ph"] = o["Phone number"].apply(_phone)
    o["start"] = pd.to_datetime(o["Coverage start date"], errors="coerce")
    o["Months On Book"] = ((today - o["start"]).dt.days / 30.44).round(1)
    o_active = o[o["Policy status"] != "Inactive"]
    o_inact = o[o["Policy status"] == "Inactive"]

    def _keys(df):
        return set(df["nm"]) | (set(df["em"]) - {""}) | (set(df["ph"]) - {""})

    A, I, T = _keys(o_active), _keys(o_inact), _keys(t)

    def _m(r, S):
        return bool(r["nm"] in S or (r["em"] in S if r["em"] else False) or (r["ph"] in S if r["ph"] else False))

    lapsed_keys = t[t.apply(lambda r: (not _m(r, A)) and _m(r, I), axis=1)]
    not_found = t[t.apply(lambda r: (not _m(r, A)) and (not _m(r, I)), axis=1)]

    lk = _keys(lapsed_keys)
    winback = o_inact[o_inact.apply(lambda r: r["nm"] in lk or r["em"] in lk or r["ph"] in lk, axis=1)].copy()
    missing = o_active[o_active.apply(lambda r: not _m(r, T), axis=1)].copy()

    _cols = ["Member name", "State", "Plan", "Coverage start date", "Coverage end date",
             "Months On Book", "Policy status", "Phone number", "Email", "Lives"]

    def _slim(df):
        return df[[c for c in _cols if c in df.columns]]

    stamp = today.strftime("%Y-%m-%d")
    f_win = out / f"oscar_winback_lapsed_{stamp}.csv"
    f_mis = out / f"oscar_missing_from_tracker_{stamp}.csv"
    f_rev = out / f"oscar_not_in_book_review_{stamp}.csv"
    _slim(winback).to_csv(f_win, index=False)
    _slim(missing).to_csv(f_mis, index=False)
    rev = not_found.copy()
    rev["months_on_book"] = ((today - rev["eff"]).dt.days / 30.44).round(1)
    rev[["first_name", "last_name", "state", "carrier", "effective_date",
         "months_on_book", "status", "ffm_app_id"]].to_csv(f_rev, index=False)

    return {
        "tracker_active": len(t),
        "oscar_active": len(o_active),
        "oscar_inactive": len(o_inact),
        "confirmed_active": int(t.apply(lambda r: _m(r, A), axis=1).sum()),
        "lapsed_winbacks": len(winback),
        "missing_from_tracker": len(missing),
        "not_in_book_review": len(not_found),
        "winback_file": str(f_win),
        "missing_file": str(f_mis),
        "review_file": str(f_rev),
    }


def _tracker_active_ambetter(snapshot_dir: str) -> pd.DataFrame:
    """The tracker's current view of active Ambetter clients (full history,
    appointment-filtered, deduped by name)."""
    months = load_all_snapshots(Path(snapshot_dir))
    allc = pd.concat(months.values(), ignore_index=True)
    allc = _filter_by_appointments(allc, _load_appointments())
    t = allc[
        allc["status"].isin(_ACTIVE)
        & allc["carrier"].astype(str).str.contains("ambetter", case=False, na=False)
    ].copy()
    t["sid"] = t["ffm_subscriber_id"].apply(_clean_id)
    t["nm"] = t.apply(lambda r: _name_key(r["first_name"], r["last_name"]), axis=1)
    t["eff"] = pd.to_datetime(t["effective_date"], errors="coerce")
    return t.sort_values("eff").drop_duplicates("nm", keep="last")


def reconcile_ambetter(ambetter_path, snapshot_dir="snapshots",
                       out_dir=".", today=None) -> dict:
    """Compare an Ambetter 'All' export against the tracker's active Ambetter
    book. Writes win-back and missing-business CSVs; returns a summary dict."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    t = _tracker_active_ambetter(snapshot_dir)

    amb = _load_carrier_csv(ambetter_path)
    amb["sid"] = amb["Exchange Subscriber ID"].apply(_clean_id)
    amb["nm"] = amb.apply(
        lambda r: _name_key(r["Insured First Name"], r["Insured Last Name"]), axis=1
    )
    amb["termed"] = pd.to_datetime(amb["Policy Term Date"], errors="coerce") < today
    amb_active, amb_termed = amb[~amb["termed"]], amb[amb["termed"]]

    aa_sid, aa_nm = set(amb_active["sid"]) - {""}, set(amb_active["nm"])
    at_sid, at_nm = set(amb_termed["sid"]) - {""}, set(amb_termed["nm"])
    t_sid, t_nm = set(t["sid"]) - {""}, set(t["nm"])

    t["in_amb_active"] = t.apply(lambda r: r["sid"] in aa_sid or r["nm"] in aa_nm, axis=1)
    t["in_amb_termed"] = t.apply(lambda r: r["sid"] in at_sid or r["nm"] in at_nm, axis=1)

    confirmed = t[t["in_amb_active"]]
    lapsed_keys = t[~t["in_amb_active"] & t["in_amb_termed"]]
    not_found = t[~t["in_amb_active"] & ~t["in_amb_termed"]]

    # Win-back call list: Ambetter-termed rows whose client the tracker still
    # has as active. Pull from the Ambetter side so we get phone + term date.
    lk_sid, lk_nm = set(lapsed_keys["sid"]) - {""}, set(lapsed_keys["nm"])
    winback = amb_termed[
        amb_termed["sid"].isin(lk_sid) | amb_termed["nm"].isin(lk_nm)
    ].copy()

    # Business in Ambetter (active) the tracker doesn't have
    missing = amb_active[
        ~amb_active["sid"].isin(t_sid) & ~amb_active["nm"].isin(t_nm)
    ].copy()

    def _add_months_on_book(df):
        df = df.copy()
        be = pd.to_datetime(df.get("Broker Effective Date"), errors="coerce")
        pe = pd.to_datetime(df.get("Policy Effective Date"), errors="coerce")
        start = be.fillna(pe)
        df["Months On Book"] = ((today - start).dt.days / 30.44).round(1)
        return df

    winback = _add_months_on_book(winback)
    missing = _add_months_on_book(missing)

    _cols = ["Insured First Name", "Insured Last Name", "State", "County",
             "Plan Name", "Policy Effective Date", "Policy Term Date",
             "Months On Book", "Member Phone Number", "Member Email",
             "Number of Members", "Monthly Premium Amount", "Policy Number"]

    def _slim(df):
        return df[[c for c in _cols if c in df.columns]]

    _stamp = today.strftime("%Y-%m-%d")
    f_winback = out / f"ambetter_winback_lapsed_{_stamp}.csv"
    f_missing = out / f"ambetter_missing_from_tracker_{_stamp}.csv"
    _slim(winback).to_csv(f_winback, index=False)
    _slim(missing).to_csv(f_missing, index=False)

    return {
        "tracker_active": len(t),
        "ambetter_active": len(amb_active),
        "ambetter_termed": len(amb_termed),
        "confirmed_active": int(t["in_amb_active"].sum()),
        "lapsed_winbacks": len(winback),
        "missing_from_tracker": len(missing),
        "unmatched_tracker_active": len(not_found),
        "winback_file": str(f_winback),
        "missing_file": str(f_missing),
    }
