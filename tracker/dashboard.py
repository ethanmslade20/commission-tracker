"""
Premium executive Dashboard tab for the Commission Tracker Google Sheet.

Writes:
  - KPI header section (Total Active Policies, Total Members, MoM Growth %, Churn Rate %)
  - Pie chart: policies by carrier (top 10 + Other)
  - Pie chart: policies by state
  - Month-over-month trend table with conditional formatting
  - Native Google Sheets charts via batchUpdate addChart requests
"""

from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import gspread

# ──────────────────────────────────────────────────────────────────────────────
# Colour constants (RGB 0–1 floats for Sheets API)
# ──────────────────────────────────────────────────────────────────────────────
_NAVY   = {"red": 0.102, "green": 0.153, "blue": 0.267}   # #1a2744
_WHITE  = {"red": 1.0,   "green": 1.0,   "blue": 1.0}
_GREEN  = {"red": 0.180, "green": 0.800, "blue": 0.443}   # #2ecc71
_RED    = {"red": 0.906, "green": 0.298, "blue": 0.235}   # #e74c3c
_LIGHT_NAVY = {"red": 0.141, "green": 0.208, "blue": 0.369}  # slightly lighter navy
_GRAY   = {"red": 0.95,  "green": 0.95,  "blue": 0.95}

_ACTIVE_STATUSES   = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
_INACTIVE_STATUSES = {"Terminated", "Cancelled"}


# ──────────────────────────────────────────────────────────────────────────────
# Data-building helpers
# ──────────────────────────────────────────────────────────────────────────────

def build_dashboard_data(
    months: Dict[str, pd.DataFrame],
    all_clients: pd.DataFrame,
) -> Dict[str, Any]:
    """
    Compute all data structures needed for the dashboard.

    Returns a dict with:
      kpis            – dict of KPI label → value
      carrier_df      – DataFrame with columns [Carrier, Policies] (top 10 + Other)
      state_df        – DataFrame with columns [State, Policies]
      mom_df          – DataFrame with MoM trend table
    """
    sorted_months = sorted(months.keys())

    # ── MoM table first (needed for KPI averages) ────────────────────────────
    # Start from the earliest real snapshot month, not earliest effective_date.
    # Months before the first snapshot are reconstructed guesses — no cancellation
    # data exists for them, so they'd show false 0% churn.
    first_snapshot_month = sorted_months[0] if sorted_months else None
    mom_df = _build_mom_from_all_clients(all_clients, start_month=first_snapshot_month)

    # ── KPIs ──────────────────────────────────────────────────────────────────
    active_df = all_clients[
        all_clients["status"].isin(_ACTIVE_STATUSES)
    ] if "status" in all_clients.columns else pd.DataFrame()

    total_active_policies = len(active_df)
    total_members = int(active_df["applicant_count"].sum()) if "applicant_count" in active_df.columns else 0

    # Averages from Jan 1 of the current year forward (reflects current-year pace)
    if not mom_df.empty and "New Policies" in mom_df.columns:
        import datetime as _dt
        _ytd_start = f"{_dt.date.today().year}-02"
        _ytd = mom_df[mom_df["Month"] >= _ytd_start] if "Month" in mom_df.columns else mom_df
        _base = _ytd if not _ytd.empty else mom_df
        # Losses averaged from Jul 2025 forward (when the book really began) — the
        # mid-2025 ramp months carry ~zero losses and drag the average down.
        _lost_base = mom_df[mom_df["Month"] >= "2025-07"] if "Month" in mom_df.columns else mom_df
        if _lost_base.empty:
            _lost_base = mom_df
        avg_added          = round(_base["New Policies"].mean(), 1)
        avg_lost           = round(_lost_base["Policies Lost"].mean(), 1)
        avg_members_added  = round(_base["New Members"].mean(), 1)
        avg_members_lost   = round(_lost_base["Members Lost"].mean(), 1)
    else:
        avg_added = avg_lost = avg_members_added = avg_members_lost = "N/A"

    kpis = {
        "Total Active Policies":     total_active_policies,
        "Total Members":             total_members,
        "Avg Policies Added/Month":  avg_added,
        "Avg Policies Lost/Month":   avg_lost,
        "Avg Members Added/Month":   avg_members_added,
        "Avg Members Lost/Month":    avg_members_lost,
    }

    # ── Carrier breakdown ─────────────────────────────────────────────────────
    if "carrier" in active_df.columns and not active_df.empty:
        carrier_counts = (
            active_df["carrier"]
            .fillna("Unknown")
            .value_counts()
            .reset_index()
        )
        carrier_counts.columns = ["Carrier", "Policies"]
        # Top 10; group the rest as "Other"
        if len(carrier_counts) > 10:
            top10 = carrier_counts.head(10).copy()
            other_count = int(carrier_counts.iloc[10:]["Policies"].sum())
            other_row = pd.DataFrame([{"Carrier": "Other", "Policies": other_count}])
            carrier_df = pd.concat([top10, other_row], ignore_index=True)
        else:
            carrier_df = carrier_counts.copy()
    else:
        carrier_df = pd.DataFrame(columns=["Carrier", "Policies"])

    # ── State breakdown ───────────────────────────────────────────────────────
    if "state" in active_df.columns and not active_df.empty:
        state_counts = (
            active_df["state"]
            .fillna("Unknown")
            .value_counts()
            .reset_index()
        )
        state_counts.columns = ["State", "Policies"]
        state_df = state_counts.copy()
    else:
        state_df = pd.DataFrame(columns=["State", "Policies"])

    return {
        "kpis":       kpis,
        "carrier_df": carrier_df,
        "state_df":   state_df,
        "mom_df":     mom_df,
    }


