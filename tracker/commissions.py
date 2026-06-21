"""
Read ACTUAL commission payments from the "Insurance PAYMENTS" Google Sheet and
reconcile them against the active book.

The sheet has one tab per month (plus "Year to Date"); each tab is a pasted
Agent Boost statement. A data row = Carrier (col C) present + a parseable amount
(col L). The "Total Commission Paid" line has a blank carrier and is skipped.
Chargebacks are shown as "(25.00)" and parsed as negative. With those rules the
line items reconcile to each statement's stated total.

Key outputs:
  - parse_payments_sheet(ss)  -> per-line-item DataFrame
  - carrier_timing(payments)  -> {carrier: lag_months} (PMPM, paid month − coverage month)
  - reconcile_book(active, payments, today) -> per-client paid status + the
    "active but not paid recently" list (likely missing commissions)
"""

import re
import unicodedata

import pandas as pd

_MONTHS = {"jan": 1, "feb": 2, "fed": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_PMPM = 23  # fallback per-member rate when a carrier has no observed average


def _money(x):
    x = str(x).strip().replace(",", "").replace("$", "")
    neg = x.startswith("(") and x.endswith(")")
    x = x.strip("() ")
    try:
        v = float(x)
        return -v if neg else v
    except ValueError:
        return None


def _tab_month(title):
    """'Jan 2026' / 'Fed 2026' / 'April 2026' -> Timestamp(first of month)."""
    m = re.match(r"\s*([A-Za-z]+)\s*(20\d\d)", title)
    if not m:
        return None
    mon = _MONTHS.get(m.group(1)[:3].lower())
    if not mon:
        return None
    return pd.Timestamp(int(m.group(2)), mon, 1)


def _name_key(s):
    """Order-insensitive name key so 'WHEELER, ROEANNA' == 'Roeanna Wheeler'."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return "".join(sorted(re.findall(r"[a-z]+", s)))


def parse_payments_sheet(spreadsheet) -> pd.DataFrame:
    rows = []
    for ws in spreadsheet.worksheets():
        if ws.title.strip().lower() == "year to date":
            continue
        pm = _tab_month(ws.title)
        if pm is None:
            continue
        for r in ws.get_all_values():
            if len(r) < 12 or not r[2].strip():
                continue
            if "Total Commission" in " ".join(r):
                continue
            amt = _money(r[11])
            if amt is None:
                continue
            rows.append(dict(
                payment_month=pm, carrier=r[2].strip(), member=r[4].strip(),
                pay_period=r[5].strip(), effective=r[6].strip(),
                subscribers=r[7].strip(), state=r[9].strip(),
                description=r[10].strip(), amount=amt))
    df = pd.DataFrame(rows)
    if not df.empty:
        df["name_key"] = df["member"].apply(_name_key)
    return df


def carrier_timing(payments: pd.DataFrame) -> dict:
    """Per-carrier lag in months for PMPM rows = (paid month) − (coverage month)."""
    if payments.empty:
        return {}
    pm = payments[payments["description"].str.upper() == "PMPM"].copy()
    pm["pp"] = pd.to_datetime(pm["pay_period"], errors="coerce")
    pm = pm.dropna(subset=["pp"])
    pm["lag"] = (pm["payment_month"].dt.year * 12 + pm["payment_month"].dt.month) \
        - (pm["pp"].dt.year * 12 + pm["pp"].dt.month)
    out = {}
    for c, g in pm.groupby("carrier"):
        g = g[(g["lag"] >= 0) & (g["lag"] <= 3)]   # ignore retro catch-ups
        if len(g):
            out[c] = int(g["lag"].mode().iloc[0])
    return out


def monthly_summary(payments: pd.DataFrame) -> pd.DataFrame:
    """Net commission per payment-month (chargebacks included)."""
    if payments.empty:
        return pd.DataFrame(columns=["Month", "Commission", "Chargebacks", "Net"])
    g = payments.groupby("payment_month")
    out = pd.DataFrame({
        "Commission": g["amount"].apply(lambda s: s[s > 0].sum()),
        "Chargebacks": g["amount"].apply(lambda s: s[s < 0].sum()),
        "Net": g["amount"].sum(),
    }).reset_index().rename(columns={"payment_month": "Month"})
    return out.sort_values("Month")


def carrier_summary(payments: pd.DataFrame) -> pd.DataFrame:
    if payments.empty:
        return pd.DataFrame(columns=["Carrier", "Net", "Payments"])
    g = payments.groupby("carrier")
    return (pd.DataFrame({"Net": g["amount"].sum(), "Payments": g.size()})
            .reset_index().rename(columns={"carrier": "Carrier"})
            .sort_values("Net", ascending=False))


def reconcile_book(active: pd.DataFrame, payments: pd.DataFrame, today=None) -> dict:
    """Match active book clients to their payment history and flag those who are
    active but have NOT been paid in the most recent statement month(s) — the
    likely-missing-commission list. Matches on name (carrier-agnostic, since a
    client may be paid under a slightly different carrier label)."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    result = {"latest_month": None, "missing": pd.DataFrame(), "chargebacks": pd.DataFrame(),
              "matched": 0, "unmatched": 0}
    if payments.empty or active is None or active.empty:
        return result

    latest = payments["payment_month"].max()
    result["latest_month"] = latest
    prev = (latest.to_period("M") - 1).to_timestamp()

    # last payment month per person (positive payments only)
    pos = payments[payments["amount"] > 0]
    last_paid = pos.groupby("name_key")["payment_month"].max().to_dict()
    paid_keys = set(last_paid)

    a = active.copy()
    a["name_key"] = a.apply(lambda r: _name_key(f"{r.get('first_name','')} {r.get('last_name','')}"), axis=1)
    a["_matched"] = a["name_key"].isin(paid_keys)
    result["matched"] = int(a["_matched"].sum())
    result["unmatched"] = int((~a["_matched"]).sum())

    # Missing = matched to a payment history, but not paid in the latest OR prior
    # month (an active client on a monthly carrier should appear every month).
    def _status(row):
        lp = last_paid.get(row["name_key"])
        if lp is None:
            return None          # never matched — could be new / name mismatch; don't false-flag
        if lp >= prev:
            return None          # paid recently
        return lp
    a["_last_paid"] = a.apply(_status, axis=1)
    miss = a[a["_last_paid"].notna()].copy()
    if not miss.empty:
        miss["Last Paid"] = pd.to_datetime(miss["_last_paid"]).dt.strftime("%b %Y")
        miss["Months Since Paid"] = ((latest.to_period("M").ordinal)
                                     - pd.to_datetime(miss["_last_paid"]).dt.to_period("M").apply(lambda p: p.ordinal)).astype(int)
        result["missing"] = miss

    # Recent chargebacks (negative amounts in the latest two months)
    cb = payments[(payments["amount"] < 0) & (payments["payment_month"] >= prev)].copy()
    result["chargebacks"] = cb
    return result
