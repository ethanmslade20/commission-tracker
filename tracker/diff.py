"""
Computes client-level diffs between two monthly snapshots.

Identity is person-based (name_key = normalized first+last name), not
policy-based. This means plan switches and AEP rollovers are treated as
retention, not churn+new.
"""

from typing import Optional

import pandas as pd


# Active statuses — used to prefer active rows when a person has multiple plans
_ACTIVE_STS = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
_STATUS_RANK = {s: 1 for s in _ACTIVE_STS}   # active → rank 1 (sorts last = "last")


def _person_key(df: pd.DataFrame) -> pd.Series:
    """
    Stable person-level key: name_key (normalized first+last name) with client_key fallback.

    ffm_subscriber_id is NOT used — HealthSherpa generates a new subscriber ID for each
    enrollment, so it changes on every plan switch and is not a stable person identifier.
    """
    from tracker.ingest import match_client_id
    df = match_client_id(df)
    nk = df.get("name_key", pd.Series("", index=df.index)).fillna("").astype(str).str.strip()
    ck = df.get("client_key", pd.Series("", index=df.index)).fillna("").astype(str)
    return nk.where(nk != "", ck)


def _dedup_month(df: pd.DataFrame) -> pd.DataFrame:
    """
    Within a single month's snapshot, reduce to one row per person.
    Prefers active-status rows; among ties takes latest effective_date.
    """
    from tracker.ingest import match_client_id, normalize_name
    df = match_client_id(df).copy()
    df["_pkey"] = _person_key(df)
    df["_srank"] = df["status"].map(_STATUS_RANK).fillna(0)
    df["effective_date"] = pd.to_datetime(df.get("effective_date"), errors="coerce")

    df = df.sort_values(["_pkey", "_srank", "effective_date"])
    deduped = df.groupby("_pkey", sort=False).last().reset_index()
    deduped = deduped.rename(columns={"_pkey": "name_key"})
    deduped = deduped.drop(columns=["_srank"], errors="ignore")
    return deduped


def compute_diff(df_a: pd.DataFrame, df_b: pd.DataFrame) -> dict:
    """
    Compare month A (older) to month B (newer) at the PERSON level.
    A plan switch (same name, different policy_id) counts as 'stayed', not lost+new.
    Returns dict with keys: new, missing, stayed.
    """
    a = _dedup_month(df_a).set_index("name_key")
    b = _dedup_month(df_b).set_index("name_key")

    keys_a = set(a.index)
    keys_b = set(b.index)

    new_keys     = keys_b - keys_a
    missing_keys = keys_a - keys_b
    stayed_keys  = keys_a & keys_b

    new_df     = b.loc[list(new_keys)].reset_index()
    missing_df = a.loc[list(missing_keys)].reset_index()
    stayed_df  = b.loc[list(stayed_keys)].reset_index()

    return {"new": new_df, "missing": missing_df, "stayed": stayed_df}


def build_all_clients(months: dict) -> pd.DataFrame:
    """
    Build a cumulative person-level roster across all months.

    One row per person (matched by name_key).
    - effective_date  = earliest plan start across all plans (true book entry date)
    - carrier/status/net_premium/applicant_count = most recent active plan, or
      most recent plan if no active plan exists
    - term_date       = NaT if the person has any currently active plan;
                        else max(term_date) across all their plans
    - months_on_book  = calendar months from effective_date to latest snapshot month
    """
    from tracker.ingest import match_client_id

    rows = []
    for month_key in sorted(months.keys()):
        df = match_client_id(months[month_key]).copy()
        df["month"] = month_key
        rows.append(df)

    if not rows:
        return pd.DataFrame()

    all_df = pd.concat(rows, ignore_index=True)

    # Ensure person key exists on every row
    all_df["_pkey"] = _person_key(all_df)

    # Status rank: active rows sort last so "last" aggregation picks them
    all_df["_srank"] = all_df["status"].map(_STATUS_RANK).fillna(0)
    all_df["effective_date"] = pd.to_datetime(all_df.get("effective_date"), errors="coerce")
    all_df["term_date"]      = pd.to_datetime(all_df.get("term_date"),      errors="coerce")

    # Sort: oldest month first, within same month inactive before active
    # → "last" in each group = most recent month, most active row
    all_df = all_df.sort_values(["_pkey", "month", "_srank"])

    # Fields that come from the most-recent (last) row
    last_fields = {
        col: (col, "last")
        for col in [
            "client_name", "first_name", "last_name", "carrier",
            "state", "ffm_app_id", "net_premium", "applicant_count",
            "status", "client_key",
        ]
        if col in all_df.columns
    }

    agg = (
        all_df.groupby("_pkey")
        .agg(
            first_seen        = ("month",          "min"),
            last_seen         = ("month",          "max"),
            effective_date    = ("effective_date", "min"),   # earliest plan start
            _term_date_last   = ("term_date",      "last"),  # most recent term_date
            _has_active       = ("_srank",         "max"),   # 1 if any active plan exists
            **last_fields,
        )
        .reset_index()
        .rename(columns={"_pkey": "name_key"})
    )

    # term_date: NaT when the person still has an active plan
    agg["term_date"] = agg["_term_date_last"].where(agg["_has_active"] == 0, other=pd.NaT)
    agg = agg.drop(columns=["_term_date_last", "_has_active"])

    # months_on_book: calendar months from effective_date to the latest snapshot month
    latest       = max(months.keys())
    latest_y     = int(latest[:4])
    latest_m     = int(latest[5:7])

    def _calendar_months(eff_date) -> Optional[int]:
        try:
            eff = pd.Timestamp(eff_date)
            if pd.isna(eff):
                return None
            return (latest_y - eff.year) * 12 + (latest_m - eff.month) + 1
        except Exception:
            return None

    agg["months_on_book"] = agg["effective_date"].apply(_calendar_months)

    cols = [
        "name_key", "client_key", "first_name", "last_name", "carrier",
        "effective_date", "term_date", "status", "state", "ffm_app_id",
        "net_premium", "applicant_count", "first_seen", "last_seen", "months_on_book",
    ]
    return agg[[c for c in cols if c in agg.columns]]


def build_history_pivot(months: dict) -> pd.DataFrame:
    """
    Month-by-month commission pivot: rows = clients, columns = months.
    """
    from tracker.ingest import match_client_id

    frames = []
    for month_key in sorted(months.keys()):
        df = match_client_id(months[month_key])[["client_key", "client_name", "commission"]].copy()
        df["month"] = month_key
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    pivot = combined.pivot_table(
        index=["client_key", "client_name"],
        columns="month",
        values="commission",
        aggfunc="sum",
        fill_value=0,
    ).reset_index()
    pivot.columns.name = None
    return pivot
