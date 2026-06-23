"""
Google Sheets integration via gspread.
Uses ADC (Application Default Credentials) with service account impersonation
so no JSON key file is needed — works inside org policies that block SA keys.
"""

import math
import time
from typing import Any, Dict, Optional

import google.auth
import gspread
import pandas as pd
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request

from tracker.dashboard import build_dashboard_data, write_dashboard, _get_sheet_charts

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
_ADC_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Summary row backgrounds
_ACTIVE_BG   = {"red": 0.714, "green": 0.929, "blue": 0.714}   # soft green  — active book
_INACTIVE_BG = {"red": 0.957, "green": 0.714, "blue": 0.714}   # soft red    — inactive


def _build_credentials(impersonation_target: str):
    source_creds, _ = google.auth.default(scopes=[_ADC_SCOPE])
    return impersonated_credentials.Credentials(
        source_credentials=source_creds,
        target_principal=impersonation_target,
        target_scopes=SCOPES,
        lifetime=3600,
    )


def _patch_retry_on_quota(client: gspread.Client, max_retries: int = 5) -> None:
    """Wrap the HTTP client's request method so 429 (quota exceeded) errors
    are retried with backoff instead of crashing mid-write and leaving a
    tab half-cleared."""
    http_client = client.http_client
    original_request = http_client.request

    def _request_with_retry(method, endpoint, **kwargs):
        for attempt in range(max_retries + 1):
            try:
                return original_request(method, endpoint, **kwargs)
            except gspread.exceptions.APIError as e:
                status = e.response.status_code
                if status == 429 and attempt < max_retries:
                    wait = 20 * (attempt + 1)
                    print(f"  Quota hit (429) — retrying in {wait}s "
                          f"(attempt {attempt + 1}/{max_retries})...")
                    time.sleep(wait)
                    continue
                raise

    http_client.request = _request_with_retry


def _open_sheet(sheet_url: str, impersonation_target: str) -> gspread.Spreadsheet:
    creds = _build_credentials(impersonation_target)
    client = gspread.authorize(creds)
    _patch_retry_on_quota(client)
    return client.open_by_url(sheet_url)


def _ensure_tab(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=title, rows=1000, cols=30)


def _clean(val: Any) -> Any:
    """Convert NaN/NaT/inf to empty string for Sheets."""
    if val is None:
        return ""
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return ""
    if pd.isna(val) if not isinstance(val, (list, dict)) else False:
        return ""
    if isinstance(val, pd.Timestamp):
        return val.strftime("%Y-%m-%d") if not pd.isna(val) else ""
    return val


def _df_to_rows(df: pd.DataFrame) -> list:
    headers = list(df.columns)
    data = [[_clean(v) for v in row] for row in df.itertuples(index=False)]
    return [headers] + data


def _write_tab(ws: gspread.Worksheet, df: pd.DataFrame) -> None:
    ws.clear()
    if df.empty:
        ws.update([["No data"]], "A1")
        return
    ws.update(_df_to_rows(df), "A1")
    ws.format("1:1", {"textFormat": {"bold": True}})


def _count(df: pd.DataFrame, status: str) -> int:
    if "status" not in df.columns:
        return 0
    return int((df["status"] == status).sum())


def _lives(df: pd.DataFrame, statuses: list) -> int:
    if "applicant_count" not in df.columns:
        return 0
    return int(df.loc[df["status"].isin(statuses), "applicant_count"].sum())


def _write_all_clients_tab(
    ws: gspread.Worksheet,
    df: pd.DataFrame,
    show_active_row: bool = True,
    show_inactive_row: bool = True,
) -> None:
    ws.clear()
    if df.empty:
        ws.update([["No data"]], "A1")
        return

    cols = list(df.columns)
    n    = len(cols)

    active_statuses   = ["Effectuated", "PendingEffectuation", "PendingFollowups"]
    inactive_statuses = ["Terminated", "Cancelled"]

    active_row = [""] * n
    active_row[0] = f"Active Policies: {sum(_count(df, s) for s in active_statuses)}"
    active_row[1] = f"Total Members: {_lives(df, active_statuses)}"

    inactive_row = [""] * n
    inactive_row[0] = f"Inactive Policies: {sum(_count(df, s) for s in inactive_statuses)}"
    inactive_row[1] = f"Total Members: {_lives(df, inactive_statuses)}"

    data = [[_clean(v) for v in row] for row in df.itertuples(index=False)]
    bold11 = {"textFormat": {"bold": True, "fontSize": 11}}

    if show_active_row and show_inactive_row:
        ws.update([active_row, inactive_row, cols] + data, "A1")
        ws.format("1:1", {**bold11, "backgroundColor": _ACTIVE_BG})
        ws.format("2:2", {**bold11, "backgroundColor": _INACTIVE_BG})
        ws.format("3:3", {"textFormat": {"bold": True}})
    elif show_active_row:
        ws.update([active_row, cols] + data, "A1")
        ws.format("1:1", {**bold11, "backgroundColor": _ACTIVE_BG})
        ws.format("2:2", {"textFormat": {"bold": True}})
    elif show_inactive_row:
        ws.update([inactive_row, cols] + data, "A1")
        ws.format("1:1", {**bold11, "backgroundColor": _INACTIVE_BG})
        ws.format("2:2", {"textFormat": {"bold": True}})
    else:
        ws.update([cols] + data, "A1")
        ws.format("1:1", {"textFormat": {"bold": True}})



