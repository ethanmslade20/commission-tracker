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


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _strip_suffix(s):
    """Drop generational suffixes: 'Hippolyte Jr' -> 'hippolyte'. A bare 'Jr'
    poisoned match keys and produced FALSE 'never paid' disputes (2026-07-07,
    Timothy Hippolyte Jr / Joseph Jones Jr — ABM proved both were paid)."""
    return " ".join(t for t in _norm(s).split() if t not in _SUFFIXES)


def _person_key(first, last):
    """Match key from a (first, last) pair: last[:4]+first[:3]. Tolerant of
    truncated/abbreviated names in the statements (e.g. 'GONZALEZ, ALL').
    Suffix-stripped so 'Jones Jr' keys like 'Jones'."""
    f = re.sub(r"[^a-z]", "", _strip_suffix(first))
    l = re.sub(r"[^a-z]", "", _strip_suffix(last))
    return l[:4] + f[:3]


def _person_keys(first, last) -> set:
    """ALL plausible keys for a person, so compound last names match however a
    statement writes them: 'Hannah Hottle Cave' appears as 'CAVE, HANNAH' in one
    statement and 'Hannah Hottle Cave' in another. Candidates: first word, last
    word, and the squashed whole of the last-name string."""
    fw = _strip_suffix(first).split()
    fvars = {re.sub(r"[^a-z]", "", "".join(fw))[:3]}           # 'Ta Nisha' -> 'tan'
    if fw:
        fvars.add(re.sub(r"[^a-z]", "", fw[0])[:3])            # 'Ta Nisha' -> 'ta'
    words = _strip_suffix(last).split()
    lvars = {re.sub(r"[^a-z]", "", w)[:4] for w in (words[:1] + words[-1:])}
    if words:
        lvars.add(re.sub(r"[^a-z]", "", "".join(words))[:4])
    cands = {l + f for l in (lvars or {""}) for f in fvars}
    return cands or fvars


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


def aor_changed_agents() -> dict:
    """Map full-name key -> the taking agent's display string ("Name (NPN: #####)")
    for confirmed-AOR-changed clients that name the new agent in data/aor_changed.json.
    Lets the report stamp the REAL agent (so a stolen client reads "AOR taken —
    Yitzchak Nassy" instead of a generic "another agent"). Entries with no agent
    named are omitted; the report falls back to the generic label for those."""
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "data" / "aor_changed.json"
    try:
        items = json.loads(p.read_text())
    except Exception:
        return {}
    out = {}
    for it in items:
        l = re.sub(r"[^a-z]", "", _norm(it.get("last", "")))
        f = re.sub(r"[^a-z]", "", _norm(it.get("first", "")))
        agent = str(it.get("agent", "")).strip()
        if l and agent:
            npn = str(it.get("npn", "")).strip()
            out[l + f] = f"{agent} (NPN: {npn})" if npn else agent
    return out


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
    m = _strip_suffix(member)
    if "," in m:
        last, first = m.split(",", 1)
    else:
        p = m.split()
        first, last = (p[0] if p else ""), (p[-1] if len(p) > 1 else "")
    return _person_key(first, last)


