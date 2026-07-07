"""
Carrier-portal reconciliation.

A carrier portal export (e.g. the Ambetter Secure Broker Portal "Export Policies"
CSV) is the carrier's OWN record of every policy: whether you are the broker of
record, whether you are eligible to be paid on it, and whether the member is paid
up. That is more authoritative than HealthSherpa for "is this person really active
and earning me a commission."

This module:
  - parse_ambetter_export(src)  -> one normalized row per policy (+ name_key)
  - classify_ambetter(amb, payments, today, book)  -> per-policy verdict that
    cross-references the carrier truth against ACTUAL payments (the PAYMENTS sheet)
    and, optionally, the HealthSherpa book.

The active rule (Ethan's): Ambetter is the final say on who is active. If
HealthSherpa says active but Ambetter says not eligible, Ambetter wins — EXCEPT
for brand-new business Ambetter hasn't caught up to yet. A policy whose effective
date is in the current month or later is in a grace window (e.g. a 7/1 effective
signed in June): don't treat carrier silence as "lost" until it's had a month.
"""

import pandas as pd

from .commissions import _person_key

# Exact-header -> canonical column. Anything not listed is dropped.
_AMBETTER_COLS = {
    "Policy Number":            "policy_number",
    "Plan Name":                "plan_name",
    "Insured First Name":       "first_name",
    "Insured Last Name":        "last_name",
    "Broker Effective Date":    "broker_effective_date",
    "Broker Term Date":         "broker_term_date",
    "Policy Effective Date":    "policy_effective_date",
    "Policy Term Date":         "policy_term_date",
    "Paid Through Date":        "paid_through_date",
    "Member Responsibility":    "member_responsibility",
    "Monthly Premium Amount":   "monthly_premium",
    "County":                   "county",
    "State":                    "state",
    "On/Off Exchange":          "exchange",
    "Member Email":             "email",
    "Member Phone Number":      "phone",
    "Eligible for Commission":  "eligible_raw",
    "Number of Members":        "members",
    "Renewal Type":             "renewal_type",
}

_DATE_COLS = ["broker_effective_date", "broker_term_date", "policy_effective_date",
              "policy_term_date", "paid_through_date"]


def parse_ambetter_export(src) -> pd.DataFrame:
    """src is a path/file-like CSV or an already-loaded DataFrame. Returns one row
    per policy with canonical columns, parsed dates/booleans, and a name_key that
    matches the payments sheet and the book."""
    raw = pd.read_csv(src, dtype=str) if not isinstance(src, pd.DataFrame) else src.copy()
    raw = raw.fillna("")
    raw.columns = [c.strip() for c in raw.columns]

    df = pd.DataFrame()
    for src_col, canon in _AMBETTER_COLS.items():
        df[canon] = raw[src_col].astype(str).str.strip() if src_col in raw.columns else ""

    for c in _DATE_COLS:
        df[c] = pd.to_datetime(df[c], format="%m/%d/%Y", errors="coerce")
    # 12/31/9999 is the carrier's "no term date" sentinel -> treat as still open.
    for c in ("broker_term_date", "policy_term_date"):
        df.loc[df[c] >= pd.Timestamp("2099-01-01"), c] = pd.NaT

    df["members"] = pd.to_numeric(df["members"], errors="coerce").fillna(0).astype(int)
    df["monthly_premium"] = pd.to_numeric(df["monthly_premium"], errors="coerce").fillna(0.0)
    # Carrier's own flag: are you eligible to be paid on this policy?
    df["eligible"] = df["eligible_raw"].str.strip().str.lower().eq("yes")
    df["name_key"] = df.apply(lambda r: _person_key(r["first_name"], r["last_name"]), axis=1)
    df["carrier"] = "Ambetter"
    return df


# verdict buckets (most-actionable first)
DISPUTE   = "Dispute — owed, unpaid"        # eligible + member current + old enough + no recent pay
WINBACK   = "Win-back — member lapsed"      # eligible but member behind on premium
TOONEW    = "Too new — give it a month"     # effective this month or later (grace)
PAID_OK   = "Paid — OK"                     # being paid recently
NOT_ELIG  = "Not eligible — not earning"    # carrier says you are not paid on this


