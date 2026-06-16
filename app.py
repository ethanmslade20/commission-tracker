"""
Commission Tracker — Interactive Web Dashboard
Run:  streamlit run app.py
      (from ~/commission-tracker with .venv activated)
"""

import calendar
import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Commission Tracker",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",  # collapsed by default on mobile
)

# Proper mobile viewport so iPhone doesn't zoom out
st.markdown(
    '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">',
    unsafe_allow_html=True,
)

# ── PIN gate ──────────────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("""
    <style>
    #MainMenu, header, footer {visibility: hidden;}
    .pin-wrap {
        display: flex; flex-direction: column; align-items: center;
        justify-content: center; height: 80vh; gap: 1rem;
    }
    .pin-title { font-size: 1.8rem; font-weight: 700; }
    .pin-sub   { font-size: 0.95rem; opacity: 0.6; }
    </style>
    <div class="pin-wrap">
        <div class="pin-title">🔒 Ethan Slade Book of Business</div>
        <div class="pin-sub">Enter your 4-digit PIN to continue</div>
    </div>
    """, unsafe_allow_html=True)
    col = st.columns([1, 1, 1])[1]
    with col:
        pin = st.text_input("PIN", type="password", max_chars=4, placeholder="••••", label_visibility="collapsed")
        submitted = st.button("Unlock", use_container_width=True) or (len(pin) == 4)
        if submitted:
            if pin == "2020":
                st.session_state.authenticated = True
                st.rerun()
            elif pin:
                st.error("Incorrect PIN")
    st.stop()

# ── Theme — Deep Navy always ──────────────────────────────────────────────────
NAVY  = "#1a2744"
LNAV  = "#243664"
BLUE  = "#4285F4"
GREEN = "#2ecc71"
RED   = "#e74c3c"
GOLD  = "#f39c12"
T = dict(
    page_bg      = "#0f1a2e",
    sidebar_bg   = "#0f1a2e",
    kpi_bg       = "#1a2744",
    kpi_border   = "#243664",
    kpi_val      = "#ffffff",
    kpi_lbl      = "#8aacd6",
    kpi_sub      = "#5a7ab5",
    divider      = "#243664",
    progress_bg  = "#0d1321",
    goal_val     = "#4285F4",
    goal_green   = "#2ecc71",
    goal_gold    = "#f39c12",
    goal_red     = "#e74c3c",
    text_primary = "#e8edf5",
)

# ── CSS: custom KPI boxes + page theming ──────────────────────────────────────
st.markdown(f"""
<style>
  [data-testid="stAppViewContainer"] {{
    background-color: {T['page_bg']};
  }}
  [data-testid="stSidebar"] {{
    background-color: {T['sidebar_bg']};
  }}
  [data-testid="stSidebar"] * {{
    color: {T['text_primary']} !important;
  }}
  .main .block-container {{
    background-color: {T['page_bg']};
  }}
  h1, h2, h3, p, label, .stMarkdown {{
    color: {T['text_primary']};
  }}
  [data-testid="stMarkdownContainer"] h1,
  [data-testid="stHeadingWithActionElements"] h1,
  .stApp h1 {{
    color: {T['text_primary']} !important;
  }}
  .kpi-box {{
    background: {T['kpi_bg']};
    border-radius: 10px;
    padding: 22px 16px 18px;
    text-align: center;
    border: 1px solid {T['kpi_border']};
  }}
  .kpi-value {{
    font-size: 2.2rem;
    font-weight: 700;
    color: {T['kpi_val']};
    line-height: 1.1;
  }}
  .kpi-label {{
    font-size: 0.72rem;
    color: {T['kpi_lbl']};
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }}
  .section-divider {{ margin: 8px 0 20px; border-top: 1px solid {T['divider']}; }}
  .goal-kpi-box {{
    background: {T['kpi_bg']};
    border-radius: 12px;
    padding: 24px 16px 20px;
    text-align: center;
    border: 1px solid {T['kpi_border']};
    position: relative;
  }}
  .goal-kpi-value {{
    font-size: 2.6rem;
    font-weight: 800;
    color: {T['goal_val']};
    line-height: 1.1;
  }}
  .goal-kpi-value.green  {{ color: {T['goal_green']}; }}
  .goal-kpi-value.gold   {{ color: {T['goal_gold']}; }}
  .goal-kpi-value.red    {{ color: {T['goal_red']}; }}
  .goal-kpi-label {{
    font-size: 0.72rem;
    color: {T['kpi_lbl']};
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }}
  .goal-kpi-sub {{
    font-size: 0.82rem;
    color: {T['kpi_lbl']};
    margin-top: 4px;
  }}
  .progress-wrap {{
    background: {T['progress_bg']};
    border-radius: 999px;
    height: 22px;
    overflow: hidden;
    margin: 10px 0 6px;
  }}
  .progress-bar {{
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, {BLUE}, {GREEN});
    transition: width 0.6s ease;
  }}

  /* ── Mobile optimizations ── */
  @media (max-width: 768px) {{
    /* Bigger tap targets on KPI boxes */
    .kpi-box {{
      padding: 18px 12px 14px;
      margin-bottom: 8px;
    }}
    .kpi-value {{
      font-size: 1.8rem;
    }}
    .kpi-label {{
      font-size: 0.65rem;
    }}
    .goal-kpi-box {{
      padding: 18px 12px 14px;
      margin-bottom: 8px;
    }}
    .goal-kpi-value {{
      font-size: 2rem;
    }}
    /* Make block container use full width with less padding */
    .block-container {{
      padding-left: 1rem !important;
      padding-right: 1rem !important;
      padding-top: 1rem !important;
    }}
    /* Streamlit dataframes scroll horizontally */
    [data-testid="stDataFrame"] {{
      overflow-x: auto;
    }}
    /* Daily Tracker table progress bars — green */
    [data-testid="stDataFrame"] [role="progressbar"] > div {{
      background-color: #2ecc71 !important;
    }}
    /* Tighten up headers */
    h1 {{ font-size: 1.6rem !important; }}
    h2 {{ font-size: 1.2rem !important; }}
    h3 {{ font-size: 1rem !important; }}
    /* Progress bar thicker for easier reading */
    .progress-wrap {{
      height: 26px;
    }}
  }}
</style>
""", unsafe_allow_html=True)


def _load_appointments() -> dict:
    """Load state→carrier appointments from config. Returns {state: [keywords]}."""
    import yaml
    appt_path = Path("config/appointments.yaml")
    if not appt_path.exists():
        return {}
    try:
        with open(appt_path) as f:
            data = yaml.safe_load(f)
        return data.get("appointments", {})
    except Exception:
        return {}

def _filter_by_appointments(df: pd.DataFrame, appointments: dict) -> pd.DataFrame:
    """Remove rows where the carrier is not in the agent's appointments for that state."""
    if not appointments or df.empty:
        return df
    if "state" not in df.columns or "carrier" not in df.columns:
        return df
    def _is_appointed(row):
        state   = str(row.get("state", "")).strip().upper()
        carrier = str(row.get("carrier", "")).strip().lower()
        if not state or not carrier:
            return True  # no state/carrier info — keep
        keywords = appointments.get(state, [])
        if not keywords:
            return False  # state not in appointments — exclude
        return any(kw.lower() in carrier for kw in keywords)
    return df[df.apply(_is_appointed, axis=1)].copy()


def kpi_html(label: str, value, sub: str = "") -> str:
    sub_html = f'<div class="kpi-sub" style="font-size:0.7rem;color:#5a7ab5;margin-top:2px;">{sub}</div>' if sub else ""
    return (
        f'<div class="kpi-box">'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-label">{label}</div>'
        f'{sub_html}'
        f'</div>'
    )


# ── Data loading — dual mode ─────────────────────────────────────────────────
# Local: reads parquet snapshots from ./snapshots/
# Cloud: reads directly from Google Sheets using st.secrets credentials

def _load_from_parquet():
    from tracker.ingest import load_all_snapshots
    from tracker.diff import build_all_clients
    from tracker.dashboard import build_dashboard_data

    months = load_all_snapshots(Path("snapshots"))
    all_clients = build_all_clients(months)
    dashboard_data = build_dashboard_data(months, all_clients)
    return months, all_clients, dashboard_data


