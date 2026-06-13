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
    initial_sidebar_state="expanded",
)

# ── Color palette ─────────────────────────────────────────────────────────────
NAVY  = "#1a2744"
LNAV  = "#243664"
BLUE  = "#4285F4"
GREEN = "#2ecc71"
RED   = "#e74c3c"
GOLD  = "#f39c12"

# ── CSS: custom KPI boxes ─────────────────────────────────────────────────────
st.markdown("""
<style>
  .kpi-box {
    background: #1a2744;
    border-radius: 10px;
    padding: 22px 16px 18px;
    text-align: center;
    border: 1px solid #243664;
  }
  .kpi-value {
    font-size: 2.2rem;
    font-weight: 700;
    color: #ffffff;
    line-height: 1.1;
  }
  .kpi-label {
    font-size: 0.72rem;
    color: #8aacd6;
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .section-divider { margin: 8px 0 20px; border-top: 1px solid #243664; }
  .goal-kpi-box {
    background: #1a2744;
    border-radius: 12px;
    padding: 24px 16px 20px;
    text-align: center;
    border: 1px solid #243664;
    position: relative;
  }
  .goal-kpi-value {
    font-size: 2.6rem;
    font-weight: 800;
    color: #4285F4;
    line-height: 1.1;
  }
  .goal-kpi-value.green  { color: #2ecc71; }
  .goal-kpi-value.gold   { color: #f39c12; }
  .goal-kpi-value.red    { color: #e74c3c; }
  .goal-kpi-label {
    font-size: 0.72rem;
    color: #8aacd6;
    margin-top: 6px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .goal-kpi-sub {
    font-size: 0.82rem;
    color: #8aacd6;
    margin-top: 4px;
  }
  .progress-wrap {
    background: #0d1321;
    border-radius: 999px;
    height: 22px;
    overflow: hidden;
    margin: 10px 0 6px;
  }
  .progress-bar {
    height: 100%;
    border-radius: 999px;
    background: linear-gradient(90deg, #4285F4, #2ecc71);
    transition: width 0.6s ease;
  }
</style>
""", unsafe_allow_html=True)