def _count_active(df: pd.DataFrame) -> int:
    if "status" not in df.columns:
        return len(df)
    return int(df["status"].isin(_ACTIVE_STATUSES).sum())


def _count_members(df: pd.DataFrame) -> int:
    if "applicant_count" not in df.columns:
        return 0
    sub = df[df["status"].isin(_ACTIVE_STATUSES)] if "status" in df.columns else df
    return int(pd.to_numeric(sub["applicant_count"], errors="coerce").fillna(0).sum())


def _count_new(df_prev: pd.DataFrame, df_curr: pd.DataFrame) -> int:
    prev_ids = _policy_ids(df_prev)
    curr_ids = _policy_ids(df_curr)
    return len(curr_ids - prev_ids)


def _count_lost(df_prev: pd.DataFrame, df_curr: pd.DataFrame) -> int:
    prev_ids = _policy_ids(df_prev)
    curr_ids = _policy_ids(df_curr)
    return len(prev_ids - curr_ids)


def _policy_ids(df: pd.DataFrame) -> set:
    if "policy_id" in df.columns:
        return set(df["policy_id"].dropna().astype(str))
    if "client_name" in df.columns:
        return set(df["client_name"].dropna().astype(str))
    return set(range(len(df)))


def _build_mom_from_all_clients(
    all_clients: pd.DataFrame,
    start_month: Optional[str] = None,
) -> pd.DataFrame:
    """
    Build the MoM trend table directly from all_clients using effective_date
    and term_date. Covers every month from the earliest effective_date in the
    data through the current month. Pass start_month (YYYY-MM) to override.

    Columns: Month, Total Policies, Total Members, New Policies, New Members,
             Policies Lost, Members Lost, Net Change, % Growth
    """
    import datetime

    if all_clients.empty:
        return pd.DataFrame()

    eff   = pd.to_datetime(all_clients.get("effective_date"), errors="coerce")
    term  = pd.to_datetime(all_clients.get("term_date"),      errors="coerce")
    # term_estimated may arrive as real booleans (local parquet) or as text
    # ("TRUE"/"FALSE") when read back from Google Sheets. A plain .astype(bool)
    # would treat the string "FALSE" as True (non-empty string), wrongly flagging
    # every row as estimated and zeroing out all losses. Parse text explicitly.
    def _as_bool(v) -> bool:
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ("true", "1", "yes", "t")
    _est  = (all_clients.get("term_estimated", pd.Series(False, index=all_clients.index))
             .apply(_as_bool))
    count = pd.to_numeric(
        all_clients.get("applicant_count", pd.Series([1] * len(all_clients))),
        errors="coerce",
    ).fillna(1)

    today     = datetime.date.today()
    end_month = pd.Timestamp(f"{today.year}-{today.month:02d}-01")

    if start_month:
        first_month = pd.Timestamp(start_month + "-01")
    else:
        min_eff = eff.dropna().min()
        detected = min_eff.to_period("M").to_timestamp() if pd.notna(min_eff) else end_month
        first_month = detected

    months_idx = pd.date_range(start=first_month, end=end_month, freq="MS")

    rows = []
    prev_total: Optional[int] = None

    for month_start in months_idx:
        month_end = month_start + pd.offsets.MonthEnd(0)

        # Active: started on or before month-end AND not yet terminated before month-start
        active_mask = (eff <= month_end) & (term.isna() | (term >= month_start))
        total_policies = int(active_mask.sum())
        total_members  = int(count[active_mask].sum())

        # New: effective_date falls within this month
        new_mask     = (eff >= month_start) & (eff <= month_end)
        new_policies = int(new_mask.sum())
        new_members  = int(count[new_mask].sum())

        # Lost: term_date falls within this month. Exclude losses with an
        # ESTIMATED term date (carrier gave no real date — we only know the
        # client is gone, not when) so they don't distort the churn rate / LTV.
        lost_mask     = term.notna() & (term >= month_start) & (term <= month_end) & (~_est)
        policies_lost = int(lost_mask.sum())
        members_lost  = int(count[lost_mask].sum())

        net_change = new_policies - policies_lost
        growth_pct = (
            round(net_change / prev_total * 100, 2)
            if prev_total and prev_total > 0 else 0.0
        )

        rows.append({
            "Month":          month_start.strftime("%Y-%m"),
            "Total Policies": total_policies,
            "Total Members":  total_members,
            "New Policies":   new_policies,
            "New Members":    new_members,
            "Policies Lost":  policies_lost,
            "Members Lost":   members_lost,
            "Net Change":     net_change,
            "% Growth":       growth_pct,
        })
        prev_total = total_policies

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Sheet-writing helpers
# ──────────────────────────────────────────────────────────────────────────────