def _read_all_clients_from_sheet(spreadsheet) -> pd.DataFrame:
    """Parse the All Clients tab — skips the 2-row summary header."""
    import re
    ws = spreadsheet.worksheet("All Clients")
    all_values = ws.get_all_values()
    if len(all_values) < 3:
        return pd.DataFrame()

    # Row 0: Active summary, Row 1: Inactive summary, Row 2: col headers, Row 3+: data
    headers = all_values[2]
    data    = all_values[3:]
    df = pd.DataFrame(data, columns=headers).replace("", None)

    for col in ["effective_date", "term_date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ["applicant_count", "net_premium", "months_on_book"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _read_daily_tab_from_sheet(spreadsheet, tab_name: str) -> pd.DataFrame:
    """Read a Daily Tracker tab — finds the DATE header row and parses below it."""
    import re
    try:
        ws = spreadsheet.worksheet(tab_name)
        all_values = ws.get_all_values()
    except Exception:
        return pd.DataFrame()

    # Locate the DATE header row
    header_idx = next(
        (i for i, row in enumerate(all_values) if row and row[0].strip().upper() == "DATE"),
        None,
    )
    if header_idx is None:
        return pd.DataFrame()

    year_match = re.search(r"\d{4}", tab_name)
    year = int(year_match.group()) if year_match else dt.date.today().year

    rows = []
    for row in all_values[header_idx + 1:]:
        if not row or not row[0].strip():
            continue
        raw_date = re.sub(r"[⭐→]", "", row[0]).strip()
        if raw_date.upper() == "TOTAL":
            break
        try:
            date = pd.to_datetime(f"{raw_date} {year}", format="%b %d %Y")
            pol  = int(row[1]) if len(row) > 1 and row[1] else 0
            mem  = int(row[3]) if len(row) > 3 and row[3] else 0
            rows.append({"Date": date, "Policies": pol, "Members": mem})
        except Exception:
            continue
    return pd.DataFrame(rows)


def _load_from_sheets():
    """Cloud mode: authenticate with service account from st.secrets, read Sheet."""
    import gspread
    from google.oauth2 import service_account
    from tracker.dashboard import _build_mom_from_all_clients

    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
    )
    client       = gspread.authorize(creds)
    spreadsheet  = client.open_by_url(st.secrets["sheet_url"])

    all_clients  = _read_all_clients_from_sheet(spreadsheet)

    # Build carrier map from RAW data (before any filtering) for the Settings page
    _raw_state_carrier_map: dict = {}
    if "state" in all_clients.columns and "carrier" in all_clients.columns:
        for _, _row in all_clients.iterrows():
            _s = str(_row.get("state", "")).strip().upper()
            _c = str(_row.get("carrier", "")).strip()
            if _s and _c and _c.lower() not in ("none", "nan", ""):
                _raw_state_carrier_map.setdefault(_s, set()).add(_c)
    _raw_state_carrier_map = {s: sorted(c) for s, c in sorted(_raw_state_carrier_map.items())}

    all_clients  = _filter_by_appointments(all_clients, _load_appointments())
    # Deduplicate — use FFM App ID where available, fall back to name
    if not all_clients.empty:
        _ac = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
        all_clients = all_clients.copy()
        all_clients["_is_active"] = all_clients["status"].isin(_ac).astype(int)
        all_clients["effective_date"] = pd.to_datetime(all_clients.get("effective_date"), errors="coerce")
        all_clients = all_clients.sort_values(["_is_active", "effective_date"], ascending=[False, False])
        _has_id = all_clients["ffm_app_id"].notna() & (all_clients["ffm_app_id"] != "")
        _by_id   = all_clients[_has_id].drop_duplicates(subset=["ffm_app_id"], keep="first")
        _by_name = all_clients[~_has_id].drop_duplicates(subset=["first_name", "last_name"], keep="first")
        all_clients = pd.concat([_by_id, _by_name], ignore_index=True).drop(columns=["_is_active"]).reset_index(drop=True)

    # Determine first snapshot month from Daily Tracker tabs
    _snapshot_months = []
    for _ws in spreadsheet.worksheets():
        if _ws.title.startswith("Daily Tracker - "):
            try:
                _snapshot_months.append(
                    pd.Timestamp(_ws.title.replace("Daily Tracker - ", "")).strftime("%Y-%m")
                )
            except Exception:
                pass
    _first_snapshot = min(_snapshot_months) if _snapshot_months else None

    # Compute dashboard data from all_clients (same logic as dashboard.py)
    _ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    active_df = all_clients[all_clients["status"].isin(_ACTIVE)] if "status" in all_clients.columns else pd.DataFrame()

    mom_df = _build_mom_from_all_clients(all_clients, start_month=_first_snapshot)

    if not mom_df.empty and "New Policies" in mom_df.columns:
        _ytd_start = f"{dt.date.today().year}-02"
        _ytd = mom_df[mom_df["Month"] >= _ytd_start] if "Month" in mom_df.columns else mom_df
        _base = _ytd if not _ytd.empty else mom_df
        avg_added          = round(_base["New Policies"].mean(), 1)
        avg_lost           = round(mom_df["Policies Lost"].mean(), 1)
        avg_members_added  = round(_base["New Members"].mean(), 1)
        avg_members_lost   = round(mom_df["Members Lost"].mean(), 1)
    else:
        avg_added = avg_lost = avg_members_added = avg_members_lost = "N/A"

    kpis = {
        "Total Active Policies":    len(active_df),
        "Total Members":            int(active_df["applicant_count"].sum()) if "applicant_count" in active_df.columns else 0,
        "Avg Policies Added/Month": avg_added,
        "Avg Policies Lost/Month":  avg_lost,
        "Avg Members Added/Month":  avg_members_added,
        "Avg Members Lost/Month":   avg_members_lost,
    }

    carrier_df = (
        active_df.groupby("carrier").size().reset_index(name="Policies")
        .rename(columns={"carrier": "Carrier"})
        .nlargest(10, "Policies")
    ) if "carrier" in active_df.columns else pd.DataFrame(columns=["Carrier", "Policies"])

    state_df = (
        active_df.groupby("state").size().reset_index(name="Policies")
        .rename(columns={"state": "State"})
        .sort_values("Policies", ascending=False)
    ) if "state" in active_df.columns else pd.DataFrame(columns=["State", "Policies"])

    dashboard_data = {"kpis": kpis, "carrier_df": carrier_df, "state_df": state_df, "mom_df": mom_df,
                      "raw_state_carrier_map": _raw_state_carrier_map}

    # Read all Daily Tracker tabs dynamically
    daily_months: dict = {}
    for ws in spreadsheet.worksheets():
        if ws.title.startswith("Daily Tracker - "):
            try:
                label = ws.title.replace("Daily Tracker - ", "")
                ts    = pd.Timestamp(label)
                m_str = ts.strftime("%Y-%m")
                ddf   = _read_daily_tab_from_sheet(spreadsheet, ws.title)
                if not ddf.empty:
                    daily_months[m_str] = ddf
            except Exception:
                continue

    return daily_months, all_clients, dashboard_data


def _running_in_cloud() -> bool:
    """True when Streamlit secrets contain GCP credentials (i.e. cloud deployment)."""
    try:
        return "gcp_service_account" in st.secrets
    except Exception:
        return False


def _gspread_client():
    """Return an authenticated gspread client using st.secrets (cloud mode)."""
    import gspread
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly"],
    )
    return gspread.authorize(creds)


_AEP_STATUSES   = ["Not Started", "Contacted", "Renewed", "Lost"]
_AEP_COLS       = ["First Name", "Last Name", "State", "Carrier", "Members", "Monthly Premium", "Effective Date", "Status", "Notes"]
_AEP_TAB_PREFIX = "AEP "


def _aep_tab_name(year=None) -> str:
    import datetime as _dt
    y = year or (_dt.date.today().year + 1)
    return f"{_AEP_TAB_PREFIX}{y}"


@st.cache_data(ttl=30)
def _read_aep_tab(tab_name: str) -> pd.DataFrame:
    """Read the AEP Tracker tab from Google Sheets. Returns empty DF if tab missing."""
    if not _running_in_cloud():
        return pd.DataFrame(columns=_AEP_COLS)
    try:
        client = _gspread_client()
        sheet  = client.open_by_url(st.secrets["sheet_url"])
        ws     = sheet.worksheet(tab_name)
        rows   = ws.get_all_records()
        if not rows:
            return pd.DataFrame(columns=_AEP_COLS)
        df = pd.DataFrame(rows)
        for c in _AEP_COLS:
            if c not in df.columns:
                df[c] = ""
        df["Status"] = df["Status"].where(df["Status"].isin(_AEP_STATUSES), "Not Started")
        if "Monthly Premium" in df.columns:
            df["Monthly Premium"] = (
                df["Monthly Premium"].astype(str).str.replace("$", "", regex=False)
                .str.strip().replace("", "0")
                .pipe(pd.to_numeric, errors="coerce").fillna(0.0)
            )
        return df[_AEP_COLS].reset_index(drop=True)
    except Exception as _e:
        st.error(f"AEP read error: {_e}")
        return pd.DataFrame(columns=_AEP_COLS)


def _save_aep_tab(tab_name: str, df: pd.DataFrame) -> bool:
    """Write the edited AEP DataFrame back to Google Sheets. Returns True on success."""
    if not _running_in_cloud():
        return False
    try:
        import math
        client = _gspread_client()
        sheet  = client.open_by_url(st.secrets["sheet_url"])
        try:
            ws = sheet.worksheet(tab_name)
        except Exception:
            ws = sheet.add_worksheet(title=tab_name, rows=max(len(df) + 10, 500), cols=15)

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

        rows = [_AEP_COLS]
        for _, row in df.iterrows():
            rows.append([_clean(row.get(c, "")) for c in _AEP_COLS])
        ws.clear()
        ws.update(rows, value_input_option="USER_ENTERED")
        ws.format("A1:H1", {"textFormat": {"bold": True}})
        _read_aep_tab.clear()
        return True
    except Exception as e:
        st.error(f"Save failed: {e}")
        return False


_SETTINGS_TAB = "App Settings"


def _read_app_settings_raw() -> dict:
    """Uncached read of the persisted Goals/Settings blob from Google Sheets."""
    if not _running_in_cloud():
        return {}
    try:
        import json
        client = _gspread_client()
        sheet  = client.open_by_url(st.secrets["sheet_url"])
        ws     = sheet.worksheet(_SETTINGS_TAB)
        raw    = ws.acell("A1").value
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


@st.cache_data(ttl=30)
def _read_app_settings() -> dict:
    return _read_app_settings_raw()


def _save_app_settings(settings: dict) -> bool:
    if not _running_in_cloud():
        return False
    try:
        import json
        client = _gspread_client()
        sheet  = client.open_by_url(st.secrets["sheet_url"])
        try:
            ws = sheet.worksheet(_SETTINGS_TAB)
        except Exception:
            ws = sheet.add_worksheet(title=_SETTINGS_TAB, rows=10, cols=2)
        ws.update([[json.dumps(settings)]], "A1")
        _read_app_settings.clear()
        return True
    except Exception as e:
        st.error(f"Settings save failed: {e}")
        return False


