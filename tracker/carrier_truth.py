"""
Carrier-portal truth overlay (Phase 2).

Applies a carrier's own book of business as the source of truth on top of the
HealthSherpa-built client list, for that carrier only:

  * In the carrier portal (active)         -> stays active
  * In the carrier portal (termed)         -> marked Cancelled (carrier term date)
  * NOT in the portal, coverage started    -> marked Cancelled (dropped off portal)
  * NOT in the portal, coverage not started-> kept active (safety net for new sales
                                              that lag a few days in the portal)
  * In the portal but missing from tracker -> added to the active book

HealthSherpa stays the source for new-business / daily-tracker timing; this only
adjusts the book/status side. Currently implemented for Ambetter.
"""

import re
import json
import unicodedata
from pathlib import Path

import pandas as pd

# Anchor data/carrier-book paths to the repo root so the pipeline works no matter
# what the current working directory is (launchd watchers can run from anywhere;
# a relative "carrier_books" would silently not load and skip carrier truth).
_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BOOKS = str(_ROOT / "carrier_books")

_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}

# Remembers when each "dropped off the portal" client was FIRST detected missing,
# so their lost date ages correctly instead of resetting to today each run.
# (These clients are absent from the carrier export entirely, so no true carrier
# term date exists — first-detected is the best available proxy.)
def _load_dropped(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def _save_dropped(d: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2))


def _email(x) -> str:
    x = str(x).strip().lower()
    return x if "@" in x else ""


def _phone(x) -> str:
    d = re.sub(r"[^0-9]", "", str(x))
    return d[-10:] if len(d) >= 10 else ""


def _clean_id(x) -> str:
    return re.sub(r"[^0-9]", "", str(x))


def _name_key(first, last) -> str:
    s = f"{first} {last}".lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


def _months_on_book(eff, today) -> float:
    if pd.isna(eff):
        return 0.0
    return round(max((today - eff).days, 0) / 30.44, 1)


