"""
Audit script for the "Policies Lost" calculation in the commission tracker.
Run from ~/commission-tracker with the project venv.
"""

import sys
import os
sys.path.insert(0, "/Users/ethanslade/commission-tracker")

import pandas as pd
import numpy as np

SNAPSHOT_DIR = "/Users/ethanslade/commission-tracker/snapshots"

# ─── Load all parquet files individually ───────────────────────────────────────
files = sorted([f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".parquet")])
print("=" * 70)
print("SNAPSHOT FILES FOUND:")
for f in files:
    print(f"  {f}")

snapshots = {}   # filename -> df
months_raw = {}  # YYYY-MM -> [df, ...]

for fname in files:
    path = os.path.join(SNAPSHOT_DIR, fname)
    df = pd.read_parquet(path)
    snapshots[fname] = df
    stem = fname.replace(".parquet", "")
    month_key = stem.split("_", 1)[0]
    months_raw.setdefault(month_key, []).append(df)

months = {m: pd.concat(dfs, ignore_index=True) for m, dfs in months_raw.items()}

print(f"\nMonths loaded: {sorted(months.keys())}")
for m, df in sorted(months.items()):
    print(f"  {m}: {len(df)} rows, columns: {list(df.columns)}")

# ─── Replicate build_all_clients from diff.py ──────────────────────────────────
print("\n" + "=" * 70)
print("BUILDING all_clients (replicating diff.py build_all_clients)...")

def normalize_name(name):
    import re, unicodedata
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()

def match_client_id(df):
    df = df.copy()
    if "name_key" not in df.columns and "client_name" in df.columns:
        df["name_key"] = df["client_name"].apply(normalize_name)
    df["client_key"] = df["policy_id"].where(
        df["policy_id"].notna()
        & (df["policy_id"] != "")
        & (df["policy_id"] != "nan"),
        other=df.get("name_key", ""),
    )
    return df

rows = []
for month_key in sorted(months.keys()):
    df = match_client_id(months[month_key]).copy()
    df["month"] = month_key
    rows.append(df)

all_df = pd.concat(rows, ignore_index=True)

last_fields = {
    col: (col, "last")
    for col in ["client_name", "first_name", "last_name", "carrier",
                "effective_date", "term_date", "state", "ffm_app_id", "net_premium", "applicant_count"]
    if col in all_df.columns
}

agg = (
    all_df.groupby("client_key")
    .agg(
        first_seen=("month", "min"),
        last_seen=("month", "max"),
        status=("status", "last"),
        **last_fields,
    )
    .reset_index()
)

latest = max(months.keys())
latest_y, latest_m = int(latest[:4]), int(latest[5:7])

def _calendar_months(eff_date):
    try:
        eff = pd.Timestamp(eff_date)
        if pd.isna(eff):
            return None
        return (latest_y - eff.year) * 12 + (latest_m - eff.month) + 1
    except Exception:
        return None

agg["months_on_book"] = agg["effective_date"].apply(_calendar_months)

cols = [
    "client_key", "first_name", "last_name", "carrier", "effective_date",
    "term_date", "status", "state", "ffm_app_id", "net_premium", "applicant_count",
    "first_seen", "last_seen", "months_on_book",
]
all_clients = agg[[c for c in cols if c in agg.columns]]

print(f"all_clients shape: {all_clients.shape}")
print(f"Unique client_keys: {all_clients['client_key'].nunique()}")

# ─── Q1: Exact logic for "Policies Lost" in _build_mom_from_all_clients ────────
print("\n" + "=" * 70)
print("Q1: POLICIES LOST LOGIC")
print("""
From dashboard.py lines 184-185:
    lost_mask = term.notna() & (term >= month_start) & (term <= month_end)
    policies_lost = int(lost_mask.sum())

Where:
    term = pd.to_datetime(all_clients.get("term_date"), errors="coerce")
    month_start = first day of the month
    month_end   = last day of the month (MonthEnd(0))

i.e. a row is counted as LOST in month M if its term_date falls within
[month_start, month_end] — regardless of status field.
""")

# ─── Reproduce MoM for June 2026 ───────────────────────────────────────────────
import datetime

eff   = pd.to_datetime(all_clients.get("effective_date"), errors="coerce")
term  = pd.to_datetime(all_clients.get("term_date"),      errors="coerce")
count = pd.to_numeric(
    all_clients.get("applicant_count", pd.Series([1] * len(all_clients))),
    errors="coerce",
).fillna(1)

today = datetime.date.today()
end_month  = pd.Timestamp(f"{today.year}-{today.month:02d}-01")
months_idx = pd.date_range(start="2026-04-01", end=end_month, freq="MS")

