"""
Computes client-level diffs between two monthly snapshots.
"""

import pandas as pd


def _keyed(df: pd.DataFrame) -> pd.DataFrame:
    from tracker.ingest import match_client_id
    return match_client_id(df).set_index("client_key")


def compute_diff(df_a: pd.DataFrame, df_b: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Compare month A (older) to month B (newer).
    Returns dict with keys: new, missing, stayed.
    """
    a = _keyed(df_a)
    b = _keyed(df_b)

    keys_a = set(a.index)
    keys_b = set(b.index)

    new_keys = keys_b - keys_a
    missing_keys = keys_a - keys_b
    stayed_keys = keys_a & keys_b

    new_df = b.loc[list(new_keys)].reset_index()
    missing_df = a.loc[list(missing_keys)].reset_index()
    stayed_df = b.loc[list(stayed_keys)].reset_index()

    return {"new": new_df, "missing": missing_df, "stayed": stayed_df}


def build_all_clients(months: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Build cumulative roster across all months.
    Each client_key gets: first_seen, last_seen, current_status, total_commission.
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

    # "last" for per-client fields — takes the most recent month's value
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

    # Calendar months from effective_date to the current (latest) month, inclusive.
    # e.g. effective_date=2025-11-01, latest=2026-05 → 7 months.
    latest = max(months.keys())
    latest_y, latest_m = int(latest[:4]), int(latest[5:7])

    def _calendar_months(eff_date) -> int:
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
    return agg[[c for c in cols if c in agg.columns]]


def build_history_pivot(months: dict[str, pd.DataFrame]) -> pd.DataFrame:
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
