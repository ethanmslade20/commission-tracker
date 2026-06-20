"""
Loads supplemental / ancillary books (dental, vision, accident, STM, etc.) from
the carrier exports and normalizes them into one roster.

Two sources today, each with its own export shape:
  - UnitedHealthcare ancillary  -> carrier_books/supp_uhc.csv   (MyBookofBusiness)
  - Allstate / National General -> carrier_books/supp_natgen.csv (Policy List)

Premiums are real; commission rates are not yet known, so we surface premium
only. When the agent provides comp rates, multiply premium by the rate per
carrier to get commission.

Normalized columns:
  first_name, last_name, carrier, product, premium, status, status_detail,
  state, email, phone
where `status` is collapsed to "Active" / "Inactive".
"""

from pathlib import Path

import pandas as pd

# Carrier display labels (what shows on the dashboard / sheet).
_UHC = "UnitedHealthcare"
_ALLSTATE = "Allstate"


def _money(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(r"[$,]", "", regex=True).str.strip(),
        errors="coerce",
    ).fillna(0.0)


def _term(series) -> pd.Series:
    """Parse a termination-date column. NatGen uses 12/31/9999 as an 'active /
    no end date' sentinel — collapse any far-future date to NaT."""
    if series is None:
        return pd.Series(pd.NaT, dtype="datetime64[ns]")
    t = pd.to_datetime(series, errors="coerce")
    return t.mask(t.dt.year >= 2900)


def _load_uhc(path: Path) -> pd.DataFrame:
    """UHC ancillary export. Dependents have a blank Plan Name and share the
    primary's policy, so we drop them (premium lives on the primary row)."""
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    df = df[df["Plan Name"].notna()].copy()
    out = pd.DataFrame({
        "first_name": df["First Name"],
        "last_name": df["Last Name"],
        "carrier": _UHC,
        "product": df["Plan Name"],
        "premium": _money(df["Premium"]),
        "status_detail": df["Status"].astype(str).str.strip(),
        "term_date": _term(df.get("Termination Date")),
        "state": df.get("State"),
        "email": df.get("Email"),
        "phone": df.get("Phone Number"),
    })
    # "Active", "Active (Grace Period) (Payment Error)", "Active (Payment Error)"
    # are all still in force. "Withdrawn", "Sub Self Term ..." are gone.
    out["status"] = out["status_detail"].str.startswith("Active").map(
        {True: "Active", False: "Inactive"})
    return out


def _load_natgen(path: Path) -> pd.DataFrame:
    """Allstate / National General policy list. One row per policy (a member can
    hold several — dental, STM, accident, etc.)."""
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    out = pd.DataFrame({
        "first_name": df["Member First Name"],
        "last_name": df["Member Last Name"],
        "carrier": _ALLSTATE,
        "product": df["Product Name"],
        "premium": _money(df["Premium Amount"]),
        "status_detail": df["Policy Status"].astype(str).str.strip(),
        "term_date": _term(df.get("Term Date")),
        "state": df.get("State"),
        "email": df.get("Email"),
        "phone": df.get("Phone"),
    })
    out["status"] = out["status_detail"].eq("Active").map(
        {True: "Active", False: "Inactive"})
    return out


def load_supplemental(carrier_books_dir: str = "carrier_books") -> pd.DataFrame:
    """Combined, normalized supplemental roster across all carriers. Empty frame
    (with the right columns) if no books are present."""
    base = Path(carrier_books_dir)
    frames = [
        _load_uhc(base / "supp_uhc.csv"),
        _load_natgen(base / "supp_natgen.csv"),
    ]
    frames = [f for f in frames if not f.empty]
    cols = ["first_name", "last_name", "carrier", "product", "premium",
            "status", "status_detail", "term_date", "state", "email", "phone"]
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True)
    return out[cols]


def summarize_supplemental(supp: pd.DataFrame) -> dict:
    """Per-carrier active-premium summary for the dashboard boxes.

    Returns {carrier: {"active_premium": float, "active_policies": int,
                       "inactive_policies": int}}.
    """
    summary: dict = {}
    if supp is None or supp.empty:
        return summary
    for carrier, grp in supp.groupby("carrier"):
        active = grp[grp["status"] == "Active"]
        summary[carrier] = {
            "active_premium": float(active["premium"].sum()),
            "active_policies": int(len(active)),
            "inactive_policies": int((grp["status"] != "Active").sum()),
        }
    return summary