def _persist_settings(**updates) -> bool:
    """Merge `updates` into the persisted settings blob and save."""
    current = _read_app_settings_raw()
    current.update(updates)
    return _save_app_settings(current)


@st.cache_data(ttl=60)
def load_data():
    if _running_in_cloud():
        return _load_from_sheets()
    return _load_from_parquet()


def build_daily_df(df: pd.DataFrame, month_str: str) -> pd.DataFrame:
    year, month = int(month_str[:4]), int(month_str[5:7])
    dim = calendar.monthrange(year, month)[1]
    all_days = pd.date_range(f"{month_str}-01", periods=dim, freq="D")

    # Cloud mode: df is already a pre-built Date/Policies/Members frame
    if "Date" in df.columns and "Policies" in df.columns and "submission_date" not in df.columns:
        return df.reset_index(drop=True)

    if "submission_date" not in df.columns or df["submission_date"].isna().all():
        return pd.DataFrame({"Date": all_days, "Policies": 0, "Members": 0})

    sub = pd.to_datetime(df["submission_date"], errors="coerce").dt.normalize()
    mem = pd.to_numeric(
        df.get("applicant_count", pd.Series([1] * len(df))), errors="coerce"
    ).fillna(1)

    m_start = pd.Timestamp(f"{month_str}-01")
    m_end   = m_start + pd.offsets.MonthEnd(0)
    mask    = (sub >= m_start) & (sub <= m_end)

    grouped = (
        pd.DataFrame({"date": sub[mask], "mem": mem[mask]})
        .groupby("date")
        .agg(Policies=("mem", "count"), Members=("mem", "sum"))
        .reset_index()
        .rename(columns={"date": "Date"})
    )
    grouped["Date"] = pd.to_datetime(grouped["Date"])

    result = (
        pd.DataFrame({"Date": all_days})
        .merge(grouped, on="Date", how="left")
        .fillna(0)
    )
    result["Policies"] = result["Policies"].astype(int)
    result["Members"]  = result["Members"].astype(int)
    return result


# ── Base chart layout shared across all charts ────────────────────────────────
def _chart_layout(**extra) -> dict:
    base = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e8edf5", size=12),
        margin=dict(t=30, b=40, l=10, r=10),
        xaxis=dict(gridcolor="#243664", showgrid=True, zeroline=False),
        yaxis=dict(gridcolor="#243664", showgrid=True, zeroline=False),
    )
    base.update(extra)
    return base


# ── Load ──────────────────────────────────────────────────────────────────────
months, all_clients, dd = load_data()

# 1. Appointment filter — remove clients from non-appointed carrier/state combos
_appointments = _load_appointments()
all_clients   = _filter_by_appointments(all_clients, _appointments)

# 2. Deduplicate — use FFM App ID where available, fall back to name
_ACTIVE_STS = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
if not all_clients.empty:
    all_clients = all_clients.copy()
    all_clients["_is_active"] = all_clients["status"].isin(_ACTIVE_STS).astype(int)
    all_clients["effective_date"] = pd.to_datetime(all_clients.get("effective_date"), errors="coerce")
    all_clients = all_clients.sort_values(["_is_active", "effective_date"], ascending=[False, False])
    # First dedupe by FFM App ID (most accurate)
    has_id = all_clients["ffm_app_id"].notna() & (all_clients["ffm_app_id"] != "")
    deduped_by_id   = all_clients[has_id].drop_duplicates(subset=["ffm_app_id"], keep="first")
    # Then dedupe remaining (no FFM ID) by name
    no_id = all_clients[~has_id]
    deduped_by_name = no_id.drop_duplicates(subset=["first_name", "last_name"], keep="first")
    all_clients = (
        pd.concat([deduped_by_id, deduped_by_name], ignore_index=True)
        .drop(columns=["_is_active"])
        .reset_index(drop=True)
    )

# Build full state→carrier map from raw snapshots (unfiltered) for Settings page
# Cloud mode: dd["raw_state_carrier_map"] was built before filtering inside _load_from_sheets()
# Local mode: build from months (parquet snapshots which have state/carrier columns)
_state_carrier_map: dict = dd.get("raw_state_carrier_map", {})
if not _state_carrier_map:
    for _snap_df in months.values():
        if "state" in _snap_df.columns and "carrier" in _snap_df.columns:
            for _, _row in _snap_df.iterrows():
                _s = str(_row.get("state", "")).strip().upper()
                _c = str(_row.get("carrier", "")).strip()
                if _s and _c and _c.lower() not in ("none", "nan", ""):
                    _state_carrier_map.setdefault(_s, set()).add(_c)
    _state_carrier_map = {s: sorted(c) for s, c in sorted(_state_carrier_map.items())}

# Pull persisted Goals/Settings from the Sheet (survives restarts and new devices)
_persisted_settings = _read_app_settings()

# Initialize session_state appointments — prefer the persisted save, falling back
# to yaml-keyword defaults for any state/carrier not yet in the saved settings.
if "settings_appointments" not in st.session_state:
    _appt_yaml = _load_appointments()
    _persisted_appts = _persisted_settings.get("appointments", {})
    _selected: dict = {}
    for state, carriers in _state_carrier_map.items():
        keywords = _appt_yaml.get(state, [])
        _saved_state = _persisted_appts.get(state, {})
        _selected[state] = {
            c: _saved_state.get(c, any(kw.lower() in c.lower() for kw in keywords))
            for c in carriers
        }
    st.session_state.settings_appointments = _selected

if "goal_members" not in st.session_state and "goal_members" in _persisted_settings:
    st.session_state["goal_members"] = _persisted_settings["goal_members"]
if "goal_date" not in st.session_state and "goal_date" in _persisted_settings:
    try:
        st.session_state["goal_date"] = dt.date.fromisoformat(_persisted_settings["goal_date"])
    except Exception:
        pass

# Override appointment filter with session_state settings
def _filter_by_settings(df: pd.DataFrame) -> pd.DataFrame:
    sel = st.session_state.get("settings_appointments", {})
    if not sel or df.empty:
        return df
    if "state" not in df.columns or "carrier" not in df.columns:
        return df
    def _keep(row):
        state   = str(row.get("state", "")).strip().upper()
        carrier = str(row.get("carrier", "")).strip()
        state_sel = sel.get(state)
        if not state_sel:
            return False
        return state_sel.get(carrier, False)
    return df[df.apply(_keep, axis=1)].copy()

all_clients = _filter_by_settings(all_clients)