print("\nREPRODUCED MoM TABLE:")
print("-" * 60)
prev_total = None
june_lost_mask = None
for month_start in months_idx:
    month_end = month_start + pd.offsets.MonthEnd(0)

    active_mask = (eff <= month_end) & (term.isna() | (term >= month_start))
    total_policies = int(active_mask.sum())
    total_members  = int(count[active_mask].sum())

    new_mask     = (eff >= month_start) & (eff <= month_end)
    new_policies = int(new_mask.sum())

    lost_mask     = term.notna() & (term >= month_start) & (term <= month_end)
    policies_lost = int(lost_mask.sum())

    net_change = new_policies - policies_lost
    growth_pct = (
        round(net_change / prev_total * 100, 2)
        if prev_total and prev_total > 0 else 0.0
    )
    print(f"  {month_start.strftime('%Y-%m')}: total={total_policies}  new={new_policies}  lost={policies_lost}  net={net_change}  growth={growth_pct}%")
    prev_total = total_policies

    if month_start.strftime("%Y-%m") == "2026-06":
        june_lost_mask = lost_mask.copy()

# ─── Q5: Examine the June 2026 losses in detail ────────────────────────────────
print("\n" + "=" * 70)
print("Q5: JUNE 2026 LOSSES — DETAILED BREAKDOWN")

if june_lost_mask is not None:
    june_lost = all_clients[june_lost_mask].copy()
    print(f"\nTotal rows with term_date in 2026-06: {len(june_lost)}")

    print("\n--- Carrier distribution ---")
    print(june_lost["carrier"].value_counts(dropna=False).to_string())

    print("\n--- State distribution ---")
    if "state" in june_lost.columns:
        print(june_lost["state"].value_counts(dropna=False).to_string())
    else:
        print("  (no state column)")

    print("\n--- Status values ---")
    print(june_lost["status"].value_counts(dropna=False).to_string())

    print("\n--- term_date populated vs null ---")
    n_null = june_lost["term_date"].isna().sum()
    n_pop  = june_lost["term_date"].notna().sum()
    print(f"  Populated: {n_pop}  |  Null: {n_null}")
    print("  (All should be populated since the lost_mask requires term.notna())")

    print("\n--- Unique term_date values ---")
    print(june_lost["term_date"].value_counts(dropna=False).to_string())

    print("\n--- policy_id fallback check ---")
    # Rows whose client_key starts with "HEALTHSHERPA_" or another source prefix are name-based
    name_based = june_lost[june_lost["client_key"].str.match(r"^[A-Z]+_[a-z]", na=False)]
    real_id    = june_lost[~june_lost["client_key"].str.match(r"^[A-Z]+_[a-z]", na=False)]
    print(f"  Name-based IDs (SOURCE_firstname_lastname): {len(name_based)}")
    print(f"  Real policy IDs: {len(real_id)}")

    print("\n--- first_seen / last_seen for June losses ---")
    print(june_lost[["client_key", "first_name", "last_name", "carrier", "status",
                      "effective_date", "term_date", "first_seen", "last_seen",
                      "state"]].to_string(index=False))

# ─── Q6: False positives — lost + also active in June 2026 ────────────────────
print("\n" + "=" * 70)
print("Q6: FALSE POSITIVE CHECK — lost AND active in June 2026")

if june_lost_mask is not None:
    june_start = pd.Timestamp("2026-06-01")
    june_end   = june_start + pd.offsets.MonthEnd(0)

    # Active in June: eff <= june_end AND (term is null OR term >= june_start)
    june_active_mask = (eff <= june_end) & (term.isna() | (term >= june_start))
    june_active = all_clients[june_active_mask].copy()

    # Overlap: appears in both lost and active sets
    # A row counted as "lost" (term in June) is ALSO active in June if term >= june_start
    # By the active_mask definition, that's always true for term in June. Check the logic:
    print("\nNOTE: The active_mask uses (term >= month_start), so a policy terminated on")
    print("2026-06-30 IS counted as active for June AND also as lost in June.")
    print("This is an intentional overlap in the formulas — but is it correct?")

    overlap = all_clients[june_lost_mask & june_active_mask]
    print(f"\nPolicies counted as BOTH active AND lost in June 2026: {len(overlap)}")
    if len(overlap) > 0:
        print(overlap[["client_key", "first_name", "last_name", "carrier", "status",
                        "effective_date", "term_date", "state"]].to_string(index=False))

    # Also check: are any june-lost client_keys present in the active-only set?
    lost_keys   = set(june_lost["client_key"])
    active_keys = set(june_active["client_key"])
    both = lost_keys & active_keys
    print(f"\nDistinct client_keys in both lost and active sets: {len(both)}")

# ─── Q7: Status vs term_date agreement ────────────────────────────────────────
print("\n" + "=" * 70)
print("Q7: STATUS vs TERM_DATE — cross-tab analysis")

_ACTIVE_STATUSES   = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
_INACTIVE_STATUSES = {"Terminated", "Cancelled"}

