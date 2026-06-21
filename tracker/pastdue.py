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

from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_BOOKS = str(_ROOT / "carrier_books")

_COLS = ["first_name", "last_name", "carrier", "state", "product", "premium",
         "paid_through", "balance", "days_overdue", "reason", "phone", "email"]


def _money(series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(r"[$,]", "", regex=True).str.strip(),
        errors="coerce")


def _ambetter_pastdue(path: Path, today: pd.Timestamp) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    a = pd.read_csv(path)
    a["ptd"]  = pd.to_datetime(a.get("Paid Through Date"), errors="coerce")
    a["term"] = pd.to_datetime(a.get("Policy Term Date"), errors="coerce")
    a["resp"] = _money(a.get("Member Responsibility"))
    in_force = a["term"].isna() | (a["term"] > today)
    # Only flag plans with a member premium > $0. A $0-responsibility (fully
    # subsidized) plan has nothing to pay, so a stale paid-through date is not a
    # real missed payment.
    pastdue  = in_force & a["ptd"].notna() & (a["ptd"] < today) & (a["resp"] > 0)
    d = a[pastdue].copy()
    if d.empty:
        return pd.DataFrame(columns=_COLS)
    out = pd.DataFrame({
        "first_name": d["Insured First Name"],
        "last_name":  d["Insured Last Name"],
        "carrier":    "Ambetter",
        "state":      d.get("State"),
        "product":    "Medical",
        "premium":    _money(d.get("Member Responsibility")),
        "paid_through": d["ptd"],
        "balance":    pd.NA,
        "days_overdue": (today - d["ptd"]).dt.days,
        "reason":     "Paid through " + d["ptd"].dt.strftime("%b %d, %Y"),
        "phone":      d.get("Member Phone Number"),
        "email":      d.get("Member Email"),
    })
    return out


def _oscar_pastdue(path: Path, today: pd.Timestamp, balance_min: float) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    o = pd.read_csv(path)
    o["bal"] = _money(o.get("Balance"))
    in_force = ~o.get("Policy status", pd.Series("", index=o.index)).astype(str).str.contains(
        "Inactive", case=False, na=False)
    pastdue = in_force & o["bal"].notna() & (o["bal"] > balance_min)
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
        "premium":    _money(d.get("Premium amount")),
        "paid_through": pd.NaT,
        "balance":    d["bal"],
        "days_overdue": pd.NA,
        "reason":     "Balance owed $" + d["bal"].round(2).astype(str),
        "phone":      d.get("Phone number"),
        "email":      d.get("Email"),
    })
    return out


def load_health_pastdue(carrier_books_dir: str = _DEFAULT_BOOKS,
                        oscar_balance_min: float = 10.0,
                        today=None) -> pd.DataFrame:
    """Active health-plan policies behind on payment, across detectable carriers."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    base = Path(carrier_books_dir)
    frames = [
        _ambetter_pastdue(base / "ambetter.csv", today),
        _oscar_pastdue(base / "oscar.csv", today, oscar_balance_min),
    ]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=_COLS)
    return pd.concat(frames, ignore_index=True)[_COLS]