def _col_letter(n: int) -> str:
    """Convert 0-indexed column number to A1-notation letter(s)."""
    result = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def _range_notation(row: int, col: int, nrows: int = 1, ncols: int = 1) -> str:
    """Build A1 range notation from 0-indexed row/col."""
    start = f"{_col_letter(col)}{row + 1}"
    end   = f"{_col_letter(col + ncols - 1)}{row + nrows}"
    return f"{start}:{end}"


def _grid_range(sheet_id: int, r1: int, r2: int, c1: int, c2: int) -> dict:
    """GridRange dict (end indices are exclusive)."""
    return {
        "sheetId":          sheet_id,
        "startRowIndex":    r1,
        "endRowIndex":      r2,
        "startColumnIndex": c1,
        "endColumnIndex":   c2,
    }


def _source_range(sheet_id: int, r1: int, r2: int, c1: int, c2: int) -> dict:
    return {"sourceRange": {"sources": [_grid_range(sheet_id, r1, r2, c1, c2)]}}


def _cell_format(
    bg: Optional[dict] = None,
    bold: bool = False,
    font_size: int = 11,
    fg: Optional[dict] = None,
    align: Optional[str] = None,
    valign: Optional[str] = None,
    wrap: Optional[str] = None,
) -> dict:
    fmt: dict = {
        "textFormat": {
            "bold":     bold,
            "fontSize": font_size,
        }
    }
    if fg:
        fmt["textFormat"]["foregroundColor"] = fg
    if bg:
        fmt["backgroundColor"] = bg
    if align:
        fmt["horizontalAlignment"] = align
    if valign:
        fmt["verticalAlignment"] = valign
    if wrap:
        fmt["wrapStrategy"] = wrap
    return fmt


def _repeat_cell_request(
    sheet_id: int,
    r1: int, r2: int, c1: int, c2: int,
    cell_format: dict,
    fields: str = "userEnteredFormat",
) -> dict:
    return {
        "repeatCell": {
            "range": _grid_range(sheet_id, r1, r2, c1, c2),
            "cell":  {"userEnteredFormat": cell_format},
            "fields": fields,
        }
    }


def _merge_request(sheet_id: int, r1: int, r2: int, c1: int, c2: int) -> dict:
    return {
        "mergeCells": {
            "range":     _grid_range(sheet_id, r1, r2, c1, c2),
            "mergeType": "MERGE_ALL",
        }
    }


