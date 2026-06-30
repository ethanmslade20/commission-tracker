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


def _norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


def _person_key(first, last):
    """Match key from a (first, last) pair: last[:4]+first[:3]. Tolerant of
    truncated/abbreviated names in the statements (e.g. 'GONZALEZ, ALL')."""
    f = re.sub(r"[^a-z]", "", _norm(first))
    l = re.sub(r"[^a-z]", "", _norm(last))
    return l[:4] + f[:3]


def aor_changed_keys() -> set:
    """Full-name keys of clients CONFIRMED stolen by another agent (their AOR was
    changed), manually curated in data/aor_changed.json. Used to drop them from
    the Money Owed / dispute list even when the HealthSherpa export's policy_aor
    field still LAGS the exchange (hasn't propagated the change yet — e.g. Tammy
    Bennett). Only confirmed AOR *changes* belong here — NEVER 'marketplace
    disconnected' clients, since Ethan is usually still their agent on those."""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "aor_changed.json"
    try:
        items = json.loads(p.read_text())
    except Exception:
        return set()
    keys = set()
    for it in items:
        l = re.sub(r"[^a-z]", "", _norm(it.get("last", "")))
        f = re.sub(r"[^a-z]", "", _norm(it.get("first", "")))
        if l:
            keys.add(l + f)
    return keys


def drop_aor_changed(df):
    """Remove rows whose (first_name,last_name) is on the confirmed-AOR-changed
    override list. Pairs with the policy_aor filter to catch lag cases the export
    field misses. No-op if the list is empty or the name columns are absent."""
    keys = aor_changed_keys()
    if not keys or df is None or getattr(df, "empty", True):
        return df
    if "first_name" not in df.columns or "last_name" not in df.columns:
        return df
    def _k(r):
        l = re.sub(r"[^a-z]", "", _norm(r.get("last_name", "")))
        f = re.sub(r"[^a-z]", "", _norm(r.get("first_name", "")))
        return l + f
    return df[~df.apply(lambda r: _k(r) in keys, axis=1)]


