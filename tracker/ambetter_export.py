"""
Ambetter's broker portal changed its book export in Oct 2026 (found Nov 2): a plain
Policies.csv instead of policies.zip, and the dates stopped meaning what they used to.
This turns the new export back into the old layout, so every reader of
carrier_books/ambetter.csv (carrier_truth, pastdue, state_roster, reconcile, ...) keeps
working unchanged.

What changed, and how it is undone:
  - Policy Term Date now shows a plan-year end (12/31) even for a lapsed policy, so a
    dead policy reads as in force, and carrier_truth would even ADD it to the book as
    Effectuated. The old export put the real end date there. The lapse now shows only as
    Eligible for Commission = No (and a blank Renewal Type, which matches it row for row
    in both layouts). In the Oct 11 old-layout book every one of the 174 "No" rows was
    terminated, and every in-force row was "Yes", including people still in their grace
    period. So a "No" row gets its end date back from the same place the old export took
    it: the paid-through date (164 of the 179 old terminations ended exactly on it), or
    the start date when nothing was ever paid (10 of the other 15).
    A "Yes" row can be dead too: a member who signed up again for next year is "Yes" with
    an Active renewal while this year's policy is long unpaid. No grace period runs past
    3 months (APTC), so a "Yes" row paid through a date 4+ months before the pull ended
    there. Checked on the Nov 2 export: 162 of the 165 policies the Oct 11 book had as
    terminated come out terminated, 152 on the same end date; the other 3 are "Yes" with
    an Active renewal (one paid through 11/30/2026 since).
  - Policy Effective Date now shows when the member first joined (as far back as 2023)
    instead of when the current policy, or his part in it, began. Readers use it for the
    new-business grace window (carrier_status) and to tell this term's paid-through date
    from an old one (pastdue), so it is written back as the later of the policy and
    broker start: 31 of the 32 Oct/Nov 2026 starts in the Oct 11 book come back (the raw
    column keeps 14).
  - Broker Effective Date now restarts each policy year or plan change, and ended
    policies are left out of the export; both are taken from the book being replaced
    (see normalize).
  - Broker Term Date now shows the plan-year end instead of 12/31/9999 ("still his").
    Policies moved to another agent are left out of the new export (unless he wins them
    back for a future year, when Broker Effective Date is in the future: readers check
    that), so it is written back as 12/31/9999.
  - Cell formats go back to the old ones, because most readers let pandas guess types:
    the subscriber ID is wrapped in ' again (bare, its leading zeros were lost and ID
    matching against HealthSherpa failed), Member Responsibility loses its $, Number of
    Members is a whole number, ICHRA is Yes/No, and the placeholder employer fields
    (01/01/1900, 0) are blank.
  - Columns: 'Employer Plan Start' is 'Employer Start Date' again, 'AR Policy Type' (gone)
    comes back blank, and the old column order is restored.
"""

import pandas as pd

# The old export's columns, in its order.
OLD_COLUMNS = [
    "Broker Name", "Broker NPN", "Policy Number", "Plan Name", "Insured First Name",
    "Insured Last Name", "Broker Effective Date", "Broker Term Date", "Policy Effective Date",
    "Policy Term Date", "Paid Through Date", "Member Responsibility", "Monthly Premium Amount",
    "County", "State", "On/Off Exchange", "Exchange Subscriber ID", "Member Phone Number",
    "Member Email", "Member Date Of Birth", "Autopay", "Eligible for Commission",
    "Number of Members", "Payable Agent", "AR Policy Type", "ICHRA Indicator",
    "Employer Group Name", "Employer Start Date", "Employer Subsidy Amount",
    "Employer Subsidy Type", "Targeted Outreach Group", "Suggested Action", "Renewal Type",
]

# Columns any Ambetter book export has, old or new: how the stager tells it apart from
# another portal's "Policies.csv".
SIGNATURE = ("Policy Number", "Exchange Subscriber ID", "Paid Through Date",
             "Eligible for Commission", "Broker NPN")

# The longest grace period (APTC members) is 3 months; a policy paid through a month
# this many months before the pull has run out of it.
PAST_ANY_GRACE = 4