# Agent's local timezone — used to place UTC creation timestamps on the right
# calendar day. (Single-tenant for now; would become per-agent in a SaaS.)
_AGENT_TZ = "America/Denver"


def _coalesce_sale_date(df: pd.DataFrame) -> pd.Series:
    """Date a policy counts as 'sold': submission_date, falling back to the
    application creation date (converted from UTC to the agent's local day)
    when HealthSherpa left submission_date blank. Returns a tz-naive Series."""
    if "submission_date" in df.columns:
        sub = pd.to_datetime(df["submission_date"], errors="coerce")
        if getattr(getattr(sub, "dt", None), "tz", None) is not None:
            sub = sub.dt.tz_localize(None)
    else:
        sub = pd.Series(pd.NaT, index=df.index)

    if "created_date" in df.columns:
        created = pd.to_datetime(df["created_date"], errors="coerce", utc=True)
        try:
            created = created.dt.tz_convert(_AGENT_TZ).dt.tz_localize(None)
        except (TypeError, AttributeError):
            pass
        sub = sub.fillna(created)
    return sub


def _build_daily_tracker_data(df: pd.DataFrame, month_str: str) -> pd.DataFrame:
    """Return a DataFrame with one row per calendar day of month_str.
    Columns: Date (Mon DD), Policies (count), Members (applicant_count sum).
    Days with no submissions show 0."""
    import calendar
    year, month = int(month_str[:4]), int(month_str[5:7])
    days_in_month = calendar.monthrange(year, month)[1]
    all_days = pd.date_range(f"{month_str}-01", periods=days_in_month, freq="D")

    # A submitted policy counts as "sold" on its submission date; when
    # HealthSherpa leaves submission_date blank (common for claimed apps that
    # are nonetheless submitted), fall back to the application creation date so
    # every submitted sale lands on the day it was written.
    sub = _coalesce_sale_date(df)

    # Option A — count only NEW business: policies whose coverage starts AFTER
    # the day they were sold. This excludes older policies the agent merely
    # claimed/serviced during the month (effective in the past), so the daily
    # tracker matches "what I actually sold this month".
    if "effective_date" in df.columns:
        _eff = pd.to_datetime(df["effective_date"], errors="coerce")
        _is_new = (_eff > sub).fillna(False)
        df = df[_is_new]
        sub = sub[_is_new]

    if sub.isna().all():
        return pd.DataFrame({
            "Date":     [d.strftime("%b %d") for d in all_days],
            "Policies": [0] * days_in_month,
            "Members":  [0] * days_in_month,
        })

    sub = sub.dt.normalize()
    mem = pd.to_numeric(df.get("applicant_count", pd.Series([1] * len(df))), errors="coerce").fillna(1)

    month_start = pd.Timestamp(f"{month_str}-01")
    month_end   = month_start + pd.offsets.MonthEnd(0)
    mask = (sub >= month_start) & (sub <= month_end)

    grouped = (
        pd.DataFrame({"date": sub[mask], "mem": mem[mask]})
        .groupby("date")
        .agg(Policies=("mem", "count"), Members=("mem", "sum"))
        .reset_index()
    )
    grouped["date"] = pd.to_datetime(grouped["date"])

    result = (
        pd.DataFrame({"date": all_days})
        .merge(grouped, on="date", how="left")
        .fillna(0)
    )
    result["Policies"] = result["Policies"].astype(int)
    result["Members"]  = result["Members"].astype(int)
    result["Date"]     = result["date"].dt.strftime("%b %d")
    return result[["Date", "Policies", "Members"]]