kpis        = dd["kpis"]
mom_df      = dd["mom_df"]
carrier_df  = dd["carrier_df"]
state_df    = dd["state_df"]
latest_m    = sorted(months.keys())[-1]
latest_label = pd.Timestamp(latest_m + "-01").strftime("%B %Y")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Commission Tracker")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    page = st.radio(
        "Navigation",
        ["Dashboard", "Month-over-Month", "Daily Tracker", "Book of Business", "Goals", "Re-Engage", "AEP Tracker", "Settings"],
        label_visibility="collapsed",
    )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.caption(f"📅 Latest snapshot: **{latest_label}**")
    st.caption(f"👥 {len(all_clients):,} total clients tracked")

    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        for _k in ["aep_df", "aep_tab"]:
            st.session_state.pop(_k, None)
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
if page == "Dashboard":
    st.title("Dashboard")
    st.caption(f"Snapshot: {latest_label}")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # Row 1 KPIs
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(kpi_html("Total Active Policies", f"{kpis['Total Active Policies']:,}"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_html("Total Members", f"{kpis['Total Members']:,}"), unsafe_allow_html=True)
    with c3:
        ap = kpis["Total Active Policies"]
        tm = kpis["Total Members"]
        avg_sz = round(tm / ap, 1) if ap else "—"
        st.markdown(kpi_html("Avg Household Size", avg_sz), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Row 2 KPIs
    c4, c5, c6 = st.columns(3)
    with c4:
        st.markdown(kpi_html("Avg Policies Added / Month", kpis["Avg Policies Added/Month"], sub="Feb 2026 – present"), unsafe_allow_html=True)
    with c5:
        try:
            _churn_pct = round(float(kpis["Avg Policies Lost/Month"]) / max(kpis["Total Active Policies"], 1) * 100, 2)
            _churn_sub = f"All history · {_churn_pct}% monthly churn"
        except Exception:
            _churn_sub = "All history"
        st.markdown(kpi_html("Avg Policies Lost / Month", kpis["Avg Policies Lost/Month"], sub=_churn_sub), unsafe_allow_html=True)
    with c6:
        try:
            net = round(float(kpis["Avg Policies Added/Month"]) - float(kpis["Avg Policies Lost/Month"]), 1)
            net_str = f"+{net}" if net >= 0 else str(net)
        except Exception:
            net_str = "N/A"
        st.markdown(kpi_html("Avg Net Growth / Month", net_str, sub="Added (Feb+) minus Lost (all-time)"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Row 3 — Revenue KPIs
    _ACTIVE_STS = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    _PMPM = 23
    _active_mask = all_clients["status"].isin(_ACTIVE_STS) if "status" in all_clients.columns else pd.Series(False, index=all_clients.index)
    _total_members = int(all_clients.loc[_active_mask, "applicant_count"].sum()) if "applicant_count" in all_clients.columns else kpis.get("Total Members", 0)
    _mrr = _total_members * _PMPM
    _arr = _mrr * 12

    # Avg client lifetime from actual effective/term dates
    _today = pd.Timestamp(dt.date.today())
    if "effective_date" in all_clients.columns:
        _lt_df = all_clients.copy()
        _lt_df["effective_date"] = pd.to_datetime(_lt_df["effective_date"], errors="coerce")
        _lt_df["term_date"]      = pd.to_datetime(_lt_df.get("term_date"), errors="coerce")
        _lt_df["end"] = _lt_df["term_date"].fillna(_today)
        _lt_df["lifetime_mo"] = (_lt_df["end"] - _lt_df["effective_date"]).dt.days / 30.44
        _lt_df = _lt_df[_lt_df["lifetime_mo"] > 0]
        _avg_lifetime = round(_lt_df["lifetime_mo"].mean(), 1) if not _lt_df.empty else "—"
    else:
        _avg_lifetime = "—"

    r1, r2, r3 = st.columns(3)
    with r1:
        st.markdown(kpi_html("Expected Monthly Commission", f"${_mrr:,.0f}"), unsafe_allow_html=True)
    with r2:
        st.markdown(kpi_html("Expected Annual Commission", f"${_arr:,.0f}"), unsafe_allow_html=True)
    with r3:
        st.markdown(kpi_html("Commission per Policy / Mo", f"${_mrr / kpis['Total Active Policies']:.2f}" if kpis.get('Total Active Policies') else "—"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # Book age distribution
    st.subheader("Book age — months on book")
    if "months_on_book" in all_clients.columns or "effective_date" in all_clients.columns:
        _mob_df = all_clients.loc[_active_mask].copy()
        if "months_on_book" not in _mob_df.columns:
            _mob_df["months_on_book"] = None
        if "effective_date" in _mob_df.columns:
            _eff = pd.to_datetime(_mob_df["effective_date"], errors="coerce")
            _derived = ((_today - _eff).dt.days / 30.44).round(1)
            _mob_df["months_on_book"] = _mob_df["months_on_book"].fillna(_derived)
        _mob = _mob_df["months_on_book"].fillna(0)  # any remaining nulls → < 3 mo bucket
    if "months_on_book" in all_clients.columns or "effective_date" in all_clients.columns:
        _buckets = {
            "< 3 mo":   int((_mob < 3).sum()),
            "3–6 mo":   int(((_mob >= 3) & (_mob < 6)).sum()),
            "6–12 mo":  int(((_mob >= 6) & (_mob < 12)).sum()),
            "12–18 mo": int(((_mob >= 12) & (_mob < 18)).sum()),
            "18 mo+":   int((_mob >= 18).sum()),
        }
        _total_active_p = sum(_buckets.values())
        ba1, ba2, ba3, ba4, ba5 = st.columns(5)
        _bucket_colors = ["#e74c3c", GOLD, BLUE, "#2d5fa6", GREEN]
        for col, (label, count), color in zip([ba1, ba2, ba3, ba4, ba5], _buckets.items(), _bucket_colors):
            pct = round(count / _total_active_p * 100) if _total_active_p else 0
            with col:
                st.markdown(
                    f'<div class="kpi-box" style="border-top: 3px solid {color};">'
                    f'<div class="kpi-value" style="font-size:2rem;">{count:,}</div>'
                    f'<div class="kpi-label">{label}</div>'
                    f'<div class="kpi-sub" style="color:{color};font-weight:600;">{pct}%</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # Bar chart of distribution
        _mob_df = pd.DataFrame({"Bucket": list(_buckets.keys()), "Policies": list(_buckets.values()),
                                 "Color": _bucket_colors})
        fig_mob = px.bar(
            _mob_df, x="Bucket", y="Policies",
            color="Bucket",
            color_discrete_sequence=_bucket_colors,
            text="Policies",
        )
        fig_mob.update_traces(textposition="outside")
        _mob_max = max(_buckets.values()) if _buckets else 1
        fig_mob.update_layout(**_chart_layout(
            showlegend=False,
            xaxis=dict(gridcolor="rgba(0,0,0,0)", showgrid=False, zeroline=False),
            yaxis=dict(gridcolor="#243664", showgrid=True, zeroline=False,
                       range=[0, _mob_max * 1.18]),
            margin=dict(t=20, b=10, l=10, r=10),
            height=260,
        ))
        st.plotly_chart(fig_mob, use_container_width=True)

        # Risk callout
        _new_pct = round((_buckets["< 3 mo"] + _buckets["3–6 mo"]) / _total_active_p * 100) if _total_active_p else 0
        _veteran_pct = round(_buckets["18 mo+"] / _total_active_p * 100) if _total_active_p else 0
        st.caption(
            f"**{_new_pct}%** of your book is under 6 months old (higher AEP risk) &nbsp;·&nbsp; "
            f"**{_veteran_pct}%** has been with you 18+ months (most loyal clients)"
        )

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # Charts
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader("Policies by Carrier")
        if not carrier_df.empty:
            fig = px.pie(
                carrier_df,
                names="Carrier",
                values="Policies",
                hole=0.38,
                color_discrete_sequence=[
                    BLUE, "#6aabff", "#2d5fa6", GREEN, GOLD,
                    "#9b59b6", "#e67e22", "#1abc9c", "#e84393", "#95a5a6",
                ],
            )
            fig.update_traces(textfont_size=11, textposition="auto")
            fig.update_layout(**_chart_layout(
                legend=dict(font=dict(size=11), orientation="v"),
                margin=dict(t=10, b=10, l=10, r=10),
            ))
            st.plotly_chart(fig, use_container_width=True)

    with col_b:
        st.subheader("Policies by State (Top 15)")
        if not state_df.empty:
            top_states = state_df.sort_values("Policies", ascending=False).head(15)
            fig2 = px.bar(
                top_states.sort_values("Policies"),
                x="Policies",
                y="State",
                orientation="h",
                color="Policies",
                color_continuous_scale=[[0, LNAV], [1, BLUE]],
                text="Policies",
            )
            fig2.update_traces(textposition="outside")
            fig2.update_layout(**_chart_layout(
                coloraxis_showscale=False,
                xaxis=dict(gridcolor="#243664", showgrid=True, zeroline=False),
                yaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(size=11)),
                margin=dict(t=10, b=10, l=60, r=40),
            ))
            st.plotly_chart(fig2, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# MONTH-OVER-MONTH
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Month-over-Month":
    st.title("Month-over-Month Trends")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    if mom_df.empty:
        st.info("No month-over-month data available yet.")
    else:
        mom_plot = mom_df.copy()
        mom_plot["Month Label"] = mom_plot["Month"].apply(
            lambda m: pd.Timestamp(str(m) + "-01").strftime("%b %Y")
        )

        # Total policies over time
        st.subheader("Total Active Policies Over Time")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=mom_plot["Month Label"],
            y=mom_plot["Total Policies"],
            mode="lines+markers+text",
            text=mom_plot["Total Policies"],
            textposition="top center",
            textfont=dict(size=11, color="white"),
            line=dict(color=BLUE, width=3),
            marker=dict(size=9, color=BLUE, line=dict(width=2, color="white")),
            fill="tozeroy",
            fillcolor="rgba(66,133,244,0.12)",
        ))
        fig.update_layout(**_chart_layout(showlegend=False, height=320))
        st.plotly_chart(fig, use_container_width=True)

        # New vs Lost
        col_l, col_r = st.columns(2)

        with col_l:
            st.subheader("New vs. Lost Policies")
            fig2 = go.Figure()
            fig2.add_trace(go.Bar(
                x=mom_plot["Month Label"], y=mom_plot["New Policies"],
                name="Added", marker_color=GREEN,
            ))
            fig2.add_trace(go.Bar(
                x=mom_plot["Month Label"], y=mom_plot["Policies Lost"],
                name="Lost", marker_color=RED,
            ))
            fig2.update_layout(**_chart_layout(
                barmode="group",
                legend=dict(orientation="h", yanchor="bottom", y=1.01),
                height=320,
            ))
            st.plotly_chart(fig2, use_container_width=True)

        with col_r:
            st.subheader("New vs. Lost Members")
            fig3 = go.Figure()
            fig3.add_trace(go.Bar(
                x=mom_plot["Month Label"], y=mom_plot["New Members"],
                name="Added", marker_color=GREEN,
            ))
            fig3.add_trace(go.Bar(
                x=mom_plot["Month Label"], y=mom_plot["Members Lost"],
                name="Lost", marker_color=RED,
            ))
            fig3.update_layout(**_chart_layout(
                barmode="group",
                legend=dict(orientation="h", yanchor="bottom", y=1.01),
                height=320,
            ))
            st.plotly_chart(fig3, use_container_width=True)

        # MoM table
        st.subheader("Full Trend Table")
        disp = mom_plot.drop(columns=["Month"]).rename(columns={"Month Label": "Month"})
        # Reorder so Month is first
        cols = ["Month"] + [c for c in disp.columns if c != "Month"]
        disp = disp[cols]

        st.dataframe(
            disp,
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Growth": st.column_config.NumberColumn("% Growth", format="%.1f%%"),
                "Net Change": st.column_config.NumberColumn("Net Change", format="%+d"),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# DAILY TRACKER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Daily Tracker":
    st.title("Daily Tracker")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    month_options = {
        pd.Timestamp(m + "-01").strftime("%B %Y"): m
        for m in reversed(sorted(months.keys()))
    }
    selected_label = st.selectbox("Select month", list(month_options.keys()))
    selected_m = month_options[selected_label]

    daily_df   = build_daily_df(months[selected_m], selected_m)
    year, mnum = int(selected_m[:4]), int(selected_m[5:7])
    dim        = calendar.monthrange(year, mnum)[1]

    today = dt.date.today()
    days_elapsed = today.day if (today.year == year and today.month == mnum) else dim

    total_pol   = int(daily_df["Policies"].sum())
    total_heads = int(daily_df["Members"].sum())
    days_active = int((daily_df["Policies"] > 0).sum())
    daily_avg   = round(total_pol / max(days_elapsed, 1), 1)
    pct_month   = round(days_active / dim * 100)
    MONTHLY_TARGET = 100

    # KPI row
    st.markdown("<br>", unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(kpi_html("Total Policies Submitted", f"{total_pol:,}"), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_html("Total Heads Sold", f"{total_heads:,}"), unsafe_allow_html=True)
    with k3:
        st.markdown(kpi_html(f"Daily Avg ({days_elapsed} days elapsed)", daily_avg), unsafe_allow_html=True)
    with k4:
        st.markdown(kpi_html(f"Days with Activity ({pct_month}% of {dim})", days_active), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Progress bar toward monthly target
    prog_pct = min(total_pol / MONTHLY_TARGET, 1.0)
    st.subheader(f"Monthly Target Progress — {total_pol} / {MONTHLY_TARGET} policies ({round(prog_pct*100)}%)")
    st.progress(prog_pct)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # Chart + table
    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        st.subheader("Submissions by Day")
        chart_df = daily_df.copy()
        chart_df["Day"] = chart_df["Date"].dt.strftime("%b %d")
        max_pol = max(int(daily_df["Policies"].max()), 1)
        chart_df["Best Day"] = chart_df["Policies"] == max_pol

        fig = px.bar(
            chart_df,
            x="Day",
            y="Policies",
            color="Best Day",
            color_discrete_map={True: GOLD, False: GREEN},
            text="Policies",
        )
        fig.update_traces(textposition="outside", textfont_size=9)
        fig.update_layout(**_chart_layout(
            showlegend=False,
            height=420,
            xaxis=dict(gridcolor="#243664", tickangle=-45, tickfont=dict(size=9)),
        ))
        st.plotly_chart(fig, use_container_width=True)

    with col_table:
        st.subheader("Day-by-Day Breakdown")
        max_hd = max(int(daily_df["Members"].max()), 1)

        tbl = daily_df.copy()
        tbl["Day"] = tbl["Date"].dt.strftime("%b %d")

        # Flag today and best days
        if today.year == year and today.month == mnum:
            today_str = today.strftime("%b %d")
            tbl["Day"] = tbl["Day"].apply(
                lambda d: f"→ {d}" if d == today_str else d
            )
        best_days = set(daily_df.loc[daily_df["Policies"] == max_pol, "Date"].dt.strftime("%b %d"))
        # (star shown via color in chart; table stays clean)

        st.dataframe(
            tbl[["Day", "Policies", "Members"]],
            use_container_width=True,
            hide_index=True,
            height=460,
            column_config={
                "Policies": st.column_config.ProgressColumn(
                    "Policies", min_value=0, max_value=max_pol, format="%d"
                ),
                "Members": st.column_config.ProgressColumn(
                    "Members", min_value=0, max_value=max_hd, format="%d"
                ),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT ROSTER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Book of Business":
    st.title("Book of Business")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # Normalise PendingEffectuation → Effectuated for display/filtering
    all_clients = all_clients.copy()
    all_clients["status_display"] = all_clients["status"].replace(
        {"PendingEffectuation": "Effectuated"}
    )

    # Filters
    f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    with f1:
        status_opts = ["All"] + sorted(all_clients["status_display"].dropna().unique().tolist())
        sel_status  = st.selectbox("Status", status_opts)
    with f2:
        carrier_opts = ["All"] + sorted(all_clients["carrier"].dropna().unique().tolist())
        sel_carrier  = st.selectbox("Carrier", carrier_opts)
    with f3:
        state_opts = ["All"] + sorted(all_clients["state"].dropna().unique().tolist())
        sel_state  = st.selectbox("State", state_opts)
    with f4:
        search = st.text_input("Search by name", placeholder="First or last name…")

    # Apply filters
    df = all_clients.copy()
    if sel_status  != "All": df = df[df["status_display"] == sel_status]
    if sel_carrier != "All": df = df[df["carrier"] == sel_carrier]
    if sel_state   != "All": df = df[df["state"]   == sel_state]
    if search.strip():
        q = search.strip()
        mask = (
            df["first_name"].fillna("").str.contains(q, case=False) |
            df["last_name"].fillna("").str.contains(q, case=False)
        )
        df = df[mask]

    # Status breakdown for filtered set
    m1, m2, m3, m4 = st.columns(4)
    active_sts = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    active_ct  = int(df["status"].isin(active_sts).sum())
    inactive_ct = int((~df["status"].isin(active_sts)).sum())
    total_mem  = int(df.loc[df["status"].isin(active_sts), "applicant_count"].sum())
    with m1:
        st.metric("Total Policies", f"{len(df):,}")
    with m2:
        st.metric("Active Policies", f"{active_ct:,}")
    with m3:
        st.metric("Inactive Policies", f"{inactive_ct:,}")
    with m4:
        st.metric("Active Members", f"{total_mem:,}")

    # Duplicate detection
    dup_mask = all_clients.duplicated(subset=["first_name", "last_name"], keep=False)
    dups = all_clients[dup_mask][["first_name", "last_name", "carrier", "state", "status", "effective_date"]].copy()
    dups = dups.sort_values(["last_name", "first_name"])
    if not dups.empty:
        st.warning(f"⚠️ {len(dups)} duplicate client names detected ({dups.groupby(['first_name','last_name']).ngroups} unique names appear more than once)")
        with st.expander("View duplicates"):
            dups.columns = [c.replace("_", " ").title() for c in dups.columns]
            st.dataframe(dups, use_container_width=True, hide_index=True,
                column_config={"Effective Date": st.column_config.DateColumn("Effective Date", format="MMM D, YYYY")})

    st.markdown("<br>", unsafe_allow_html=True)

    # Table
    display_cols = [
        "first_name", "last_name", "carrier", "state", "status_display",
        "effective_date", "term_date", "months_on_book", "applicant_count", "net_premium",
    ]
    disp = df[[c for c in display_cols if c in df.columns]].copy()
    disp = disp.rename(columns={"status_display": "status"})
    disp.columns = [c.replace("_", " ").title() for c in disp.columns]

    st.dataframe(
        disp,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "Effective Date":  st.column_config.DateColumn("Effective Date", format="MMM D, YYYY"),
            "Term Date":       st.column_config.DateColumn("Term Date",      format="MMM D, YYYY"),
            "Net Premium":     st.column_config.NumberColumn("Net Premium", format="$%.2f"),
            "Applicant Count": st.column_config.NumberColumn("Members"),
            "Months On Book":  st.column_config.NumberColumn("Mo. on Book"),
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# GOALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Goals":
    TODAY            = dt.date.today()
    COMMISSION_PMPM  = 23
    MAX_TENURE_MONTHS = 60
    _ACTIVE_STS      = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    _CHURN_STS       = {"Cancelled", "Terminated"}

    # ── Editable goal inputs ──────────────────────────────────────────────────
    st.title("Goals")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown("#### Set your goal")
    _prev_goal_members = st.session_state.get("goal_members", 2000)
    _prev_goal_date     = st.session_state.get("goal_date", dt.date(2027, 2, 1))

    gi1, gi2 = st.columns(2)
    with gi1:
        GOAL = st.number_input("Member goal", min_value=1, value=_prev_goal_members, step=50)
        st.session_state["goal_members"] = GOAL
    with gi2:
        GOAL_DATE = st.date_input("Target date", value=_prev_goal_date)
        st.session_state["goal_date"] = GOAL_DATE

    if (GOAL != _prev_goal_members or GOAL_DATE != _prev_goal_date) and _running_in_cloud():
        _persist_settings(goal_members=GOAL, goal_date=GOAL_DATE.isoformat())

    # Policy equivalent
    _active_mask_g = all_clients["status"].isin(_ACTIVE_STS) if "status" in all_clients.columns else pd.Series(False, index=all_clients.index)
    _active_pol = int(_active_mask_g.sum())
    _active_mem = int(all_clients.loc[_active_mask_g, "applicant_count"].sum()) if "applicant_count" in all_clients.columns else 0
    _avg_hh = _active_mem / max(_active_pol, 1)
    _goal_policies = round(GOAL / max(_avg_hh, 1))
    st.markdown(
        f'<p style="color:#8aacd6;font-size:0.95rem;margin-top:-6px;">'
        f'<b style="color:#e8edf5">{GOAL:,} members</b> ≈ '
        f'<b style="color:#4285F4">{_goal_policies:,} policies</b> '
        f'(based on your avg household size of {_avg_hh:.2f})</p>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Core member counts ────────────────────────────────────────────────────
    _active_mask = _active_mask_g
    _churn_mask  = all_clients["status"].isin(_CHURN_STS)  if "status" in all_clients.columns else pd.Series(False, index=all_clients.index)
    current      = _active_mem
    gap          = max(GOAL - current, 0)
    pct_done     = min(current / GOAL * 100, 100)

    # ── LTV — trailing 3-month churn rate (more accurate than all-time cumulative) ──
    # Uses avg members lost / avg active members over the last 3 snapshot months.
    # Falls back to all-time if MoM data is unavailable.
    if not mom_df.empty and "Members Lost" in mom_df.columns and "Total Members" in mom_df.columns:
        _trailing = mom_df.tail(3)
        _avg_lost   = _trailing["Members Lost"].mean()
        _avg_active = _trailing["Total Members"].mean()
        monthly_churn_rate = _avg_lost / max(_avg_active, 1)
        _churn_label = "trailing 3-mo avg"
    else:
        _total_ever  = int(_active_mask.sum()) + int(_churn_mask.sum())
        _total_churned = int(_churn_mask.sum())
        monthly_churn_rate = (_total_churned / max(_total_ever, 1)) / max(len(months), 1)
        _churn_label = "all-time avg"
    implied_tenure_mo  = min(1 / monthly_churn_rate if monthly_churn_rate > 0 else MAX_TENURE_MONTHS, MAX_TENURE_MONTHS)
    ltv_per_member     = round(COMMISSION_PMPM * implied_tenure_mo)

    # ── Revenue figures ───────────────────────────────────────────────────────
    current_mrr       = current * COMMISSION_PMPM
    current_arr       = current_mrr * 12
    current_book_ltv  = current * ltv_per_member
    goal_mrr          = GOAL * COMMISSION_PMPM
    goal_arr          = goal_mrr * 12
    goal_book_ltv     = GOAL * ltv_per_member
    revenue_gap_ltv   = goal_book_ltv - current_book_ltv
    revenue_gap_arr   = goal_arr - current_arr

    # ── Pace numbers ─────────────────────────────────────────────────────────
    days_left       = (GOAL_DATE - TODAY).days
    months_left     = max(round(days_left / 30.44, 1), 0.1)
    weeks_left      = max(round(days_left / 7, 1), 0.1)
    needed_per_day  = round(gap / days_left, 2) if days_left > 0 else 0
    needed_per_week = round(gap / weeks_left, 1) if weeks_left > 0 else 0
    needed_per_mo   = round(gap / months_left, 1) if months_left > 0 else 0

    # Net new members per month from MoM history (last 3 months)
    if not mom_df.empty and "New Members" in mom_df.columns and "Members Lost" in mom_df.columns:
        recent_mo_growth = (mom_df["New Members"] - mom_df["Members Lost"]).tail(3).mean()
    else:
        recent_mo_growth = 0.0
    projected_at_goal_date   = round(current + recent_mo_growth * months_left)
    projected_arr_at_goal    = projected_at_goal_date * COMMISSION_PMPM * 12
    on_track = projected_at_goal_date >= GOAL

    # ── Helper ────────────────────────────────────────────────────────────────
    def _goal_kpi(label: str, value, sub: str, color: str = ""):
        return (
            f'<div class="goal-kpi-box">'
            f'<div class="goal-kpi-value {color}">{value}</div>'
            f'<div class="goal-kpi-label">{label}</div>'
            f'<div class="goal-kpi-sub">{sub}</div>'
            f'</div>'
        )

    # ══ PAGE ══════════════════════════════════════════════════════════════════
    st.markdown(
        f'<p style="color:#8aacd6;font-size:0.95rem;">'
        f'LTV: {_churn_label} churn ({monthly_churn_rate*100:.2f}%/mo → '
        f'{implied_tenure_mo:.0f}-mo tenure → <b style="color:#2ecc71">'
        f'${ltv_per_member:,}/member</b>)</p>',
        unsafe_allow_html=True,
    )

    # ── Dual progress bars: Members + Revenue ────────────────────────────────
    rev_pct   = min(current_arr / goal_arr * 100, 100)
    bar_color = "#2ecc71" if pct_done >= 75 else ("#f39c12" if pct_done >= 40 else "#4285F4")
    rev_color = "#2ecc71" if rev_pct >= 75 else ("#f39c12" if rev_pct >= 40 else "#4285F4")

    st.markdown(
        f"""
        <div style="margin-bottom:4px;display:flex;justify-content:space-between;
                    font-size:0.85rem;color:#8aacd6;">
          <span>Members &nbsp;<b style="color:#fff">{current:,}</b></span>
          <span><b style="color:#fff">{pct_done:.1f}%</b> of {GOAL:,}</span>
          <span><b style="color:#8aacd6">{gap:,} to go &nbsp;·&nbsp; {days_left:,} days left</b></span>
        </div>
        <div class="progress-wrap" style="margin-bottom:14px;">
          <div class="progress-bar" style="width:{pct_done:.1f}%;background:{bar_color};"></div>
        </div>
        <div style="margin-bottom:4px;display:flex;justify-content:space-between;
                    font-size:0.85rem;color:#8aacd6;">
          <span>Annual Revenue &nbsp;<b style="color:#fff">${current_arr:,.0f}</b></span>
          <span><b style="color:#fff">{rev_pct:.1f}%</b> of ${goal_arr:,.0f}</span>
          <span><b style="color:#8aacd6">${revenue_gap_arr:,.0f} ARR to go</b></span>
        </div>
        <div class="progress-wrap" style="margin-bottom:28px;">
          <div class="progress-bar" style="width:{rev_pct:.1f}%;background:{rev_color};"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Revenue snapshot ──────────────────────────────────────────────────────
    st.subheader("Revenue — where you are now")
    r1, r2, r3, r4 = st.columns(4)
    with r1:
        st.markdown(_goal_kpi("Monthly Recurring Revenue", f"${current_mrr:,.0f}", f"{current:,} members × ${COMMISSION_PMPM}/mo"), unsafe_allow_html=True)
    with r2:
        st.markdown(_goal_kpi("Annual Run Rate", f"${current_arr:,.0f}", "MRR × 12 months"), unsafe_allow_html=True)
    with r3:
        st.markdown(_goal_kpi("LTV per Member", f"${ltv_per_member:,}", f"${COMMISSION_PMPM}/mo × {implied_tenure_mo:.0f}-mo tenure"), unsafe_allow_html=True)
    with r4:
        st.markdown(_goal_kpi("Total Book LTV", f"${current_book_ltv:,.0f}", f"{current:,} members × ${ltv_per_member:,}", "green"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Pace needed ───────────────────────────────────────────────────────────
    st.subheader("Pace needed to hit goal")
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(_goal_kpi("New members / day", f"+{needed_per_day}", f"{days_left:,} days remaining"), unsafe_allow_html=True)
    with k2:
        st.markdown(_goal_kpi("New members / week", f"+{needed_per_week:.0f}", f"{weeks_left:.0f} weeks remaining"), unsafe_allow_html=True)
    with k3:
        st.markdown(_goal_kpi("New members / month", f"+{needed_per_mo:.0f}", f"{months_left:.0f} months remaining"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── At your current pace ──────────────────────────────────────────────────
    st.subheader("At your current pace")
    p1, p2, p3 = st.columns(3)
    proj_color = "green" if on_track else "red"
    proj_label = "On track ✓" if on_track else "Behind pace"
    shortfall  = GOAL - projected_at_goal_date
    with p1:
        st.markdown(_goal_kpi("Avg net new members / mo (last 3)", f"+{recent_mo_growth:.0f}", "based on recent history"), unsafe_allow_html=True)
    with p2:
        st.markdown(_goal_kpi("Projected members by goal date", f"{projected_at_goal_date:,}", proj_label, proj_color), unsafe_allow_html=True)
    with p3:
        if shortfall > 0:
            st.markdown(_goal_kpi("Projected ARR by goal date", f"${projected_arr_at_goal:,.0f}", f"${goal_arr - projected_arr_at_goal:,.0f} short of goal ARR", "red"), unsafe_allow_html=True)
        else:
            st.markdown(_goal_kpi("Projected ARR by goal date", f"${projected_arr_at_goal:,.0f}", f"Goal ARR exceeded ✓", "green"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Growth chart ──────────────────────────────────────────────────────────
    st.subheader("Growth vs. required pace")

    if not mom_df.empty and "Month" in mom_df.columns and "Total Members" in mom_df.columns:
        hist = mom_df[["Month", "Total Members"]].dropna().copy()
        hist = hist.rename(columns={"Month": "month", "Total Members": "active"})
        hist["month"] = pd.to_datetime(hist["month"])
        hist = hist.sort_values("month")
        hist["arr"] = hist["active"] * COMMISSION_PMPM * 12

        start_date  = hist["month"].iloc[0]
        start_count = hist["active"].iloc[0]
        goal_ts     = pd.Timestamp(GOAL_DATE)
        pace_months = pd.date_range(start=start_date, end=goal_ts, freq="MS")
        total_span  = (goal_ts - start_date).days
        pace_vals   = [start_count + (GOAL - start_count) * (t - start_date).days / total_span for t in pace_months]
        pace_df     = pd.DataFrame({"month": pace_months, "required": pace_vals, "required_arr": [v * COMMISSION_PMPM * 12 for v in pace_vals]})

        tab_members, tab_revenue = st.tabs(["Members", "Annual Revenue"])

        with tab_members:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=hist["month"], y=hist["active"], mode="lines+markers", name="Actual", line=dict(color=BLUE, width=3), marker=dict(size=7)))
            fig.add_trace(go.Scatter(x=pace_df["month"], y=pace_df["required"], mode="lines", name="Required pace", line=dict(color=GOLD, width=2, dash="dash")))
            fig.add_hline(y=GOAL, line_color=GREEN, line_dash="dot", line_width=1.5, annotation_text=f"Goal: {GOAL:,}", annotation_position="top left", annotation_font_color=GREEN)
            fig.add_vline(x=TODAY.isoformat(), line_color="#8aacd6", line_dash="dot", line_width=1, annotation_text="Today", annotation_position="top right", annotation_font_color="#8aacd6")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#e8edf5", xaxis=dict(showgrid=False, title=""), yaxis=dict(showgrid=True, gridcolor="#1a2744", title="Active members"), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), margin=dict(l=0, r=0, t=30, b=0), height=360)
            st.plotly_chart(fig, use_container_width=True)

        with tab_revenue:
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=hist["month"], y=hist["arr"], mode="lines+markers", name="Actual ARR", line=dict(color=GREEN, width=3), marker=dict(size=7)))
            fig2.add_trace(go.Scatter(x=pace_df["month"], y=pace_df["required_arr"], mode="lines", name="Required pace", line=dict(color=GOLD, width=2, dash="dash")))
            fig2.add_hline(y=goal_arr, line_color=GREEN, line_dash="dot", line_width=1.5, annotation_text=f"Goal ARR: ${goal_arr:,.0f}", annotation_position="top left", annotation_font_color=GREEN)
            fig2.add_vline(x=TODAY.isoformat(), line_color="#8aacd6", line_dash="dot", line_width=1, annotation_text="Today", annotation_position="top right", annotation_font_color="#8aacd6")
            fig2.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font_color="#e8edf5", xaxis=dict(showgrid=False, title=""), yaxis=dict(showgrid=True, gridcolor="#1a2744", title="Annual Revenue ($)", tickprefix="$", tickformat=",.0f"), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1), margin=dict(l=0, r=0, t=30, b=0), height=360)
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("Not enough history to plot growth chart.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Weekly callout ────────────────────────────────────────────────────────
    _week_policies = round(needed_per_week / max(_avg_hh, 1))
    st.markdown(
        f'<div style="background:#1a2744;border-left:4px solid #f39c12;padding:14px 18px;'
        f'border-radius:6px;margin-bottom:20px;">'
        f'<div style="font-size:0.85rem;color:#8aacd6;text-transform:uppercase;letter-spacing:0.05em;">This week\'s target</div>'
        f'<div style="font-size:1.8rem;font-weight:700;color:#f39c12;">+{needed_per_week:.0f} members</div>'
        f'<div style="font-size:0.9rem;color:#8aacd6;">≈ {_week_policies} policies &nbsp;·&nbsp; {weeks_left:.0f} weeks remaining to reach {GOAL:,} members by {GOAL_DATE.strftime("%b %d, %Y")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Monthly targets table ─────────────────────────────────────────────────
    st.subheader("Monthly targets")
    breakdown_rows = []
    ref_date      = TODAY.replace(day=1)
    running_members = current
    for i in range(int(months_left) + 2):
        mo          = ref_date + pd.DateOffset(months=i)
        mo_label    = mo.strftime("%B %Y")
        add_target  = round(needed_per_mo)
        running_members = min(running_members + add_target, GOAL)
        breakdown_rows.append({
            "Month":              mo_label,
            "Members to add":     add_target,
            "Policies to add":    round(add_target / max(_avg_hh, 1)),
            "Running members":    running_members,
            "Running policies":   round(running_members / max(_avg_hh, 1)),
            "MRR at target":      f"${running_members * COMMISSION_PMPM:,.0f}",
            "ARR at target":      f"${running_members * COMMISSION_PMPM * 12:,.0f}",
        })
        if running_members >= GOAL:
            break

    breakdown_df = pd.DataFrame(breakdown_rows)
    st.dataframe(breakdown_df, use_container_width=True, hide_index=True, height=340)


# ══════════════════════════════════════════════════════════════════════════════
# RE-ENGAGE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Re-Engage":
    import json
    from tracker.diff import compute_diff, _dedup_month

    WINBACK_FILE = Path("data/winbacks.json")
    WINBACK_FILE.parent.mkdir(exist_ok=True)

    def _load_winbacks() -> dict:
        if WINBACK_FILE.exists():
            try:
                return json.loads(WINBACK_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_winbacks(data: dict):
        WINBACK_FILE.write_text(json.dumps(data, indent=2))

    winbacks = _load_winbacks()  # {name_key: {name, carrier, state, winback_month, detected_date}}

    # ── Auto-detect win-backs from snapshot history ───────────────────────────
    # A win-back = someone who appears as NEW in month M but whose first_seen
    # predates month M-1 (i.e. they were with us before, went missing, came back).
    sorted_month_keys = sorted(months.keys())
    if len(sorted_month_keys) >= 2:
        latest_m = sorted_month_keys[-1]
        prior_m  = sorted_month_keys[-2]
        try:
            _diff = compute_diff(months[prior_m], months[latest_m])
            _new_keys = set(_diff["new"]["name_key"].dropna().tolist()) if "name_key" in _diff["new"].columns else set()
            _new_names = set(_diff["new"].apply(
                lambda r: (str(r.get("first_name","")).strip().lower() + " " + str(r.get("last_name","")).strip().lower()).strip(),
                axis=1
            ).tolist())

            # Cross-reference with all_clients to find those who existed before prior_m
            if "first_seen" in all_clients.columns and "name_key" in all_clients.columns:
                _wb_candidates = all_clients[
                    all_clients["name_key"].isin(_new_keys) &
                    (all_clients["first_seen"] < prior_m)
                ]
                for _, row in _wb_candidates.iterrows():
                    nk = str(row.get("name_key", ""))
                    if nk and nk not in winbacks:
                        fn = str(row.get("first_name","")).strip()
                        ln = str(row.get("last_name","")).strip()
                        winbacks[nk] = {
                            "name":          f"{fn} {ln}".strip(),
                            "carrier":       str(row.get("carrier","")),
                            "state":         str(row.get("state","")),
                            "winback_month": latest_m,
                            "detected_date": dt.date.today().isoformat(),
                            "members":       int(row.get("applicant_count", 1)) if pd.notna(row.get("applicant_count")) else 1,
                        }
                _save_winbacks(winbacks)
        except Exception:
            pass

    st.title("Re-Engage")
    st.caption("Clients who cancelled or went missing — sorted by most recently lost. Reach out while the relationship is fresh.")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    _CHURN_STS = {"Cancelled", "Terminated"}
    today_ts   = pd.Timestamp(dt.date.today())

    if "status" in all_clients.columns:
        lost_df = all_clients[all_clients["status"].isin(_CHURN_STS)].copy()
    else:
        lost_df = pd.DataFrame()

    tab_outreach, tab_saved = st.tabs([
        f"Need Outreach ({max(len(lost_df) - len(winbacks), 0)})",
        f"Won Back ✅ ({len(winbacks)})"
    ])

    if lost_df.empty:
        with tab_outreach:
            st.info("No cancelled or terminated clients found.")
    else:
        lost_df["term_date"]       = pd.to_datetime(lost_df.get("term_date"), errors="coerce")
        lost_df["days_since_lost"] = (today_ts - lost_df["term_date"]).dt.days.clip(lower=0)
        lost_df["months_on_book"]  = pd.to_numeric(lost_df.get("months_on_book"), errors="coerce")

        def _nk(row):
            if "name_key" in row and pd.notna(row.get("name_key")):
                return str(row["name_key"])
            return (str(row.get("first_name","")).strip().lower() + " " + str(row.get("last_name","")).strip().lower()).strip()

        def _dname(row):
            if "first_name" in row:
                return (str(row.get("first_name","")) + " " + str(row.get("last_name",""))).strip()
            return str(row.get("client_name",""))

        lost_df["_nk"]   = lost_df.apply(_nk, axis=1)
        lost_df["_name"] = lost_df.apply(_dname, axis=1)

        def _urgency(days):
            if pd.isna(days): return "Unknown"
            if days <= 30:    return "🔴 <30 days"
            if days <= 60:    return "🟡 30-60 days"
            if days <= 90:    return "🟠 60-90 days"
            return "⚪ 90+ days"

        lost_df["Urgency"] = lost_df["days_since_lost"].apply(_urgency)
        outreach_df = lost_df[~lost_df["_nk"].isin(winbacks.keys())].copy()

        # ── TAB 1: Need Outreach ──────────────────────────────────────────────
        with tab_outreach:
            last_30 = int((outreach_df["days_since_lost"] <= 30).sum())
            last_60 = int((outreach_df["days_since_lost"] <= 60).sum())
            last_90 = int((outreach_df["days_since_lost"] <= 90).sum())

            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.markdown(kpi_html("Need Outreach", f"{len(outreach_df):,}", sub="Not yet won back"), unsafe_allow_html=True)
            with k2:
                st.markdown(kpi_html("Lost < 30 Days", f"{last_30:,}", sub="Hottest leads"), unsafe_allow_html=True)
            with k3:
                st.markdown(kpi_html("Lost < 60 Days", f"{last_60:,}", sub="Still warm"), unsafe_allow_html=True)
            with k4:
                st.markdown(kpi_html("Won Back", f"{len(winbacks):,}", sub="Auto-detected saves"), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            f1, f2, f3 = st.columns(3)
            with f1:
                window_opts  = {"Last 30 days": 30, "Last 60 days": 60, "Last 90 days": 90, "All time": 99999}
                window_label = st.selectbox("Show lost in", list(window_opts.keys()), index=2)
                window_days  = window_opts[window_label]
            with f2:
                carriers = ["All"] + sorted(outreach_df["carrier"].dropna().unique().tolist()) if "carrier" in outreach_df.columns else ["All"]
                carrier_filter = st.selectbox("Carrier", carriers)
            with f3:
                states = ["All"] + sorted(outreach_df["state"].dropna().unique().tolist()) if "state" in outreach_df.columns else ["All"]
                state_filter = st.selectbox("State", states)

            view = outreach_df[outreach_df["days_since_lost"] <= window_days].copy()
            if carrier_filter != "All" and "carrier" in view.columns:
                view = view[view["carrier"] == carrier_filter]
            if state_filter != "All" and "state" in view.columns:
                view = view[view["state"] == state_filter]
            view = view.sort_values("days_since_lost", ascending=True, na_position="last").reset_index(drop=True)

            st.caption(f"Showing **{len(view)}** clients · {window_label.lower()}"
                       + (f" · {carrier_filter}" if carrier_filter != "All" else "")
                       + (f" · {state_filter}" if state_filter != "All" else ""))

            if view.empty:
                st.info("No clients match the current filters.")
            else:
                disp = pd.DataFrame()
                disp["Name"]           = view["_name"]
                disp["Carrier"]        = view["carrier"] if "carrier" in view.columns else ""
                disp["State"]          = view["state"]   if "state"   in view.columns else ""
                disp["Term Date"]      = view["term_date"].dt.strftime("%b %d, %Y").where(view["term_date"].notna(), "Unknown")
                disp["Days Since Lost"]= view["days_since_lost"].fillna(0).astype(int)
                disp["Mo. on Book"]    = view["months_on_book"].fillna("?").astype(str).str.replace(r"\.0$", "", regex=True)
                disp["Members"]        = view["applicant_count"].fillna(1).astype(int) if "applicant_count" in view.columns else 1
                disp["Urgency"]        = view["Urgency"]

                def _row_color(row):
                    u = str(row.get("Urgency",""))
                    if "🔴" in u: return ["background-color: rgba(231,76,60,0.15)"] * len(row)
                    if "🟡" in u: return ["background-color: rgba(243,156,18,0.15)"] * len(row)
                    if "🟠" in u: return ["background-color: rgba(230,126,34,0.10)"] * len(row)
                    return [""] * len(row)

                st.dataframe(disp.style.apply(_row_color, axis=1), use_container_width=True, hide_index=True, height=520)

        # ── TAB 2: Won Back ───────────────────────────────────────────────────
        with tab_saved:
            if not winbacks:
                st.info("No win-backs detected yet. When you upload a new CSV and a previously lost client reappears as active, they'll automatically show up here.")
            else:
                st.caption("Automatically detected when a lost client reappears as active in a new upload.")
                st.markdown("<br>", unsafe_allow_html=True)

                wb_rows = []
                for nk, info in sorted(winbacks.items(), key=lambda x: x[1].get("winback_month",""), reverse=True):
                    wb_rows.append({
                        "Name":           info.get("name", nk),
                        "Carrier":        info.get("carrier", ""),
                        "State":          info.get("state", ""),
                        "Won Back Month": info.get("winback_month", ""),
                        "Members":        info.get("members", 1),
                    })

                wb_df = pd.DataFrame(wb_rows)
                st.dataframe(wb_df, use_container_width=True, hide_index=True, height=min(80 + len(wb_rows) * 35, 480))


# ══════════════════════════════════════════════════════════════════════════════
# AEP TRACKER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "AEP Tracker":
    import datetime as _dt

    _aep_year     = _dt.date.today().year + 1
    _aep_tab      = _aep_tab_name(_aep_year)

    st.title(f"🔄 Open Enrollment Tracker — {_aep_year}")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    if not _running_in_cloud():
        st.info("AEP Tracker is only available in the deployed app. Run `track aep-init` to populate the sheet, then open the live site.")
    else:
        # Load data
        if "aep_df" not in st.session_state or st.session_state.get("aep_tab") != _aep_tab:
            _raw = _read_aep_tab(_aep_tab)
            st.session_state.aep_df  = _raw.copy()
            st.session_state.aep_tab = _aep_tab

        aep_df = st.session_state.aep_df

        if aep_df.empty:
            st.warning(f"No AEP Tracker data found for {_aep_year}.")
            st.markdown(
                "To set it up, run this command on your Mac:\n"
                "```\ntrack aep-init\n```\n"
                "This will create the **AEP Tracker** tab in your Google Sheet with all active clients ready to track."
            )
        else:
            # ── Progress stats ─────────────────────────────────────────────
            _total     = len(aep_df)
            _counts    = aep_df["Status"].value_counts()
            _not_start = int(_counts.get("Not Started", 0))
            _contacted = int(_counts.get("Contacted",   0))
            _renewed   = int(_counts.get("Renewed",     0))
            _lost      = int(_counts.get("Lost",        0))
            _done_pct  = round((_renewed + _lost) / max(_total, 1) * 100)

            c1, c2, c3, c4 = st.columns(4)
            _kpi_style = f'background:{NAVY};border-radius:10px;padding:14px 18px;text-align:center;'
            c1.markdown(f'<div style="{_kpi_style}"><div style="font-size:1.6rem;font-weight:700;color:{GREEN}">{_renewed}</div><div style="font-size:0.75rem;color:#aaa">Renewed</div></div>', unsafe_allow_html=True)
            c2.markdown(f'<div style="{_kpi_style}"><div style="font-size:1.6rem;font-weight:700;color:{BLUE}">{_contacted}</div><div style="font-size:0.75rem;color:#aaa">Contacted</div></div>', unsafe_allow_html=True)
            c3.markdown(f'<div style="{_kpi_style}"><div style="font-size:1.6rem;font-weight:700;color:{GOLD}">{_not_start}</div><div style="font-size:0.75rem;color:#aaa">Not Started</div></div>', unsafe_allow_html=True)
            c4.markdown(f'<div style="{_kpi_style}"><div style="font-size:1.6rem;font-weight:700;color:{RED}">{_lost}</div><div style="font-size:0.75rem;color:#aaa">Lost</div></div>', unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Progress bar
            st.markdown(f"**Overall progress: {_renewed + _contacted} of {_total} clients touched ({_done_pct}% fully resolved)**")
            _prog_val = (_renewed + _lost) / max(_total, 1)
            st.progress(min(_prog_val, 1.0))
            st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

            # ── Filters ────────────────────────────────────────────────────
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                _state_opts = ["All States"] + sorted(aep_df["State"].dropna().unique().tolist())
                _f_state    = st.selectbox("State", _state_opts, key="aep_f_state")
            with fc2:
                _carrier_opts = ["All Carriers"] + sorted(aep_df["Carrier"].dropna().unique().tolist())
                _f_carrier    = st.selectbox("Carrier", _carrier_opts, key="aep_f_carrier")
            with fc3:
                _status_opts = ["All Statuses"] + _AEP_STATUSES
                _f_status    = st.selectbox("Status", _status_opts, key="aep_f_status")

            _view = aep_df.copy()
            if _f_state   != "All States":    _view = _view[_view["State"]   == _f_state]
            if _f_carrier != "All Carriers":  _view = _view[_view["Carrier"] == _f_carrier]
            if _f_status  != "All Statuses":  _view = _view[_view["Status"]  == _f_status]

            st.caption(f"Showing {len(_view)} of {_total} clients")
            st.markdown("<br>", unsafe_allow_html=True)

            # ── Editable table ─────────────────────────────────────────────
            _view_display = _view.reset_index(drop=True).copy()
            _edited = st.data_editor(
                _view_display,
                use_container_width=True,
                hide_index=True,
                height=min(80 + len(_view_display) * 35, 600),
                column_config={
                    "First Name":      st.column_config.TextColumn("First Name",      disabled=True),
                    "Last Name":       st.column_config.TextColumn("Last Name",       disabled=True),
                    "State":           st.column_config.TextColumn("State",           disabled=True, width="small"),
                    "Carrier":         st.column_config.TextColumn("Carrier",         disabled=True),
                    "Members":         st.column_config.NumberColumn("Members",       disabled=True, width="small"),
                    "Monthly Premium": st.column_config.NumberColumn("Monthly Premium", disabled=True, format="$%.2f", width="medium"),
                    "Effective Date":  st.column_config.TextColumn("Effective Date",  disabled=True),
                    "Status": st.column_config.SelectboxColumn(
                        "Status", options=_AEP_STATUSES, required=True, width="medium",
                    ),
                    "Notes": st.column_config.TextColumn("Notes", width="large"),
                },
                key="aep_editor",
            )

            st.markdown("<br>", unsafe_allow_html=True)
            _sc1, _sc2 = st.columns([1, 4])
            with _sc1:
                if st.button("💾 Save changes", use_container_width=True, type="primary"):
                    # Map edits back positionally — name-based keys fail on duplicate names.
                    # Re-apply the same filters to find which original rows are in the view.
                    _merged = st.session_state.aep_df.copy()
                    _mask = pd.Series([True] * len(_merged), index=_merged.index)
                    if _f_state   != "All States":   _mask &= _merged["State"]   == _f_state
                    if _f_carrier != "All Carriers": _mask &= _merged["Carrier"] == _f_carrier
                    if _f_status  != "All Statuses": _mask &= _merged["Status"]  == _f_status
                    _orig_indices = _merged.index[_mask].tolist()
                    for _pos, _orig_idx in enumerate(_orig_indices):
                        if _pos < len(_edited):
                            _merged.at[_orig_idx, "Status"] = _edited.iloc[_pos]["Status"]
                            _merged.at[_orig_idx, "Notes"]  = _edited.iloc[_pos]["Notes"]
                    if _save_aep_tab(_aep_tab, _merged):
                        st.session_state.aep_df = _merged
                        st.success("Saved!")
                    else:
                        st.error("Save failed — check connection.")


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Settings":
    st.title("Settings")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown("Toggle the carriers you are appointed with in each state. Changes apply immediately to the dashboard.")
    st.markdown("<br>", unsafe_allow_html=True)

    sel = st.session_state.settings_appointments
    total_on  = sum(v for state_d in sel.values() for v in state_d.values())
    total_all = sum(len(d) for d in sel.values())
    st.caption(f"{total_on} of {total_all} carrier/state combos active")
    st.markdown("<br>", unsafe_allow_html=True)

    for state in sorted(sel.keys()):
        carriers = sel[state]
        active_count = sum(carriers.values())
        with st.expander(f"**{state}** — {active_count}/{len(carriers)} carriers active"):
            cols = st.columns(2)
            for i, carrier in enumerate(sorted(carriers.keys())):
                with cols[i % 2]:
                    new_val = st.checkbox(
                        carrier,
                        value=carriers[carrier],
                        key=f"appt_{state}_{carrier}"
                    )
                    st.session_state.settings_appointments[state][carrier] = new_val

    st.markdown("<br>", unsafe_allow_html=True)
    if not _running_in_cloud():
        st.caption("Settings persistence is only available on the deployed (cloud) app.")
    elif st.button("💾 Save Settings", type="primary"):
        if _persist_settings(appointments=st.session_state.settings_appointments):
            st.success("Saved — these settings will now load on any device.")