_FMT = "%m/%d/%Y"


def _d(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format=_FMT, errors="coerce")


def _formats(out: pd.DataFrame) -> pd.DataFrame:
    """Cell formats back to the old export's (see the module docstring)."""
    sid = out["Exchange Subscriber ID"].astype(str).str.strip().str.strip("'")
    out["Exchange Subscriber ID"] = sid.where(sid == "", "'" + sid + "'")
    out["Member Responsibility"] = (out["Member Responsibility"].astype(str)
                                    .str.replace(r"[$,]", "", regex=True).str.strip())
    members = pd.to_numeric(out["Number of Members"], errors="coerce")
    out["Number of Members"] = members.map(lambda v: "" if pd.isna(v) else str(int(v)))
    out["ICHRA Indicator"] = out["ICHRA Indicator"].astype(str).str.strip().str.lower().map(
        {"true": "Yes", "false": "No", "yes": "Yes", "no": "No"}).fillna(out["ICHRA Indicator"])
    no_group = out["Employer Group Name"].astype(str).str.strip() == ""
    out.loc[no_group & (out["Employer Start Date"] == "01/01/1900"), "Employer Start Date"] = ""
    out.loc[no_group & (out["Employer Subsidy Amount"].astype(str).str.strip() == "0"),
            "Employer Subsidy Amount"] = ""
    return out