def _update_cell_value(
    sheet_id: int, row: int, col: int, value: Any
) -> dict:
    """Build a single-cell updateCells request with a string/number value."""
    if isinstance(value, (int, float)):
        cell_val = {"numberValue": value}
    else:
        cell_val = {"stringValue": str(value)}
    return {
        "updateCells": {
            "rows": [{"values": [{"userEnteredValue": cell_val}]}],
            "fields": "userEnteredValue",
            "start":  {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col},
        }
    }


def _column_width_request(sheet_id: int, col: int, pixel_size: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId":    sheet_id,
                "dimension":  "COLUMNS",
                "startIndex": col,
                "endIndex":   col + 1,
            },
            "properties": {"pixelSize": pixel_size},
            "fields":     "pixelSize",
        }
    }


def _row_height_request(sheet_id: int, row: int, pixel_size: int) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId":    sheet_id,
                "dimension":  "ROWS",
                "startIndex": row,
                "endIndex":   row + 1,
            },
            "properties": {"pixelSize": pixel_size},
            "fields":     "pixelSize",
        }
    }


# ──────────────────────────────────────────────────────────────────────────────
# Main write function
# ──────────────────────────────────────────────────────────────────────────────

# Layout constants (row indices, 0-based)
_KPI_ROW           = 0   # row 1 in Sheets
_KPI_LABEL_ROW     = 1   # row 2 – labels under KPI values
_SEPARATOR_ROW     = 3
_CARRIER_LABEL_ROW = 4   # data table + chart start here
_STATE_LABEL_ROW   = 4

# Column layout:
#  A(0) B(1) | C(2) chart | … | F(5) spacer | G(6) H(7) | I(8) chart
_CARRIER_COL      = 0   # carrier table: cols A-B
_STATE_COL        = 6   # state table:   cols G-H
_CARRIER_CHART_COL = 2  # carrier chart anchored at col C
_STATE_CHART_COL   = 8  # state chart anchored at col I

# MoM table starts just below the data tables (state has up to 21 rows + 1 header)
_MOM_ROW_OFFSET = 27


