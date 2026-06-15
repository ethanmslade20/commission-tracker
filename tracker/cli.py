#!/usr/bin/env python3
"""
Commission Tracker CLI
Usage:
  track ingest [--month YYYY-MM]
  track report
  track diff <month1> <month2>
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,  "may": 5,  "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def _month_from_filename(stem: str) -> Optional[date]:
    """
    Try to extract a YYYY-MM month from a filename stem.
    Recognises patterns like:
      healthsherpa_2025-06  healthsherpa_202506  healthsherpa_jun2025
      healthsherpa_june2025  2025_06_healthsherpa
    Returns None if no date found.
    """
    s = stem.lower()
    # YYYY-MM or YYYY_MM
    m = re.search(r'(20\d{2})[-_](0[1-9]|1[0-2])', s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), 1).date()
    # YYYYMM (6 consecutive digits starting with 20)
    m = re.search(r'(20\d{2})(0[1-9]|1[0-2])', s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), 1).date()
    # MonYYYY or MonthYYYY  e.g. jun2025, june2025
    m = re.search(r'([a-z]{3,9})(20\d{2})', s)
    if m:
        abbr = m.group(1)[:3]
        mo = _MONTH_ABBR.get(abbr)
        if mo:
            return datetime(int(m.group(2)), mo, 1).date()
    # YYYY + Mon  e.g. 2025jun, 2025june
    m = re.search(r'(20\d{2})([a-z]{3,9})', s)
    if m:
        abbr = m.group(2)[:3]
        mo = _MONTH_ABBR.get(abbr)
        if mo:
            return datetime(int(m.group(1)), mo, 1).date()
    # Mon_YYYY  e.g. aug_2025
    m = re.search(r'([a-z]{3,9})_?(20\d{2})', s)
    if m:
        abbr = m.group(1)[:3]
        mo = _MONTH_ABBR.get(abbr)
        if mo:
            return datetime(int(m.group(2)), mo, 1).date()
    return None

import click

from tracker.config import load_settings, load_carrier_configs


@click.group()
def cli():
    """ACA commission tracker — ingest CSVs, diff months, update Google Sheets."""
    pass


@cli.command()
@click.option("--month", default=None, help="Tag snapshots as this month (YYYY-MM). Defaults to current month.")
@click.option("--dry-run", is_flag=True, help="Preview what would be written without saving anything.")
def ingest(month: Optional[str], dry_run: bool):
    """Process all CSV files in /input/ and save normalized snapshots."""
    from tabulate import tabulate
    from tracker.ingest import ingest_file
    from tracker.diff import build_all_clients, build_history_pivot, compute_diff
    from tracker.ingest import load_all_snapshots

    from tracker.config import load_full_carrier_config
    settings = load_settings()
    source_configs = load_carrier_configs(settings["carrier_config_path"])
    full_config    = load_full_carrier_config(settings["carrier_config_path"])
    input_dir = Path(settings["input_dir"])
    snapshot_dir = Path(settings["snapshot_dir"])

    if month:
        try:
            month_date = datetime.strptime(month, "%Y-%m").date().replace(day=1)
        except ValueError:
            click.echo(f"Invalid month format: {month}. Use YYYY-MM.", err=True)
            sys.exit(1)
    else:
        month_date = date.today().replace(day=1)

    csv_files = sorted(input_dir.glob("*.csv"))
    if not csv_files:
        click.echo(f"No CSV files found in {input_dir}/")
        return

    if dry_run:
        click.echo(f"DRY RUN — previewing {len(csv_files)} file(s) for {month_date.strftime('%Y-%m')} (nothing will be saved)\n")
    else:
        click.echo(f"Ingesting {len(csv_files)} file(s) for {month_date.strftime('%Y-%m')}...")

    errors = []
    ingested_frames = {}

    for csv_path in csv_files:
        # Use --month if given, otherwise try to parse from filename, else today
        file_month = month_date if month else (_month_from_filename(csv_path.stem) or month_date)
        try:
            snapshot_path, df = ingest_file(
                csv_path, source_configs, snapshot_dir, file_month,
                dry_run=dry_run, full_config=full_config
            )
            ingested_frames[csv_path.name] = df
            if dry_run:
                click.echo(f"  {csv_path.name}: {len(df)} rows, {df['carrier'].nunique()} carriers")
            else:
                tag = f" [month detected: {file_month.strftime('%Y-%m')}]" if not month and file_month != month_date else ""
                click.echo(f"  ✓ {csv_path.name} → {snapshot_path.name} ({len(df)} rows){tag}")
        except Exception as e:
            click.echo(f"  ✗ {csv_path.name}: {e}", err=True)
            errors.append(csv_path.name)

    if errors:
        click.echo(f"\n{len(errors)} file(s) failed. Check filenames match source patterns.")
        return

    if not dry_run:
        click.echo(f"\nAll files ingested. Run `track report` to push to Google Sheets.")
        return

    # --- Dry run: show previews of all sheet tabs ---
    import pandas as pd

    month_key = month_date.strftime("%Y-%m")
    this_month = pd.concat(list(ingested_frames.values()), ignore_index=True)

    # Load prior snapshots for diff
    existing_months = load_all_snapshots(snapshot_dir)
    prior_months = {k: v for k, v in existing_months.items() if k < month_key}
    all_months = {**prior_months, month_key: this_month}

    sorted_keys = sorted(all_months.keys())
    prior_key = sorted_keys[-2] if len(sorted_keys) >= 2 else None

    click.echo(f"\n{'='*60}")
    click.echo(f"  DRY RUN PREVIEW — what would be written to Google Sheets")
    click.echo(f"{'='*60}\n")

    preview_cols = ["policy_id", "client_name", "carrier", "plan_name", "commission", "status"]

    # This Month tab
    click.echo(f"[This Month] — {len(this_month)} rows")
    show = [c for c in preview_cols if c in this_month.columns]
    click.echo(tabulate(this_month[show].head(8), headers="keys", tablefmt="simple", showindex=False))

    # Status breakdown
    click.echo(f"\n  Status breakdown:")
    for status, count in this_month["status"].value_counts().items():
        click.echo(f"    {status}: {count}")

    # Carrier breakdown
    click.echo(f"\n  Carriers ({this_month['carrier'].nunique()}):")
    for carrier, count in this_month["carrier"].value_counts().head(10).items():
        click.echo(f"    {carrier}: {count}")

    commission_col = "commission" if "commission" in this_month.columns else None
    if commission_col:
        click.echo(f"\n  Total gross_premium this month: ${this_month[commission_col].sum():,.2f}")

    # Diff tabs
    if prior_key:
        click.echo(f"\n[New / Missing / Stayed] — comparing {prior_key} → {month_key}")
        diff = compute_diff(all_months[prior_key], this_month)
        for label in ("new", "missing", "stayed"):
            df_label = diff[label]
            click.echo(f"\n  {label.upper()} ({len(df_label)} clients):")
            if not df_label.empty:
                show = [c for c in preview_cols if c in df_label.columns]
                click.echo(tabulate(df_label[show].head(5), headers="keys", tablefmt="simple", showindex=False))
                if len(df_label) > 5:
                    click.echo(f"  ... and {len(df_label) - 5} more")
    else:
        click.echo(f"\n[New / Missing / Stayed] — no prior month snapshot to diff against")

    # All Clients tab
    all_clients = build_all_clients(all_months)
    click.echo(f"\n[All Clients] — {len(all_clients)} total clients across all months")
    ac_cols = [c for c in ["client_key", "client_name", "carrier", "first_seen", "last_seen", "current_status", "total_commission"] if c in all_clients.columns]
    click.echo(tabulate(all_clients[ac_cols].head(8), headers="keys", tablefmt="simple", showindex=False))

    # History pivot
    history = build_history_pivot(all_months)
    click.echo(f"\n[History] — {len(history)} clients × {len([c for c in history.columns if c not in ('client_key','client_name')])} month(s)")

    click.echo(f"\n{'='*60}")
    click.echo(f"  Dry run complete. To commit, run: track ingest --month {month_key}")
    click.echo(f"{'='*60}\n")


@cli.command()
def report():
    """Rebuild all Google Sheet tabs from current snapshots."""
    from tracker.report import run_report

    settings = load_settings()
    run_report(settings)


@cli.command("aep-init")
@click.option("--year", default=None, type=int, help="AEP year (e.g. 2027). Defaults to next calendar year.")
def aep_init(year: Optional[int]):
    """Create or refresh the AEP Tracker tab in Google Sheets.

    Pulls all currently active clients and adds them to the tab.
    Existing rows keep their Status and Notes — new clients are added
    as 'Not Started'. Safe to re-run anytime to pick up new clients.
    """
    import math
    from datetime import date as _date

    import gspread
    import pandas as pd
    from google.oauth2 import service_account

    from tracker.diff import build_all_clients
    from tracker.ingest import load_all_snapshots
    from tracker.report import _filter_by_appointments, _load_appointments

    settings   = load_settings()
    aep_year   = year or (_date.today().year + 1)
    tab_name   = f"AEP {aep_year}"

    click.echo(f"Building AEP Tracker for {aep_year}...")

    snapshot_dir = Path(settings["snapshot_dir"])
    months       = load_all_snapshots(snapshot_dir)
    if not months:
        click.echo("No snapshots found. Run `track ingest` first.", err=True)
        return

    all_clients = build_all_clients(months)
    appts       = _load_appointments()
    all_clients = _filter_by_appointments(all_clients, appts)

    _ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    active  = all_clients[all_clients["status"].isin(_ACTIVE)].copy() if "status" in all_clients.columns else all_clients.copy()

    # Build key for matching existing rows (first+last name)
    active["_key"] = (
        active.get("first_name", pd.Series("", index=active.index)).fillna("").str.strip().str.lower()
        + "|"
        + active.get("last_name",  pd.Series("", index=active.index)).fillna("").str.strip().str.lower()
    )

    click.echo(f"  {len(active)} active clients found")

    # Connect to Sheets
    sheet_url  = settings.get("sheet_url", "")
    imp_target = settings.get("impersonation_target", "")
    if not sheet_url or not imp_target:
        click.echo("sheet_url / impersonation_target missing from settings.yaml", err=True)
        return

    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request as _Req

    src_creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds = impersonated_credentials.Credentials(
        source_credentials=src_creds,
        target_principal=imp_target,
        target_scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"],
        lifetime=3600,
    )
    client      = gspread.authorize(creds)
    spreadsheet = client.open_by_url(sheet_url)

    # Load existing tab to preserve Status / Notes
    existing: dict = {}  # key → {status, notes}
    try:
        ws      = spreadsheet.worksheet(tab_name)
        rows    = ws.get_all_records()
        for r in rows:
            k = (str(r.get("First Name","")).strip().lower()
                 + "|"
                 + str(r.get("Last Name","")).strip().lower())
            existing[k] = {
                "status": r.get("Status", "Not Started") or "Not Started",
                "notes":  r.get("Notes", "") or "",
            }
        click.echo(f"  Found existing tab '{tab_name}' with {len(existing)} rows — preserving statuses")
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=max(len(active) + 10, 500), cols=15)
        click.echo(f"  Created new tab '{tab_name}'")

    _STATUSES = ["Not Started", "Contacted", "Renewed", "Lost"]

    def _clean(v):
        if v is None:
            return ""
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return ""
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass
        return str(v)

    headers = ["First Name", "Last Name", "State", "Carrier", "Members", "Effective Date", "Status", "Notes"]
    data_rows = [headers]
    for _, row in active.sort_values(["state", "last_name", "first_name"], na_position="last").iterrows():
        key   = row.get("_key", "")
        prev  = existing.get(key, {})
        status = prev.get("status", "Not Started")
        if status not in _STATUSES:
            status = "Not Started"
        eff = row.get("effective_date", "")
        if hasattr(eff, "strftime"):
            eff = eff.strftime("%Y-%m-%d")
        data_rows.append([
            _clean(row.get("first_name", "")),
            _clean(row.get("last_name",  "")),
            _clean(row.get("state",      "")),
            _clean(row.get("carrier",    "")),
            _clean(row.get("applicant_count", 1)),
            _clean(eff),
            status,
            prev.get("notes", ""),
        ])

    ws.clear()
    ws.update(data_rows, value_input_option="USER_ENTERED")

    # Bold header row
    ws.format("A1:H1", {"textFormat": {"bold": True}})

    click.echo(f"  Wrote {len(data_rows)-1} clients to '{tab_name}'")
    click.echo(f"Done. Open Google Sheets to view or update statuses, then hit 'Refresh data' in the app.")


@cli.command()
@click.argument("month1")
@click.argument("month2")
@click.option("--output", "-o", default=None, help="Save diff to CSV file.")
def diff(month1: str, month2: str, output: Optional[str]):
    """Compare two months and print new/missing/stayed client counts.

    MONTH1 and MONTH2 should be in YYYY-MM format (e.g. 2024-01 2024-02).
    """
    from tabulate import tabulate
    from tracker.ingest import load_all_snapshots
    from tracker.diff import compute_diff

    settings = load_settings()
    snapshot_dir = Path(settings["snapshot_dir"])
    months = load_all_snapshots(snapshot_dir)

    for m in (month1, month2):
        if m not in months:
            available = sorted(months.keys())
            click.echo(f"Month '{m}' not found in snapshots.", err=True)
            click.echo(f"Available: {', '.join(available) if available else 'none'}", err=True)
            sys.exit(1)

    result = compute_diff(months[month1], months[month2])

    click.echo(f"\n=== Diff: {month1} → {month2} ===\n")
    for label in ("new", "missing", "stayed"):
        df = result[label]
        click.echo(f"--- {label.upper()} ({len(df)}) ---")
        if df.empty:
            click.echo("  (none)\n")
        else:
            cols = [c for c in ["client_key", "client_name", "carrier", "commission"] if c in df.columns]
            click.echo(tabulate(df[cols].head(20), headers="keys", tablefmt="simple", showindex=False))
            if len(df) > 20:
                click.echo(f"  ... and {len(df) - 20} more")
            click.echo()

    if output:
        import pandas as pd
        for label, df in result.items():
            df["diff_type"] = label
        combined = pd.concat(result.values(), ignore_index=True)
        out_path = Path(output)
        combined.to_csv(out_path, index=False)
        click.echo(f"Diff saved to {out_path}")


@cli.command("auth-check")
def auth_check():
    """Verify ADC credentials and service account impersonation."""
    import google.auth
    from google.auth import impersonated_credentials
    from google.auth.transport.requests import Request
    import gspread

    settings = load_settings()
    target = settings.get("impersonation_target", "")
    sheet_url = settings.get("sheet_url", "")

    click.echo("1. Checking Application Default Credentials...")
    try:
        source_creds, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        source_creds.refresh(Request())
        click.echo(f"   ✓ ADC OK (project: {project or 'unknown'})")
    except Exception as e:
        click.echo(f"   ✗ ADC failed: {e}", err=True)
        click.echo("\n   Run: gcloud auth application-default login", err=True)
        raise SystemExit(1)

    click.echo(f"2. Impersonating service account: {target}")
    try:
        target_creds = impersonated_credentials.Credentials(
            source_credentials=source_creds,
            target_principal=target,
            target_scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
            lifetime=300,
        )
        target_creds.refresh(Request())
        click.echo("   ✓ Impersonation OK")
    except Exception as e:
        click.echo(f"   ✗ Impersonation failed: {e}", err=True)
        click.echo(
            "\n   Ensure you have roles/iam.serviceAccountTokenCreator on the SA.\n"
            "   Run: gcloud iam service-accounts add-iam-policy-binding \\\n"
            f"     {target} \\\n"
            "     --member=user:YOUR_EMAIL \\\n"
            "     --role=roles/iam.serviceAccountTokenCreator",
            err=True,
        )
        raise SystemExit(1)

    click.echo(f"3. Opening Google Sheet...")
    try:
        client = gspread.authorize(target_creds)
        sheet = client.open_by_url(sheet_url)
        click.echo(f"   ✓ Sheet accessible: '{sheet.title}'")
    except Exception as e:
        click.echo(f"   ✗ Sheet access failed: {e}", err=True)
        click.echo(
            f"\n   Ensure the sheet is shared with Editor access for:\n   {target}",
            err=True,
        )
        raise SystemExit(1)

    click.echo("\nAll checks passed. Ready to run `track ingest` and `track report`.")


if __name__ == "__main__":
    cli()