def kpi_html(label: str, value) -> str:
    return (
        f'<div class="kpi-box">'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-label">{label}</div>'
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

    # Compute dashboard data from all_clients (same logic as dashboard.py)
    _ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    active_df = all_clients[all_clients["status"].isin(_ACTIVE)] if "status" in all_clients.columns else pd.DataFrame()

    mom_df = _build_mom_from_all_clients(all_clients)

    if not mom_df.empty and "New Policies" in mom_df.columns:
        avg_added          = round(mom_df["New Policies"].mean(), 1)
        avg_lost           = round(mom_df["Policies Lost"].mean(), 1)
        avg_members_added  = round(mom_df["New Members"].mean(), 1)
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

    dashboard_data = {"kpis": kpis, "carrier_df": carrier_df, "state_df": state_df, "mom_df": mom_df}

    # Read daily tracker tabs — return as pre-built DataFrames keyed by month string
    daily_months: dict = {}
    for tab in ["Daily Tracker - May 2026", "Daily Tracker - Jun 2026"]:
        m_str = "2026-05" if "May" in tab else "2026-06"
        ddf   = _read_daily_tab_from_sheet(spreadsheet, tab)
        if not ddf.empty:
            daily_months[m_str] = ddf

    return daily_months, all_clients, dashboard_data


def _running_in_cloud() -> bool:
    """True when Streamlit secrets contain GCP credentials (i.e. cloud deployment)."""
    try:
        return "gcp_service_account" in st.secrets
    except Exception:
        return False


@st.cache_data(ttl=300)
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
        ["Overview", "Month-over-Month", "Daily Tracker", "Client Roster", "Goals"],
        label_visibility="collapsed",
    )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.caption(f"📅 Latest snapshot: **{latest_label}**")
    st.caption(f"👥 {len(all_clients):,} total clients tracked")

    if st.button("🔄 Refresh data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
if page == "Overview":
    st.title("Overview")
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
        st.markdown(kpi_html("Avg Policies Added / Month", kpis["Avg Policies Added/Month"]), unsafe_allow_html=True)
    with c5:
        st.markdown(kpi_html("Avg Policies Lost / Month", kpis["Avg Policies Lost/Month"]), unsafe_allow_html=True)
    with c6:
        try:
            net = round(float(kpis["Avg Policies Added/Month"]) - float(kpis["Avg Policies Lost/Month"]), 1)
            net_str = f"+{net}" if net >= 0 else str(net)
        except Exception:
            net_str = "N/A"
        st.markdown(kpi_html("Avg Net Growth / Month", net_str), unsafe_allow_html=True)

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

    recent_months = sorted(months.keys())[-2:]
    month_options = {
        pd.Timestamp(m + "-01").strftime("%B %Y"): m
        for m in reversed(recent_months)
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
            color_discrete_map={True: GREEN, False: BLUE},
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
elif page == "Client Roster":
    st.title("Client Roster")
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
        st.metric("Showing", f"{len(df):,} clients")
    with m2:
        st.metric("Active", f"{active_ct:,}")
    with m3:
        st.metric("Inactive", f"{inactive_ct:,}")
    with m4:
        st.metric("Active Members", f"{total_mem:,}")

    st.markdown("<br>", unsafe_allow_html=True)

    # Table
    display_cols = [
        "first_name", "last_name", "carrier", "state", "status_display",
        "effective_date", "term_date", "applicant_count", "net_premium",
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
            "Effective Date": st.column_config.DateColumn("Effective Date", format="MMM D, YYYY"),
            "Term Date":      st.column_config.DateColumn("Term Date",      format="MMM D, YYYY"),
            "Net Premium":    st.column_config.NumberColumn("Net Premium", format="$%.2f"),
            "Applicant Count": st.column_config.NumberColumn("Members"),
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# GOALS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Goals":
    GOAL             = 2_000
    GOAL_DATE        = dt.date(2027, 2, 1)
    TODAY            = dt.date.today()
    COMMISSION_PMPM  = 23          # $23 per member per month
    MAX_TENURE_MONTHS = 48         # cap LTV calc at 4 years (conservative ceiling)
    _ACTIVE_STS      = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    _CHURN_STS       = {"Cancelled", "Terminated"}

    # ── Core member counts ────────────────────────────────────────────────────
    _active_mask = all_clients["status"].isin(_ACTIVE_STS) if "status" in all_clients.columns else pd.Series(False, index=all_clients.index)
    _churn_mask  = all_clients["status"].isin(_CHURN_STS)  if "status" in all_clients.columns else pd.Series(False, index=all_clients.index)
    current      = int(all_clients.loc[_active_mask, "applicant_count"].sum()) if "applicant_count" in all_clients.columns else 0
    gap          = max(GOAL - current, 0)
    pct_done     = min(current / GOAL * 100, 100)

    # ── LTV — calculated live from actual churn rate ──────────────────────────
    # Monthly churn rate = churned policies / total-ever-active / data span in months
    total_ever_active = int(_active_mask.sum()) + int(_churn_mask.sum())
    total_churned_ct  = int(_churn_mask.sum())
    # Data span = number of months we have snapshots for
    data_span_months = max(len(months), 1)
    monthly_churn_rate = (total_churned_ct / max(total_ever_active, 1)) / data_span_months
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

    st.title("Goals")
    st.markdown(
        f'<p style="color:#8aacd6;font-size:1rem;">Target: <b style="color:#e8edf5">'
        f'{GOAL:,} active members</b> by <b style="color:#e8edf5">'
        f'{GOAL_DATE.strftime("%B %d, %Y")}</b> &nbsp;·&nbsp; '
        f'LTV source: your live churn rate ({monthly_churn_rate*100:.2f}%/mo → '
        f'{implied_tenure_mo:.0f}-mo avg tenure → <b style="color:#2ecc71">'
        f'${ltv_per_member:,}/member</b>)</p>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

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
        st.markdown(_goal_kpi("Projected members by Feb 1", f"{projected_at_goal_date:,}", proj_label, proj_color), unsafe_allow_html=True)
    with p3:
        if shortfall > 0:
            st.markdown(_goal_kpi("Projected ARR by Feb 1", f"${projected_arr_at_goal:,.0f}", f"${goal_arr - projected_arr_at_goal:,.0f} short of goal ARR", "red"), unsafe_allow_html=True)
        else:
            st.markdown(_goal_kpi("Projected ARR by Feb 1", f"${projected_arr_at_goal:,.0f}", f"Goal ARR exceeded ✓", "green"), unsafe_allow_html=True)

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
            fig.add_hline(y=GOAL, line_color=GREEN, line_dash="dot", line_width=1.5, annotation_text="Goal: 2,000", annotation_position="top left", annotation_font_color=GREEN)
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
            "Month":           mo_label,
            "Members to add":  add_target,
            "Running total":   running_members,
            "MRR at target":   f"${running_members * COMMISSION_PMPM:,.0f}",
            "ARR at target":   f"${running_members * COMMISSION_PMPM * 12:,.0f}",
        })
        if running_members >= GOAL:
            break

    breakdown_df = pd.DataFrame(breakdown_rows)
    st.dataframe(breakdown_df, use_container_width=True, hide_index=True, height=340)