def _member_key(member):
    """Match key for a statement member name, handling 'LAST, FIRST' and 'First Last'."""
    m = _norm(member)
    if "," in m:
        last, first = m.split(",", 1)
    else:
        p = m.split()
        first, last = (p[0] if p else ""), (p[-1] if len(p) > 1 else "")
    return _person_key(first, last)


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
                payment_month=pm, carrier=r[2].strip(), policy_id=r[3].strip(),
                member=r[4].strip(),
                pay_period=r[5].strip(), effective=r[6].strip(),
                subscribers=r[7].strip(), state=r[9].strip(),
                description=r[10].strip(), amount=amt))
    df = pd.DataFrame(rows)
    if not df.empty:
        df["name_key"] = df["member"].apply(_member_key)
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

    # The agent gets two checks a month (~20th and ~27th), so the CURRENT month
    # is incomplete — don't penalize a client for a not-yet-arrived current-month
    # payment. But a payment that IS present (even in the current month) means
    # they're being paid. So: "current" = last paid in the latest COMPLETE month
    # OR later (incl the current month). Only flag if last paid is BEFORE the
    # latest complete month.
    cur = pd.Timestamp(today.year, today.month, 1)
    complete_latest = (cur.to_period("M") - 1).to_timestamp()   # last fully-paid month
    result["latest_month"] = complete_latest

    # last payment month per person over ALL months (incl the current one)
    pos = payments[payments["amount"] > 0]
    last_paid = pos.groupby("name_key")["payment_month"].max().to_dict()
    paid_keys = set(last_paid)

    a = active.copy()
    a["name_key"] = a.apply(lambda r: _person_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    a["_matched"] = a["name_key"].isin(paid_keys)
    result["matched"] = int(a["_matched"].sum())
    result["unmatched"] = int((~a["_matched"]).sum())

    # Stopped = was paid before, but last payment is older than the latest
    # complete month (so it's not just the pending current-month check).
    def _status(row):
        lp = last_paid.get(row["name_key"])
        if lp is None or lp >= complete_latest:
            return None
        return lp
    a["_last_paid"] = a.apply(_status, axis=1)
    miss = a[a["_last_paid"].notna()].copy()
    if not miss.empty:
        miss["Last Paid"] = pd.to_datetime(miss["_last_paid"]).dt.strftime("%b %Y")
        miss["Months Since Paid"] = ((complete_latest.to_period("M").ordinal)
                                     - pd.to_datetime(miss["_last_paid"]).dt.to_period("M").apply(lambda p: p.ordinal)).astype(int)
        result["missing"] = miss

    # Recent chargebacks (negative amounts in the latest complete month or later)
    cb = payments[(payments["amount"] < 0) & (payments["payment_month"] >= complete_latest)].copy()
    result["chargebacks"] = cb
    return result


def unpaid_active(active: pd.DataFrame, payments: pd.DataFrame, today=None,
                  min_months: int = 2) -> pd.DataFrame:
    """Active clients with NO commission payment EVER (any month, including the
    current one — a present payment means they ARE being paid). Excludes clients
    whose coverage started too recently to have a payment due in a complete month
    yet, so brand-new business isn't false-flagged. These are 'active but I have
    never been paid' — verify each (genuine gap vs a name spelled differently)."""
    if active is None or active.empty or payments is None or payments.empty:
        return pd.DataFrame()
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    cur = pd.Timestamp(today.year, today.month, 1)
    # ANY positive payment (incl current month) counts as being paid.
    paid = set(payments[payments["amount"] > 0]["name_key"])
    a = active.copy()
    a["name_key"] = a.apply(lambda r: _person_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    a["_eff"] = pd.to_datetime(a.get("effective_date"), errors="coerce")
    mob = pd.to_numeric(a.get("months_on_book"), errors="coerce").fillna(0)
    # coverage must have started before last month, so a payment was due in a
    # complete month even allowing for a +1-month carrier lag.
    eff_cutoff = cur - pd.DateOffset(months=1)
    elig = (~a["name_key"].isin(paid)) & (mob >= min_months) & (a["_eff"] < eff_cutoff)
    return a[elig].drop(columns=["_eff"], errors="ignore").copy()


def payment_history(payments: pd.DataFrame) -> dict:
    """name_key -> {months: 'Jan 2026, Feb 2026', last: 'Feb 2026', total: $, count}
    from all positive payments. The evidence trail for a commissions dispute."""
    hist = {}
    if payments is None or payments.empty:
        return hist
    pos = payments[payments["amount"] > 0]
    for k, g in pos.groupby("name_key"):
        mos = sorted(pd.to_datetime(g["payment_month"]).unique())
        hist[k] = {
            "months": ", ".join(pd.Timestamp(m).strftime("%b %Y") for m in mos),
            "last": pd.Timestamp(mos[-1]).strftime("%b %Y"),
            "total": float(g["amount"].sum()),
            "count": int(len(g)),
        }
    return hist


def _norm_id(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _base_policy(s):
    return re.sub(r"-\d{1,3}$", "", str(s or "").strip())


def _route_carrier(carrier):
    c = str(carrier).lower()
    if "ambetter" in c: return "ambetter"
    if "oscar" in c: return "oscar"
    if "anthem" in c or "wellpoint" in c or "healthcare plan of georgia" in c: return "anthem"
    return None


def carrier_policy_map(books_dir):
    """{'ambetter'|'oscar'|'anthem': {name_key: policy#}} from the portal exports.
    Kept PER-CARRIER so a same-name client in a different carrier can't grab the
    wrong policy number."""
    import csv
    from pathlib import Path
    base = Path(books_dir)
    amb, osc, ant = {}, {}, {}
    def nk(f, l):
        return _norm_id(str(l) + str(f))[:12]
    p = base / "ambetter.csv"
    if p.exists():
        for r in csv.DictReader(open(p)):
            k = nk(r.get("Insured First Name"), r.get("Insured Last Name"))
            if k and r.get("Policy Number"):
                amb.setdefault(k, r["Policy Number"])
    p = base / "oscar.csv"
    if p.exists():
        for r in csv.DictReader(open(p)):
            nm = str(r.get("Member name") or "").split()
            if len(nm) >= 2 and r.get("Member ID"):
                osc.setdefault(nk(nm[0], nm[-1]), r["Member ID"])
    p = base / "anthem.csv"
    if p.exists():
        try:
            rows = list(csv.DictReader((l for i, l in enumerate(open(p)) if i >= 1)))
        except Exception:
            rows = []
        cid = next((c for c in (rows[0] if rows else {}) if "client" in c.lower() and "id" in c.lower()), None)
        for r in rows:
            nmcell = next((v for v in r.values() if v and "," in str(v)), "")
            pt = str(nmcell).split(",")
            k = nk(pt[1] if len(pt) > 1 else "", pt[0]) if nmcell else ""
            if k and cid and r.get(cid):
                ant.setdefault(k, r[cid])
    return {"ambetter": amb, "oscar": osc, "anthem": ant}


def audit_gaps(gaps, payments, books_dir, today=None):
    """Cross-reference each gap client's carrier policy number against the policy
    IDs on the commission statements (matched on the BASE policy, so a payment
    under a different household member still counts as paid). Adds columns:
      Policy #  — carrier policy number (blank if no portal export for that carrier)
      Ever Paid — Yes / No / ?   (? = can't verify, no carrier export)
      Dispute   — '✅ Dispute' (never paid + established) / '⏳ Too new' / '' / 'needs portal'
    Too-new = effective in the current or previous calendar month (pay cycle not
    complete yet), so those are held rather than disputed."""
    if gaps is None or gaps.empty:
        return gaps
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    cutoff = today.replace(day=1) - pd.offsets.MonthBegin(1)   # first of previous month
    polmap = carrier_policy_map(books_dir)
    paid = set()
    if payments is not None and not payments.empty and "policy_id" in payments.columns:
        for pid in payments["policy_id"].dropna():
            b = _norm_id(_base_policy(pid))
            if b:
                paid.add(b)

    def nk(f, l):
        return _norm_id(str(l) + str(f))[:12]

    pols, ever, disp = [], [], []
    for _, r in gaps.iterrows():
        rt = _route_carrier(r.get("Carrier", ""))
        pol = polmap.get(rt, {}).get(nk(r.get("First Name", ""), r.get("Last Name", ""))) if rt else None
        pols.append(pol or "")
        if pol:
            was_paid = _norm_id(_base_policy(pol)) in paid
            ever.append("Yes" if was_paid else "No")
            eff = pd.to_datetime(r.get("Effective Date"), errors="coerce")
            too_new = pd.notna(eff) and eff >= cutoff
            disp.append("" if was_paid else ("⏳ Too new" if too_new else "✅ Dispute"))
        else:
            ever.append("?")
            disp.append("needs portal")
    g = gaps.copy()
    g["Policy #"] = pols
    g["Ever Paid"] = ever
    g["Dispute"] = disp
    return g


def build_gaps(active: pd.DataFrame, payments: pd.DataFrame, today=None) -> pd.DataFrame:
    """Commission-gap report: active clients never paid or stopped, each with
    their full payment history (which months, last month, total, # payments) so
    it doubles as a dispute report for the commissions team."""
    rec = reconcile_book(active, payments, today)
    hist = payment_history(payments)

    def _row(r, gap):
        k = _person_key(r.get("first_name", ""), r.get("last_name", ""))
        h = hist.get(k)
        # "Client Since" = when they became OUR client (broker-of-record / first
        # seen), not the policy's original coverage date.
        client_since = r.get("client_since")
        if client_since is None or (hasattr(pd, "isna") and pd.isna(client_since)):
            client_since = r.get("broker_effective_date") or r.get("effective_date", "")
        return {
            "First Name": r.get("first_name", ""), "Last Name": r.get("last_name", ""),
            "Carrier": r.get("carrier", ""), "State": r.get("state", ""),
            "Client Since": client_since,
            "Effective Date": r.get("effective_date", ""),
            "Mo. on Book": r.get("months_on_book", ""),
            "Premium": r.get("net_premium", ""), "Gap": gap,
            "Months Paid": (h["months"] if h else "(never)"),
            "Last Paid": (h["last"] if h else "—"),
            "Total Paid": (round(h["total"], 2) if h else 0.0),
            "# Pmts": (h["count"] if h else 0),
            "_key": k,
        }

    rows = [_row(r, "Never paid") for _, r in unpaid_active(active, payments, today).iterrows()]
    never_keys = {r["_key"] for r in rows}
    miss = rec.get("missing")
    if miss is not None and not miss.empty:
        for _, r in miss.iterrows():
            if _person_key(r.get("first_name", ""), r.get("last_name", "")) not in never_keys:
                rows.append(_row(r, "Stopped"))

    df = pd.DataFrame(rows)
    if not df.empty:
        df["Effective Date"] = pd.to_datetime(df["Effective Date"], errors="coerce")
        df["Client Since"] = pd.to_datetime(df["Client Since"], errors="coerce")
        df = (df.sort_values(["Gap", "Carrier", "Last Name"])
              .drop(columns=["_key"]).reset_index(drop=True))
    return df
