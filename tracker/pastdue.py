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
    a["resp"] = _money(a.get("Member Responsibility"))
    in_force = a["term"].isna() | (a["term"] > today)
    # Months behind = how many month-boundaries between the paid-through month and
    # the current month. "Paid through end of LAST month" (=1) just means this
    # month's premium is still expected (autopay posts mid-cycle) — NOT a missed
    # payment. Only flag 2+ months behind (a fully-elapsed month went unpaid).
    months_behind = (today.year - a["ptd"].dt.year) * 12 + (today.month - a["ptd"].dt.month)
    # Skip $0-responsibility (fully subsidized) plans — nothing to miss.
    pastdue = in_force & a["ptd"].notna() & (months_behind >= 2) & (a["resp"] > 0)
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
    in_force = ~o.get("Policy status", pd.Series("", index=o.index)).astype(str).str.contains(
        "Inactive", case=False, na=False)
    # Oscar has no paid-through date, just a balance owed. A balance up to ~one
    # month's premium is the current cycle (still expected to pay), so only flag
    # when they owe MORE than one month (genuinely behind). Exclude $0-premium.
    pastdue = in_force & o["bal"].notna() & (o["prem"] > 0) & (o["bal"] > o["prem"])
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
    return pd.concat(frames, ignore_index=True)[_COLS]