def write_dashboard(
    spreadsheet: gspread.Spreadsheet,
    ws: gspread.Worksheet,
    dashboard_data: Dict[str, Any],
    cancelled_ws: Optional[gspread.Worksheet] = None,
) -> None:
    """
    Write the full dashboard to worksheet `ws`.
    Uses batchUpdate for formatting and charts; direct cell writes for data.

    Pass `cancelled_ws` to enable clickable Policies Lost hyperlinks that jump
    to the All Missing/Cancelled tab with a pre-built filter for that month.
    """
    ws.clear()
    sheet_id = ws.id

    # Unmerge KPI rows BEFORE writing any values.
    # ws.clear() removes values but NOT merge formatting, so any cell that sits
    # inside an old merged range would silently discard its new value.
    spreadsheet.batch_update({"requests": [
        {"unmergeCells": {"range": _grid_range(sheet_id, _KPI_ROW, _KPI_LABEL_ROW + 1, 0, 12)}}
    ]})

    kpis:       Dict[str, Any]  = dashboard_data["kpis"]
    carrier_df: pd.DataFrame    = dashboard_data["carrier_df"]
    state_df:   pd.DataFrame    = dashboard_data["state_df"]
    mom_df:     pd.DataFrame    = dashboard_data["mom_df"]

    # ── 1. Write raw data to the sheet ────────────────────────────────────────

    # KPI layout: 6 blocks of 2 cols each across 12 columns (A–L)
    # Start columns (0-based): A=0, C=2, E=4, G=6, I=8, K=10
    kpi_keys      = list(kpis.keys())
    kpi_values    = [kpis[k] for k in kpi_keys]
    kpi_col_starts = [0, 2, 4, 6, 8, 10]   # A, C, E, G, I, K
    for col, val, key in zip(kpi_col_starts, kpi_values, kpi_keys):
        col_ltr = _col_letter(col)
        ws.update([[val]], f"{col_ltr}{_KPI_ROW + 1}")
        ws.update([[key]], f"{col_ltr}{_KPI_LABEL_ROW + 1}")

    # Separator label
    ws.update([["— Policies by Carrier —"]], f"A{_SEPARATOR_ROW + 1}")
    ws.update([["— Policies by State —"]],   f"G{_SEPARATOR_ROW + 1}")

    # Carrier data table (col A=carrier, col B=count)
    carrier_rows = [["Carrier", "Policies"]] + carrier_df.values.tolist()
    carrier_end_row = _CARRIER_LABEL_ROW + len(carrier_rows)
    ws.update(carrier_rows, f"A{_CARRIER_LABEL_ROW + 1}")

    # State data table (col G=state, col H=count)
    state_rows = [["State", "Policies"]] + state_df.values.tolist()
    state_end_row = _STATE_LABEL_ROW + len(state_rows)
    ws.update(state_rows, f"G{_STATE_LABEL_ROW + 1}")

    # MoM table
    if not mom_df.empty:
        mom_headers = list(mom_df.columns)
        mom_data    = mom_df.values.tolist()
        mom_start   = _MOM_ROW_OFFSET
        ws.update([["— Month-over-Month Trend —"]], f"A{mom_start + 1}")
        ws.update([mom_headers] + mom_data, f"A{mom_start + 2}")
        ws.format(f"A{mom_start + 2}:{_col_letter(len(mom_headers) - 1)}{mom_start + 2}",
                  {"textFormat": {"bold": True}})

    # ── 2. Build batchUpdate requests ─────────────────────────────────────────
    requests: List[dict] = []

    # --- Clear all stale cell formatting first (ws.clear() only removes values) ---
    requests.append({
        "repeatCell": {
            "range": _grid_range(sheet_id, 0, 200, 0, 20),
            "cell": {"userEnteredFormat": {}},
            "fields": "userEnteredFormat",
        }
    })

    # --- Column widths ---
    # A-B: carrier table (320px) | C-E: carrier chart (420px) | F: spacer
    # G-H: state table  (280px)  | I-K: state chart  (420px)
    requests += [
        _column_width_request(sheet_id, 0, 220),   # A  carrier label
        _column_width_request(sheet_id, 1,  90),   # B  carrier count
        _column_width_request(sheet_id, 2, 120),   # C  chart anchor / KPI block 2 start
        _column_width_request(sheet_id, 3, 160),   # D  }
        _column_width_request(sheet_id, 4, 240),   # E  } under carrier chart (420px total C-E)
        _column_width_request(sheet_id, 5, 130),   # F  MoM col / KPI block 3 start
        _column_width_request(sheet_id, 6, 190),   # G  state label
        _column_width_request(sheet_id, 7,  80),   # H  state count
        _column_width_request(sheet_id, 8, 100),   # I  state chart anchor / MoM data
        _column_width_request(sheet_id, 9, 160),   # J  }
        _column_width_request(sheet_id,10, 240),   # K  } under state chart (420px total I-K)
        _column_width_request(sheet_id,11, 220),   # L  KPI block 6 (Avg Members Lost/Month)
    ]

    # --- Unmerge entire KPI area first (clears any stale merge layout) ----------
    requests.append({
        "unmergeCells": {
            "range": _grid_range(sheet_id, _KPI_ROW, _KPI_LABEL_ROW + 1, 0, 11)
        }
    })

    # --- Merge KPI cells so each block has full readable width ---
    # Six blocks of 2 cols each: A-B, C-D, E-F, G-H, I-J, K-L
    kpi_blocks = [(0, 2), (2, 4), (4, 6), (6, 8), (8, 10), (10, 12)]
    for c1, c2 in kpi_blocks:
        requests.append(_merge_request(sheet_id, _KPI_ROW,       _KPI_ROW + 1,       c1, c2))
        requests.append(_merge_request(sheet_id, _KPI_LABEL_ROW, _KPI_LABEL_ROW + 1, c1, c2))

    # --- KPI value row (row 1) — big navy cells ---
    requests += [
        _row_height_request(sheet_id, _KPI_ROW, 70),
        _row_height_request(sheet_id, _KPI_LABEL_ROW, 28),
    ]
    for c1, c2 in kpi_blocks:
        requests.append(
            _repeat_cell_request(
                sheet_id,
                _KPI_ROW, _KPI_ROW + 1,
                c1, c2,
                _cell_format(
                    bg=_NAVY, bold=True, font_size=26,
                    fg=_WHITE, align="CENTER", valign="MIDDLE",
                ),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment)",
            )
        )
        requests.append(
            _repeat_cell_request(
                sheet_id,
                _KPI_LABEL_ROW, _KPI_LABEL_ROW + 1,
                c1, c2,
                _cell_format(
                    bg=_LIGHT_NAVY, bold=False, font_size=9,
                    fg=_WHITE, align="CENTER",
                ),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            )
        )

    # --- Separator rows ---
    # Carrier separator: cols A-B (0-1); State separator: cols G-H (_STATE_COL to +2)
    for sep_col in (0, _STATE_COL):
        requests.append(
            _repeat_cell_request(
                sheet_id,
                _SEPARATOR_ROW, _SEPARATOR_ROW + 1,
                sep_col, sep_col + 2,
                _cell_format(
                    bg=_NAVY, bold=True, font_size=11,
                    fg=_WHITE, align="CENTER",
                ),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            )
        )

    # --- Carrier data table header ---
    if not carrier_df.empty:
        requests.append(
            _repeat_cell_request(
                sheet_id,
                _CARRIER_LABEL_ROW, _CARRIER_LABEL_ROW + 1,
                _CARRIER_COL, _CARRIER_COL + 2,
                _cell_format(bg=_NAVY, bold=True, font_size=10, fg=_WHITE, align="CENTER"),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            )
        )
        # Data rows alternating background
        for i in range(len(carrier_df)):
            bg = _GRAY if i % 2 == 0 else _WHITE
            requests.append(
                _repeat_cell_request(
                    sheet_id,
                    _CARRIER_LABEL_ROW + 1 + i, _CARRIER_LABEL_ROW + 2 + i,
                    _CARRIER_COL, _CARRIER_COL + 2,
                    _cell_format(bg=bg, font_size=10),
                    fields="userEnteredFormat(backgroundColor,textFormat)",
                )
            )

    # --- State data table header ---
    if not state_df.empty:
        requests.append(
            _repeat_cell_request(
                sheet_id,
                _STATE_LABEL_ROW, _STATE_LABEL_ROW + 1,
                _STATE_COL, _STATE_COL + 2,
                _cell_format(bg=_NAVY, bold=True, font_size=10, fg=_WHITE, align="CENTER"),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            )
        )
        for i in range(len(state_df)):
            bg = _GRAY if i % 2 == 0 else _WHITE
            requests.append(
                _repeat_cell_request(
                    sheet_id,
                    _STATE_LABEL_ROW + 1 + i, _STATE_LABEL_ROW + 2 + i,
                    _STATE_COL, _STATE_COL + 2,
                    _cell_format(bg=bg, font_size=10),
                    fields="userEnteredFormat(backgroundColor,textFormat)",
                )
            )

    # --- MoM table formatting ---
    if not mom_df.empty:
        mom_start  = _MOM_ROW_OFFSET
        mom_ncols  = len(mom_df.columns)
        mom_nrows  = len(mom_df)
        header_row = mom_start + 1   # 0-based row index of the header
        data_start = mom_start + 2   # 0-based row index of first data row

        # Section header
        requests.append(
            _repeat_cell_request(
                sheet_id,
                mom_start, mom_start + 1,
                0, mom_ncols,
                _cell_format(bg=_NAVY, bold=True, font_size=12, fg=_WHITE, align="CENTER"),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            )
        )
        # Column header row
        requests.append(
            _repeat_cell_request(
                sheet_id,
                header_row, header_row + 1,
                0, mom_ncols,
                _cell_format(bg=_LIGHT_NAVY, bold=True, font_size=10, fg=_WHITE, align="CENTER"),
                fields="userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            )
        )

        # Freeze just the KPI rows (rows 1-2)
        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 2},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        })

    # ── 3. Pre-flight: delete stale charts AND conditional format rules ──────────
    preflight: List[dict] = []

    existing_charts = _get_sheet_charts(spreadsheet, sheet_id)
    for cid in existing_charts:
        preflight.append({"deleteEmbeddedObject": {"objectId": cid}})

    # Conditional format rules accumulate across runs — wipe them all first.
    n_cf_rules = _get_conditional_format_rule_count(spreadsheet, sheet_id)
    for i in range(n_cf_rules - 1, -1, -1):   # delete from highest index down
        preflight.append({"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}})

    if preflight:
        spreadsheet.batch_update({"requests": preflight})
        if existing_charts:
            print(f"  Deleted {len(existing_charts)} existing chart(s)")
        if n_cf_rules:
            print(f"  Cleared {n_cf_rules} stale conditional format rule(s)")

    # ── 4. Pie charts via addChart ─────────────────────────────────────────────

    if not carrier_df.empty:
        c_data_start = _CARRIER_LABEL_ROW + 1   # row after header
        c_data_end   = _CARRIER_LABEL_ROW + 1 + len(carrier_df)
        requests.append(_pie_chart_request(
            sheet_id   = sheet_id,
            title      = "Policies by Carrier",
            label_r1   = c_data_start,
            label_r2   = c_data_end,
            label_col  = _CARRIER_COL,
            value_col  = _CARRIER_COL + 1,
            anchor_row = _CARRIER_LABEL_ROW,
            anchor_col = _CARRIER_CHART_COL,
            width      = 420,
            height     = 360,
        ))

    if not state_df.empty:
        s_data_start = _STATE_LABEL_ROW + 1
        s_data_end   = _STATE_LABEL_ROW + 1 + len(state_df)
        requests.append(_pie_chart_request(
            sheet_id   = sheet_id,
            title      = "Policies by State",
            label_r1   = s_data_start,
            label_r2   = s_data_end,
            label_col  = _STATE_COL,
            value_col  = _STATE_COL + 1,
            anchor_row = _STATE_LABEL_ROW,
            anchor_col = _STATE_CHART_COL,
            width      = 420,
            height     = 360,
        ))

    # ── 5. Execute all requests ────────────────────────────────────────────────
    if requests:
        spreadsheet.batch_update({"requests": requests})

    # ── 6. Policies Lost hyperlinks (filter views on All Missing/Cancelled) ───
    if cancelled_ws is not None and not mom_df.empty:
        try:
            _setup_policies_lost_links(spreadsheet, ws, cancelled_ws, mom_df)
        except Exception as e:
            print(f"  Warning: could not create Policies Lost links: {e}")

    print(f"  Dashboard written: {len(kpis)} KPIs, "
          f"{len(carrier_df)} carriers, {len(state_df)} states, "
          f"{len(mom_df)} MoM rows")


def _get_conditional_format_rule_count(spreadsheet: gspread.Spreadsheet, sheet_id: int) -> int:
    """Return the number of conditional format rules on the given sheet."""
    try:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}"
        resp = spreadsheet.client.request(
            "get", url,
            params={"fields": "sheets(properties/sheetId,conditionalFormats)"},
        )
        for sheet in resp.json().get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") == sheet_id:
                return len(sheet.get("conditionalFormats", []))
    except Exception as e:
        print(f"  Warning: could not fetch conditional format rules: {e}")
    return 0


def _get_filter_view_ids(
    spreadsheet: gspread.Spreadsheet,
    sheet_id: int,
) -> List[int]:
    """Return list of filter view IDs currently on the given sheet."""
    try:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}"
        resp = spreadsheet.client.request(
            "get", url,
            params={"fields": "sheets(properties/sheetId,filterViews/filterViewId)"},
        )
        for sheet in resp.json().get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") == sheet_id:
                return [fv["filterViewId"] for fv in sheet.get("filterViews", [])]
    except Exception as e:
        print(f"  Warning: could not fetch filter views: {e}")
    return []