has_term   = all_clients["term_date"].notna()
no_term    = all_clients["term_date"].isna()
is_active  = all_clients["status"].isin(_ACTIVE_STATUSES)
is_inactive= all_clients["status"].isin(_INACTIVE_STATUSES)

print(f"\nEffectuated/Active but term_date IS SET:      {int((is_active & has_term).sum())}")
print(f"Effectuated/Active and term_date is null:     {int((is_active & no_term).sum())}")
print(f"Cancelled/Terminated but term_date is NULL:   {int((is_inactive & no_term).sum())}")
print(f"Cancelled/Terminated AND term_date is set:    {int((is_inactive & has_term).sum())}")

# Show sample of active + has term_date
bad_active = all_clients[is_active & has_term]
if len(bad_active) > 0:
    print(f"\nSample of Active-status rows WITH term_date (first 20):")
    print(bad_active[["client_key", "first_name", "last_name", "carrier",
                       "status", "effective_date", "term_date", "state",
                       "first_seen", "last_seen"]].head(20).to_string(index=False))

# Show sample of Cancelled + no term_date
bad_cancelled = all_clients[is_inactive & no_term]
if len(bad_cancelled) > 0:
    print(f"\nSample of Inactive-status rows WITHOUT term_date (first 20):")
    print(bad_cancelled[["client_key", "first_name", "last_name", "carrier",
                          "status", "effective_date", "term_date", "state",
                          "first_seen", "last_seen"]].head(20).to_string(index=False))

# ─── Q2: Deduplication detail ─────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Q2: HOW all_clients IS BUILT — deduplication details")

print(f"\nTotal raw rows across all months: {len(all_df)}")
print(f"Total unique client_keys in all_clients: {len(all_clients)}")
print(f"Rows collapsed by dedup: {len(all_df) - len(all_clients)}")

# How many client_keys appear in multiple months?
multi_month = all_df.groupby("client_key")["month"].nunique()
print(f"\nClient keys seen in only 1 month: {(multi_month == 1).sum()}")
print(f"Client keys seen in 2+ months: {(multi_month >= 2).sum()}")
print(f"Client keys seen in all {len(months)} months: {(multi_month == len(months)).sum()}")

# Policies that appear in June with term_date in June — what was their last_seen?
if june_lost_mask is not None:
    print("\nFor June-2026 losses: last_seen month distribution")
    print(june_lost["last_seen"].value_counts(dropna=False).to_string())

# ─── Q3: term_date sourcing by parquet file ────────────────────────────────────
print("\n" + "=" * 70)
print("Q3: TERM_DATE SOURCING — per-file analysis")

for fname, df in snapshots.items():
    if "term_date" in df.columns:
        n_total  = len(df)
        n_set    = df["term_date"].notna().sum()
        n_null   = df["term_date"].isna().sum()
        print(f"\n  {fname}: {n_total} rows  |  term_date set: {n_set}  |  null: {n_null}")
        if n_set > 0:
            print(f"    Sample term_dates: {df['term_date'].dropna().head(5).tolist()}")
            if "status" in df.columns:
                ct = df[df["term_date"].notna()]["status"].value_counts()
                print(f"    Status of rows with term_date: {ct.to_dict()}")
    else:
        print(f"\n  {fname}: NO term_date column")

# ─── Q4: policy_id reliability ────────────────────────────────────────────────
print("\n" + "=" * 70)
print("Q4: POLICY_ID RELIABILITY — per-file analysis")

for fname, df in snapshots.items():
    if "policy_id" in df.columns:
        n_null  = df["policy_id"].isna().sum()
        n_blank = (df["policy_id"].fillna("") == "").sum()
        n_named = df["policy_id"].fillna("").str.match(r"^[A-Z]+_[a-z]").sum()
        n_real  = len(df) - n_null - n_named
        print(f"\n  {fname}: total={len(df)}  null={n_null}  name-based={n_named}  real={n_real}")
        if n_named > 0:
            print(f"    Sample name-based IDs: {df[df['policy_id'].str.match(r'^[A-Z]+_[a-z]', na=False)]['policy_id'].head(3).tolist()}")
    else:
        print(f"\n  {fname}: NO policy_id column")

# ─── Check for name-change duplicates across months ──────────────────────────
print("\n" + "=" * 70)
print("NAME-CHANGE DUPLICATE CHECK")
# Look for rows where the same policy_id appears but with different name_key values
if "name_key" in all_df.columns:
    pid_names = all_df.groupby("policy_id")["name_key"].nunique()
    multi_name = pid_names[pid_names > 1]
    print(f"Policy IDs with 2+ different name_keys across months: {len(multi_name)}")
    if len(multi_name) > 0:
        for pid in multi_name.index[:10]:
            names = all_df[all_df["policy_id"] == pid][["month", "name_key", "first_name", "last_name"]].drop_duplicates()
            print(f"\n  policy_id={pid}:")
            print(names.to_string(index=False))

print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
