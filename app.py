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
        ["Overview", "Month-over-Month", "Daily Tracker", "Client Roster"],
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

    # Filters
    f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
    with f1:
        status_opts = ["All"] + sorted(all_clients["status"].dropna().unique().tolist())
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
    if sel_status  != "All": df = df[df["status"]  == sel_status]
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
        "first_name", "last_name", "carrier", "state", "status",
        "effective_date", "term_date", "applicant_count", "net_premium",
    ]
    disp = df[[c for c in display_cols if c in df.columns]].copy()
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