def _setup_policies_lost_links(
    spreadsheet: gspread.Spreadsheet,
    dash_ws: gspread.Worksheet,
    cancelled_ws: gspread.Worksheet,
    mom_df: pd.DataFrame,
) -> None:
    """
    For each MoM row:
      1. Create a filter view on the All Missing/Cancelled tab that shows only
         rows where term_date contains that month string (e.g. "2026-06").
      2. Overwrite the Policies Lost cell with a HYPERLINK formula that jumps
         directly to that filter view.
    Any month with 0 losses is left as a plain number.
    """
    if mom_df.empty or "Policies Lost" not in mom_df.columns:
        return

    cancelled_sheet_id = cancelled_ws.id
    months_list = list(mom_df["Month"])  # ["2026-04", "2026-05", ...]

    # term_date is 0-based column 4 in All Missing/Cancelled
    # _ALL_CLIENTS_COLS: first_name(0) last_name(1) carrier(2) effective_date(3) term_date(4)
    TERM_DATE_COL = 4

    # -- Delete any stale filter views on the cancelled tab --------------------
    existing_fvids = _get_filter_view_ids(spreadsheet, cancelled_sheet_id)
    delete_reqs = [
        {"deleteFilterView": {"filterId": fvid}} for fvid in existing_fvids
    ]

    # -- Create one filter view per month (TEXT_CONTAINS "YYYY-MM") ------------
    create_reqs = [
        {
            "addFilterView": {
                "filter": {
                    "title": f"Term {month_str}",
                    "range": {"sheetId": cancelled_sheet_id},
                    "criteria": {
                        str(TERM_DATE_COL): {
                            "condition": {
                                "type": "TEXT_CONTAINS",
                                "values": [{"userEnteredValue": month_str}],
                            }
                        }
                    },
                }
            }
        }
        for month_str in months_list
    ]

    resp = spreadsheet.batch_update({"requests": delete_reqs + create_reqs})
    replies = resp.get("replies", [])
    create_replies = replies[len(delete_reqs):]

    month_to_fvid: Dict[str, int] = {}
    for month_str, reply in zip(months_list, create_replies):
        fvid = (
            reply.get("addFilterView", {})
                 .get("filter", {})
                 .get("filterViewId")
        )
        if fvid is not None:
            month_to_fvid[month_str] = fvid

    if not month_to_fvid:
        print("  Warning: no filter view IDs returned; Policies Lost cells will be plain numbers")
        return

    # -- Write HYPERLINK formulas for the Policies Lost column -----------------
    # MoM data rows start at 1-based row: _MOM_ROW_OFFSET + 2 + 1
    #   _MOM_ROW_OFFSET (0-based section header) + 1 col-header row + 1-based offset
    try:
        policies_lost_col_idx = list(mom_df.columns).index("Policies Lost")
    except ValueError:
        return

    col_letter = _col_letter(policies_lost_col_idx)
    base_url = (
        f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}"
        f"/edit#gid={cancelled_sheet_id}"
    )

    formulas: List[List[Any]] = []
    linked = 0
    for _, row in mom_df.iterrows():
        month_str = str(row["Month"])
        lost_val  = int(row["Policies Lost"])
        if month_str in month_to_fvid and lost_val > 0:
            fvid    = month_to_fvid[month_str]
            url     = f"{base_url}&fvid={fvid}"
            formula = f'=HYPERLINK("{url}",{lost_val})'
            linked += 1
        else:
            formula = lost_val
        formulas.append([formula])

    data_start_row = _MOM_ROW_OFFSET + 2 + 1  # 1-based
    # raw=False tells gspread to send USER_ENTERED so Sheets evaluates the formulas
    dash_ws.update(
        formulas,
        f"{col_letter}{data_start_row}",
        raw=False,
    )
    print(f"  Policies Lost: {linked} month(s) linked to filtered view")