def apply_ambetter_truth(all_clients: pd.DataFrame,
                         carrier_books_dir: str = _DEFAULT_BOOKS,
                         today=None):
    """Return (adjusted_all_clients, summary_dict). No-op if no Ambetter book."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    book = Path(carrier_books_dir) / "ambetter.csv"
    if all_clients.empty or not book.exists():
        return all_clients, {"applied": False}

    amb = pd.read_csv(book)
    amb["sid"] = amb["Exchange Subscriber ID"].apply(_clean_id)
    amb["nm"] = amb.apply(lambda r: _name_key(r["Insured First Name"], r["Insured Last Name"]), axis=1)
    amb["eff"] = pd.to_datetime(amb["Policy Effective Date"], errors="coerce")
    amb["term"] = pd.to_datetime(amb["Policy Term Date"], errors="coerce")
    # Broker Effective Date = when the agent became broker of record. This is the
    # TRUE "how long I've had this client" date — the policy can predate the
    # relationship by years (inherited / agent-of-record transfers).
    amb["bed"] = pd.to_datetime(amb["Broker Effective Date"], errors="coerce")
    # Earliest broker date per client (a renewal can produce multiple rows).
    bed_by_sid = amb[amb["sid"] != ""].groupby("sid")["bed"].min().to_dict()
    bed_by_nm = amb.groupby("nm")["bed"].min().to_dict()
    amb["termed"] = amb["term"] < today
    amb_active, amb_termed = amb[~amb["termed"]], amb[amb["termed"]]

    aa_sid, aa_nm = set(amb_active["sid"]) - {""}, set(amb_active["nm"])
    at_sid, at_nm = set(amb_termed["sid"]) - {""}, set(amb_termed["nm"])

    ac = all_clients.copy()
    if "term_estimated" not in ac.columns:
        ac["term_estimated"] = False
    ac["_sid"] = ac["ffm_subscriber_id"].apply(_clean_id) if "ffm_subscriber_id" in ac.columns else ""
    ac["_nm"] = ac.apply(lambda r: _name_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    ac["_eff"] = pd.to_datetime(ac.get("effective_date"), errors="coerce")
    is_amb = ac["carrier"].astype(str).str.contains("ambetter", case=False, na=False)
    is_active = ac["status"].isin(_ACTIVE)

    dropped = _load_dropped((_ROOT / "data" / "ambetter_dropped.json"))
    today_iso = today.strftime("%Y-%m-%d")

    n_cancel_termed = n_cancel_dropped = n_protected = 0
    for idx in ac.index[is_amb & is_active]:
        sid, nm = ac.at[idx, "_sid"], ac.at[idx, "_nm"]
        if (sid and sid in aa_sid) or nm in aa_nm:
            continue  # confirmed active in portal
        eff = ac.at[idx, "_eff"]
        if (sid and sid in at_sid) or nm in at_nm:
            ac.at[idx, "status"] = "Cancelled"          # portal says termed
            tmatch = amb_termed[(amb_termed["sid"] == sid) | (amb_termed["nm"] == nm)]
            if not tmatch.empty and "term_date" in ac.columns and pd.notna(tmatch.iloc[0]["term"]):
                ac.at[idx, "term_date"] = tmatch.iloc[0]["term"]
            n_cancel_termed += 1
        elif pd.notna(eff) and eff > today:
            n_protected += 1                            # safety net: new sale, not yet in portal
        else:
            ac.at[idx, "status"] = "Cancelled"          # established but absent from portal
            # No carrier term date exists; use the date we FIRST saw them gone.
            key = sid if sid else nm
            first_seen = dropped.setdefault(key, today_iso)
            if "term_date" in ac.columns:
                ac.at[idx, "term_date"] = pd.Timestamp(first_seen)
            ac.at[idx, "term_estimated"] = True
            n_cancel_dropped += 1

    _save_dropped(dropped, (_ROOT / "data" / "ambetter_dropped.json"))

    # Add portal-active clients the tracker doesn't have
    t_sid = set(ac.loc[is_amb, "_sid"]) - {""}
    t_nm = set(ac.loc[is_amb, "_nm"])
    missing = amb_active[~amb_active["sid"].isin(t_sid) & ~amb_active["nm"].isin(t_nm)]
    new_rows = []
    for _, r in missing.iterrows():
        eff = r["eff"]
        new_rows.append({
            "first_name": r["Insured First Name"],
            "last_name": r["Insured Last Name"],
            "carrier": "Ambetter",
            "effective_date": eff,
            "term_date": pd.NaT,
            "status": "Effectuated",
            "state": r.get("State"),
            "ffm_app_id": "",
            "ffm_subscriber_id": r["sid"],
            "net_premium": pd.to_numeric(r.get("Member Responsibility"), errors="coerce"),
            "applicant_count": pd.to_numeric(r.get("Number of Members"), errors="coerce"),
            "months_on_book": _months_on_book(eff, today),
            "broker_effective_date": r["bed"],
        })

    # Stamp the broker-of-record date onto existing Ambetter rows (true tenure
    # start). Match on subscriber ID first, fall back to name.
    if "broker_effective_date" not in ac.columns:
        ac["broker_effective_date"] = pd.NaT
    _bed = ac["_sid"].map(bed_by_sid)
    _bed = _bed.where(_bed.notna(), ac["_nm"].map(bed_by_nm))
    ac.loc[is_amb, "broker_effective_date"] = pd.to_datetime(_bed[is_amb], errors="coerce")

    ac = ac.drop(columns=["_sid", "_nm", "_eff"])
    if new_rows:
        ac = pd.concat([ac, pd.DataFrame(new_rows)], ignore_index=True)

    summary = {
        "applied": True,
        "portal_active": len(amb_active),
        "portal_termed": len(amb_termed),
        "cancelled_termed": n_cancel_termed,
        "cancelled_dropped": n_cancel_dropped,
        "protected_new_sales": n_protected,
        "added_from_portal": len(new_rows),
    }
    return ac, summary


# ── Oscar ─────────────────────────────────────────────────────────────────────
# Oscar's export has an explicit "Policy status" (Inactive = lapsed; everything
# else = in force) but no FFM subscriber ID, so match by name / email / phone.
_OSCAR_INACTIVE = {"Inactive"}


def apply_oscar_truth(all_clients: pd.DataFrame,
                      carrier_books_dir: str = _DEFAULT_BOOKS,
                      today=None):
    """Return (adjusted_all_clients, summary_dict). No-op if no Oscar book."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    book = Path(carrier_books_dir) / "oscar.csv"
    if all_clients.empty or not book.exists():
        return all_clients, {"applied": False}

    o = pd.read_csv(book)
    o["nm"] = o["Member name"].apply(
        lambda n: _name_key(*str(n).split(" ", 1)) if " " in str(n) else _name_key(n, ""))
    o["em"] = o["Email"].apply(_email)
    o["ph"] = o["Phone number"].apply(_phone)
    o["start"] = pd.to_datetime(o["Coverage start date"], errors="coerce")
    o["end"] = pd.to_datetime(o["Coverage end date"], errors="coerce")
    o_active = o[~o["Policy status"].isin(_OSCAR_INACTIVE)]
    o_inact = o[o["Policy status"].isin(_OSCAR_INACTIVE)]

    def _keys(df):
        return set(df["nm"]) | (set(df["em"]) - {""}) | (set(df["ph"]) - {""})

    aa, ii = _keys(o_active), _keys(o_inact)

    ac = all_clients.copy()
    if "term_estimated" not in ac.columns:
        ac["term_estimated"] = False
    ac["_nm"] = ac.apply(lambda r: _name_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    ac["_em"] = ac["email"].apply(_email) if "email" in ac.columns else ""
    ac["_ph"] = ac["phone"].apply(_phone) if "phone" in ac.columns else ""
    ac["_eff"] = pd.to_datetime(ac.get("effective_date"), errors="coerce")
    is_osc = ac["carrier"].astype(str).str.contains("oscar", case=False, na=False)
    is_active = ac["status"].isin(_ACTIVE)

    def _match(nm, em, ph, S):
        return bool(nm in S or (em in S if em else False) or (ph in S if ph else False))

    dropped = _load_dropped((_ROOT / "data" / "oscar_dropped.json"))
    today_iso = today.strftime("%Y-%m-%d")
    n_cancel_inactive = n_cancel_dropped = n_protected = 0

    for idx in ac.index[is_osc & is_active]:
        nm, em, ph = ac.at[idx, "_nm"], ac.at[idx, "_em"], ac.at[idx, "_ph"]
        if _match(nm, em, ph, aa):
            continue  # active in Oscar portal
        eff = ac.at[idx, "_eff"]
        if _match(nm, em, ph, ii):
            ac.at[idx, "status"] = "Cancelled"
            m = o_inact[(o_inact["nm"] == nm) | (o_inact["em"] == em) | (o_inact["ph"] == ph)]
            if not m.empty and "term_date" in ac.columns and pd.notna(m.iloc[0]["end"]):
                ac.at[idx, "term_date"] = m.iloc[0]["end"]
            n_cancel_inactive += 1
        elif pd.notna(eff) and eff > today:
            n_protected += 1
        else:
            ac.at[idx, "status"] = "Cancelled"
            key = em or ph or nm
            first_seen = dropped.setdefault(key, today_iso)
            if "term_date" in ac.columns:
                ac.at[idx, "term_date"] = pd.Timestamp(first_seen)
            ac.at[idx, "term_estimated"] = True
            n_cancel_dropped += 1

    _save_dropped(dropped, (_ROOT / "data" / "oscar_dropped.json"))

    # Add Oscar-active clients the tracker lacks
    t_keys = set(ac.loc[is_osc, "_nm"]) | (set(ac.loc[is_osc, "_em"]) - {""}) | (set(ac.loc[is_osc, "_ph"]) - {""})
    missing = o_active[o_active.apply(
        lambda r: not _match(r["nm"], r["em"], r["ph"], t_keys), axis=1)]
    new_rows = []
    for _, r in missing.iterrows():
        eff = r["start"]
        status = "PendingEffectuation" if str(r.get("Policy status")) == "Unpaid binder" else "Effectuated"
        fn = str(r["Member name"]).split(" ", 1)
        new_rows.append({
            "first_name": fn[0],
            "last_name": fn[1] if len(fn) > 1 else "",
            "carrier": "Oscar",
            "effective_date": eff,
            "term_date": pd.NaT,
            "status": status,
            "state": r.get("State"),
            "ffm_app_id": "",
            "net_premium": pd.to_numeric(str(r.get("Premium amount", "")).replace("$", "").replace(",", ""), errors="coerce"),
            "applicant_count": pd.to_numeric(r.get("Lives"), errors="coerce"),
            "months_on_book": _months_on_book(eff, today),
            "email": r.get("Email"),
            "phone": r.get("Phone number"),
        })

    ac = ac.drop(columns=["_nm", "_em", "_ph", "_eff"])
    if new_rows:
        ac = pd.concat([ac, pd.DataFrame(new_rows)], ignore_index=True)

    return ac, {
        "applied": True,
        "portal_active": len(o_active),
        "portal_inactive": len(o_inact),
        "cancelled_inactive": n_cancel_inactive,
        "cancelled_dropped": n_cancel_dropped,
        "protected_new_sales": n_protected,
        "added_from_portal": len(new_rows),
    }


# ── UnitedHealthcare ──────────────────────────────────────────────────────────
# UHC export: one row per MEMBER, planStatus A=active / I=inactive(lapsed),
# grouped into policies by "IFP - FFM APP ID". No subscriber ID that matches
# HealthSherpa and no coverage dates, so match by name/phone and use the
# tracker's own effective date for the new-sale safety net.
def apply_uhc_truth(all_clients: pd.DataFrame,
                    carrier_books_dir: str = _DEFAULT_BOOKS,
                    today=None):
    """Return (adjusted_all_clients, summary_dict). No-op if no UHC book."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    book = Path(carrier_books_dir) / "uhc_source.xlsx"
    if all_clients.empty or not book.exists():
        return all_clients, {"applied": False}

    u = pd.read_excel(book, header=2).dropna(subset=["memberFirstName", "memberLastName"])
    u["nm"] = u.apply(lambda r: _name_key(r["memberFirstName"], r["memberLastName"]), axis=1)
    u["ph"] = u["memberPhone"].apply(_phone)
    u["aid"] = u["IFP - FFM APP ID"].apply(_clean_id)
    ua, ui = u[u["planStatus"] == "A"], u[u["planStatus"] == "I"]

    A = set(ua["nm"]) | (set(ua["ph"]) - {""})
    I = set(ui["nm"]) | (set(ui["ph"]) - {""})

    ac = all_clients.copy()
    if "term_estimated" not in ac.columns:
        ac["term_estimated"] = False
    ac["_nm"] = ac.apply(lambda r: _name_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    ac["_ph"] = ac["phone"].apply(_phone) if "phone" in ac.columns else ""
    ac["_eff"] = pd.to_datetime(ac.get("effective_date"), errors="coerce")
    is_uhc = ac["carrier"].astype(str).str.contains("united", case=False, na=False)
    is_active = ac["status"].isin(_ACTIVE)

    def _m(nm, ph, S):
        return bool(nm in S or (ph in S if ph else False))

    dropped = _load_dropped((_ROOT / "data" / "uhc_dropped.json"))
    today_iso = today.strftime("%Y-%m-%d")
    n_lapsed = n_dropped = n_protected = 0

    for idx in ac.index[is_uhc & is_active]:
        nm, ph = ac.at[idx, "_nm"], ac.at[idx, "_ph"]
        if _m(nm, ph, A):
            continue  # active in UHC
        eff = ac.at[idx, "_eff"]
        if _m(nm, ph, I):
            ac.at[idx, "status"] = "Cancelled"          # UHC marks inactive
            key = ph or nm
            first_seen = dropped.setdefault(key, today_iso)
            if "term_date" in ac.columns:
                ac.at[idx, "term_date"] = pd.Timestamp(first_seen)
            ac.at[idx, "term_estimated"] = True
            n_lapsed += 1
        elif pd.notna(eff) and eff > today:
            n_protected += 1                            # new sale not yet in UHC export
        else:
            ac.at[idx, "status"] = "Cancelled"          # gone from UHC entirely
            key = ph or nm
            first_seen = dropped.setdefault(key, today_iso)
            if "term_date" in ac.columns:
                ac.at[idx, "term_date"] = pd.Timestamp(first_seen)
            ac.at[idx, "term_estimated"] = True
            n_dropped += 1

    _save_dropped(dropped, (_ROOT / "data" / "uhc_dropped.json"))

    # Add UHC-active business missing from tracker, grouped into policies by App ID
    t_keys = set(ac.loc[is_uhc, "_nm"]) | (set(ac.loc[is_uhc, "_ph"]) - {""})
    miss = ua[ua.apply(lambda r: not _m(r["nm"], r["ph"], t_keys), axis=1)].copy()
    miss["grp"] = miss.apply(lambda r: r["aid"] if r["aid"] else f"solo_{r.name}", axis=1)
    new_rows = []
    for _, g in miss.groupby("grp"):
        rep = g.iloc[0]
        new_rows.append({
            "first_name": rep["memberFirstName"],
            "last_name": rep["memberLastName"],
            "carrier": "UnitedHealthcare",
            "effective_date": pd.NaT,
            "term_date": pd.NaT,
            "status": "Effectuated",
            "state": rep.get("memberState"),
            "ffm_app_id": "",
            "applicant_count": len(g),
            "months_on_book": 0.0,
            "phone": rep.get("memberPhone"),
        })

    ac = ac.drop(columns=["_nm", "_ph", "_eff"])
    if new_rows:
        ac = pd.concat([ac, pd.DataFrame(new_rows)], ignore_index=True)

    return ac, {
        "applied": True,
        "portal_active_members": len(ua),
        "portal_inactive_members": len(ui),
        "cancelled_lapsed": n_lapsed,
        "cancelled_dropped": n_dropped,
        "protected_new_sales": n_protected,
        "added_policies": len(new_rows),
    }


# ── Anthem / Wellpoint ────────────────────────────────────────────────────────
# Anthem (Producer Toolbox) export: explicit Status (Active / Future Active /
# Inactive) + Cancellation Date + Effective Date. Name is "Last, First"; no
# phone/email/ID/group size, so match by name + state and assume 1 member.
def _split_lastfirst(name):
    n = str(name)
    if "," in n:
        last, first = n.split(",", 1)
        fp = first.strip().split()
        return (fp[0] if fp else ""), last.strip()
    return n, ""


def apply_anthem_truth(all_clients: pd.DataFrame,
                       carrier_books_dir: str = _DEFAULT_BOOKS,
                       today=None):
    """Return (adjusted_all_clients, summary_dict). No-op if no Anthem book."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    book = Path(carrier_books_dir) / "anthem.csv"
    if all_clients.empty or not book.exists():
        return all_clients, {"applied": False}

    a = pd.read_csv(book, skiprows=1)
    fn_ln = a["Client Name"].apply(_split_lastfirst)
    a["nm"] = [(_name_key(f, l)) for f, l in fn_ln]
    a["st"] = a["State"].astype(str).str.upper()
    a["key"] = list(zip(a["nm"], a["st"]))
    a["cancel"] = pd.to_datetime(a["Cancellation Date"], errors="coerce")
    a["eff"] = pd.to_datetime(a["Effective Date"], errors="coerce")
    a_act = a[a["Status"].isin(["Active", "Future Active"])]
    a_in = a[a["Status"] == "Inactive"]
    A, I = set(a_act["key"]), set(a_in["key"])

    ac = all_clients.copy()
    if "term_estimated" not in ac.columns:
        ac["term_estimated"] = False
    ac["_k"] = ac.apply(lambda r: (_name_key(r.get("first_name", ""), r.get("last_name", "")),
                                   str(r.get("state") or "").upper()), axis=1)
    ac["_eff"] = pd.to_datetime(ac.get("effective_date"), errors="coerce")
    is_anth = ac["carrier"].astype(str).str.contains("anthem|wellpoint", case=False, na=False, regex=True)
    is_active = ac["status"].isin(_ACTIVE)

    dropped = _load_dropped((_ROOT / "data" / "anthem_dropped.json"))
    today_iso = today.strftime("%Y-%m-%d")
    n_lapsed = n_dropped = n_protected = 0

    for idx in ac.index[is_anth & is_active]:
        k = ac.at[idx, "_k"]
        if k in A:
            continue
        eff = ac.at[idx, "_eff"]
        if k in I:
            ac.at[idx, "status"] = "Cancelled"
            m = a_in[a_in["key"] == k]
            if not m.empty and "term_date" in ac.columns and pd.notna(m.iloc[0]["cancel"]):
                ac.at[idx, "term_date"] = m.iloc[0]["cancel"]
            n_lapsed += 1
        elif pd.notna(eff) and eff > today:
            n_protected += 1
        else:
            ac.at[idx, "status"] = "Cancelled"
            kk = f"{k[0]}|{k[1]}"
            first_seen = dropped.setdefault(kk, today_iso)
            if "term_date" in ac.columns:
                ac.at[idx, "term_date"] = pd.Timestamp(first_seen)
            ac.at[idx, "term_estimated"] = True
            n_dropped += 1

    _save_dropped(dropped, (_ROOT / "data" / "anthem_dropped.json"))

    t_keys = set(ac.loc[is_anth, "_k"])
    miss = a_act[~a_act["key"].isin(t_keys)]
    new_rows = []
    for _, r in miss.iterrows():
        f, l = _split_lastfirst(r["Client Name"])
        eff = r["eff"]
        new_rows.append({
            "first_name": f, "last_name": l, "carrier": "Anthem/Wellpoint",
            "effective_date": eff, "term_date": pd.NaT,
            "status": "Effectuated", "state": r.get("State"), "ffm_app_id": "",
            "applicant_count": 1, "months_on_book": _months_on_book(eff, today),
        })

    ac = ac.drop(columns=["_k", "_eff"])
    if new_rows:
        ac = pd.concat([ac, pd.DataFrame(new_rows)], ignore_index=True)

    return ac, {
        "applied": True,
        "portal_active": len(a_act), "portal_inactive": len(a_in),
        "cancelled_lapsed": n_lapsed, "cancelled_dropped": n_dropped,
        "protected_new_sales": n_protected, "added_policies": len(new_rows),
    }
