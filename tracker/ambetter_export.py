"""
Ambetter's broker portal changed its book export in Oct 2026 (found Nov 2): a plain
Policies.csv instead of policies.zip, and the dates stopped meaning what they used to.
This turns the new export back into the old layout, so every reader of
carrier_books/ambetter.csv (carrier_truth, pastdue, state_roster, reconcile, ...) keeps
working unchanged.

What changed, and how it is undone:
  - Policy Term Date now shows the plan-year end (12/31) even for a lapsed policy, so a
    dead policy reads as in force, and carrier_truth would even ADD it to the book as
    Effectuated. The old export put the real end date there. The lapse now shows only as
    Eligible for Commission = No (and a blank Renewal Type, which matches it row for row
    in both layouts). In the Oct 11 old-layout book every one of the 174 "No" rows was
    terminated, and every in-force row was "Yes", including people still in their grace
    period. So a "No" row gets its end date back from the same place the old export took
    it: the paid-through date (164 of the 179 old terminations ended exactly on it), or
    the start date when nothing was ever paid (10 of the other 15). Checked on the Nov 2
    export: 159 of the 165 policies the Oct 11 book had as terminated come out
    terminated, 151 on the same end date. The other 6 are now "Yes" with a renewal on
    file in Ambetter's own data.
  - Broker Term Date now shows the plan-year end instead of 12/31/9999 ("still his").
    Policies moved to another agent are left out of the new export altogether, so every
    row is his: written back as 12/31/9999.
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

_FMT = "%m/%d/%Y"


def _d(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format=_FMT, errors="coerce")


def normalize(df: pd.DataFrame, as_of) -> pd.DataFrame:
    """The export in the old layout with real term dates. `as_of` is the day it was
    pulled. Read the export with dtype=str and keep_default_na=False, so every cell
    this does not touch is written back exactly as Ambetter sent it."""
    if "AR Policy Type" in df.columns:
        return df                                   # already the old layout
    as_of = pd.Timestamp(as_of).normalize()
    out = df.rename(columns={"Employer Plan Start": "Employer Start Date"}).copy()
    for c in OLD_COLUMNS:
        if c not in out.columns:
            out[c] = ""

    ptd = _d(out["Paid Through Date"])
    eff = _d(out["Policy Effective Date"])
    bed = _d(out["Broker Effective Date"])
    pterm = _d(out["Policy Term Date"])
    start = eff.fillna(bed)              # 25 never-paid rows carry no Policy Effective Date
    # Where coverage really stopped: the last day paid for, or the start date when nothing
    # was ever paid in this term.
    ended = ptd.where(ptd.notna() & ~(ptd < start), start)
    # Nothing paid on a plan that started under a month ago is a first premium still
    # due, not a lapse (every such row was "Yes" on Nov 2; this keeps it that way).
    binder_due = ptd.isna() & (start > as_of - pd.DateOffset(months=1))
    lapsed = (out["Eligible for Commission"].astype(str).str.strip().str.lower() == "no") \
        & ended.notna() & ~binder_due
    # A 12/31 term is the plan-year placeholder (a 2025 policy paid into 2026 still says
    # 12/31/2025), so the paid-through date wins over it. Any other term date is a real
    # one Ambetter still sends; keep it when it is earlier.
    plan_year_end = (pterm.dt.month == 12) & (pterm.dt.day == 31)
    fix = lapsed & (pterm.isna() | plan_year_end | (ended < pterm))
    out.loc[fix, "Policy Term Date"] = ended[fix].dt.strftime(_FMT)

    bterm = _d(out["Broker Term Date"])
    open_ended = (bterm.dt.month == 12) & (bterm.dt.day == 31)
    out.loc[open_ended, "Broker Term Date"] = "12/31/9999"

    extra = [c for c in out.columns if c not in OLD_COLUMNS]
    return out[OLD_COLUMNS + extra]