def normalize(df: pd.DataFrame, as_of, prior: pd.DataFrame = None) -> pd.DataFrame:
    """The export in the old layout with real dates. `as_of` is the day it was pulled;
    `prior` is the book it replaces (carrier_books/ambetter.csv), for what the new export
    no longer keeps: original broker start dates and policies that have ended. Read both with dtype=str and
    keep_default_na=False, so every cell this does not touch is written back exactly as
    Ambetter sent it."""
    if "AR Policy Type" in df.columns:
        return df                                   # already the old layout
    as_of = pd.Timestamp(as_of).normalize()
    out = df.rename(columns={"Employer Plan Start": "Employer Start Date"}).copy()
    for c in OLD_COLUMNS:
        if c not in out.columns:
            out[c] = ""
    out = _formats(out)

    ptd = _d(out["Paid Through Date"])
    eff = _d(out["Policy Effective Date"])
    bed = _d(out["Broker Effective Date"])
    pterm = _d(out["Policy Term Date"])

    # Broker Effective Date now restarts with each policy year or plan change (52 of 558
    # policies on Nov 2, every one moved later, mostly a 2025 date to 01/01/2026), while
    # readers use it as "his client since": tenure, and whether a sale is a re-enrollment
    # (sheets' Is New). Keep the earlier date the book already had. A start after the pull
    # stands only when the book had him leaving the policy (a real broker end: a GA policy
    # moved away 10/03/2026 and won back for 1/1/2027); if it was his all along, it is the
    # renewal reset (a KS policy his since 09/01/2026 read 01/01/2027).
    keep = pd.Series(False, index=out.index)
    kept_bed = bed
    if prior is not None and {"Policy Number", "Broker Effective Date"} <= set(prior.columns):
        p = pd.DataFrame({"pn": prior["Policy Number"].astype(str),
                          "bed": _d(prior["Broker Effective Date"])})
        # 12/31/9999 doesn't parse, so a blank date is an open-ended one: still his.
        p_bterm = (_d(prior["Broker Term Date"]) if "Broker Term Date" in prior.columns
                   else pd.Series(pd.NaT, index=prior.index))
        p["his"] = (p_bterm.isna() | (p_bterm >= as_of)).values
        had = p.dropna(subset=["bed"]).groupby("pn")["bed"].min()
        stayed = p.groupby("pn")["his"].all()
        pn = out["Policy Number"].astype(str)
        earlier = pn.map(had)
        was_his = pn.map(stayed).eq(True)
        keep = earlier.notna() & (earlier < bed) & (~(bed > as_of) | was_his)
        kept_bed = bed.where(~keep, earlier)
        out.loc[keep, "Broker Effective Date"] = earlier[keep].dt.strftime(_FMT)

    # When this policy, or his part in it, began. 25 never-paid rows have no Policy
    # Effective Date at all. A future renewal reset undone above doesn't count.
    start = pd.concat([eff, bed.where(~(keep & (bed > as_of)), kept_bed)], axis=1).max(axis=1)
    out["Policy Effective Date"] = start.dt.strftime(_FMT).fillna("")
    # The end dates below are rebuilt the way the old export dated them, which went by
    # the policy's own start (151 of 165 Oct 11 end dates match this way, 116 by `start`).
    began = eff.fillna(bed)

    # A 12/31 term is the plan-year placeholder (a 2025 policy paid into 2026 still says
    # 12/31/2025); any other term date is a real one Ambetter still sends.
    plan_year_end = (pterm.dt.month == 12) & (pterm.dt.day == 31)
    yes = out["Eligible for Commission"].astype(str).str.strip().str.lower() == "yes"
    no = out["Eligible for Commission"].astype(str).str.strip().str.lower() == "no"

    # "No" rows: where coverage really stopped is the last day paid for, or the start
    # date when nothing was paid in this term. A paid-through date after the pull (one
    # row on Nov 2, a refund) can't be the end of a policy that has already ended: the
    # day before the pull stands in for it.
    ended = ptd.where(ptd.notna() & ~(ptd < began), began)
    ended = ended.where(~(ended >= as_of), as_of - pd.Timedelta(days=1))
    # Nothing paid on a plan that started under a month ago is a first premium still
    # due, not a lapse (every such row was "Yes" on Nov 2; this keeps it that way).
    binder_due = ptd.isna() & (began > as_of - pd.DateOffset(months=1))
    # A start after the pull (next year's renewal, or a policy he wins back on 1/1) has
    # not begun, so it can't have lapsed; readers skip it until then.
    lapsed_no = no & ended.notna() & ~binder_due & ~(start > as_of)

    # "Yes" rows paid in this term but not for longer than any grace period lasts.
    months_behind = (as_of.year - ptd.dt.year) * 12 + (as_of.month - ptd.dt.month)
    lapsed_yes = yes & ptd.notna() & ~(ptd < start) & (months_behind >= PAST_ANY_GRACE)
    ended = ended.where(~lapsed_yes, ptd)

    lapsed = lapsed_no | lapsed_yes
    fix = lapsed & (pterm.isna() | plan_year_end | (ended < pterm))
    out.loc[fix, "Policy Term Date"] = ended[fix].dt.strftime(_FMT)

    # An in-force row paid past its 12/31 placeholder (a TX policy paid through 11/30/2026
    # still said 12/31/2025) runs to the end of the year it is paid into.
    paid_past = ~lapsed & plan_year_end & (ptd > pterm)
    out.loc[paid_past, "Policy Term Date"] = ptd[paid_past].dt.year.map(lambda y: f"12/31/{y}")

    bterm = _d(out["Broker Term Date"])
    open_ended = (bterm.dt.month == 12) & (bterm.dt.day == 31)
    out.loc[open_ended, "Broker Term Date"] = "12/31/9999"

    extra = [c for c in out.columns if c not in OLD_COLUMNS]
    out = out[OLD_COLUMNS + extra]

    # The new export leaves out policies that already ended (14 of the Oct 11 book's on
    # Nov 2, 9 of them his in 2026), which the old one kept, and readers use them as proof
    # a plan ended (the GA/IL stale-roster pass cancels a state-exchange client by them).
    # Keep the book's rows for policies the export no longer lists that had ended before
    # the pull. Ones still running when they dropped out stay out: that absence is all
    # anyone knows.
    if prior is not None and {"Policy Number", "Policy Term Date"} <= set(prior.columns):
        gone = prior[~prior["Policy Number"].astype(str).isin(set(out["Policy Number"].astype(str)))]
        gone = gone[(_d(gone["Policy Term Date"]) < as_of).values]
        if len(gone):
            out = pd.concat([out, gone.reindex(columns=out.columns).fillna("")],
                            ignore_index=True)
    return out
