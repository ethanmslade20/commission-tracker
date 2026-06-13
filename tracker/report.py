"""
Builds all DataFrames from snapshots and pushes them to Google Sheets.
"""

from pathlib import Path

import pandas as pd

from tracker.diff import build_all_clients, compute_diff
from tracker.ingest import load_all_snapshots
from tracker.sheets import update_sheet

_ALL_CLIENTS_COLS = ["first_name", "last_name", "carrier", "effective_date", "term_date",
                     "status", "state", "ffm_app_id", "net_premium", "applicant_count", "months_on_book"]

_ACTIVE_COLS = ["first_name", "last_name", "carrier", "effective_date",
                "status", "state", "ffm_app_id", "net_premium", "applicant_count", "months_on_book"]

_STATUS_ORDER = ["Effectuated", "PendingEffectuation", "PendingFollowups", "Cancelled", "Terminated"]


def _select(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    return df[[c for c in cols if c in df.columns]]


def _sort_by_date(df: pd.DataFrame) -> pd.DataFrame:
    """Sort ascending by effective_date (oldest first), NaT pushed to end."""
    if df.empty or "effective_date" not in df.columns:
        return df
    return df.sort_values("effective_date", ascending=True, na_position="last").reset_index(drop=True)


def _sort_by_term_date_desc(df: pd.DataFrame) -> pd.DataFrame:
    """Sort descending by term_date (most recent cancellations first), NaT pushed to end."""
    if df.empty or "term_date" not in df.columns:
        return df
    return df.sort_values("term_date", ascending=False, na_position="last").reset_index(drop=True)


def _sort(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if "status" in df.columns:
        order_map = {s: i for i, s in enumerate(_STATUS_ORDER)}
        df = df.copy()
        df["_status_rank"] = df["status"].map(order_map).fillna(len(_STATUS_ORDER))
        sort_cols = ["_status_rank"] + (["last_name"] if "last_name" in df.columns else [])
        df = df.sort_values(sort_cols, key=lambda s: s.str.lower() if s.dtype == object else s)
        df = df.drop(columns=["_status_rank"]).reset_index(drop=True)
    return df


def run_report(settings: dict) -> None:
    snapshot_dir = Path(settings["snapshot_dir"])
    months = load_all_snapshots(snapshot_dir)

    if not months:
        print("No snapshots found. Run `track ingest` first.")
        return

    sorted_months = sorted(months.keys())
    latest_month = sorted_months[-1]
    prior_month = sorted_months[-2] if len(sorted_months) >= 2 else None

    print(f"Building report. Latest month: {latest_month}")

    all_clients = build_all_clients(months)

    # Compute diff to identify missing clients (those who dropped off last month)
    if prior_month:
        diff = compute_diff(months[prior_month], months[latest_month])
        missing_df = diff["missing"]
        print(f"  Comparing {prior_month} → {latest_month}: "
              f"{len(diff['new'])} new, {len(missing_df)} missing, {len(diff['stayed'])} stayed")
    else:
        missing_df = pd.DataFrame()
        print("  Only one month of data.")

    # All Active: Effectuated, PendingEffectuation, or PendingFollowups
    # Must match _ACTIVE_STATUSES in dashboard.py so member counts agree.
    active_pending = all_clients[
        all_clients["status"].isin(["Effectuated", "PendingEffectuation", "PendingFollowups"])
    ].copy() if "status" in all_clients.columns else pd.DataFrame()

    # All Missing/Cancelled: Cancelled/Terminated + clients who dropped off (missing diff)
    cancelled = all_clients[
        all_clients["status"].isin(["Cancelled", "Terminated"])
    ].copy() if "status" in all_clients.columns else pd.DataFrame()

    _active_statuses = {"Effectuated", "PendingEffectuation", "PendingFollowups"}

    if not missing_df.empty:
        existing_keys = set(cancelled["name_key"].dropna()) if "name_key" in cancelled.columns else set()
        extra = missing_df[
            ~missing_df.get("name_key", pd.Series(dtype=str)).isin(existing_keys)
        ].copy()
        # Anyone who dropped off the export is treated as Cancelled regardless of
        # their last known status (covers Pending clients who never effectuated)
        if "status" in extra.columns:
            extra.loc[extra["status"].isin(_active_statuses), "status"] = "Cancelled"
        cancelled_missing = pd.concat([cancelled, extra], ignore_index=True)
    else:
        cancelled_missing = cancelled

    sheet_url = settings.get("sheet_url", "")
    if not sheet_url:
        print("No sheet_url in config/settings.yaml.")
        return

    impersonation_target = settings.get("impersonation_target", "")
    if not impersonation_target:
        print("No impersonation_target in config/settings.yaml.")
        return

    print("Pushing to Google Sheets...")
    update_sheet(
        sheet_url=sheet_url,
        impersonation_target=impersonation_target,
        tab_names=settings["tabs"],
        all_clients=_sort(_select(all_clients, _ALL_CLIENTS_COLS)),
        active_pending_df=_sort_by_date(_select(active_pending, _ACTIVE_COLS)),
        cancelled_missing_df=_sort_by_term_date_desc(_select(cancelled_missing, _ALL_CLIENTS_COLS)),
        months=months,
    )
    print("Done.")