def _build_daily_detail(months: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-policy submission detail across all months (same coalesced-sale-date +
    Option-A new-business logic as the daily tracker). Each policy is counted ONCE
    in the month it was submitted (avoids the same policy reappearing in every
    later monthly snapshot). Adds "Is New": "Yes" for a person's FIRST-ever
    submission, "No" for later ones (OEP renewals / re-submissions), so records
    can reflect genuinely new clients vs. the annual renewal wave.
    Columns: Date, Month, First Name, Last Name, Members, Carrier, State, Is New."""
    import re as _re
    cols = ["Date", "Month", "First Name", "Last Name", "Members", "Carrier", "State", "Is New"]

    def _key(f, l):
        return _re.sub(r"[^a-z]", "", f"{f}{l}".lower())

    # first_seen = earliest snapshot month each person appears (any row). A sale
    # is "new business" only if it happens in that first month; a submission in a
    # later month (the OEP renewal wave, etc.) is a renewal.
    first_seen = {}
    for m in sorted((months or {}).keys()):
        df = months[m]
        if df is None or df.empty:
            continue
        for f, l in zip(df.get("first_name", pd.Series(dtype=str)).fillna(""),
                        df.get("last_name", pd.Series(dtype=str)).fillna("")):
            k = _key(str(f), str(l))
            if k:
                first_seen.setdefault(k, m)

    frames = []
    for m, df in (months or {}).items():
        if df is None or df.empty:
            continue
        d = df.copy()
        d["_sale"] = _coalesce_sale_date(d)
        if "effective_date" in d.columns:
            eff = pd.to_datetime(d["effective_date"], errors="coerce")
            d = d[(eff > d["_sale"]).fillna(False)]
        d = d[d["_sale"].notna()]
        # Count each submission only in the month it was actually submitted.
        m_start = pd.Timestamp(m + "-01")
        m_end = m_start + pd.offsets.MonthEnd(0)
        d = d[(d["_sale"] >= m_start) & (d["_sale"] <= m_end)]
        if d.empty:
            continue
        _keys = [_key(str(a), str(b)) for a, b in
                 zip(d.get("first_name", "").fillna(""), d.get("last_name", "").fillna(""))]
        frames.append(pd.DataFrame({
            "Date": d["_sale"].dt.strftime("%Y-%m-%d"),
            "Month": m,
            "First Name": d.get("first_name", ""),
            "Last Name": d.get("last_name", ""),
            "Members": pd.to_numeric(d.get("applicant_count", 1), errors="coerce").fillna(1).astype(int),
            "Carrier": d.get("carrier", ""),
            "State": d.get("state", ""),
            "Is New": ["Yes" if first_seen.get(k) == m else "No" for k in _keys],
        }))
    if not frames:
        return pd.DataFrame(columns=cols)
    return (pd.concat(frames, ignore_index=True)
            .sort_values(["Month", "Date", "Last Name"]).reset_index(drop=True))


def _write_daily_tracker_tab(
    spreadsheet: gspread.Spreadsheet,
    ws: gspread.Worksheet,
    months: Dict[str, pd.DataFrame],
    latest_month: str,
) -> None:
    """Premium daily submission tracker: KPI boxes, in-cell SPARKLINE bars,
    today highlight, best-day star, native chart, bottom callout section."""
    import calendar
    import datetime as dt

    ws.clear()
    sheet_id = ws.id

    # ── Data ─────────────────────────────────────────────────────────────────
    # months may not contain an entry for this month (e.g. Feb–Apr before CSVs
    # were ingested) — fall back to an empty DataFrame so the tab still renders.
    daily_df = _build_daily_tracker_data(
        months.get(latest_month, pd.DataFrame()), latest_month
    )
    n = len(daily_df)                              # days in month (e.g. 30)

    total_pol   = int(daily_df["Policies"].sum())
    total_heads = int(daily_df["Members"].sum())
    max_pol     = int(daily_df["Policies"].max()) if not daily_df.empty else 0
    days_active = int((daily_df["Policies"] > 0).sum())

    today = dt.date.today()

    year, month = int(latest_month[:4]), int(latest_month[5:7])
    days_in_month = calendar.monthrange(year, month)[1]
    # Daily average: divide by days elapsed so far (for the current month),
    # or full month length for past months.
    if today.year == year and today.month == month:
        days_elapsed = today.day
    else:
        days_elapsed = days_in_month
    daily_avg = round(total_pol / max(days_elapsed, 1), 1)
    pct_month = round(days_active / days_in_month * 100)

    best_dates = daily_df.loc[daily_df["Policies"] == max_pol, "Date"].tolist() if max_pol > 0 else []
    MONTHLY_TARGET = 100

    today_in_month = (today.year == year and today.month == month)

    month_dt    = pd.Timestamp(latest_month + "-01")
    month_label = month_dt.strftime("%B %Y")

    # ── Layout constants (0-based row indices) ────────────────────────────────
    KPI_NUM_ROW   = 0    # big number
    KPI_LABEL_ROW = 2    # label text
    KPI_SUB_ROW   = 3    # sub-label (% of month etc.)
    KPI_ROWS      = 4    # total KPI section height
    HEADER_ROW    = 5
    DATA_START    = 6
    DATA_END      = DATA_START + n - 1
    TOTAL_ROW     = DATA_END + 1
    SPACER_ROW    = TOTAL_ROW + 1
    BOT_ROW       = SPACER_ROW + 1   # bottom callout start
    BOT_END       = BOT_ROW + 4      # exclusive end row for bottom section

    # Col indices
    C_DATE    = 0   # A
    C_POL     = 1   # B
    C_POLBAR  = 2   # C
    C_HEADS   = 3   # D
    C_HDBAR   = 4   # E
    C_GAP     = 5   # F
    C_CHART   = 6   # G  ← chart anchor
    N_COLS    = 12  # A–L

    # ── Colors ────────────────────────────────────────────────────────────────
    NAVY  = {"red": 0.102, "green": 0.153, "blue": 0.267}
    LNAV  = {"red": 0.141, "green": 0.208, "blue": 0.369}   # slightly lighter
    WHITE = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
    GREEN = {"red": 0.180, "green": 0.800, "blue": 0.443}
    BLUE  = {"red": 0.259, "green": 0.522, "blue": 0.957}
    LBLUE = {"red": 0.871, "green": 0.918, "blue": 0.996}   # today row tint
    GRAY  = {"red": 0.949, "green": 0.949, "blue": 0.949}
    GREEN_TEXT = {"red": 0.055, "green": 0.600, "blue": 0.345}

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _gr(r1: int, r2: int, c1: int, c2: int) -> dict:
        return {"sheetId": sheet_id, "startRowIndex": r1, "endRowIndex": r2,
                "startColumnIndex": c1, "endColumnIndex": c2}

    def _rc(r1, r2, c1, c2, bg=None, bold=False, fg=None, size=11,
            align=None, valign=None, wrap=None) -> dict:
        fmt: dict = {"textFormat": {"bold": bold, "fontSize": size}}
        if fg:     fmt["textFormat"]["foregroundColor"] = fg
        if bg:     fmt["backgroundColor"] = bg
        if align:  fmt["horizontalAlignment"] = align
        if valign: fmt["verticalAlignment"] = valign
        if wrap:   fmt["wrapStrategy"] = wrap
        return {"repeatCell": {
            "range": _gr(r1, r2, c1, c2),
            "cell":  {"userEnteredFormat": fmt},
            "fields": "userEnteredFormat(backgroundColor,textFormat,"
                      "horizontalAlignment,verticalAlignment,wrapStrategy)",
        }}

    def _merge(r1, r2, c1, c2) -> dict:
        return {"mergeCells": {"range": _gr(r1, r2, c1, c2), "mergeType": "MERGE_ALL"}}

    def _col_w(col: int, px: int) -> dict:
        return {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS",
                      "startIndex": col, "endIndex": col + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize",
        }}

    def _row_h(row: int, px: int) -> dict:
        return {"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "ROWS",
                      "startIndex": row, "endIndex": row + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize",
        }}

    # ── 1. Write cell values ──────────────────────────────────────────────────
    # KPI numbers: write to top-left of each box (boxes at cols 0,3,6,9)
    kpi_num_row = [""] * N_COLS
    kpi_num_row[0] = total_pol
    kpi_num_row[3] = total_heads
    kpi_num_row[6] = daily_avg
    kpi_num_row[9] = days_active
    ws.update([kpi_num_row], f"A{KPI_NUM_ROW + 1}")

    # KPI labels
    kpi_lbl_row = [""] * N_COLS
    kpi_lbl_row[0] = "TOTAL POLICIES SUBMITTED"
    kpi_lbl_row[3] = "TOTAL HEADS SOLD"
    kpi_lbl_row[6] = "DAILY AVERAGE  (policies / day)"
    kpi_lbl_row[9] = "DAYS WITH ACTIVITY"
    ws.update([kpi_lbl_row], f"A{KPI_LABEL_ROW + 1}")

    # KPI sub-labels (row 3)
    kpi_sub_row = [""] * N_COLS
    kpi_sub_row[9] = f"{pct_month}% of {days_in_month}-day month"
    ws.update([kpi_sub_row], f"A{KPI_SUB_ROW + 1}")

    # Table header + data rows + total
    date_col = []
    for _, row in daily_df.iterrows():
        d = row["Date"]
        prefix = "⭐ " if d in best_dates and max_pol > 0 else ""
        date_col.append(prefix + d)

    table_header = ["DATE", "POLICIES", "", "HEADS SOLD", ""]
    data_rows = [
        [date_col[i], int(daily_df.iloc[i]["Policies"]), "",
         int(daily_df.iloc[i]["Members"]), ""]
        for i in range(n)
    ]
    total_row_data = ["TOTAL", total_pol, "", total_heads, ""]
    ws.update([table_header] + data_rows + [total_row_data], f"A{HEADER_ROW + 1}")

    # Bottom callout section (3 boxes × 3 cols each = cols 0-11)
    best_date_str = "  &  ".join(best_dates) if best_dates else "—"
    progress_pct  = min(round(total_pol / MONTHLY_TARGET * 100), 100)

    bot_titles = ["⭐  TOP PERFORMING DAY", "", "", "",
                  "🔥  KEEP IT UP!", "", "", "",
                  "🎯  MONTHLY TARGET", "", "", ""]
    bot_vals   = [f"{max_pol} policies", "", "", "",
                  f"{total_pol} policies submitted", "", "", "",
                  f"Goal: {MONTHLY_TARGET} policies", "", "", ""]
    bot_subs   = [best_date_str, "", "", "",
                  f"{days_active} active days this month", "", "", "",
                  f"{progress_pct}% of target reached", "", "", ""]
    ws.update([bot_titles, bot_vals, bot_subs], f"A{BOT_ROW + 1}")

    # ── 2. SPARKLINE formulas ─────────────────────────────────────────────────
    ds1b = DATA_START + 1   # 1-based first data row
    de1b = DATA_END + 1     # 1-based last data row
    pol_max = f"MAX($B${ds1b}:$B${de1b})"
    hd_max  = f"MAX($D${ds1b}:$D${de1b})"

    pol_bars  = [[f'=SPARKLINE(B{ds1b + i},{{"charttype","bar";"max",{pol_max};"color1","#4285F4"}})'
                  ] for i in range(n)]
    head_bars = [[f'=SPARKLINE(D{ds1b + i},{{"charttype","bar";"max",{hd_max};"color1","#2ecc71"}})'
                  ] for i in range(n)]

    ws.update(pol_bars,  f"C{ds1b}", raw=False)
    ws.update(head_bars, f"E{ds1b}", raw=False)

    # Progress bar in bottom box 3 (row BOT_ROW+4, col 8)
    prog_row_1b = BOT_ROW + 4 + 1  # 1-based
    prog_col    = "I"              # col index 8
    ws.update(
        [[f'=SPARKLINE({total_pol}/100,{{"charttype","bar";"max",1;"color1","#2ecc71"}})']],
        f"{prog_col}{prog_row_1b}",
        raw=False,
    )

    # ── 3. Formatting batch ───────────────────────────────────────────────────
    requests: list = []

    # Delete stale charts
    for cid in _get_sheet_charts(spreadsheet, sheet_id):
        requests.append({"deleteEmbeddedObject": {"objectId": cid}})

    # Unfreeze first — any lingering frozenRowCount would block merges that
    # cross the frozen/non-frozen boundary.
    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": 0}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # Unmerge any lingering merges from previous runs
    requests.append({"unmergeCells": {"range": _gr(0, BOT_END + 1, 0, N_COLS)}})

    # ── Column widths ─────────────────────────────────────────────────────────
    col_widths = [
        (C_DATE,   92),   # A: date
        (C_POL,    52),   # B: policy count
        (C_POLBAR, 130),  # C: policy bar
        (C_HEADS,  60),   # D: heads count
        (C_HDBAR,  130),  # E: heads bar
        (C_GAP,    18),   # F: gap
        (C_CHART,  18),   # G: chart anchor col (narrow; chart floats)
        (7,        95),   # H
        (8,        95),   # I
        (9,        95),   # J
        (10,       95),   # K
        (11,       100),  # L
    ]
    for col, px in col_widths:
        requests.append(_col_w(col, px))

    # ── Row heights ───────────────────────────────────────────────────────────
    requests.append(_row_h(KPI_NUM_ROW,   62))
    requests.append(_row_h(KPI_NUM_ROW+1,  4))   # micro-spacer
    requests.append(_row_h(KPI_LABEL_ROW, 26))
    requests.append(_row_h(KPI_SUB_ROW,   20))
    requests.append(_row_h(KPI_ROWS,      12))   # gap before table
    requests.append(_row_h(HEADER_ROW,    26))
    for i in range(n):
        requests.append(_row_h(DATA_START + i, 22))
    requests.append(_row_h(TOTAL_ROW,     28))
    requests.append(_row_h(SPACER_ROW,    16))
    for i in range(5):
        requests.append(_row_h(BOT_ROW + i, 28 if i < 3 else 22))

    # ── Clear all formatting in sheet area first ──────────────────────────────
    requests.append({"repeatCell": {
        "range": _gr(0, BOT_END + 2, 0, N_COLS),
        "cell": {"userEnteredFormat": {}},
        "fields": "userEnteredFormat",
    }})

    # ── KPI section: full navy background ─────────────────────────────────────
    requests.append(_rc(0, KPI_ROWS, 0, N_COLS, bg=NAVY))

    # KPI box merges (4 boxes × 3 cols): rows merged for number and label rows
    for box_c in [0, 3, 6, 9]:
        c2 = box_c + 3
        requests.append(_merge(KPI_NUM_ROW,   KPI_NUM_ROW + 2,   box_c, c2))   # number (rows 0-1)
        requests.append(_merge(KPI_LABEL_ROW, KPI_LABEL_ROW + 1, box_c, c2))   # label  (row 2)
        requests.append(_merge(KPI_SUB_ROW,   KPI_SUB_ROW + 1,   box_c, c2))   # sub    (row 3)

    # KPI number text style
    requests.append(_rc(KPI_NUM_ROW, KPI_NUM_ROW + 2, 0, N_COLS,
                        bg=NAVY, bold=True, fg=WHITE, size=30,
                        align="CENTER", valign="MIDDLE"))

    # KPI label text style
    requests.append(_rc(KPI_LABEL_ROW, KPI_LABEL_ROW + 1, 0, N_COLS,
                        bg=NAVY, bold=False, fg=WHITE, size=8,
                        align="CENTER", valign="MIDDLE"))

    # KPI sub-label text style
    requests.append(_rc(KPI_SUB_ROW, KPI_SUB_ROW + 1, 0, N_COLS,
                        bg=NAVY, bold=False,
                        fg={"red": 0.6, "green": 0.78, "blue": 0.98},
                        size=8, align="CENTER", valign="MIDDLE"))

    # Thin separator lines between KPI boxes
    for sep_c in [3, 6, 9]:
        requests.append({
            "updateBorders": {
                "range": _gr(0, KPI_ROWS, sep_c - 1, sep_c),
                "right": {"style": "SOLID", "color": LNAV, "width": 1},
            }
        })

    # ── Table header ──────────────────────────────────────────────────────────
    requests.append(_rc(HEADER_ROW, HEADER_ROW + 1, 0, C_GAP + 1,
                        bg=LNAV, bold=True, fg=WHITE, size=9, align="CENTER"))

    # ── Data rows: alternating shading ───────────────────────────────────────
    for i in range(n):
        ri = DATA_START + i
        bg = GRAY if i % 2 == 0 else WHITE
        requests.append(_rc(ri, ri + 1, 0, C_GAP + 1, bg=bg, size=10))
        # Center the count columns
        requests.append(_rc(ri, ri + 1, C_POL,   C_POL + 1,  bg=bg, size=10, align="CENTER"))
        requests.append(_rc(ri, ri + 1, C_HEADS,  C_HEADS + 1, bg=bg, size=10, align="CENTER"))

    # Today's row: light blue tint + blue border
    if today_in_month:
        tr = DATA_START + today.day - 1
        if DATA_START <= tr <= DATA_END:
            requests.append(_rc(tr, tr + 1, 0, C_GAP + 1,
                                bg=LBLUE, bold=True, size=10))
            requests.append({
                "updateBorders": {
                    "range": _gr(tr, tr + 1, 0, C_GAP + 1),
                    "top":    {"style": "SOLID_MEDIUM", "color": BLUE},
                    "bottom": {"style": "SOLID_MEDIUM", "color": BLUE},
                    "left":   {"style": "SOLID_MEDIUM", "color": BLUE},
                    "right":  {"style": "SOLID_MEDIUM", "color": BLUE},
                }
            })

    # ── Total row ─────────────────────────────────────────────────────────────
    requests.append(_rc(TOTAL_ROW, TOTAL_ROW + 1, 0, C_GAP + 1,
                        bg=WHITE, bold=True, fg=NAVY, size=11))
    requests.append(_rc(TOTAL_ROW, TOTAL_ROW + 1, C_POL,   C_POL + 1,
                        bg=WHITE, bold=True, fg=GREEN_TEXT, size=11, align="CENTER"))
    requests.append(_rc(TOTAL_ROW, TOTAL_ROW + 1, C_HEADS,  C_HEADS + 1,
                        bg=WHITE, bold=True, fg=GREEN_TEXT, size=11, align="CENTER"))
    requests.append({
        "updateBorders": {
            "range": _gr(TOTAL_ROW, TOTAL_ROW + 1, 0, C_GAP + 1),
            "top": {"style": "SOLID_MEDIUM", "color": NAVY},
        }
    })

    # ── Freeze header row ─────────────────────────────────────────────────────
    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_id,
                           "gridProperties": {"frozenRowCount": HEADER_ROW + 1}},
            "fields": "gridProperties.frozenRowCount",
        }
    })

    # ── Bottom callout section ────────────────────────────────────────────────
    # 3 boxes each 4 cols wide (0-3, 4-7, 8-11)
    bot_box_cols = [(0, 4), (4, 8), (8, 12)]
    for c1, c2 in bot_box_cols:
        # Merge all rows for each box column span
        for br in range(BOT_ROW, BOT_ROW + 4):
            requests.append(_merge(br, br + 1, c1, c2))
        # Box background
        requests.append(_rc(BOT_ROW, BOT_ROW + 4, c1, c2, bg=LNAV))
        # Outer border
        requests.append({
            "updateBorders": {
                "range": _gr(BOT_ROW, BOT_ROW + 4, c1, c2),
                "top":    {"style": "SOLID", "color": BLUE, "width": 1},
                "bottom": {"style": "SOLID", "color": BLUE, "width": 1},
                "left":   {"style": "SOLID", "color": BLUE, "width": 1},
                "right":  {"style": "SOLID", "color": BLUE, "width": 1},
            }
        })

    # Box 1 formatting
    requests.append(_rc(BOT_ROW,     BOT_ROW + 1, 0, 4, bg=LNAV, bold=True,  fg=WHITE, size=9,  align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 1, BOT_ROW + 2, 0, 4, bg=LNAV, bold=True,  fg=GREEN, size=18, align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 2, BOT_ROW + 3, 0, 4, bg=LNAV, bold=False, fg=WHITE, size=9,  align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 3, BOT_ROW + 4, 0, 4, bg=LNAV))

    # Box 2 formatting
    requests.append(_rc(BOT_ROW,     BOT_ROW + 1, 4, 8, bg=LNAV, bold=True,  fg=WHITE, size=9,  align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 1, BOT_ROW + 2, 4, 8, bg=LNAV, bold=True,  fg=GREEN, size=14, align="CENTER", valign="MIDDLE", wrap="WRAP"))
    requests.append(_rc(BOT_ROW + 2, BOT_ROW + 3, 4, 8, bg=LNAV, bold=False, fg=WHITE, size=9,  align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 3, BOT_ROW + 4, 4, 8, bg=LNAV))

    # Box 3 formatting
    requests.append(_rc(BOT_ROW,     BOT_ROW + 1, 8, 12, bg=LNAV, bold=True,  fg=WHITE, size=9,  align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 1, BOT_ROW + 2, 8, 12, bg=LNAV, bold=False, fg=WHITE, size=11, align="CENTER", valign="MIDDLE"))
    requests.append(_rc(BOT_ROW + 2, BOT_ROW + 3, 8, 12, bg=LNAV, bold=False, fg={"red": 0.6, "green": 0.78, "blue": 0.98}, size=9, align="CENTER", valign="MIDDLE"))
    # Progress bar row (row BOT_ROW+3 is merged at I col = index 8)
    requests.append(_rc(BOT_ROW + 3, BOT_ROW + 4, 8, 12, bg=LNAV, align="CENTER"))

    # ── Native bar chart ──────────────────────────────────────────────────────
    requests.append({
        "addChart": {
            "chart": {
                "spec": {
                    "title": f"Daily Policy Submissions — {month_label}",
                    "titleTextFormat": {
                        "bold": True, "fontSize": 12,
                        "foregroundColor": NAVY,
                    },
                    "backgroundColor": {"red": 0.98, "green": 0.98, "blue": 0.98},
                    "basicChart": {
                        "chartType": "COLUMN",
                        "legendPosition": "NO_LEGEND",
                        "axis": [
                            {"position": "BOTTOM_AXIS",
                             "format": {"fontSize": 8}},
                            {"position": "LEFT_AXIS",
                             "title": "Policies Submitted",
                             "titleTextPosition": {"horizontalAlignment": "CENTER"}},
                        ],
                        "domains": [{
                            "domain": {"sourceRange": {"sources": [{
                                "sheetId": sheet_id,
                                "startRowIndex": DATA_START,
                                "endRowIndex": DATA_END + 1,
                                "startColumnIndex": C_DATE,
                                "endColumnIndex": C_DATE + 1,
                            }]}}
                        }],
                        "series": [{
                            "series": {"sourceRange": {"sources": [{
                                "sheetId": sheet_id,
                                "startRowIndex": DATA_START,
                                "endRowIndex": DATA_END + 1,
                                "startColumnIndex": C_POL,
                                "endColumnIndex": C_POL + 1,
                            }]}},
                            "targetAxis": "LEFT_AXIS",
                            "color": BLUE,
                        }],
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": HEADER_ROW,
                            "columnIndex": C_CHART,
                        },
                        "widthPixels":  548,
                        "heightPixels": 490,
                    }
                },
            }
        }
    })

    spreadsheet.batch_update({"requests": requests})


# Tabs the app manages on its own (AEP Tracker per-year, Goals/Settings
# persistence) — never auto-deleted by the CLI's report cleanup, since they
# aren't rebuilt by `track report` and would otherwise be wiped every run.
_PROTECTED_TAB_PREFIXES = ("AEP ",)
_PROTECTED_TAB_NAMES    = {"App Settings"}


def _is_protected_tab(title: str) -> bool:
    return title in _PROTECTED_TAB_NAMES or title.startswith(_PROTECTED_TAB_PREFIXES)


def _delete_stale_tabs(spreadsheet: gspread.Spreadsheet, keep: set) -> None:
    """Delete any worksheet whose title is not in the keeper set (and isn't
    one of the app-managed protected tabs)."""
    for ws in list(spreadsheet.worksheets()):
        if ws.title not in keep and not _is_protected_tab(ws.title):
            try:
                spreadsheet.del_worksheet(ws)
                print(f"  Deleted stale tab: {ws.title}")
            except Exception as e:
                print(f"  Could not delete tab '{ws.title}': {e}")


def update_sheet(
    sheet_url: str,
    impersonation_target: str,
    tab_names: dict,
    all_clients: pd.DataFrame,
    active_pending_df: pd.DataFrame,
    cancelled_missing_df: pd.DataFrame,
    months: Optional[Dict[str, pd.DataFrame]] = None,
    supplemental_df: Optional[pd.DataFrame] = None,
    health_pastdue_df: Optional[pd.DataFrame] = None,
    commission_gaps_df: Optional[pd.DataFrame] = None,
    ambetter_disputes_df: Optional[pd.DataFrame] = None,
    follow_ups_df: Optional[pd.DataFrame] = None,
) -> None:
    spreadsheet = _open_sheet(sheet_url, impersonation_target)

    # (title, df, show_active_row, show_inactive_row)
    data_tabs = [
        (tab_names["all_clients"],       all_clients,          True,  True),
        (tab_names["active_pending"],    active_pending_df,    True,  False),
        (tab_names["cancelled_missing"], cancelled_missing_df, False, True),
    ]

    dashboard_title = tab_names.get("dashboard", "Dashboard")
    supp_title = tab_names.get("supplemental", "Supplemental")
    pastdue_title = tab_names.get("health_pastdue", "Health Past Due")
    gaps_title = tab_names.get("commission_gaps", "Commission Gaps")
    disputes_title = tab_names.get("ambetter_disputes", "Ambetter Disputes")
    follow_ups_title = tab_names.get("follow_ups", "Follow-ups")
    daily_detail_title = tab_names.get("daily_detail", "Daily Detail")

    # Daily tracker tabs — one per ingested month, all history included.
    daily_tracker_tabs: Dict[str, str] = {}   # month_str → tab title
    if months:
        for m in sorted(months.keys()):
            m_label = pd.Timestamp(m + "-01").strftime("%b %Y")
            daily_tracker_tabs[m] = f"Daily Tracker - {m_label}"

    _has_supp = supplemental_df is not None and not supplemental_df.empty
    _has_pastdue = health_pastdue_df is not None and not health_pastdue_df.empty
    _has_gaps = commission_gaps_df is not None and not commission_gaps_df.empty
    _has_disputes = ambetter_disputes_df is not None and not ambetter_disputes_df.empty
    _has_follow_ups = follow_ups_df is not None and not follow_ups_df.empty
    all_titles = {dashboard_title} | {t for t, *_ in data_tabs} | set(daily_tracker_tabs.values())
    if _has_supp:
        all_titles |= {supp_title}
    if _has_pastdue:
        all_titles |= {pastdue_title}
    if _has_gaps:
        all_titles |= {gaps_title}
    if _has_disputes:
        all_titles |= {disputes_title}
    if _has_follow_ups:
        all_titles |= {follow_ups_title}
    _daily_detail = _build_daily_detail(months) if months else pd.DataFrame()
    _has_detail = not _daily_detail.empty
    if _has_detail:
        all_titles |= {daily_detail_title}

    # Remove any tabs that no longer belong
    _delete_stale_tabs(spreadsheet, keep=all_titles)

    # ── Dashboard tab — always at index 0 ─────────────────────────────────────
    dash_ws = _ensure_tab(spreadsheet, dashboard_title)
    try:
        dash_ws.update_index(0)
    except Exception as e:
        print(f"  Warning: could not reorder Dashboard tab: {e}")

    if months is not None:
        print(f"  Building dashboard data...")
        try:
            dashboard_data = build_dashboard_data(months, all_clients)
            # Pre-fetch (or create) the All Missing/Cancelled worksheet so we can
            # build per-month filter views and wire up Policies Lost hyperlinks.
            cancelled_ws = _ensure_tab(spreadsheet, tab_names["cancelled_missing"])
            write_dashboard(spreadsheet, dash_ws, dashboard_data, cancelled_ws=cancelled_ws)
            print(f"  Updated tab: {dashboard_title}")
        except Exception as e:
            print(f"  Warning: dashboard write failed: {e}")
    else:
        print(f"  Skipping dashboard (no months data supplied).")

    # ── Remaining data tabs ────────────────────────────────────────────────────
    for tab_title, df, show_active, show_inactive in data_tabs:
        ws = _ensure_tab(spreadsheet, tab_title)
        _write_all_clients_tab(ws, df, show_active_row=show_active, show_inactive_row=show_inactive)
        print(f"  Updated tab: {tab_title} ({len(df)} rows)")

    # ── Supplemental tab (dental/vision/STM/accident across carriers) ─────────
    if _has_supp:
        try:
            supp_ws = _ensure_tab(spreadsheet, supp_title)
            _write_tab(supp_ws, supplemental_df)
            print(f"  Updated tab: {supp_title} ({len(supplemental_df)} rows)")
        except Exception as e:
            print(f"  Warning: supplemental write failed: {e}")

    # ── Health Past Due tab (active medical plans behind on payment) ──────────
    if _has_pastdue:
        try:
            pastdue_ws = _ensure_tab(spreadsheet, pastdue_title)
            _write_tab(pastdue_ws, health_pastdue_df)
            print(f"  Updated tab: {pastdue_title} ({len(health_pastdue_df)} rows)")
        except Exception as e:
            print(f"  Warning: health past-due write failed: {e}")

    # ── Commission Gaps tab (active clients not being paid / stopped) ─────────
    if _has_gaps:
        try:
            gaps_ws = _ensure_tab(spreadsheet, gaps_title)
            _write_tab(gaps_ws, commission_gaps_df)
            print(f"  Updated tab: {gaps_title} ({len(commission_gaps_df)} rows)")
        except Exception as e:
            print(f"  Warning: commission gaps write failed: {e}")

    # ── Ambetter Disputes tab (carrier says owed + member current, but unpaid) ──
    if _has_disputes:
        try:
            disputes_ws = _ensure_tab(spreadsheet, disputes_title)
            _write_tab(disputes_ws, ambetter_disputes_df)
            print(f"  Updated tab: {disputes_title} ({len(ambetter_disputes_df)} rows)")
        except Exception as e:
            print(f"  Warning: Ambetter disputes write failed: {e}")

    # ── Follow-ups tab (HealthSherpa DMI/SVI verifications: open + expired) ────
    if _has_follow_ups:
        try:
            follow_ups_ws = _ensure_tab(spreadsheet, follow_ups_title)
            _write_tab(follow_ups_ws, follow_ups_df)
            print(f"  Updated tab: {follow_ups_title} ({len(follow_ups_df)} rows)")
        except Exception as e:
            print(f"  Warning: Follow-ups write failed: {e}")

    # ── Daily Detail tab (per-policy submissions, for the chart drill-down) ───
    if _has_detail:
        try:
            detail_ws = _ensure_tab(spreadsheet, daily_detail_title)
            _write_tab(detail_ws, _daily_detail)
            print(f"  Updated tab: {daily_detail_title} ({len(_daily_detail)} rows)")
        except Exception as e:
            print(f"  Warning: daily detail write failed: {e}")

    # ── Daily tracker tabs (one per month) ───────────────────────────────────
    # Pause between tabs to avoid hitting the Sheets API write-per-minute quota.
    for i, (m, tab_title) in enumerate(daily_tracker_tabs.items()):
        if i > 0:
            print(f"  Pausing 15 s to stay under Sheets API quota...")
            time.sleep(15)
        try:
            daily_ws = _ensure_tab(spreadsheet, tab_title)
            _write_daily_tracker_tab(spreadsheet, daily_ws, months, m)
            print(f"  Updated tab: {tab_title}")
        except Exception as e:
            print(f"  Warning: daily tracker write failed for {tab_title}: {e}")
