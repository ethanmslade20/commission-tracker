"""
Detects HEALTH-PLAN policies that are still active but behind on payment
(missed payment / in grace period), read straight from the carrier portals.

Coverage by carrier:
  - Ambetter : "Paid Through Date" in the past while the policy is in force
  - Oscar    : an outstanding "Balance" above a small noise threshold
Anthem (name-only export) and UHC medical (no clean payment column) can't be
detected, so they're absent here — that's a data limitation, not a clean book.

Normalized columns:
  first_name, last_name, carrier, state, product, premium, paid_through,
  balance, days_overdue, reason, phone, email
"""

import re
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BOOKS = str(_ROOT / "carrier_books")

_COLS = ["first_name", "last_name", "carrier", "state", "product", "status", "premium",
         "members", "paid_through", "balance", "days_overdue", "reason", "phone", "email"]


def _money(series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(r"[$,]", "", regex=True).str.strip(),
        errors="coerce")


def _members(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(1).clip(lower=1).astype(int)


def _phone(series) -> pd.Series:
    """Clean phone display: strip a trailing '.0' (CSVs read numbers as floats)
    and format the 10 digits as (xxx) xxx-xxxx."""
    def fmt(x):
        x = re.sub(r"\D", "", str(x))
        if len(x) == 11 and x.startswith("1"):
            x = x[1:]
        return f"({x[:3]}) {x[3:6]}-{x[6:]}" if len(x) == 10 else re.sub(r"\.0$", "", str(x))
    if series is None:
        return pd.Series(dtype=str)
    return series.map(fmt)


def _ambetter_pastdue(path: Path, today: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    a = pd.read_csv(path)
    a["ptd"]  = pd.to_datetime(a.get("Paid Through Date"), errors="coerce")
    a["term"] = pd.to_datetime(a.get("Policy Term Date"), errors="coerce")
    a["eff"]  = pd.to_datetime(a.get("Policy Effective Date"), errors="coerce")
    a["bed"]  = pd.to_datetime(a.get("Broker Effective Date"), errors="coerce")
    a["resp"] = _money(a.get("Member Responsibility"))
    in_force = a["term"].isna() | (a["term"] > today)
    # Months behind = how many month-boundaries between the paid-through month and
    # the current month. "Paid through end of LAST month" (=1) just means this
    # month's premium is still expected (autopay posts mid-cycle) — NOT a missed
    # payment. Only flag 2+ months behind (a fully-elapsed month went unpaid).
    months_behind = (today.year - a["ptd"].dt.year) * 12 + (today.month - a["ptd"].dt.month)
    # Only the agent's CURRENT-term lapses:
    #  - agent must already BE the broker (broker-effective in the past) — a future
    #    linkage (e.g. 7/1) isn't his client yet, so don't chase.
    #  - paid-through must fall WITHIN the current term (>= policy effective) — a
    #    paid-through before the policy started is stale prior-term data, not a lapse.
    agent_active = a["bed"].isna() | (a["bed"] <= today)
    current_term = a["eff"].isna() | (a["ptd"] >= a["eff"])
    # Skip $0-responsibility (fully subsidized) plans — nothing to miss.
    pastdue = (in_force & a["ptd"].notna() & (months_behind >= 2) & (a["resp"] > 0)
               & agent_active & current_term)
    d = a[pastdue].copy()
    if d.empty:
        return pd.DataFrame(columns=_COLS)
    out = pd.DataFrame({
        "first_name": d["Insured First Name"],
        "last_name":  d["Insured Last Name"],
        "carrier":    "Ambetter",
        "state":      d.get("State"),
        "product":    "Medical",
        # Ambetter's export has no status field; in-force + paid-through passed
        # means the member is in their grace window before cancellation.
        "status":     "Grace period",
        "premium":    _money(d.get("Member Responsibility")),
        "members":    _members(d.get("Number of Members")),
        "paid_through": d["ptd"],
        "balance":    pd.NA,
        "days_overdue": (today - d["ptd"]).dt.days,
        "reason":     "Paid through " + d["ptd"].dt.strftime("%b %d, %Y"),
        "phone":      _phone(d.get("Member Phone Number")),
        "email":      d.get("Member Email"),
    })
    return out


def _oscar_pastdue(path: Path, today: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    o = pd.read_csv(path)
    o["bal"] = _money(o.get("Balance"))
    o["prem"] = _money(o.get("Premium amount"))
    o["start"] = pd.to_datetime(o.get("Coverage start date"), errors="coerce")
    cur = pd.Timestamp(today.year, today.month, 1)
    _status = o.get("Policy status", pd.Series("", index=o.index)).astype(str)
    in_force = ~_status.str.contains("Inactive", case=False, na=False)
    # Exclude brand-new business: coverage starting this month or later, and unpaid
    # binders that never activated — those are in their first-payment window, not a
    # lapse (same grace rule as Ambetter's effective-date guard).
    not_new = o["start"].isna() | (o["start"] < cur)
    not_binder = ~_status.str.contains("binder", case=False, na=False)
    # Oscar has no paid-through date, just a balance owed. A balance up to ~one
    # month's premium is the current cycle (still expected to pay), so only flag
    # when they owe MORE than one month (genuinely behind). Exclude $0-premium.
    pastdue = (in_force & o["bal"].notna() & (o["prem"] > 0) & (o["bal"] > o["prem"])
               & not_new & not_binder)
    d = o[pastdue].copy()
    if d.empty:
        return pd.DataFrame(columns=_COLS)
    nm = d["Member name"].astype(str)
    out = pd.DataFrame({
        "first_name": nm.str.split(" ", n=1).str[0],
        "last_name":  nm.str.split(" ", n=1).str[1].fillna(""),
        "carrier":    "Oscar",
        "state":      d.get("State"),
        "product":    "Medical",
        # Oscar reports the real status: Grace period / Delinquent / Unpaid binder.
        "status":     d.get("Policy status"),
        "premium":    d["prem"],
        "members":    _members(d.get("Lives")),
        "paid_through": pd.NaT,
        "balance":    d["bal"],
        "days_overdue": pd.NA,
        "reason":     "Balance owed $" + d["bal"].round(2).astype(str),
        "phone":      _phone(d.get("Phone number")),
        "email":      d.get("Email"),
    })
    return out


def load_health_pastdue(carrier_books_dir: str = _DEFAULT_BOOKS,
                        today=None) -> pd.DataFrame:
    """Active health-plan policies behind on payment, across detectable carriers.
    Includes every policy with a member premium > $0 (commission is a flat rate
    per member, so premium size doesn't matter); only $0-premium plans are
    excluded since there's nothing to miss."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    base = Path(carrier_books_dir)
    frames = [
        _ambetter_pastdue(base / "ambetter.csv", today),
        _oscar_pastdue(base / "oscar.csv", today),
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=_COLS)
    return _drop_superseded(pd.concat(frames, ignore_index=True)[_COLS], today)


def _drop_superseded(pastdue: pd.DataFrame, today) -> pd.DataFrame:
    """Drop past-due rows for clients SWITCHED to a newer plan on a different
    carrier (active/pending in HealthSherpa, effective in the last ~2 months or
    later) — the old plan is meant to lapse, so there's nobody to call (Ethan
    2026-07-13, Freddie Moss: Ambetter past-due but moved to BCBS-TN July 1).
    History is untouched; the client only leaves the reach-out list."""
    import glob
    import re
    if pastdue.empty:
        return pastdue
    try:
        _snaps = sorted(glob.glob(str(Path(__file__).resolve().parent.parent
                                      / "snapshots" / "*healthsherpa*.parquet")))
        if not _snaps:
            return pastdue
        hs = pd.read_parquet(_snaps[-1])
    except Exception:
        return pastdue

    def _k(f, l):
        return re.sub(r"[^a-z]", "", f"{f}{l}".lower())

    hs = hs[hs.get("status", pd.Series(dtype=str)).isin(
        ["Effectuated", "PendingEffectuation"])].copy()
    if hs.empty:
        return pastdue
    hs["_k"] = [_k(str(a), str(b)) for a, b in
                zip(hs["first_name"].fillna(""), hs["last_name"].fillna(""))]
    hs["_eff"] = pd.to_datetime(hs.get("effective_date"), errors="coerce")
    _recent = pd.Timestamp(today).normalize().replace(day=1) - pd.offsets.MonthBegin(2)

    keep = []
    for _, r in pastdue.iterrows():
        k = _k(str(r.get("first_name", "")), str(r.get("last_name", "")))
        pd_carrier = str(r.get("carrier", "")).split()[0].lower()
        superseded = False
        if k and pd_carrier:
            others = hs[(hs["_k"] == k)
                        & ~hs["carrier"].astype(str).str.lower().str.contains(pd_carrier, regex=False)]
            superseded = bool((others["_eff"] >= _recent).any())
            if superseded:
                print(f"  Past-due: {r.get('first_name')} {r.get('last_name')} skipped — "
                      f"switched to {others.iloc[0]['carrier']}")
        keep.append(not superseded)
    return pastdue[pd.Series(keep, index=pastdue.index)].reset_index(drop=True)