def parse_payments_sheet(spreadsheet) -> pd.DataFrame:
    # One values_batch_get for ALL monthly tabs instead of get_all_values per
    # tab — the per-tab version took ~10s on every cold page load that touches
    # payments (and risked 20-60s quota-retry sleeps).
    titles = [ws.title for ws in spreadsheet.worksheets()]
    vals_by_tab: dict = {}
    CH = 40
    for i in range(0, len(titles), CH):
        chunk = titles[i:i + CH]
        resp = spreadsheet.values_batch_get([f"'{t}'" for t in chunk])
        for t, vr in zip(chunk, resp.get("valueRanges", [])):
            vals_by_tab[t] = vr.get("values", [])

    rows = []
    for title, tab_vals in vals_by_tab.items():
        if title.strip().lower() == "year to date":
            continue
        pm = _tab_month(title)
        if pm is None:
            continue
        for r in tab_vals:
            # the values API trims trailing empty cells (get_all_values used to
            # pad) — pad back so positional access behaves identically
            if len(r) < 12:
                r = list(r) + [""] * (12 - len(r))
            if not r[2].strip():
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
    a["_keyset"] = a.apply(lambda r: _person_keys(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    a["_matched"] = a.apply(lambda r: bool((r["_keyset"] | {r["name_key"]}) & paid_keys), axis=1)
    result["matched"] = int(a["_matched"].sum())
    result["unmatched"] = int((~a["_matched"]).sum())

    # Stopped = was paid before, but last payment is older than the latest
    # complete month (so it's not just the pending current-month check).
    def _status(row):
        _hits = [last_paid[k] for k in (row["_keyset"] | {row["name_key"]}) if k in last_paid]
        lp = max(_hits) if _hits else None
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
    # Paid under ANY plausible key (suffix/compound-surname variants) = paid.
    _paid_any = a.apply(lambda r: bool((_person_keys(r.get("first_name", ""), r.get("last_name", ""))
                                        | {r["name_key"]}) & paid), axis=1)
    a["_eff"] = pd.to_datetime(a.get("effective_date"), errors="coerce")
    mob = pd.to_numeric(a.get("months_on_book"), errors="coerce").fillna(0)
    # coverage must have started before last month, so a payment was due in a
    # complete month even allowing for a +1-month carrier lag.
    eff_cutoff = cur - pd.DateOffset(months=1)
    elig = (~_paid_any) & (mob >= min_months) & (a["_eff"] < eff_cutoff)
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


def _carrier_brand(c) -> str:
    c = str(c).lower()
    for kw, b in (("ambetter", "Ambetter"), ("oscar", "Oscar"), ("wellpoint", "Anthem"),
                  ("anthem", "Anthem"), ("unitedhealth", "UnitedHealthcare"),
                  ("united health", "UnitedHealthcare"), ("uhc", "UnitedHealthcare"),
                  ("cigna", "Cigna"), ("molina", "Molina"), ("selecthealth", "SelectHealth"),
                  ("select health", "SelectHealth"), ("blue", "BCBS"), ("bcbs", "BCBS"),
                  ("u of u", "University of Utah"), ("university of utah", "University of Utah")):
        if kw in c:
            return b
    return str(c).title()[:16] or "Other"


def carrier_lags(payments: pd.DataFrame) -> dict:
    """Per-CARRIER-BRAND pay lag in months = (paid month) − (coverage month), from PMPM
    rows only, mode, capped 0–3 to ignore retro catch-ups. Brand-keyed so it matches the
    roster's brand-collapsed carrier. This is the correct method (coverage month, not
    effective date, so it isn't skewed by clients whose coverage predates the data)."""
    if payments is None or payments.empty:
        return {"_default": 1}
    pm = payments[payments.get("description", "").astype(str).str.upper() == "PMPM"].copy()
    pm["pp"] = pd.to_datetime(pm.get("pay_period"), errors="coerce")
    pm = pm.dropna(subset=["pp"])
    if pm.empty:
        return {"_default": 1}
    pm["lag"] = (pm["payment_month"].dt.year * 12 + pm["payment_month"].dt.month) \
        - (pm["pp"].dt.year * 12 + pm["pp"].dt.month)
    pm = pm[(pm["lag"] >= 0) & (pm["lag"] <= 3)]
    pm["brand"] = pm["carrier"].apply(_carrier_brand)
    out = {b: int(g["lag"].mode().iloc[0]) for b, g in pm.groupby("brand") if len(g)}
    out["_default"] = int(pm["lag"].mode().iloc[0]) if len(pm) else 1
    return out


def month_reconciliation(active: pd.DataFrame, payments: pd.DataFrame, today=None) -> dict:
    """Reconcile the active book against paid commission BY ARRIVAL MONTH, using each
    carrier's learned pay lag. For each month M: how many active clients should have had a
    payment land that month (effective + carrier lag ≤ M), how many did, who's missing, and
    how many active clients simply aren't due yet. A month is 'closed' once statements dated
    after it exist (so we know all of M's payments are in). Returns:
      {"lags": {brand: lag}, "months": DataFrame[Month, Expected, Paid, Missing, NotDue,
       Closed, AtRisk], "detail": {month_str: DataFrame of missing clients}}."""
    empty = {"lags": {}, "months": pd.DataFrame(), "detail": {}}
    if active is None or active.empty or payments is None or payments.empty:
        return empty
    lags = carrier_lags(payments)
    dflt = lags.get("_default", 1)
    pmt = payments.copy()
    pmt["am"] = pd.to_datetime(pmt["payment_month"], errors="coerce").dt.to_period("M")
    pmt = pmt.dropna(subset=["am"])
    if pmt.empty:
        return empty
    paid_set = set(zip(pmt["name_key"].astype(str), pmt["am"]))
    max_paid = pmt["am"].max()
    cur = pd.Period(pd.Timestamp(today), "M") if today else pd.Timestamp.today().to_period("M")

    a = active.copy()
    a["_k"] = [_person_key(f, l) for f, l in zip(a.get("first_name", ""), a.get("last_name", ""))]
    a["_em"] = pd.to_datetime(a.get("effective_date"), errors="coerce").dt.to_period("M")
    a["_brand"] = a.get("carrier", "").apply(_carrier_brand)
    a["_lag"] = a["_brand"].map(lambda b: lags.get(b, dflt))
    a["_mem"] = pd.to_numeric(a.get("applicant_count"), errors="coerce").fillna(1).clip(lower=1).astype(int)
    a = a.dropna(subset=["_em"])

    months = pd.period_range(pmt["am"].min(), cur, freq="M")
    rows, detail = [], {}
    for M in months:
        due = a[(a["_em"] + a["_lag"]) <= M]                      # first payment due by M
        exp = len(due)
        paid_mask = [(k, M) in paid_set for k in due["_k"]]
        miss = due[[not p for p in paid_mask]]
        notdue = int(((a["_em"] + a["_lag"]) > M).sum()) if M == cur else 0
        closed = bool(M < max_paid)
        rows.append({"Month": str(M), "Expected": exp, "Paid": exp - len(miss),
                     "Missing": len(miss), "NotDue": notdue, "Closed": closed,
                     "AtRisk": float((miss["_mem"] * 23).sum()) if len(miss) else 0.0})
        detail[str(M)] = miss
    return {"lags": {k: v for k, v in lags.items() if k != "_default"},
            "months": pd.DataFrame(rows), "detail": detail}


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
        # Dispute timing keys off the CURRENT active plan's start, not the earliest
        # plan ever (effective_date = MIN). A client with an old cancelled plan and a
        # new June-1 plan must read as June-1 here, else "too new" is wrongly skipped
        # and they're flagged as a real dispute when no commission is due yet.
        cur_eff = r.get("current_effective")
        if cur_eff is None or (hasattr(pd, "isna") and pd.isna(cur_eff)):
            cur_eff = r.get("effective_date", "")
        return {
            "First Name": r.get("first_name", ""), "Last Name": r.get("last_name", ""),
            "Carrier": r.get("carrier", ""), "State": r.get("state", ""),
            "Client Since": client_since,
            "Effective Date": cur_eff,
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