def _get_sheet_charts(spreadsheet: gspread.Spreadsheet, sheet_id: int) -> List[int]:
    """Return list of chart IDs currently embedded in the given sheet."""
    try:
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet.id}"
        resp = spreadsheet.client.request(
            "get", url,
            params={"fields": "sheets(properties/sheetId,charts/chartId)"},
        )
        for sheet in resp.json().get("sheets", []):
            if sheet.get("properties", {}).get("sheetId") == sheet_id:
                return [c["chartId"] for c in sheet.get("charts", [])]
    except Exception as e:
        print(f"  Warning: could not fetch chart list: {e}")
    return []


def _pie_chart_request(
    sheet_id:   int,
    title:      str,
    label_r1:   int,
    label_r2:   int,
    label_col:  int,
    value_col:  int,
    anchor_row: int,
    anchor_col: int,
    width:      int = 480,
    height:     int = 340,
) -> dict:
    """Build an addChart request for a pie chart."""
    return {
        "addChart": {
            "chart": {
                "spec": {
                    "title": title,
                    "titleTextFormat": {
                        "bold":     True,
                        "fontSize": 14,
                        "foregroundColor": _NAVY,
                    },
                    "backgroundColor": _WHITE,
                    "pieChart": {
                        "legendPosition": "RIGHT_LEGEND",
                        "domain": _source_range(sheet_id, label_r1, label_r2, label_col, label_col + 1),
                        "series": _source_range(sheet_id, label_r1, label_r2, value_col,  value_col  + 1),
                        "threeDimensional": False,
                        "pieHole": 0.4,
                    },
                },
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId":     sheet_id,
                            "rowIndex":    anchor_row,
                            "columnIndex": anchor_col,
                        },
                        "offsetXPixels": 0,
                        "offsetYPixels": 0,
                        "widthPixels":   width,
                        "heightPixels":  height,
                    }
                },
            }
        }
    }