def classify_ambetter(amb: pd.DataFrame, payments: pd.DataFrame, today=None,
                      book: pd.DataFrame = None) -> pd.DataFrame:
    """Cross-reference each Ambetter policy against actual payments. Returns the
    export plus a 'verdict' column and the supporting fields. If `book` (the
    HealthSherpa active book) is given, also flags policies HealthSherpa still
    shows active but the carrier no longer pays on (stale book)."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    cur = pd.Timestamp(today.year, today.month, 1)             # first of this month
    complete_latest = (cur.to_period("M") - 1).to_timestamp()  # last fully-paid month
    # Ambetter pays +1 MONTH in arrears: a June-1 effective's first check is in
    # the JULY statements. Grace must cover effective dates through LAST month,
    # or every 1-month-old policy false-flags the day the month rolls over
    # (34 phantom disputes on 2026-07-07).
    grace_cutoff = cur - pd.offsets.MonthBegin(1)

    out = amb.copy()

    # last positive payment month per person (from the PAYMENTS sheet)
    if payments is not None and not payments.empty:
        pos = payments[payments["amount"] > 0]
        last_paid = pos.groupby("name_key")["payment_month"].max().to_dict()
    else:
        last_paid = {}

    from tracker.commissions import _person_keys

    def _last_paid_for(r):
        # Probe every plausible key — compound last names ('Hottle Cave') and
        # suffixes ('Jones Jr') appear differently across statements vs the
        # carrier book, which false-flagged PAID clients as disputes (2026-07-07).
        cands = _person_keys(r.get("first_name", ""), r.get("last_name", "")) | {r["name_key"]}
        hits = [last_paid[k] for k in cands if k in last_paid]
        return max(hits) if hits else None

    def _verdict(r):
        if not r["eligible"]:
            return NOT_ELIG
        lp = _last_paid_for(r)
        paid_recently = lp is not None and lp >= complete_latest
        if paid_recently:
            return PAID_OK
        eff = r["policy_effective_date"]
        if pd.notna(eff) and eff >= grace_cutoff:
            return TOONEW
        # eligible, old enough, not paid recently -> is the member still current?
        ptd = r["paid_through_date"]
        member_current = pd.notna(ptd) and ptd >= cur
        return DISPUTE if member_current else WINBACK

    out["verdict"] = out.apply(_verdict, axis=1)
    out["last_paid"] = out.apply(
        lambda r: (lambda lp: pd.Timestamp(lp).strftime("%b %Y") if lp is not None else "—")(_last_paid_for(r)),
        axis=1)

    # Optional: HealthSherpa shows active, carrier says not eligible & past grace.
    out["hs_stale"] = False
    if book is not None and not book.empty:
        b = book.copy()
        b["name_key"] = b.apply(lambda r: _person_key(r.get("first_name", ""),
                                                      r.get("last_name", "")), axis=1)
        hs_active = set(b["name_key"])
        out["hs_stale"] = out.apply(
            lambda r: (not r["eligible"]) and (r["name_key"] in hs_active)
            and pd.notna(r["policy_effective_date"])
            and r["policy_effective_date"] < grace_cutoff, axis=1)
    return out


# win-back source/priority labels
SRC_CARRIER = "Carrier"
SRC_HS      = "HealthSherpa"
SRC_BOTH    = "Both"


def build_winback(classified: pd.DataFrame, hs_dropped=None, hs_active=None) -> pd.DataFrame:
    """Unified Win-Back / Re-Engage list for Ambetter members, merging carrier
    truth with HealthSherpa drop signals.

    Truth rule: the carrier is authoritative on whether a member is still a paying
    client; HealthSherpa is the daily early-warning net. Where they disagree we
    label it rather than guess.

      hs_dropped : name_keys HealthSherpa shows cancelled/terminated/missing
      hs_active  : name_keys HealthSherpa currently shows active

    (Pure-HealthSherpa win-backs for NON-Ambetter carriers are handled upstream
    in report.py — this only reconciles members present in the carrier export.)
    """
    hs_dropped = set(hs_dropped or [])
    rows = []
    for _, r in classified.iterrows():
        k = r["name_key"]
        v = r["verdict"]
        dropped = k in hs_dropped
        carrier_gone = not r["eligible"]
        carrier_ok = r["eligible"] and v in (PAID_OK, DISPUTE)  # eligible + member current
        stale = bool(r.get("hs_stale", False))

        if v == WINBACK:                       # carrier: premium lapsed, still eligible
            src, conf, reason, action = (SRC_CARRIER, "High",
                "Premium lapsed at carrier (Paid Through past) — still your policy",
                "Win back NOW — in grace period")
        elif dropped and carrier_gone:         # both sources agree gone
            src, conf, reason, action = (SRC_BOTH, "High",
                "HealthSherpa dropped + carrier no longer eligible", "Win back")
        elif stale:                            # carrier gone, HS still shows active
            src, conf, reason, action = (SRC_CARRIER, "High",
                "Carrier no longer pays; HealthSherpa still shows active (stale)",
                "Win back / fix HealthSherpa")
        elif dropped and carrier_ok:           # conflict — carrier says still yours
            src, conf, reason, action = (SRC_CARRIER, "Verify",
                "HealthSherpa dropped them but carrier shows active & current",
                "Verify only — likely data sync, probably NOT a real loss")
        else:
            continue

        rows.append({
            "First Name": r["first_name"], "Last Name": r["last_name"],
            "Carrier": "Ambetter", "State": r["state"], "Policy #": r["policy_number"],
            "Source": src, "Confidence": conf, "Reason": reason, "Action": action,
            "Paid Through": (r["paid_through_date"].strftime("%m/%d/%Y")
                             if pd.notna(r["paid_through_date"]) else "—"),
            "Last Paid": r["last_paid"], "Members": r["members"],
            "Phone": r.get("phone", ""), "Email": r.get("email", ""),
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        pri = {"High": 0, "Verify": 1}
        df["_p"] = df["Confidence"].map(pri).fillna(2)
        df = df.sort_values(["_p", "Carrier", "Last Name"]).drop(columns="_p").reset_index(drop=True)
    return df


def dispute_display(classified: pd.DataFrame) -> pd.DataFrame:
    """Sheet/page-ready dispute list: carrier confirms you're owed (eligible +
    member current) but the payments sheet shows no recent payment."""
    d = classified[classified["verdict"] == DISPUTE].copy()
    if d.empty:
        return pd.DataFrame()
    out = pd.DataFrame({
        "First Name": d["first_name"], "Last Name": d["last_name"],
        "Carrier": "Ambetter", "State": d["state"], "Policy #": d["policy_number"],
        "Effective": d["policy_effective_date"].dt.strftime("%m/%d/%Y"),
        "Paid Through": d["paid_through_date"].dt.strftime("%m/%d/%Y"),
        "Eligible (carrier)": "Yes", "Last Paid": d["last_paid"],
        "Members": d["members"], "Monthly Premium": d["monthly_premium"].round(2),
        "Phone": d["phone"], "Email": d["email"],
    })
    return out.sort_values(["State", "Last Name"]).reset_index(drop=True)


def verdict_summary(classified: pd.DataFrame) -> pd.DataFrame:
    """Count of policies + members by verdict, dispute-first."""
    order = [DISPUTE, WINBACK, TOONEW, PAID_OK, NOT_ELIG]
    g = (classified.groupby("verdict")
         .agg(Policies=("policy_number", "count"), Members=("members", "sum"))
         .reset_index())
    g["_o"] = g["verdict"].apply(lambda v: order.index(v) if v in order else 99)
    return g.sort_values("_o").drop(columns="_o").reset_index(drop=True)
