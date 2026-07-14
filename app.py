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

from tracker.config import get_agent

_AGENT = get_agent()
_AGENT_NAME = _AGENT["name"]
_AGENT_NPN = _AGENT["npn"]
_AGENT_FN = _AGENT["first_name"].lower()
_AGENT_LN = _AGENT["last_name"].lower()

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

# Keep the login across the full-page reload a Dashboard card link triggers:
# unlocking adds ?k=<token> to the URL, which survives reloads so the PIN isn't
# re-asked. (Casual deterrent only — the app code is public.)
_AUTH_TOKEN = "ok2026"
if st.query_params.get("k") == _AUTH_TOKEN:
    st.session_state.authenticated = True

if not st.session_state.authenticated:
    _lock_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                 'stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/>'
                 '<path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>')
    _shield_svg = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                   'stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>'
                   '<polyline points="9 12 11 14 15 10"/></svg>')
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    #MainMenu, header, footer, [data-testid="stToolbar"], [data-testid="stSidebar"], [data-testid="stStatusWidget"] {{
        visibility: hidden; display: none;
    }}
    html, body, [data-testid="stAppViewContainer"], .stButton, input {{
        font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    }}
    [data-testid="stAppViewContainer"] {{
        background:
          radial-gradient(circle at 35% 20%, rgba(59,130,246,0.22), transparent 32%),
          radial-gradient(circle at 72% 35%, rgba(124,58,237,0.22), transparent 35%),
          radial-gradient(circle at 50% 100%, rgba(37,99,235,0.12), transparent 35%),
          #050b16;
    }}
    /* Glass card = the centered block container */
    .block-container {{
        max-width: 760px !important;
        margin: 7vh auto 0 !important;
        background: rgba(15,28,52,0.72);
        border: 1px solid rgba(129,140,248,0.38);
        border-radius: 30px;
        box-shadow: 0 0 80px rgba(59,130,246,0.18), inset 0 1px 0 rgba(255,255,255,0.04);
        backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
        padding: 46px 56px 40px !important;
    }}
    .lock-circle {{
        width: 92px; height: 92px; margin: 0 auto 26px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        background: linear-gradient(145deg, rgba(59,130,246,0.22), rgba(124,58,237,0.26));
        border: 1px solid rgba(129,140,248,0.4);
        box-shadow: 0 0 35px rgba(124,58,237,0.3);
    }}
    .lock-circle svg {{ width: 42px; height: 42px; stroke: #e0e7ff; }}
    .lock-title {{
        display: flex; align-items: center; justify-content: center; gap: 14px;
        font-size: 2.1rem; font-weight: 800; letter-spacing: -0.02em; color: #f8fafc;
    }}
    .lock-title svg {{ width: 30px; height: 30px; stroke: #818cf8; }}
    .lock-sub {{ text-align: center; color: #94a3b8; font-size: 1rem; margin-top: 12px; }}
    .lock-divider {{
        height: 1px; max-width: 420px; margin: 26px auto 22px;
        background: linear-gradient(90deg, transparent, rgba(129,140,248,0.5), transparent);
    }}
    .lock-note {{
        display: flex; align-items: center; justify-content: center; gap: 10px;
        color: #94a3b8; font-size: 0.9rem; font-weight: 500; margin-top: 22px;
    }}
    .lock-note svg {{ width: 18px; height: 18px; stroke: #64748b; }}
    /* PIN input — style ONLY the outer BaseWeb wrapper (avoid double border) */
    [data-testid="stTextInput"] [data-baseweb="input"] {{
        background: rgba(15,23,42,0.75) !important;
        border: 1px solid rgba(129,140,248,0.25) !important;
        border-radius: 16px !important;
        height: 60px; overflow: hidden;
        transition: border-color .15s ease, box-shadow .15s ease;
    }}
    /* inner wrapper transparent so it doesn't peek out as a second box */
    [data-testid="stTextInput"] [data-baseweb="base-input"] {{
        background: transparent !important; border: none !important;
        box-shadow: none !important; border-radius: 0 !important;
    }}
    [data-testid="stTextInput"] [data-baseweb="input"]:focus-within {{
        border-color: rgba(96,165,250,0.7) !important;
        box-shadow: 0 0 30px rgba(59,130,246,0.22) !important;
    }}
    [data-testid="stTextInput"] input {{
        background: transparent !important;
        border: none !important; box-shadow: none !important;
        color: #f8fafc !important; height: 100%;
        font-size: 1.4rem; letter-spacing: 0.45em; text-align: center;
    }}
    /* Eye (show/hide) button — blend into the field, no separate box */
    [data-testid="stTextInput"] [data-baseweb="input"] button {{
        background: transparent !important; border: none !important;
        color: #94a3b8 !important; margin-right: 6px;
    }}
    [data-testid="stTextInput"] [data-baseweb="input"] button:hover {{ color: #f8fafc !important; }}
    /* Hide the "0/4" character counter (it triggers the red outline) */
    [data-testid="InputInstructions"] {{ display: none !important; }}
    /* Unlock button */
    .stButton > button {{
        background: linear-gradient(90deg, #3b82f6, #7c3aed) !important;
        color: #fff !important; border: none !important; border-radius: 16px !important;
        height: 60px; font-size: 1.15rem; font-weight: 700;
        box-shadow: 0 0 35px rgba(59,130,246,0.25);
        transition: transform .15s ease, filter .15s ease, box-shadow .15s ease;
    }}
    .stButton > button:hover {{
        transform: translateY(-2px); filter: brightness(1.1);
        box-shadow: 0 0 45px rgba(124,58,237,0.35);
    }}
    @media (max-width: 640px) {{
        .block-container {{ padding: 32px 22px !important; border-radius: 22px; }}
        .lock-title {{ font-size: 1.5rem; gap: 9px; }}
        .lock-title svg {{ width: 22px; height: 22px; }}
    }}
    </style>
    <div class="lock-circle">{_lock_svg}</div>
    <div class="lock-title">{_lock_svg} {_AGENT_NAME} Book of Business</div>
    <div class="lock-sub">Enter your 4-digit PIN to continue.</div>
    <div class="lock-divider"></div>
    """, unsafe_allow_html=True)

    pin = st.text_input("PIN", type="password", max_chars=4, placeholder="••••", label_visibility="collapsed")

    # Force the iOS numeric keypad (not the full keyboard) for PIN entry.
    import streamlit.components.v1 as _components
    _components.html(
        """
        <script>
        const doc = window.parent.document;
        let focused = false;
        function setNumeric() {
            const inp = doc.querySelector('[data-testid="stTextInput"] input');
            if (inp) {
                inp.setAttribute('inputmode', 'numeric');
                inp.setAttribute('pattern', '[0-9]*');
                inp.setAttribute('autocomplete', 'one-time-code');
                inp.setAttribute('autofocus', 'autofocus');
                if (!focused) { try { inp.focus(); } catch (e) {} focused = true; }
                // NOTE: no auto-submit-at-4-digits. The synthetic Enter + forced
                // blur raced the 4th keystroke after a Streamlit update and ATE
                // it (couldn't type the last digit, 2026-07-07). Type 4 digits,
                // press Enter or Unlock — boring and solid.
            }
        }
        setNumeric();
        new MutationObserver(setNumeric).observe(doc.body, {childList: true, subtree: true});
        </script>
        """,
        height=0,
    )

    submitted = st.button("🔒 Unlock", use_container_width=True) or (len(pin) == 4)
    st.markdown(
        f'<div class="lock-note">{_shield_svg} Your data is encrypted and secure</div>',
        unsafe_allow_html=True,
    )
    if submitted:
        if pin == "1212":
            st.session_state.authenticated = True
            st.query_params["k"] = _AUTH_TOKEN
            st.rerun()
        elif pin:
            st.error("Incorrect PIN")
    st.stop()

# ── Theme — Premium midnight fintech ────────────────────────────────────────
NAVY  = "#0f1c34"
LNAV  = "#1b2c4d"
BLUE  = "#3b82f6"
ELEC  = "#60a5fa"
PURPLE= "#7c3aed"
CYAN  = "#22d3ee"
GREEN = "#22c55e"
RED   = "#ef4444"
GOLD  = "#f59e0b"
T = dict(
    page_bg      = "#070f22",
    sidebar_bg   = "#081426",
    kpi_bg       = "rgba(15, 28, 52, 0.82)",
    kpi_border   = "rgba(96, 165, 250, 0.25)",
    kpi_val      = "#f8fafc",
    kpi_lbl      = "#94a3b8",
    kpi_sub      = "#6b84ad",
    divider      = "rgba(96, 165, 250, 0.18)",
    progress_bg  = "#0a1326",
    goal_val     = "#60a5fa",
    goal_green   = "#22c55e",
    goal_gold    = "#f59e0b",
    goal_red     = "#ef4444",
    text_primary = "#f8fafc",
)

# ── CSS: premium midnight fintech theme ───────────────────────────────────────
st.markdown(f"""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

  html, body, [data-testid="stAppViewContainer"], [data-testid="stSidebar"],
  .stMarkdown, .stButton, input, textarea, select, [class*="css"] {{
    font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}

  /* Page background with radial lighting */
  [data-testid="stAppViewContainer"] {{
    background:
      radial-gradient(1100px 560px at 82% -8%, rgba(124,58,237,0.13), transparent 60%),
      radial-gradient(900px 520px at 8% -4%, rgba(59,130,246,0.13), transparent 55%),
      {T['page_bg']};
  }}
  [data-testid="stHeader"] {{ background: transparent; }}
  .main .block-container {{
    background: transparent;
    max-width: 1320px;
    padding-top: 2.2rem;
  }}
  h1, h2, h3, h4, p, label, .stMarkdown {{ color: {T['text_primary']}; }}
  [data-testid="stMarkdownContainer"] h1, .stApp h1 {{ color: {T['text_primary']} !important; }}

  /* ── Sidebar — dark blue gradient, rounded, bordered ── */
  [data-testid="stSidebar"] {{
    background: linear-gradient(185deg, #0b1830 0%, {T['sidebar_bg']} 100%);
    border-right: 1px solid {T['divider']};
  }}
  [data-testid="stSidebar"] > div:first-child {{
    padding-top: 10px;
    border-radius: 0 22px 22px 0;
  }}
  [data-testid="stSidebar"] * {{ color: {T['text_primary']}; }}
  [data-testid="stSidebar"] h2 {{
    font-size: 1.15rem; font-weight: 800; letter-spacing: -0.01em;
    padding: 6px 4px 2px;
  }}
  /* Nav items (radio styled as menu) */
  section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap: 6px; }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label {{
    display: flex; align-items: center; gap: 14px; width: 100%;
    padding: 13px 16px; border-radius: 14px; margin: 2px 0;
    transition: background .15s ease, box-shadow .15s ease;
    cursor: pointer;
  }}
  /* per-item icon slot (icon image set per nth-of-type below) */
  section[data-testid="stSidebar"] div[role="radiogroup"] > label::before {{
    content: ""; flex: 0 0 auto; width: 20px; height: 20px;
    background-repeat: no-repeat; background-position: center; background-size: contain;
    opacity: 0.85;
  }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {{
    background: rgba(96,165,250,0.07);
  }}
  /* selected page: dark pill with a blue→purple ring, soft glow, red dot */
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) {{
    background: rgba(30,48,92,0.45);
    box-shadow: inset 0 0 0 1.5px rgba(96,165,250,0.75),
                0 0 0 1px rgba(139,92,246,0.45),
                0 0 18px rgba(96,165,250,0.22);
  }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked)::before {{
    opacity: 1;
  }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p {{
    font-weight: 700; color: #dbe7ff;
  }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) > div:last-child,
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) [data-testid="stMarkdownContainer"] {{
    flex: 1; width: 100%;
  }}
  section[data-testid="stSidebar"] div[role="radiogroup"] > label:has(input:checked) p::after {{
    content: ""; margin-left: auto; width: 10px; height: 10px; border-radius: 50%;
    background: #f43f5e; box-shadow: 0 0 9px rgba(244,63,94,0.85);
  }}
  /* hide the radio dot so it reads as a clean nav item (streamlit is PINNED
     to 1.50.0 in requirements.txt — an unpinned cloud version rendered a
     different DOM where this rule missed and the dot showed) */
  section[data-testid="stSidebar"] div[role="radiogroup"] > label > div:first-child,
  section[data-testid="stSidebar"] div[role="radiogroup"] > label > span:first-child {{
    display: none !important;
  }}
  section[data-testid="stSidebar"] div[role="radiogroup"] label p {{
    font-size: 0.97rem; font-weight: 500; color: #cbd5e1;
    display: flex; align-items: center; width: 100%;
  }}
  /* sidebar footer info rows (snapshot / client count) */
  .sb-foot {{ display: flex; align-items: center; gap: 11px; margin: 7px 2px; }}
  .sb-foot .tile {{
    flex: 0 0 auto; width: 32px; height: 32px; border-radius: 9px;
    background: #131f3a; box-shadow: inset 0 0 0 1px rgba(96,165,250,0.14);
    display: flex; align-items: center; justify-content: center; font-size: .85rem;
  }}
  .sb-foot .txt {{ font-size: .88rem; color: #9fb2cc; }}
  .sb-foot .txt b {{ color: #e8edf5; }}
  /* Brand logo row */
  .brand-row {{ display: flex; align-items: center; gap: 11px; padding: 8px 4px 2px; }}
  .brand-row .brand-logo {{
    width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(145deg, {BLUE}, {PURPLE}); box-shadow: 0 6px 16px rgba(124,58,237,0.35);
  }}
  .brand-row .brand-logo svg {{ width: 18px; height: 18px; stroke: #fff; fill: none; stroke-width: 2.2; }}
  .brand-row .brand-text {{ font-size: 1.12rem; font-weight: 800; letter-spacing: -0.01em; color: #f8fafc; }}
  /* Sidebar buttons */
  [data-testid="stSidebar"] .stButton > button {{
    background: linear-gradient(90deg, {BLUE}, {PURPLE});
    color: #fff; border: none; border-radius: 12px; font-weight: 600;
    box-shadow: 0 8px 22px rgba(59,130,246,0.28);
    transition: filter .15s ease, transform .15s ease;
  }}
  [data-testid="stSidebar"] .stButton > button:hover {{
    filter: brightness(1.08); transform: translateY(-1px);
  }}
  [data-testid="stSidebar"] [data-testid="stDownloadButton"] > button {{
    background: rgba(15,28,52,0.6); color: #cbd5e1;
    border: 1px solid rgba(96,165,250,0.32); border-radius: 12px; font-weight: 600;
    box-shadow: none;
  }}
  [data-testid="stSidebar"] [data-testid="stDownloadButton"] > button:hover {{
    border-color: rgba(96,165,250,0.6); color: #fff;
  }}

  /* ── Dashboard header + topbar ── */
  .dash-header {{
    display: flex; align-items: flex-start; justify-content: space-between;
    margin: 2px 0 4px;
  }}
  /* Sleek hero header */
  .dash-hero {{
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    margin: 2px 0 8px; padding-bottom: 16px;
    border-bottom: 1px solid transparent;
    border-image: linear-gradient(90deg, rgba(96,165,250,0.5), rgba(124,58,237,0.28), rgba(96,165,250,0)) 1;
  }}
  .dash-hero-left {{ display: flex; align-items: center; gap: 17px; }}
  .dash-accent {{ width: 6px; height: 56px; border-radius: 6px; flex: 0 0 auto;
    background: linear-gradient(180deg, #60a5fa, #7c3aed);
    box-shadow: 0 0 20px rgba(96,165,250,0.55); }}
  .dash-title {{ font-size: 2.8rem; font-weight: 800; letter-spacing: -0.03em; line-height: 1;
    background: linear-gradient(96deg, #ffffff 0%, #d6e4ff 45%, #8fb3ec 100%);
    -webkit-background-clip: text; background-clip: text;
    -webkit-text-fill-color: transparent; color: transparent; }}
  .dash-sub {{ color: {T['kpi_lbl']}; font-size: 0.92rem; margin-top: 9px;
    display: flex; align-items: center; gap: 8px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }}
  .dash-sub .live-dot {{ width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
    box-shadow: 0 0 0 4px rgba(34,197,94,0.16); display: inline-block; }}
  .date-badge {{ display: inline-flex; align-items: center; gap: 8px; flex: 0 0 auto; white-space: nowrap;
    background: linear-gradient(160deg, rgba(96,165,250,0.14), rgba(124,58,237,0.10));
    border: 1px solid rgba(96,165,250,0.32); border-radius: 999px; padding: 10px 18px;
    color: #dce8ff; font-weight: 700; font-size: 0.84rem; letter-spacing: 0.02em;
    box-shadow: 0 8px 24px rgba(8,20,46,0.45); }}
  .date-badge svg {{ width: 15px; height: 15px; stroke: #8fb3ec; fill: none; stroke-width: 2; }}
  .legend-pill {{ display: inline-flex; align-items: center; gap: 7px; padding: 5px 12px; margin-right: 9px;
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 999px; }}
  .topbar {{ display: flex; align-items: center; gap: 12px; }}
  .topbar .tb-icon {{
    width: 38px; height: 38px; border-radius: 11px; display: flex; align-items: center; justify-content: center;
    background: rgba(15,28,52,0.7); border: 1px solid rgba(96,165,250,0.2);
  }}
  .topbar .tb-icon svg {{ width: 18px; height: 18px; stroke: {T['kpi_lbl']}; fill: none; stroke-width: 2; }}
  .avatar {{
    width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: 0.85rem; color: #fff;
    background: linear-gradient(145deg, {BLUE}, {PURPLE});
    box-shadow: 0 6px 18px rgba(124,58,237,0.35);
  }}

  /* ── Section headers ── */
  .section-head {{ display: flex; align-items: center; gap: 11px; margin: 30px 0 16px; }}
  .section-head .sh-icon svg {{ width: 16px; height: 16px; stroke: {ELEC}; fill: none; stroke-width: 2; }}
  .section-head .sh-title {{
    font-size: 0.78rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase;
    color: {T['kpi_lbl']}; white-space: nowrap;
  }}
  .section-head .sh-line {{ flex: 1; height: 1px; background: linear-gradient(90deg, rgba(96,165,250,0.28), rgba(96,165,250,0.02)); }}

  /* ── Metric cards (glassy) ── */
  .metric-card {{
    position: relative; overflow: hidden;
    background: linear-gradient(160deg, rgba(20,34,62,0.9), rgba(11,21,42,0.85));
    border: 1px solid {T['kpi_border']};
    border-radius: 18px; padding: 22px 22px 18px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03), 0 10px 30px rgba(0,0,0,0.25);
    transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
    height: 100%;
  }}
  .metric-card:hover {{
    transform: translateY(-3px);
    border-color: rgba(96,165,250,0.55);
    box-shadow: 0 0 0 1px rgba(96,165,250,0.25), 0 16px 42px rgba(8,20,46,0.6), 0 0 32px rgba(59,130,246,0.16);
  }}
  .metric-card.highlight {{
    border-color: rgba(124,58,237,0.6);
    background: linear-gradient(160deg, rgba(38,29,74,0.92), rgba(16,20,52,0.9));
    box-shadow: 0 0 0 1px rgba(124,58,237,0.42), 0 0 42px rgba(124,58,237,0.28);
  }}
  .metric-card.highlight:hover {{ box-shadow: 0 0 0 1px rgba(124,58,237,0.6), 0 0 54px rgba(124,58,237,0.4); }}
  .metric-card.highlight.green {{
    border-color: rgba(34,197,94,0.6);
    background: linear-gradient(160deg, rgba(20,60,40,0.92), rgba(14,30,28,0.9));
    box-shadow: 0 0 0 1px rgba(34,197,94,0.42), 0 0 42px rgba(34,197,94,0.28);
  }}
  .metric-card.highlight.green:hover {{ box-shadow: 0 0 0 1px rgba(34,197,94,0.6), 0 0 54px rgba(34,197,94,0.4); }}
  .metric-card.highlight.green .mc-icon svg {{ stroke: {GREEN}; }}
  .mc-icon {{
    width: 42px; height: 42px; border-radius: 12px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(145deg, rgba(59,130,246,0.18), rgba(124,58,237,0.13));
    border: 1px solid rgba(96,165,250,0.22);
  }}
  .mc-icon svg {{ width: 20px; height: 20px; stroke: {ELEC}; fill: none; stroke-width: 2; }}
  .metric-card.highlight .mc-icon svg {{ stroke: #c4b5fd; }}
  .mc-value {{ font-size: 2.5rem; font-weight: 800; color: {T['kpi_val']}; line-height: 1.04; margin-top: 16px; letter-spacing: -0.02em; }}
  .mc-label {{ font-size: 0.72rem; color: {T['kpi_lbl']}; text-transform: uppercase; letter-spacing: 0.08em; margin-top: 5px; font-weight: 600; }}
  .mc-sub {{ font-size: 0.72rem; color: {T['kpi_sub']}; margin-top: 9px; }}
  .mc-spark {{ position: absolute; top: 20px; right: 20px; opacity: 0.95; }}

  /* ── Legacy KPI boxes (other pages) restyled to match ── */
  .kpi-box {{
    background: linear-gradient(160deg, rgba(20,34,62,0.9), rgba(11,21,42,0.85));
    border-radius: 16px; padding: 20px 16px 16px; text-align: center;
    border: 1px solid {T['kpi_border']};
    transition: transform .2s ease, border-color .2s ease;
  }}
  .kpi-box:hover {{ transform: translateY(-2px); border-color: rgba(96,165,250,0.5); }}
  .kpi-value {{ font-size: 2.1rem; font-weight: 800; color: {T['kpi_val']}; line-height: 1.1; }}
  .kpi-label {{ font-size: 0.72rem; color: {T['kpi_lbl']}; margin-top: 6px; text-transform: uppercase; letter-spacing: 0.06em; }}
  .section-divider {{ margin: 8px 0 20px; border-top: 1px solid {T['divider']}; }}

  .goal-kpi-box {{
    background: linear-gradient(160deg, rgba(20,34,62,0.9), rgba(11,21,42,0.85));
    border-radius: 16px; padding: 24px 16px 20px; text-align: center;
    border: 1px solid {T['kpi_border']}; position: relative;
    transition: transform .2s ease, border-color .2s ease;
  }}
  .goal-kpi-box:hover {{ transform: translateY(-2px); border-color: rgba(96,165,250,0.5); }}
  .goal-kpi-value {{ font-size: 2.6rem; font-weight: 800; color: {T['goal_val']}; line-height: 1.1; }}
  .goal-kpi-value.green  {{ color: {T['goal_green']}; }}
  .goal-kpi-value.gold   {{ color: {T['goal_gold']}; }}
  .goal-kpi-value.red    {{ color: {T['goal_red']}; }}
  .goal-kpi-label {{ font-size: 0.72rem; color: {T['kpi_lbl']}; margin-top: 6px; text-transform: uppercase; letter-spacing: 0.06em; }}
  .goal-kpi-sub {{ font-size: 0.82rem; color: {T['kpi_lbl']}; margin-top: 4px; }}
  .progress-wrap {{ background: {T['progress_bg']}; border-radius: 999px; height: 22px; overflow: hidden; margin: 10px 0 6px; }}
  .progress-bar {{ height: 100%; border-radius: 999px; background: linear-gradient(90deg, {BLUE}, {PURPLE}); transition: width 0.6s ease; }}

  /* ── Glass panels (st.container(border=True)) ── */
  [data-testid="stVerticalBlockBorderWrapper"] {{
    background: linear-gradient(160deg, rgba(16,28,52,0.78), rgba(10,18,38,0.72));
    border: 1px solid rgba(96,165,250,0.22) !important;
    border-radius: 20px !important;
    padding: 8px 14px 10px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.03), 0 10px 30px rgba(0,0,0,0.25);
    transition: border-color .2s ease, box-shadow .2s ease;
  }}
  [data-testid="stVerticalBlockBorderWrapper"]:hover {{
    border-color: rgba(96,165,250,0.45) !important;
    box-shadow: 0 0 0 1px rgba(96,165,250,0.2), 0 14px 40px rgba(8,20,46,0.55), 0 0 28px rgba(59,130,246,0.12);
  }}
  /* Chart card header */
  .chart-head {{ display: flex; align-items: flex-start; gap: 12px; padding: 12px 6px 6px; }}
  .chart-head .ch-icon {{
    width: 34px; height: 34px; border-radius: 10px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(145deg, rgba(59,130,246,0.18), rgba(124,58,237,0.13)); border: 1px solid rgba(96,165,250,0.22);
  }}
  .chart-head .ch-icon svg {{ width: 17px; height: 17px; stroke: {ELEC}; fill: none; stroke-width: 2; }}
  .chart-head .ch-title {{ font-size: 1.05rem; font-weight: 700; color: {T['text_primary']}; line-height: 1.15; }}
  .chart-head .ch-sub {{ font-size: 0.76rem; color: {T['kpi_lbl']}; margin-top: 2px; }}
  .chart-head .ch-dots {{ margin-left: auto; color: #64748b; font-size: 1.4rem; line-height: 1; }}
  /* Book-age cards */
  .ba-card {{
    position: relative; overflow: hidden; height: 100%;
    background: linear-gradient(160deg, rgba(20,34,62,0.9), rgba(11,21,42,0.85));
    border: 1px solid rgba(96,165,250,0.22); border-radius: 16px; padding: 16px 16px 14px;
    transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
  }}
  .ba-card:hover {{ transform: translateY(-3px); border-color: rgba(96,165,250,0.5);
    box-shadow: 0 12px 32px rgba(8,20,46,0.5); }}
  .ba-card .ba-bar {{ position: absolute; top: 0; left: 0; right: 0; height: 3px; }}
  .ba-icon {{ width: 38px; height: 38px; border-radius: 11px; display: flex; align-items: center; justify-content: center; }}
  .ba-icon svg {{ width: 18px; height: 18px; fill: none; stroke-width: 2; }}
  .ba-val {{ font-size: 1.95rem; font-weight: 800; color: {T['kpi_val']}; line-height: 1; margin-top: 12px; }}
  .ba-lbl {{ font-size: 0.72rem; color: {T['kpi_lbl']}; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 5px; }}
  .ba-pct {{ font-size: 0.98rem; font-weight: 700; margin-top: 6px; }}
  /* Insight callout */
  .insight {{
    display: flex; align-items: flex-start; gap: 14px; margin: 16px 6px 6px;
    background: linear-gradient(90deg, rgba(59,130,246,0.13), rgba(59,130,246,0.04));
    border: 1px solid rgba(96,165,250,0.3); border-radius: 14px; padding: 14px 18px;
  }}
  .insight .in-icon {{
    flex: 0 0 auto; width: 34px; height: 34px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    background: rgba(59,130,246,0.18); border: 1px solid rgba(96,165,250,0.4);
  }}
  .insight .in-icon svg {{ width: 18px; height: 18px; stroke: {ELEC}; fill: none; stroke-width: 2; }}
  .insight .in-main {{ font-size: 0.95rem; font-weight: 700; color: #f1f5f9; }}
  .insight .in-sub {{ font-size: 0.82rem; color: {T['kpi_lbl']}; margin-top: 3px; }}

  /* ── Stat cards (icon-left layout) ── */
  .stat-card {{
    display: flex; align-items: center; gap: 16px; height: 100%;
    background: linear-gradient(160deg, rgba(20,34,62,0.9), rgba(11,21,42,0.85));
    border: 1px solid rgba(96,165,250,0.22); border-radius: 18px; padding: 20px 20px;
    transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
  }}
  .stat-card:hover {{ transform: translateY(-3px); border-color: rgba(96,165,250,0.5);
    box-shadow: 0 12px 32px rgba(8,20,46,0.5), 0 0 28px rgba(59,130,246,0.12); }}
  .stat-card .sc-icon {{ flex: 0 0 auto; width: 50px; height: 50px; border-radius: 14px;
    display: flex; align-items: center; justify-content: center; }}
  .stat-card .sc-icon svg {{ width: 23px; height: 23px; fill: none; stroke-width: 2; }}
  .stat-card .sc-val {{ font-size: 2rem; font-weight: 800; color: {T['kpi_val']}; line-height: 1; }}
  .stat-card .sc-lbl {{ font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: {T['kpi_lbl']}; margin-top: 7px; font-weight: 600; }}
  /* ── Hover tooltip on the Dashboard action cards ── */
  [data-testid="stHorizontalBlock"], [data-testid="column"], [data-testid="stVerticalBlock"],
  [data-testid="stMarkdownContainer"], .element-container, .stMarkdown {{ overflow: visible !important; }}
  .tip-wrap {{ position: relative; display: block; }}
  .tip-pop {{
    position: absolute; top: calc(100% + 10px); left: 50%; transform: translateX(-50%) translateY(6px);
    z-index: 1000; width: 340px; max-width: 88vw; text-align: left;
    background: linear-gradient(160deg, rgba(27,44,77,0.99), rgba(13,23,42,0.99));
    border: 1px solid rgba(96,165,250,0.45); border-radius: 16px; padding: 18px 20px;
    color: #e8f0ff; font-size: 1.02rem; line-height: 1.5; font-weight: 500; letter-spacing: .1px;
    box-shadow: 0 22px 60px rgba(0,0,0,0.6), 0 0 34px rgba(59,130,246,0.18);
    opacity: 0; visibility: hidden; pointer-events: none;
    transition: opacity .18s ease, transform .18s ease;
  }}
  .tip-pop::before {{
    content: ""; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%);
    border: 8px solid transparent; border-bottom-color: rgba(96,165,250,0.45);
  }}
  .tip-pop .tip-title {{ display: block; font-size: 0.74rem; font-weight: 800; text-transform: uppercase;
    letter-spacing: 0.08em; color: #8fb3ec; margin-bottom: 8px; }}
  .tip-wrap:hover .tip-pop, .tip-wrap.tip-show .tip-pop {{ opacity: 1; visibility: visible; transform: translateX(-50%) translateY(0); }}
  /* ── Target progress bar ── */
  .tp-head {{ display: flex; align-items: center; gap: 9px; margin: 6px 2px 2px; }}
  .tp-head .tp-title {{ font-size: 1.15rem; font-weight: 700; color: {T['text_primary']}; }}
  .tp-head .tp-info svg {{ width: 16px; height: 16px; stroke: {T['kpi_lbl']}; fill: none; stroke-width: 2; vertical-align: middle; }}
  .target-track {{ background: rgba(10,19,38,0.9); border: 1px solid rgba(96,165,250,0.18);
    border-radius: 999px; height: 14px; overflow: hidden; margin: 10px 2px 6px; }}
  .target-fill {{ height: 100%; border-radius: 999px;
    background: linear-gradient(90deg, #f43f5e, #fb7185);
    box-shadow: 0 0 18px rgba(244,63,94,0.5); transition: width .6s ease; }}

  /* ── Form inputs (number / date / text / select) — cohesive dark fields ── */
  [data-testid="stNumberInput"] div[data-baseweb="input"],
  [data-testid="stDateInput"] div[data-baseweb="input"] {{
    background: rgba(15,23,42,0.6) !important;
    border: 1px solid rgba(96,165,250,0.22) !important;
    border-radius: 12px !important;
    overflow: hidden;
  }}
  [data-testid="stNumberInput"] input,
  [data-testid="stDateInput"] input {{
    background: transparent !important; color: {T['text_primary']} !important;
  }}
  [data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within,
  [data-testid="stDateInput"] div[data-baseweb="input"]:focus-within {{
    border-color: rgba(96,165,250,0.6) !important;
    box-shadow: 0 0 0 1px rgba(96,165,250,0.22) !important;
  }}
  /* number-input stepper (− / +) buttons blended into the field */
  [data-testid="stNumberInput"] button {{
    background: transparent !important; border: none !important;
    border-left: 1px solid rgba(96,165,250,0.15) !important;
    color: {T['kpi_lbl']} !important; border-radius: 0 !important;
  }}
  [data-testid="stNumberInput"] button:hover {{
    background: rgba(59,130,246,0.18) !important; color: #fff !important;
  }}
  /* selectbox dropdowns */
  [data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
    background: rgba(15,23,42,0.6) !important;
    border: 1px solid rgba(96,165,250,0.22) !important;
    border-radius: 12px !important;
  }}

  /* ── Mobile / tablet ── */
  @media (max-width: 768px) {{
    .dash-title {{ font-size: 1.9rem; }}
    .topbar {{ gap: 8px; }}
    .kpi-box {{ padding: 16px 12px 12px; margin-bottom: 8px; }}
    .kpi-value {{ font-size: 1.8rem; }}
    .kpi-label {{ font-size: 0.65rem; }}
    .metric-card {{ padding: 18px 16px 14px; margin-bottom: 8px; }}
    .mc-value {{ font-size: 2rem; }}
    .goal-kpi-box {{ padding: 18px 12px 14px; margin-bottom: 8px; }}
    .goal-kpi-value {{ font-size: 2rem; }}
    /* compact stat-cards (money + action rows) so 4-across stacks cleanly on phones */
    .stat-card {{ padding: 14px 14px; gap: 12px; margin-bottom: 8px; }}
    .stat-card .sc-icon {{ width: 40px; height: 40px; }}
    .stat-card .sc-icon svg {{ width: 19px; height: 19px; }}
    .stat-card .sc-val {{ font-size: 1.5rem; }}
    .stat-card .sc-lbl {{ font-size: 0.6rem; }}
    .stat-card .sc-delta {{ font-size: 0.66rem; }}
    .ch-title {{ font-size: 1rem; }}
    .block-container {{ padding-left: 1rem !important; padding-right: 1rem !important; padding-top: 1rem !important; }}
    [data-testid="stDataFrame"] {{ overflow-x: auto; }}
    [data-testid="stDataFrame"] [role="progressbar"] > div {{ background-color: {GREEN} !important; }}
    h1 {{ font-size: 1.6rem !important; }}
    h2 {{ font-size: 1.2rem !important; }}
    h3 {{ font-size: 1rem !important; }}
    .progress-wrap {{ height: 26px; }}
  }}
</style>
""", unsafe_allow_html=True)


# ── Sidebar nav icons — injected per item via CSS (keeps st.radio logic intact) ─
def _nav_icon_css():
    from urllib.parse import quote
    def _svg(inner):
        return ("data:image/svg+xml," + quote(
            f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' "
            f"stroke='#cbd5e1' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>{inner}</svg>"
        ))
    # order MUST match the st.radio options below (grouped nav)
    nav = [
        # ── OVERVIEW ──
        "<rect x='3' y='3' width='7' height='7'/><rect x='14' y='3' width='7' height='7'/><rect x='14' y='14' width='7' height='7'/><rect x='3' y='14' width='7' height='7'/>",  # Dashboard (grid)
        "<rect x='3' y='4' width='18' height='18' rx='2'/><line x1='16' y1='2' x2='16' y2='6'/><line x1='8' y1='2' x2='8' y2='6'/><line x1='3' y1='10' x2='21' y2='10'/>",  # Daily Tracker (calendar)
        "<circle cx='12' cy='12' r='10'/><circle cx='12' cy='12' r='6'/><circle cx='12' cy='12' r='2'/>",  # Goals (target)
        # ── CLIENTS ──
        "<circle cx='11' cy='11' r='8'/><line x1='21' y1='21' x2='16.65' y2='16.65'/>",  # Client Lookup (magnifier)
        "<rect x='2' y='7' width='20' height='14' rx='2'/><path d='M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16'/>",  # Book (briefcase)
        "<polyline points='23 6 13.5 15.5 8.5 10.5 1 18'/><polyline points='17 6 23 6 23 12'/>",  # Monthly Trends (trend)
        # ── MONEY ──
        "<line x1='12' y1='1' x2='12' y2='23'/><path d='M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6'/>",  # Commissions (dollar)
        "<circle cx='12' cy='12' r='10'/><line x1='12' y1='8' x2='12' y2='12'/><line x1='12' y1='16' x2='12.01' y2='16'/>",  # Money Owed (alert)
        "<circle cx='12' cy='12' r='10'/><polyline points='12 6 12 12 16 14'/>",  # Past Due (clock)
        # ── WORKFLOWS ──
        "<path d='M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/><circle cx='8.5' cy='7' r='4'/><line x1='18' y1='8' x2='23' y2='13'/><line x1='23' y1='8' x2='18' y2='13'/>",  # AOR Defense (user-x)
        "<path d='M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2'/><rect x='8' y='2' width='8' height='4' rx='1'/><polyline points='9 14 11 16 15 12'/>",  # Follow-ups (clipboard-check)
        "<path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/><circle cx='9' cy='7' r='4'/><path d='M23 21v-2a4 4 0 0 0-3-3.87'/><path d='M16 3.13a4 4 0 0 1 0 7.75'/>",  # Re-Engage (users)
        "<polyline points='23 4 23 10 17 10'/><polyline points='1 20 1 14 7 14'/><path d='M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'/>",  # Supplemental Re-Engage (refresh)
        "<path d='M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z'/>",  # AEP Tracker (shield)
        "<circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/>",  # Settings (gear)
    ]
    rules = "".join(
        f'section[data-testid="stSidebar"] div[role="radiogroup"] > label:nth-of-type({i})::before'
        f'{{background-image:url("{_svg(inner)}");}}'
        for i, inner in enumerate(nav, start=1)
    )
    # Group headers (small dim uppercase) painted above the first tab of each
    # section, plus a divider before Settings. Positions follow _NAV order.
    _SB = 'section[data-testid="stSidebar"] div[role="radiogroup"] > label'
    groups = [(1, "OVERVIEW"), (4, "CLIENTS"), (7, "MONEY"), (10, "FOLLOW UPS")]
    for i, title in groups:
        rules += (
            f'{_SB}:nth-of-type({i}) {{margin-top:26px; position:relative; overflow:visible;}}'
            f'{_SB}:nth-of-type({i})::after {{content:"{title}"; position:absolute; top:-19px; left:14px;'
            f'font-size:.6rem; font-weight:800; letter-spacing:.16em; color:#5b6b84;}}'
        )
    rules += (
        f'{_SB}:nth-of-type(1) {{margin-top:20px;}}'
        # Settings: pinned visually apart — hairline divider above + its own
        # filled rounded row (matches the reference sidebar).
        f'{_SB}:nth-of-type(15) {{margin-top:30px; position:relative; overflow:visible;'
        f'background:rgba(19,31,58,.55);}}'
        f'{_SB}:nth-of-type(15)::after {{content:""; position:absolute; top:-15px; left:10px; right:10px;'
        f'height:1px; background:rgba(148,163,184,.16);}}'
    )
    return f"<style>{rules}</style>"

st.markdown(_nav_icon_css(), unsafe_allow_html=True)

# No data export: hide the dataframe hover toolbar (CSV download / search /
# fullscreen) on every table (Ethan 2026-07-07).
st.markdown("""<style>
  [data-testid="stElementToolbar"] {display:none !important;}
</style>""", unsafe_allow_html=True)


# ── UI helpers: icons, sparklines, cards, section headers ─────────────────────
ICONS = {
    "shield":   '<svg viewBox="0 0 24 24"><path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z"/></svg>',
    "users":    '<svg viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
    "home":     '<svg viewBox="0 0 24 24"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/></svg>',
    "plus":     '<svg viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    "minus":    '<svg viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    "trend":    '<svg viewBox="0 0 24 24"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>',
    "dollar":   '<svg viewBox="0 0 24 24"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>',
    "calendar": '<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    "file":     '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    "book":     '<svg viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    "search":   '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    "bell":     '<svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>',
    "clock":    '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    "info":     '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    "pie":      '<svg viewBox="0 0 24 24"><path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/></svg>',
    "pin":      '<svg viewBox="0 0 24 24"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>',
    "bars":     '<svg viewBox="0 0 24 24"><line x1="6" y1="20" x2="6" y2="14"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="18" y1="20" x2="18" y2="10"/></svg>',
    "target":   '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
    "refresh":  '<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
    "gear":     '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
}


def chart_head(title, sub, icon_key):
    return (
        f'<div class="chart-head"><div class="ch-icon">{ICONS.get(icon_key, "")}</div>'
        f'<div><div class="ch-title">{title}</div><div class="ch-sub">{sub}</div></div>'
        f'<div class="ch-dots">⋮</div></div>'
    )


def stat_card(label, value, icon_key, color, delta=None, delta_good=True):
    """Icon-left KPI card (tinted circular icon + value + label).
    Optional `delta` renders a small trend line under the value (e.g. "▲ 9% vs
    last month"); colored green when delta_good else red."""
    icon = ICONS.get(icon_key, "").replace("<svg ", f'<svg stroke="{color}" ', 1)
    delta_html = ""
    if delta:
        dc = GREEN if delta_good else RED
        delta_html = (f'<div class="sc-delta" style="color:{dc};font-size:0.72rem;'
                      f'font-weight:700;margin-top:3px;letter-spacing:.2px;">{delta}</div>')
    return (
        f'<div class="stat-card">'
        f'<div class="sc-icon" style="background:{color}22;border:1px solid {color}55;">{icon}</div>'
        f'<div><div class="sc-val">{value}</div><div class="sc-lbl">{label}</div>{delta_html}</div>'
        f'</div>'
    )


# Color language (consistent across the app):
#   green = money / good · red = risk / loss · gold = needs attention
#   electric/cyan = neutral info · purple = time / coverage
GOOD, RISK, ATTN, INFO = GREEN, RED, GOLD, ELEC


def _rel_day(ts):
    """Human relative date: 'today', 'yesterday', '3 days ago', 'in 5 days'."""
    ts = pd.to_datetime(ts, errors="coerce")
    if pd.isna(ts):
        return "—"
    d = (ts.normalize() - pd.Timestamp.today().normalize()).days
    if d == 0:
        return "today"
    if d == -1:
        return "yesterday"
    if d == 1:
        return "tomorrow"
    return f"{-d} days ago" if d < 0 else f"in {d} days"


def color_legend():
    """Tiny inline legend explaining the color language; render with st.markdown."""
    items = [(GREEN, "money / good"), (RED, "risk / loss"), (GOLD, "needs attention")]
    chips = "".join(
        f'<span class="legend-pill">'
        f'<span style="width:9px;height:9px;border-radius:50%;background:{c};'
        f'box-shadow:0 0 8px {c}99;display:inline-block;"></span>'
        f'<span style="color:#aebfd6;font-size:0.72rem;font-weight:600;letter-spacing:.02em;">{t}</span></span>'
        for c, t in items)
    return f'<div style="margin:-2px 0 12px;">{chips}</div>'


def _filter_aor_mine(df):
    """Keep rows where the current agent of record is Ethan OR blank/unknown; drop
    rows whose AOR is a DIFFERENT named agent (you're not their agent anymore).
    Also drops anyone on the confirmed-AOR-changed override list (catches lag
    cases where the export's policy_aor field hasn't propagated yet, e.g. Tammy
    Bennett). Marketplace-disconnected clients keep policy_aor=Ethan and are NOT
    on that list, so they stay — you may still be their agent."""
    if df is None or getattr(df, "empty", True):
        return df
    out = df
    if "policy_aor" in getattr(df, "columns", []):
        a = df["policy_aor"].fillna("").astype(str)
        not_mine = (a.str.strip().ne("") & ~a.str.contains("None")
                    & ~a.str.contains(_AGENT_NPN)
                    & ~(a.str.contains(_AGENT_FN, case=False) & a.str.contains(_AGENT_LN, case=False)))
        out = df[~not_mine]
    try:
        from tracker.commissions import drop_aor_changed
        out = drop_aor_changed(out)
    except Exception:
        pass
    return out


def link_card(label, value, icon_key, color, goto, sec=None, tip=None):
    """A stat_card wrapped in a same-tab link that deep-links to another page via
    a ?goto= query param (read by the sidebar nav). Optional `sec` tells the
    destination which section to jump to; `tip` shows a hover explanation."""
    from urllib.parse import quote
    from html import escape
    inner = stat_card(label, value, icon_key, color)
    # Carry the auth token so the card-click reload stays logged in.
    href = f"?k={quote(_AUTH_TOKEN)}&goto={quote(goto)}"
    if sec:
        href += f"&sec={quote(sec)}"
    pop = ""
    if tip:
        pop = (f'<span class="tip-pop"><span class="tip-title">{escape(label)}</span>'
               f'{escape(tip)}</span>')
    return (f'<a href="{href}" target="_self" class="tip-wrap" '
            f'style="text-decoration:none;color:inherit;display:block;">{inner}{pop}</a>')


def show_chart(fig):
    """Render a Plotly chart: keep hover tooltips, but disable the floating
    toolbar and all zoom/pan/drag so it's display-only."""
    fig.update_xaxes(fixedrange=True)
    fig.update_yaxes(fixedrange=True)
    fig.update_layout(dragmode=False)
    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False},
    )


def _spark_vals(series, n=10):
    """Last n numeric values from a mom_df column, as a clean float list."""
    try:
        v = pd.to_numeric(series, errors="coerce").dropna().tail(n).tolist()
        return [float(x) for x in v]
    except Exception:
        return []


def sparkline(values, color=ELEC, w=86, h=30):
    """Inline SVG sparkline with a soft gradient fill."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = i / (n - 1) * w
        y = h - (v - lo) / rng * (h - 6) - 3
        pts.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(pts)
    area = f"0,{h} " + poly + f" {w},{h}"
    gid = f"sg{abs(hash(poly)) % 999999}"
    return (
        f'<svg class="mc-spark" width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none">'
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{color}" stop-opacity="0.35"/>'
        f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
        f'<polygon points="{area}" fill="url(#{gid})"/>'
        f'<polyline points="{poly}" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )


def section_header(title, icon_key):
    return (
        f'<div class="section-head"><span class="sh-icon">{ICONS.get(icon_key, "")}</span>'
        f'<span class="sh-title">{title}</span><span class="sh-line"></span></div>'
    )


def metric_card(label, value, sub="", icon_key="", spark="", highlight=False):
    # highlight=True -> purple accent; highlight="green" (or any class string) ->
    # that color variant.
    if highlight:
        cls = "metric-card highlight" + (f" {highlight}" if isinstance(highlight, str) else "")
    else:
        cls = "metric-card"
    icon_html = f'<div class="mc-icon">{ICONS.get(icon_key, "")}</div>' if icon_key else ""
    sub_html = f'<div class="mc-sub">{sub}</div>' if sub else ""
    return (
        f'<div class="{cls}">{icon_html}{spark}'
        f'<div class="mc-value">{value}</div>'
        f'<div class="mc-label">{label}</div>{sub_html}</div>'
    )


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

    from tracker.supplemental import load_supplemental, summarize_supplemental
    from tracker.pastdue import load_health_pastdue

    from tracker.carriers import normalize_carrier_series

    months = load_all_snapshots(Path("snapshots"))
    all_clients = build_all_clients(months)
    if "carrier" in all_clients.columns:
        all_clients["carrier"] = normalize_carrier_series(all_clients["carrier"])
    dashboard_data = build_dashboard_data(months, all_clients)
    _supp = load_supplemental()
    dashboard_data["supp"] = summarize_supplemental(_supp)
    dashboard_data["supp_df"] = _supp
    dashboard_data["health_pastdue_df"] = load_health_pastdue()
    return months, all_clients, dashboard_data


def _read_all_clients_from_sheet(spreadsheet) -> pd.DataFrame:
    """Parse the All Clients tab — skips the 2-row summary header."""
    import re
    all_values = _tab_values("All Clients")
    if len(all_values) < 3:
        return pd.DataFrame()

    # Row 0: Active summary, Row 1: Inactive summary, Row 2: col headers, Row 3+: data
    headers = all_values[2]
    data    = all_values[3:]
    df = pd.DataFrame(data, columns=headers).replace("", None)

    for col in ["effective_date", "term_date", "client_since"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    for col in ["applicant_count", "net_premium", "months_on_book"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _read_daily_tab_from_sheet(spreadsheet, tab_name: str) -> pd.DataFrame:
    """Read a Daily Tracker tab — finds the DATE header row and parses below it."""
    import re
    all_values = _tab_values(tab_name)
    if not all_values:
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


def _read_supplemental_df_from_sheet(spreadsheet) -> pd.DataFrame:
    """Read the Supplemental tab into the normalized roster shape used elsewhere
    (first_name, last_name, carrier, product, premium, status, term_date, ...).
    Empty frame if the tab is missing/empty."""
    cols = ["first_name", "last_name", "carrier", "policy_number", "product", "premium",
            "status", "status_detail", "term_date", "state", "email", "phone"]
    try:
        rows = _tab_records("Supplemental")
    except Exception:
        return pd.DataFrame(columns=cols)
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    if "Carrier" not in df.columns:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "first_name": df.get("First Name"),
        "last_name": df.get("Last Name"),
        "carrier": df.get("Carrier"),
        "policy_number": df.get("Policy Number", pd.Series("", index=df.index)).astype(str).str.strip(),
        "product": df.get("Product"),
        "premium": pd.to_numeric(
            df.get("Monthly Premium", "").astype(str).str.replace(r"[$,]", "", regex=True),
            errors="coerce").fillna(0.0),
        "status": df.get("Status", "").astype(str).str.strip(),
        "status_detail": df.get("Status Detail"),
        "term_date": pd.to_datetime(df.get("Term Date"), errors="coerce"),
        "state": df.get("State"),
        "email": df.get("Email"),
        "phone": df.get("Phone"),
    })
    return out


def _read_supplemental_summary_from_sheet(spreadsheet) -> dict:
    """Per-carrier active-premium summary for the dashboard boxes."""
    from tracker.supplemental import summarize_supplemental
    return summarize_supplemental(_read_supplemental_df_from_sheet(spreadsheet))


def _read_pastdue_df_from_sheet(spreadsheet) -> pd.DataFrame:
    """Read the Health Past Due tab into the normalized roster shape. Empty frame
    if the tab is missing/empty."""
    cols = ["first_name", "last_name", "carrier", "state", "status", "premium", "members",
            "paid_through", "balance", "days_overdue", "reason", "phone", "email"]
    try:
        rows = _tab_records("Health Past Due")
    except Exception:
        return pd.DataFrame(columns=cols)
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    if "Carrier" not in df.columns:
        return pd.DataFrame(columns=cols)
    # Status: use the carrier's real status if the report has written it; otherwise
    # fall back (Ambetter past-due = grace; anything else = generic "Past due").
    if "Status" in df.columns:
        _status = df["Status"]
    else:
        _status = df.get("Carrier").map(lambda c: "Grace period" if str(c) == "Ambetter" else "Past due")
    out = pd.DataFrame({
        "first_name": df.get("First Name"),
        "last_name": df.get("Last Name"),
        "carrier": df.get("Carrier"),
        "state": df.get("State"),
        "status": _status,
        "members": pd.to_numeric(df.get("Members"), errors="coerce").fillna(1).astype(int),
        "premium": pd.to_numeric(
            df.get("Premium", "").astype(str).str.replace(r"[$,]", "", regex=True),
            errors="coerce"),
        "paid_through": pd.to_datetime(df.get("Paid Through"), errors="coerce"),
        "balance": pd.to_numeric(
            df.get("Balance", "").astype(str).str.replace(r"[$,]", "", regex=True),
            errors="coerce"),
        "days_overdue": pd.to_numeric(df.get("Days Overdue"), errors="coerce"),
        "reason": df.get("Reason"),
        "phone": df.get("Phone"),
        "email": df.get("Email"),
    })
    return out


def _supp_name_key(first, last) -> str:
    import re, unicodedata
    s = f"{first} {last}".lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


# How supplemental carriers are labeled in the UI (agent's preferred names).
_SUPP_CARRIER_LABEL = {"UnitedHealthcare": "UHOne", "Allstate": "Allstate"}


def _supp_carrier_label(carrier) -> str:
    c = str(carrier or "").strip()
    return _SUPP_CARRIER_LABEL.get(c, c)


def _attach_supplemental(df: pd.DataFrame, supp_df) -> pd.DataFrame:
    """Add supplemental-coverage columns to a client roster, matched by name:
      _supp_products  comma list of products held (active if any, else lapsed)
      _supp_premium   total active monthly premium (blank if none active)
      _supp_status    Active / Inactive / "" (none)
      _supp_term      latest termination date when inactive
    """
    out = df.copy()
    if supp_df is None or len(supp_df) == 0 or out.empty:
        out["_supp_products"] = ""
        out["_supp_premium"] = pd.NA
        out["_supp_status"] = ""
        out["_supp_term"] = pd.NaT
        return out
    idx: dict = {}
    for r in supp_df.itertuples(index=False):
        key = _supp_name_key(getattr(r, "first_name", ""), getattr(r, "last_name", ""))
        if key:
            idx.setdefault(key, []).append(r)
    prods, prems, stats, terms = [], [], [], []
    for fr, lr in zip(out["first_name"].fillna(""), out["last_name"].fillna("")):
        rows = idx.get(_supp_name_key(fr, lr), [])
        if not rows:
            prods.append(""); prems.append(pd.NA); stats.append(""); terms.append(pd.NaT)
            continue
        def _st(r): return str(getattr(r, "status", "")).strip()
        active = [r for r in rows if _st(r) == "Active"]
        grace  = [r for r in rows if _st(r) == "Grace Period"]
        # Status priority: any clean-active -> Active; else any grace -> Grace
        # Period (in force but behind); else Inactive (lapsed).
        in_force = active + grace
        use = in_force if in_force else rows
        names: list = []
        for r in use:
            c = _supp_carrier_label(getattr(r, "carrier", ""))
            if c and c not in names:
                names.append(c)
        prods.append(", ".join(names))
        if active:
            prems.append(round(sum(float(getattr(r, "premium", 0) or 0) for r in active), 2))
            stats.append("Active")
            terms.append(pd.NaT)
        elif grace:
            prems.append(round(sum(float(getattr(r, "premium", 0) or 0) for r in grace), 2))
            stats.append("Grace Period")
            terms.append(pd.NaT)
        else:
            prems.append(pd.NA)
            stats.append("Inactive")
            tt = [pd.to_datetime(getattr(r, "term_date", None), errors="coerce") for r in rows]
            tt = [t for t in tt if pd.notna(t)]
            terms.append(max(tt) if tt else pd.NaT)
    out["_supp_products"] = prods
    out["_supp_premium"] = prems
    out["_supp_status"] = stats
    out["_supp_term"] = terms
    return out


def _load_from_sheets():
    """Cloud mode: authenticate with service account from st.secrets, read Sheet."""
    import gspread
    from google.oauth2 import service_account
    from tracker.dashboard import _build_mom_from_all_clients
    from tracker.sheets import _patch_retry_on_quota

    _bulk = _main_sheet_values()          # every tab, 1-2 API calls
    spreadsheet = None                     # readers now use the bulk cache
    all_clients = _read_all_clients_from_sheet(spreadsheet)
    if not all_clients.empty and "carrier" in all_clients.columns:
        from tracker.carriers import normalize_carrier_series
        all_clients["carrier"] = normalize_carrier_series(all_clients["carrier"])

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
    for _t in _bulk.keys():
        if _t.startswith("Daily Tracker - "):
            try:
                _snapshot_months.append(
                    pd.Timestamp(_t.replace("Daily Tracker - ", "")).strftime("%Y-%m")
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

    from tracker.supplemental import summarize_supplemental
    _supp_df = _read_supplemental_df_from_sheet(spreadsheet)
    dashboard_data = {"kpis": kpis, "carrier_df": carrier_df, "state_df": state_df, "mom_df": mom_df,
                      "raw_state_carrier_map": _raw_state_carrier_map,
                      "supp": summarize_supplemental(_supp_df), "supp_df": _supp_df,
                      "health_pastdue_df": _read_pastdue_df_from_sheet(spreadsheet)}

    # Read all Daily Tracker tabs dynamically
    daily_months: dict = {}
    for _t in list(_bulk.keys()):
        if _t.startswith("Daily Tracker - "):
            try:
                label = _t.replace("Daily Tracker - ", "")
                ts    = pd.Timestamp(label)
                m_str = ts.strftime("%Y-%m")
                ddf   = _read_daily_tab_from_sheet(spreadsheet, _t)
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


@st.cache_resource
def _gspread_client():
    """Return an authenticated gspread client using st.secrets (cloud mode).
    Retries on 429 (quota exceeded) instead of raising, since this app reads
    Sheets on every page load and can outrun the per-minute read quota."""
    import gspread
    from google.oauth2 import service_account
    from tracker.sheets import _patch_retry_on_quota
    creds = service_account.Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly"],
    )
    client = gspread.authorize(creds)
    _patch_retry_on_quota(client)
    return client


@st.cache_data(ttl=3600, show_spinner="Loading your book (hourly refresh)…")
def _main_sheet_values() -> dict:
    """EVERY tab of the main sheet in 1-2 API calls via values_batch_get.
    Replaces ~25 per-tab reads that tripped Google's per-minute quota and
    made cold page loads crawl (20-60s retry sleeps)."""
    ss = _gspread_client().open_by_url(st.secrets["sheet_url"])
    titles = [w.title for w in ss.worksheets()]
    out = {}
    CH = 40
    for i in range(0, len(titles), CH):
        chunk = titles[i:i + CH]
        resp = ss.values_batch_get([f"'{t}'" for t in chunk])
        for t, vr in zip(chunk, resp.get("valueRanges", [])):
            vals = vr.get("values", [])
            w = max((len(r) for r in vals), default=0)
            out[t] = [r + [""] * (w - len(r)) for r in vals]
    return out


def _tab_values(title: str) -> list:
    """get_all_values() equivalent served from the bulk cache."""
    try:
        return _main_sheet_values().get(title, [])
    except Exception:
        return []


def _tab_records(title: str) -> list:
    """get_all_records() equivalent served from the bulk cache."""
    v = _tab_values(title)
    if len(v) < 2:
        return []
    return [dict(zip(v[0], r)) for r in v[1:]]


@st.cache_data(ttl=3600)
def _load_payments() -> pd.DataFrame:
    """Read the Insurance PAYMENTS sheet into commission line items. Works in
    cloud (service account from secrets) and local (ADC impersonation)."""
    import yaml
    from tracker.commissions import parse_payments_sheet
    url = imp = None
    try:
        cfg = yaml.safe_load(open(Path(__file__).parent / "config" / "settings.yaml"))
        url, imp = cfg.get("payments_sheet_url"), cfg.get("impersonation_target")
    except Exception:
        pass
    try:
        url = st.secrets.get("payments_sheet_url", url)
    except Exception:
        pass
    if not url:
        return pd.DataFrame()
    try:
        if _running_in_cloud():
            ss = _gspread_client().open_by_url(url)
        else:
            from tracker.sheets import _open_sheet
            ss = _open_sheet(url, imp)
        return parse_payments_sheet(ss)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def _load_ambetter_disputes() -> pd.DataFrame:
    """Ambetter policies the carrier's own export confirms you're owed on
    (Eligible for Commission = Yes + member paid-through current) but the payments
    sheet shows no recent payment. Cloud reads the Ambetter Disputes tab the
    report writes; local builds it live from carrier_books/ambetter.csv."""
    cols = ["First Name", "Last Name", "Carrier", "State", "Policy #", "Effective",
            "Paid Through", "Eligible (carrier)", "Last Paid", "Members",
            "Monthly Premium", "Phone", "Email"]
    try:
        if _running_in_cloud():
            rows = _tab_records("Ambetter Disputes")
            return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
        from tracker.carrier_status import (parse_ambetter_export,
                                            classify_ambetter, dispute_display)
        book = Path(__file__).parent / "carrier_books" / "ambetter.csv"
        pay = _load_payments()
        if not book.exists() or pay is None or pay.empty:
            return pd.DataFrame(columns=cols)
        clf = classify_ambetter(parse_ambetter_export(str(book)), pay)
        d = dispute_display(clf)
        return d if d is not None and not d.empty else pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data(ttl=3600)
def _gap_audit_from_sheet() -> dict:
    """name_key -> {Policy #, Ever Paid, Dispute} from the Commission Gaps tab the
    report wrote (policy-verified). Lets the cloud app show the audit columns even
    though it has no carrier_books to compute them live."""
    import re as _re
    def _nk(f, l): return _re.sub(r"[^a-z0-9]", "", (str(l) + str(f)).lower())[:12]
    try:
        v = _tab_values("Commission Gaps")
    except Exception:
        return {}
    hr = next((i for i, row in enumerate(v[:6]) if any(str(c).strip() == "First Name" for c in row)), 0)
    h = v[hr]
    def ix(n): return h.index(n) if n in h else None
    fi, li, pi, ei, di = ix("First Name"), ix("Last Name"), ix("Policy #"), ix("Ever Paid"), ix("Dispute")
    if fi is None or li is None or di is None:
        return {}
    out = {}
    for r in v[hr + 1:]:
        if len(r) <= max(x for x in (fi, li, pi, ei, di) if x is not None):
            continue
        out[_nk(r[fi], r[li])] = {
            "Policy #": r[pi] if pi is not None else "",
            "Ever Paid": r[ei] if ei is not None else "?",
            "Dispute": r[di] if di is not None else "",
        }
    return out


@st.cache_data(ttl=3600)
def _load_aor_defense() -> pd.DataFrame:
    """The AOR-at-risk defense table. Local mode builds it live from the scraped
    data/aor_at_risk.json + latest HealthSherpa export; cloud mode reads the
    'AOR Defense' tab the report wrote."""
    try:
        from tracker.aor_defense import build_aor_defense, _RISK_PATH
        if Path(_RISK_PATH).exists():
            df = build_aor_defense()
            if df is not None and not df.empty:
                return df
    except Exception:
        pass
    try:
        v = _tab_values("AOR Defense")
        if len(v) > 1:
            df = pd.DataFrame(v[1:], columns=v[0])
            for c in ("Members", "Est $/yr"):
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
            if "Days Ago" in df.columns:
                # Unknown taken-dates stay blank (Int64 keeps NaN) — filling 0
                # would float them above genuinely fresh steals in the sort.
                df["Days Ago"] = pd.to_numeric(df["Days Ago"], errors="coerce").astype("Int64")
            return df
    except Exception:
        pass
    return pd.DataFrame()


@st.cache_data(ttl=3600)
def _load_pastdue() -> pd.DataFrame:
    """Active health plans with a premium > $0 that are behind on payment
    (Ambetter paid-through passed / Oscar balance owed). Cloud reads the Health
    Past Due tab the report writes; local builds it from the carrier books."""
    cols = ["first_name", "last_name", "carrier", "state", "premium", "members",
            "paid_through", "balance", "days_overdue", "reason", "phone", "email"]
    try:
        if _running_in_cloud():
            return _read_pastdue_df_from_sheet(None)
        from tracker.pastdue import load_health_pastdue
        df = load_health_pastdue()
        return df if df is not None and not df.empty else pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data(ttl=3600)
def _load_follow_ups() -> pd.DataFrame:
    """HealthSherpa verification follow-ups (DMI/SVI): Open = save the subsidy,
    Expired = lost. Cloud reads the Follow-ups tab; local builds from the export."""
    cols = ["First Name", "Last Name", "Carrier", "State", "Follow-up", "Status",
            "Detail", "Phone", "Email"]
    try:
        if _running_in_cloud():
            rows = _tab_records("Follow-ups")
            return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
        from tracker.report import _build_follow_ups
        src = Path(__file__).parent / "input" / "healthsherpa.csv"
        if not src.exists():
            return pd.DataFrame(columns=cols)
        df = pd.read_csv(src, dtype=str, low_memory=False).fillna("")
        if "agent" in df.columns:
            df = df[df["agent"].str.contains(_AGENT_LN, case=False, na=False)]
        df = df.rename(columns={"issuer": "carrier", "dmi_outstanding_count": "dmi_outstanding",
                                "dmi_expired_count": "dmi_expired", "svi_outstanding_count": "svi_outstanding",
                                "svi_expired_count": "svi_expired"})
        out = _build_follow_ups(df)
        if out is not None and not out.empty and "Carrier" in out.columns:
            from tracker.carriers import normalize_carrier_series
            out["Carrier"] = normalize_carrier_series(out["Carrier"])
        return out if out is not None and not out.empty else pd.DataFrame(columns=cols)
    except Exception:
        return pd.DataFrame(columns=cols)


@st.cache_data(ttl=3600)
def _expired_followup_keys() -> set:
    """name_keys of clients whose verification has EXPIRED — excluded from the
    past-due reach-out lists (subsidy lost; they're handled as Cancelled outreach)."""
    try:
        from tracker.carrier_status import _person_key
        fu = _load_follow_ups()
        if fu is None or fu.empty or "Status" not in fu.columns:
            return set()
        exp = fu[fu["Status"] == "Expired"]
        return set(exp.apply(lambda r: _person_key(r.get("First Name", ""), r.get("Last Name", "")), axis=1))
    except Exception:
        return set()


@st.cache_data(ttl=3600)
def _load_daily_detail() -> pd.DataFrame:
    """Per-policy submission detail (Date, Month, First/Last Name, Members,
    Carrier, State) for the Daily Tracker drill-down. Cloud reads the Daily
    Detail tab; local builds it from the raw snapshots."""
    cols = ["Date", "Month", "First Name", "Last Name", "Members", "Carrier", "State"]
    try:
        if _running_in_cloud():
            rows = _tab_records("Daily Detail")
            return pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
        from tracker.ingest import load_all_snapshots
        from tracker.sheets import _build_daily_detail
        from tracker.report import (_filter_by_appointments, _load_appointments,
                                    _filter_excluded, _load_exclusions)
        months = load_all_snapshots(Path("snapshots"))
        months = {m: _filter_by_appointments(_filter_excluded(d, _load_exclusions()), _load_appointments())
                  for m, d in months.items()}
        return _build_daily_detail(months)
    except Exception:
        return pd.DataFrame(columns=cols)


_AEP_STATUSES   = ["Not Started", "Contacted", "Renewed", "Lost"]
_AEP_COLS       = ["First Name", "Last Name", "State", "Carrier", "Members", "Monthly Premium", "Effective Date", "Status", "Notes"]
_AEP_TAB_PREFIX = "AEP "


def _aep_tab_name(year=None) -> str:
    import datetime as _dt
    y = year or (_dt.date.today().year + 1)
    return f"{_AEP_TAB_PREFIX}{y}"


@st.cache_data(ttl=3600)
def _read_aep_tab(tab_name: str) -> pd.DataFrame:
    """Read the AEP Tracker tab from Google Sheets. Returns empty DF if tab missing."""
    if not _running_in_cloud():
        return pd.DataFrame(columns=_AEP_COLS)
    try:
        import gspread
        client = _gspread_client()
        sheet  = client.open_by_url(st.secrets["sheet_url"])
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            return pd.DataFrame(columns=_AEP_COLS)
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


@st.cache_data(ttl=3600)
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


# Long TTL: navigation stays instant (no Sheets re-fetch). The "Refresh data"
# button clears the cache, so the agent pulls fresh data only when they want it.
@st.cache_data(ttl=3600)
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

    from tracker.sheets import _coalesce_sale_date
    sub = _coalesce_sale_date(df)
    # Option A — new business only: coverage effective after the day sold.
    if "effective_date" in df.columns:
        _eff = pd.to_datetime(df["effective_date"], errors="coerce")
        _is_new = (_eff > sub).fillna(False)
        df = df[_is_new]
        sub = sub[_is_new]
    if sub.isna().all():
        return pd.DataFrame({"Date": all_days, "Policies": 0, "Members": 0})

    sub = sub.dt.normalize()
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
        font=dict(color="#cbd5e1", size=12, family="Inter, sans-serif"),
        margin=dict(t=30, b=40, l=10, r=10),
        xaxis=dict(gridcolor="rgba(96,165,250,0.12)", showgrid=True, zeroline=False),
        yaxis=dict(gridcolor="rgba(96,165,250,0.12)", showgrid=True, zeroline=False),
        hoverlabel=dict(bgcolor="#0f1c34", bordercolor="rgba(96,165,250,0.4)",
                        font=dict(color="#f8fafc", family="Inter, sans-serif")),
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
    st.markdown(
        '<div class="brand-row">'
        '<div class="brand-logo"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
        '<line x1="6" y1="20" x2="6" y2="13"/><line x1="12" y1="20" x2="12" y2="8"/>'
        '<line x1="18" y1="20" x2="18" y2="4"/></svg></div>'
        '<div class="brand-text">Commission Tracker</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # Grouped nav: OVERVIEW / CLIENTS / MONEY / WORKFLOWS / settings — group
    # headers + dividers are painted by CSS in _nav_icon_css (nth-of-type), so
    # this order, the icon list, and the header positions MUST stay in sync.
    _NAV = ["Dashboard", "Daily Tracker", "Goals",
            "Client Lookup", "Book", "Monthly Trends",
            "Commissions", "Money Owed", "Past Due",
            "AOR Defense", "Follow-ups", "Re-Engage", "Supplemental Re-Engage", "AEP Tracker",
            "Settings"]
    # Old page names still arrive via bookmarks / stale ?goto= links.
    _ALIASES = {"Month-over-Month": "Monthly Trends", "Book of Business": "Book",
                "Re-Engage (Supp)": "Supplemental Re-Engage"}
    # Deep-link: a "?goto=Page" link (e.g. a Dashboard action card) selects that page.
    _goto = st.query_params.get("goto")
    _goto = _ALIASES.get(_goto, _goto)
    if _goto in _NAV:
        st.session_state["nav"] = _goto
        del st.query_params["goto"]
    if st.session_state.get("nav") in _ALIASES:
        st.session_state["nav"] = _ALIASES[st.session_state["nav"]]

    # Attention badges — counts shown on the tabs that need action today.
    # Loaders are @st.cache_data so this costs nothing on rerun.
    _badges = {}
    try:
        _bp = _load_pastdue()
        _badges["Past Due"] = 0 if _bp is None or _bp.empty else len(_bp)
    except Exception:
        pass
    try:
        _bf = _load_follow_ups()
        _badges["Follow-ups"] = (int((_bf["Status"].astype(str) == "Open").sum())
                                 if _bf is not None and not _bf.empty else 0)
    except Exception:
        pass
    try:
        _ba = _load_aor_defense()
        if _ba is not None and not _ba.empty and "Type" in _ba.columns:
            _badges["AOR Defense"] = int(((_ba["Type"] == "Taken")
                                          & (_ba["Handled"].fillna("") == "")).sum())
    except Exception:
        pass

    # Display-only renames (internal routing keys stay the same so badges,
    # deep links and page code are untouched).
    _NAV_LABELS = {"Follow-ups": "Verifications"}
    def _nav_label(p):
        _l = _NAV_LABELS.get(p, p)
        return f"{_l}  ·  {_badges[p]}" if _badges.get(p) else _l
    page = st.radio(
        "Navigation",
        _NAV,
        label_visibility="collapsed",
        key="nav",
        format_func=_nav_label,
    )

    # Compact mode: icons-only rail for more content width.
    _compact = st.session_state.get("nav_compact", False)
    st.markdown("""<style>
      section[data-testid="stSidebar"] .st-key-nav_compact_btn button {
        background: rgba(19,31,58,.6) !important; color: #60a5fa !important;
        box-shadow: none !important; border: 1px solid rgba(96,165,250,.14) !important;
        font-weight: 600;
      }
      section[data-testid="stSidebar"] .st-key-nav_compact_btn button:hover {
        border-color: rgba(96,165,250,.4) !important; transform: none;
      }
    </style>""", unsafe_allow_html=True)
    if st.button("⇥  Expand" if _compact else "⇤  Compact", key="nav_compact_btn",
                 use_container_width=True, help="Collapse the sidebar to icons only"):
        st.session_state["nav_compact"] = not _compact
        st.rerun()
    if _compact:
        st.markdown("""<style>
          section[data-testid="stSidebar"] {width:92px !important; min-width:92px !important; max-width:92px !important;}
          section[data-testid="stSidebar"] .brand-text {display:none;}
          section[data-testid="stSidebar"] div[role="radiogroup"] > label p {display:none;}
          section[data-testid="stSidebar"] div[role="radiogroup"] > label {justify-content:center; padding:11px 6px;}
          section[data-testid="stSidebar"] div[role="radiogroup"] > label::after {display:none !important;}
          section[data-testid="stSidebar"] div[data-testid="stCaptionContainer"] {display:none;}
          section[data-testid="stSidebar"] .sb-foot {display:none;}
          section[data-testid="stSidebar"] div[data-testid="stDownloadButton"] {display:none;}
          section[data-testid="stSidebar"] .st-key-refresh_btn {display:none;}
          section[data-testid="stSidebar"] .st-key-nav_compact_btn button {padding:4px 2px; font-size:.8rem;}
        </style>""", unsafe_allow_html=True)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sb-foot"><div class="tile">📅</div>'
        f'<div class="txt">Latest snapshot: <b>{latest_label}</b></div></div>'
        f'<div class="sb-foot"><div class="tile">👥</div>'
        f'<div class="txt"><b>{len(all_clients):,}</b> total clients tracked</div></div>',
        unsafe_allow_html=True,
    )

    if st.button("🔄 Refresh data", use_container_width=True, key="refresh_btn"):
        st.cache_data.clear()
        for _k in ["aep_df", "aep_tab"]:
            st.session_state.pop(_k, None)
        st.rerun()

    # Export Report button removed on purpose (Ethan 2026-07-07): no data
    # export from the site — tables also have their download toolbars hidden.


# ══════════════════════════════════════════════════════════════════════════════
# OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════
# ── Global client finder — every page, top-right under the toolbar ───────────
def _jump_to_client():
    _v = st.session_state.get("global_lookup")
    if _v:
        st.session_state["nav"] = "Client Lookup"      # switch pages
        st.session_state["lookup_select"] = _v         # preselect the client
        st.session_state["global_lookup"] = None       # reset the finder

_gl_names = sorted({p for p in ((all_clients["first_name"].fillna("").astype(str).str.title().str.strip()
                                 + " " +
                                 all_clients["last_name"].fillna("").astype(str).str.title().str.strip())
                                .str.strip()) if p})
if page != "Client Lookup":   # the lookup page has its own big search box
    _gl_sp, _gl_box = st.columns([5, 2])
    with _gl_box:
        with st.container(key="global_finder"):
            st.selectbox("Find client", _gl_names, index=None, key="global_lookup",
                         on_change=_jump_to_client, label_visibility="collapsed",
                         placeholder="Find a client…")
    st.markdown("""<style>
      /* pill control with an inset magnifier, soft glow, focus ring */
      .st-key-global_finder div[data-baseweb="select"] > div {
          font-size:.92rem; border-radius:13px; min-height:44px;
          background-color:rgba(12,20,38,.9);
          border:1px solid rgba(96,165,250,.26);
          box-shadow:0 6px 20px rgba(2,8,20,.45);
          padding-left:38px;
          background-image:url("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%237f93b3' stroke-width='2.2' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><line x1='21' y1='21' x2='16.4' y2='16.4'/></svg>");
          background-repeat:no-repeat; background-position:13px center; background-size:17px;
          transition:border-color .15s ease, box-shadow .15s ease;
      }
      .st-key-global_finder div[data-baseweb="select"] > div:focus-within {
          border-color:rgba(96,165,250,.65);
          box-shadow:0 0 0 3px rgba(59,130,246,.16), 0 6px 20px rgba(2,8,20,.45);
      }
      .st-key-global_finder div[data-baseweb="select"] input {font-size:.92rem;}
      .st-key-global_finder [data-baseweb="select"] [data-baseweb="placeholder"],
      .st-key-global_finder div[data-baseweb="select"] > div > div {color:#8aa0c2;}
      .st-key-global_finder div[data-baseweb="select"] svg {width:18px;height:18px;color:#7f93b3;}
      /* dropdown menu: dark navy panel, rounded, blue hover */
      div[data-baseweb="popover"] ul[role="listbox"] {
          background:#0d1628; border:1px solid rgba(96,165,250,.22);
          border-radius:13px; padding:6px;
          box-shadow:0 18px 44px rgba(2,8,20,.65);
      }
      div[data-baseweb="popover"] li[role="option"] {
          font-size:.9rem; color:#dbe4f3; border-radius:9px; padding-top:9px; padding-bottom:9px;
      }
      div[data-baseweb="popover"] li[role="option"]:hover,
      div[data-baseweb="popover"] li[aria-selected="true"] {
          background:rgba(59,130,246,.2) !important; color:#fff;
      }
    </style>""", unsafe_allow_html=True)

if page == "Dashboard":
    # ── Header ────────────────────────────────────────────────────────────────
    _cal_svg = ('<svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/>'
                '<line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/>'
                '<line x1="3" y1="10" x2="21" y2="10"/></svg>')
    st.markdown(
        '<div class="dash-hero">'
          '<div class="dash-hero-left">'
            '<div class="dash-accent"></div>'
            '<div><div class="dash-title">Dashboard</div>'
            f'<div class="dash-sub"><span class="live-dot"></span>{latest_label} Snapshot · Live</div></div>'
          '</div>'
          f'<div class="date-badge">{_cal_svg}{latest_label}</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── YOUR MONEY (headline) ─────────────────────────────────────────────────
    st.markdown(color_legend(), unsafe_allow_html=True)
    st.markdown(section_header("Your Money", "dollar"), unsafe_allow_html=True)
    _net_mo = _ytd = _avg_mo = 0.0
    _mdelta = None
    _mdelta_good = True
    _mlabel = "This Month"
    try:
        from tracker.commissions import monthly_summary
        _ms = monthly_summary(_load_payments())
        if _ms is not None and not _ms.empty:
            _ms = _ms.copy()
            _ms["_m"] = pd.to_datetime(_ms["Month"], errors="coerce")
            _ms = _ms.sort_values("_m")
            _last = _ms.iloc[-1]
            _net_mo = float(_last["Net"])
            _mlabel = _last["_m"].strftime("%b %Y") if pd.notna(_last["_m"]) else "This Month"
            _yr = _last["_m"].year if pd.notna(_last["_m"]) else None
            _ytd = float(_ms[_ms["_m"].dt.year == _yr]["Net"].sum()) if _yr else float(_ms["Net"].sum())
            _avg_mo = float(_ms["Net"].mean())
            if len(_ms) >= 2:
                _prev = float(_ms.iloc[-2]["Net"])
                if _prev:
                    _p = (_net_mo - _prev) / abs(_prev) * 100
                    _mdelta = f"{'▲' if _p >= 0 else '▼'} {abs(_p):.0f}% vs last month"
                    _mdelta_good = _p >= 0
    except Exception:
        pass
    mc1, mc2, mc3 = st.columns(3)
    with mc1:
        st.markdown(stat_card(f"{_mlabel} Net", f"${_net_mo:,.0f}", "dollar", GREEN,
                              delta=_mdelta, delta_good=_mdelta_good), unsafe_allow_html=True)
    with mc2:
        st.markdown(stat_card("Year-to-Date", f"${_ytd:,.0f}", "calendar", ELEC), unsafe_allow_html=True)
    with mc3:
        st.markdown(stat_card("Avg / Month", f"${_avg_mo:,.0f}", "trend", CYAN), unsafe_allow_html=True)

    # ── WHAT TO DO TODAY ──────────────────────────────────────────────────────
    st.markdown(section_header("What To Do Today", "clock"), unsafe_allow_html=True)
    try:
        _pdd = _load_pastdue()
    except Exception:
        _pdd = None
    _pd_n = 0 if _pdd is None or _pdd.empty else len(_pdd)
    try:
        _fu = _load_follow_ups()
        _fu_open = int((_fu["Status"].astype(str) == "Open").sum()) if _fu is not None and not _fu.empty else 0
    except Exception:
        _fu_open = 0
    try:
        _disp = _load_ambetter_disputes()
        _disp_n = 0 if _disp is None or _disp.empty else len(_disp)
    except Exception:
        _disp_n = 0
    _c1 = link_card("Past-Due to Call", f"{_pd_n:,}", "clock", RED, "Past Due",
                    tip="Clients still active but behind on their premium. Call them to update payment "
                        "before the carrier cancels them for non-payment — if they lapse, you lose the commission.")
    _c2 = link_card("Follow-ups Open", f"{_fu_open:,}", "shield", GOLD, "Follow-ups",
                    tip="HealthSherpa verifications (income/coverage or enrollment) your clients still owe. "
                        "If one expires, the client loses their subsidy and usually drops — reach out before the deadline.")
    _c3 = link_card("Ambetter Disputes", f"{_disp_n:,}", "minus", GOLD, "Money Owed", sec="disputes",
                    tip="Policies Ambetter's own export confirms you're the broker for, but you haven't been "
                        "paid on. Take these to your commissions team to get paid what you're owed.")
    # Render all three in one block (not st.columns) so the hover popup isn't clipped.
    st.markdown(
        '<div style="display:flex;gap:18px;flex-wrap:wrap;overflow:visible;margin-bottom:6px;">'
        + "".join(f'<div style="flex:1 1 200px;position:relative;overflow:visible;">{c}</div>'
                  for c in (_c1, _c2, _c3))
        + "</div>",
        unsafe_allow_html=True)
    st.caption("👆 Hover (or tap on phone) a box for what it means · on phone, tap once to read it, "
               "tap again to open it — Past-Due & Disputes open **Money Owed**, Follow-ups opens **Follow-ups**.")
    # On touch devices (iPhone), first tap reveals the explanation; a second tap follows the link.
    import streamlit.components.v1 as _c
    _c.html(
        "<script>"
        "const d=window.parent.document,w=window.parent;"
        "const touch=('ontouchstart' in w)||(w.navigator&&w.navigator.maxTouchPoints>0);"
        "function wire(){if(!touch)return;"
        "d.querySelectorAll('a.tip-wrap').forEach(function(a){"
        "if(a.dataset.tapwired)return;a.dataset.tapwired='1';"
        "a.addEventListener('click',function(e){"
        "if(a.dataset.armed==='1')return;"
        "e.preventDefault();"
        "d.querySelectorAll('a.tip-wrap').forEach(function(o){o.dataset.armed='';o.classList.remove('tip-show');});"
        "a.classList.add('tip-show');a.dataset.armed='1';"
        "setTimeout(function(){a.dataset.armed='';a.classList.remove('tip-show');},4000);"
        "});});}"
        "wire();new MutationObserver(wire).observe(d.body,{childList:true,subtree:true});"
        "</script>", height=0)

    # ── BOOK SNAPSHOT ─────────────────────────────────────────────────────────
    st.markdown(section_header("Book Snapshot", "book"), unsafe_allow_html=True)
    _ap = kpis["Total Active Policies"]
    _tm = kpis["Total Members"]
    _avg_sz = round(_tm / _ap, 1) if _ap else "—"
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(metric_card("Total Active Policies", f"{_ap:,}", icon_key="shield",
                                spark=sparkline(_spark_vals(mom_df["Total Policies"]) if "Total Policies" in mom_df.columns else [])),
                    unsafe_allow_html=True)
    with c2:
        st.markdown(metric_card("Total Members", f"{_tm:,}", icon_key="users",
                                spark=sparkline(_spark_vals(mom_df["Total Members"]) if "Total Members" in mom_df.columns else [], color=CYAN)),
                    unsafe_allow_html=True)
    with c3:
        st.markdown(metric_card("Avg Household Size", _avg_sz, icon_key="home"), unsafe_allow_html=True)

    # ── GROWTH METRICS ────────────────────────────────────────────────────────
    st.markdown(section_header("Growth Metrics", "trend"), unsafe_allow_html=True)
    # Proper churn rate = total members lost / total active member-months (same
    # basis as the LTV calc), so the dashboard and Goals page always agree.
    try:
        if not mom_df.empty and "Members Lost" in mom_df.columns and mom_df["Total Members"].sum() > 0:
            _churn_pct = round(mom_df["Members Lost"].sum() / mom_df["Total Members"].sum() * 100, 2)
        else:
            _churn_pct = round(float(kpis["Avg Policies Lost/Month"]) / max(kpis["Total Active Policies"], 1) * 100, 2)
        _churn_sub = f"All history • {_churn_pct}% monthly churn"
    except Exception:
        _churn_sub = "All history"
    try:
        net = round(float(kpis["Avg Policies Added/Month"]) - float(kpis["Avg Policies Lost/Month"]), 1)
        net_str = f"+{net}" if net >= 0 else str(net)
    except Exception:
        net_str = "N/A"
    g1, g2, g3 = st.columns(3)
    with g1:
        st.markdown(metric_card("Avg Policies Added / Month", kpis["Avg Policies Added/Month"], sub="Feb 2026 – present",
                                icon_key="plus", spark=sparkline(_spark_vals(mom_df["New Policies"]) if "New Policies" in mom_df.columns else [], color=GREEN)),
                    unsafe_allow_html=True)
    with g2:
        st.markdown(metric_card("Avg Policies Lost / Month", kpis["Avg Policies Lost/Month"], sub=_churn_sub,
                                icon_key="minus", spark=sparkline(_spark_vals(mom_df["Policies Lost"]) if "Policies Lost" in mom_df.columns else [], color=RED)),
                    unsafe_allow_html=True)
    with g3:
        st.markdown(metric_card("Avg Net Growth / Month", net_str, sub="Added (Feb+) minus Lost (all-time)",
                                icon_key="trend", spark=sparkline(_spark_vals(mom_df["Net Change"]) if "Net Change" in mom_df.columns else [], color=ELEC)),
                    unsafe_allow_html=True)

    # ── MEMBER GROWTH ─────────────────────────────────────────────────────────
    # Same metrics at the member (covered-lives) level rather than per policy.
    st.markdown(section_header("Member Growth", "trend"), unsafe_allow_html=True)
    try:
        net_mem = round(float(kpis["Avg Members Added/Month"]) - float(kpis["Avg Members Lost/Month"]), 1)
        net_mem_str = f"+{net_mem}" if net_mem >= 0 else str(net_mem)
    except Exception:
        net_mem_str = "N/A"
    if {"New Members", "Members Lost"}.issubset(mom_df.columns):
        _net_mem_series = (mom_df["New Members"] - mom_df["Members Lost"])
    else:
        _net_mem_series = pd.Series(dtype=float)
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(metric_card("Avg Members Added / Month", kpis["Avg Members Added/Month"], sub="Feb 2026 – present",
                                icon_key="plus", spark=sparkline(_spark_vals(mom_df["New Members"]) if "New Members" in mom_df.columns else [], color=GREEN)),
                    unsafe_allow_html=True)
    with m2:
        st.markdown(metric_card("Avg Members Lost / Month", kpis["Avg Members Lost/Month"], sub="All history",
                                icon_key="minus", spark=sparkline(_spark_vals(mom_df["Members Lost"]) if "Members Lost" in mom_df.columns else [], color=RED)),
                    unsafe_allow_html=True)
    with m3:
        st.markdown(metric_card("Net Members Gained / Month", net_mem_str, sub="Added (Feb+) minus Lost (all-time)",
                                icon_key="trend", spark=sparkline(_spark_vals(_net_mem_series), color=ELEC)),
                    unsafe_allow_html=True)

    # ── COMMISSION FORECAST ───────────────────────────────────────────────────
    # Use the SAME member/policy counts shown in the Book Snapshot cards so the
    # commission always equals $PMPM × the displayed Total Members.
    _ACTIVE_STS = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    _active_mask = all_clients["status"].isin(_ACTIVE_STS) if "status" in all_clients.columns else pd.Series(False, index=all_clients.index)
    _PMPM = 23
    _total_members = int(kpis.get("Total Members", 0) or 0)
    _active_policies = int(kpis.get("Total Active Policies", 0) or 0)
    _mrr = _total_members * _PMPM
    _arr = _mrr * 12
    _today = pd.Timestamp(dt.date.today())

    _per_policy = f"${_mrr / _active_policies:.2f}" if _active_policies else "—"
    _mem_spark = sparkline(_spark_vals(mom_df["Total Members"]) if "Total Members" in mom_df.columns else [], color="#c4b5fd")

    st.markdown(section_header("Commission Forecast", "dollar"), unsafe_allow_html=True)
    r1, r2, r3 = st.columns(3)
    with r1:
        st.markdown(metric_card("Expected Monthly Commission", f"${_mrr:,.0f}", icon_key="dollar",
                                spark=_mem_spark, highlight="green"), unsafe_allow_html=True)
    with r2:
        st.markdown(metric_card("Expected Annual Commission", f"${_arr:,.0f}", icon_key="calendar"), unsafe_allow_html=True)
    with r3:
        st.markdown(metric_card("Commission per Policy / Mo", _per_policy, icon_key="file"), unsafe_allow_html=True)

    # ── SUPPLEMENTAL PREMIUM ──────────────────────────────────────────────────
    # Dental / vision / STM / accident etc., split by carrier so each carrier's
    # contribution is visible. Premium only for now — commission once rates known.
    _supp = dd.get("supp") or {}
    if _supp:
        st.markdown(section_header("Supplemental Premium (Active)", "dollar"), unsafe_allow_html=True)
        # Stable order: largest active premium first.
        _supp_order = sorted(_supp.items(), key=lambda kv: kv[1].get("active_premium", 0), reverse=True)
        _supp_cols = st.columns(max(len(_supp_order), 1))
        for _col, (_carrier, _info) in zip(_supp_cols, _supp_order):
            _prem = _info.get("active_premium", 0.0)
            _np = _info.get("active_policies", 0)
            _ni = _info.get("inactive_policies", 0)
            with _col:
                st.markdown(metric_card(
                    f"{_supp_carrier_label(_carrier)} — Monthly Premium",
                    f"${_prem:,.0f}",
                    sub=f"{_np} active policies · {_ni} lapsed · ${_prem*12:,.0f}/yr",
                    icon_key="dollar"), unsafe_allow_html=True)
        st.caption("Premium shown — commission will appear once per-carrier comp rates are added.")

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Book age — months on book (glass panel) ───────────────────────────────
    if "months_on_book" in all_clients.columns or "effective_date" in all_clients.columns:
        _mob_src = all_clients.loc[_active_mask].copy()
        if "months_on_book" not in _mob_src.columns:
            _mob_src["months_on_book"] = None
        if "effective_date" in _mob_src.columns:
            _eff = pd.to_datetime(_mob_src["effective_date"], errors="coerce")
            _derived = ((_today - _eff).dt.days / 30.44).round(1)
            _mob_src["months_on_book"] = _mob_src["months_on_book"].fillna(_derived)
        _mob = _mob_src["months_on_book"].fillna(0)  # any remaining nulls → < 3 mo bucket

        _buckets = {
            "< 3 MO":   int((_mob < 3).sum()),
            "3–6 MO":   int(((_mob >= 3) & (_mob < 6)).sum()),
            "6–12 MO":  int(((_mob >= 6) & (_mob < 12)).sum()),
            "12–18 MO": int(((_mob >= 12) & (_mob < 18)).sum()),
            "18 MO+":   int((_mob >= 18).sum()),
        }
        _total_active_p = sum(_buckets.values())
        _bucket_colors = [RED, GOLD, BLUE, PURPLE, GREEN]

        with st.container(border=True):
            st.markdown(chart_head("Book age — months on book",
                                   "Active policies grouped by tenure", "calendar"),
                        unsafe_allow_html=True)

            cols = st.columns(5)
            for col, (label, count), color in zip(cols, _buckets.items(), _bucket_colors):
                pct = round(count / _total_active_p * 100) if _total_active_p else 0
                clock = (f'<svg viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
                         f'stroke-linecap="round" stroke-linejoin="round">'
                         f'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>')
                with col:
                    st.markdown(
                        f'<div class="ba-card">'
                        f'<div class="ba-bar" style="background:linear-gradient(90deg,{color},rgba(0,0,0,0));"></div>'
                        f'<div class="ba-icon" style="background:{color}22;border:1px solid {color}55;">{clock}</div>'
                        f'<div class="ba-val">{count:,}</div>'
                        f'<div class="ba-lbl">{label}</div>'
                        f'<div class="ba-pct" style="color:{color};">{pct}%</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            _mob_chart = pd.DataFrame({"Bucket": list(_buckets.keys()), "Policies": list(_buckets.values())})
            fig_mob = px.bar(_mob_chart, x="Bucket", y="Policies", text="Policies")
            fig_mob.update_traces(
                marker_color=_bucket_colors, marker_cornerradius=8,
                textposition="outside", textfont=dict(size=13, color="#e2e8f0"),
                hovertemplate="%{x}: %{y} policies<extra></extra>",
            )
            _mob_max = max(_buckets.values()) if any(_buckets.values()) else 1
            fig_mob.update_layout(**_chart_layout(
                showlegend=False,
                xaxis=dict(gridcolor="rgba(0,0,0,0)", showgrid=False, zeroline=False, tickfont=dict(size=12)),
                yaxis=dict(title="Policies", gridcolor="rgba(96,165,250,0.10)", showgrid=True, zeroline=False,
                           range=[0, _mob_max * 1.2]),
                margin=dict(t=16, b=20, l=10, r=10), height=300, bargap=0.45,
            ))
            show_chart(fig_mob)

            _new_pct = round((_buckets["< 3 MO"] + _buckets["3–6 MO"]) / _total_active_p * 100) if _total_active_p else 0
            _vet_pct = round(_buckets["18 MO+"] / _total_active_p * 100) if _total_active_p else 0
            st.markdown(
                f'<div class="insight"><div class="in-icon">{ICONS["info"]}</div>'
                f'<div><div class="in-main">{_new_pct}% of your book is under 6 months old (higher AEP risk)</div>'
                f'<div class="in-sub">{_vet_pct}% has been with you 18+ months (most loyal clients)</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Carrier + State charts (side-by-side glass cards) ─────────────────────
    col_a, col_b = st.columns(2)

    with col_a:
        with st.container(border=True):
            st.markdown(chart_head("Policies by Carrier",
                                   "Carrier distribution across active policies", "pie"),
                        unsafe_allow_html=True)
            if not carrier_df.empty:
                fig = px.pie(
                    carrier_df, names="Carrier", values="Policies", hole=0.55,
                    color_discrete_sequence=[
                        BLUE, ELEC, "#2d5fa6", GREEN, GOLD,
                        CYAN, "#f97316", PURPLE, "#e84393", "#94a3b8",
                    ],
                )
                fig.update_traces(
                    textposition="inside", textinfo="percent",
                    insidetextorientation="horizontal", textfont_size=12,
                    marker=dict(line=dict(color="#0a1326", width=2)),
                    hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
                )
                fig.update_layout(**_chart_layout(
                    uniformtext_minsize=11, uniformtext_mode="hide",
                    legend=dict(orientation="h", yanchor="top", y=-0.03,
                                xanchor="center", x=0.5, font=dict(size=11)),
                    margin=dict(t=10, b=10, l=10, r=10), height=440,
                ))
                show_chart(fig)

    with col_b:
        with st.container(border=True):
            st.markdown(chart_head("Policies by State (Top 15)",
                                   "Top 15 states by active policy count", "pin"),
                        unsafe_allow_html=True)
            if not state_df.empty:
                top_states = state_df.sort_values("Policies", ascending=False).head(15)
                fig2 = px.bar(
                    top_states.sort_values("Policies"),
                    x="Policies", y="State", orientation="h",
                    color="Policies", color_continuous_scale=[[0, "#1b2c4d"], [1, BLUE]],
                    text="Policies",
                )
                fig2.update_traces(
                    marker_cornerradius=5, textposition="outside",
                    textfont=dict(size=11, color="#cbd5e1"),
                    hovertemplate="%{y}: %{x} policies<extra></extra>",
                )
                fig2.update_layout(**_chart_layout(
                    coloraxis_showscale=False,
                    xaxis=dict(title="Policies", gridcolor="rgba(96,165,250,0.10)", showgrid=True, zeroline=False),
                    yaxis=dict(gridcolor="rgba(0,0,0,0)", tickfont=dict(size=11)),
                    margin=dict(t=6, b=20, l=50, r=44), height=370,
                ))
                show_chart(fig2)


# ══════════════════════════════════════════════════════════════════════════════
# MONTH-OVER-MONTH
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Monthly Trends":
    st.title("Month-over-Month Trends")
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    if mom_df.empty:
        st.info("No month-over-month data available yet.")
    else:
        mom_plot = mom_df.copy()
        mom_plot["Month Label"] = mom_plot["Month"].apply(
            lambda m: pd.Timestamp(str(m) + "-01").strftime("%b %Y")
        )

        # ── Total members over time (glass card, area chart) ──────────────────
        with st.container(border=True):
            st.markdown(chart_head("Total Active Members Over Time",
                                   "Cumulative active members by month", "trend"),
                        unsafe_allow_html=True)
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=mom_plot["Month Label"], y=mom_plot["Total Members"],
                mode="lines+markers+text",
                text=mom_plot["Total Members"], textposition="top center",
                textfont=dict(size=11, color="#e2e8f0"),
                line=dict(color=ELEC, width=3, shape="spline"),
                marker=dict(size=8, color=BLUE, line=dict(width=2, color="#dbeafe")),
                fill="tozeroy", fillcolor="rgba(59,130,246,0.15)",
                hovertemplate="%{x}: %{y} members<extra></extra>",
            ))
            _n_mom = len(mom_plot)
            fig.update_layout(**_chart_layout(
                showlegend=False, height=360,
                xaxis=dict(gridcolor="rgba(96,165,250,0.06)", showgrid=False, zeroline=False,
                           range=[-0.6, _n_mom - 0.2], automargin=True),
                yaxis=dict(title="Members", gridcolor="rgba(96,165,250,0.10)", showgrid=True,
                           zeroline=False, automargin=True),
                margin=dict(t=24, b=30, l=10, r=70),
            ))
            show_chart(fig)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── New vs Lost (two glass cards) ─────────────────────────────────────
        col_l, col_r = st.columns(2)

        def _new_vs_lost(added_col, lost_col):
            f = go.Figure()
            f.add_trace(go.Bar(x=mom_plot["Month Label"], y=mom_plot[added_col], name="Added", marker_color=GREEN))
            f.add_trace(go.Bar(x=mom_plot["Month Label"], y=mom_plot[lost_col], name="Lost", marker_color=RED))
            f.update_traces(marker_cornerradius=3,
                            hovertemplate="%{x}: %{y}<extra></extra>")
            f.update_layout(**_chart_layout(
                barmode="group", bargap=0.3,
                legend=dict(orientation="h", yanchor="bottom", y=1.03, x=0,
                            bgcolor="rgba(0,0,0,0)", font=dict(size=12)),
                height=360, margin=dict(t=34, b=44, l=10, r=10),
                xaxis=dict(gridcolor="rgba(0,0,0,0)", showgrid=False, zeroline=False,
                           tickangle=-45, tickfont=dict(size=10), automargin=True),
                yaxis=dict(gridcolor="rgba(96,165,250,0.10)", showgrid=True, zeroline=False, automargin=True),
            ))
            return f

        with col_l:
            with st.container(border=True):
                st.markdown(chart_head("New vs. Lost Policies",
                                       "Policies added vs. lost each month", "bars"),
                            unsafe_allow_html=True)
                show_chart(_new_vs_lost("New Policies", "Policies Lost"))
        with col_r:
            with st.container(border=True):
                st.markdown(chart_head("New vs. Lost Members",
                                       "Members added vs. lost each month", "bars"),
                            unsafe_allow_html=True)
                show_chart(_new_vs_lost("New Members", "Members Lost"))

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Full trend table (glass card) ─────────────────────────────────────
        with st.container(border=True):
            st.markdown(chart_head("Full Trend Table", "Month-by-month detail", "calendar"),
                        unsafe_allow_html=True)
            disp = mom_plot.drop(columns=["Month"]).rename(columns={"Month Label": "Month"})
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

    # ── All-time personal bests (best day / week / month, policies & members) ──
    _det_all = _load_daily_detail()
    if _det_all is not None and not _det_all.empty:
        _d = _det_all.copy()
        # New business only — exclude OEP renewals so records reflect real selling.
        if "Is New" in _d.columns:
            _d = _d[_d["Is New"].astype(str).str.strip().str.lower().isin(["yes", "true", "1"])]
        _d["_dt"] = pd.to_datetime(_d["Date"], errors="coerce")
        _d["_mem"] = pd.to_numeric(_d["Members"], errors="coerce").fillna(1)
        _d = _d.dropna(subset=["_dt"])

        def _record(grouper, fmt):
            g = _d.groupby(grouper).agg(pol=("_mem", "size"), mem=("_mem", "sum"))
            if g.empty:
                return None
            bp, bm = g["pol"].idxmax(), g["mem"].idxmax()
            return dict(pol=int(g.loc[bp, "pol"]), pol_when=fmt(bp),
                        mem=int(g.loc[bm, "mem"]), mem_when=fmt(bm))

        _day = _record(_d["_dt"].dt.date, lambda k: pd.Timestamp(k).strftime("%b %-d, %Y"))
        _week = _record(_d["_dt"].dt.to_period("W"), lambda k: "week of " + k.start_time.strftime("%b %-d, %Y"))
        _month = _record(_d["_dt"].dt.to_period("M"), lambda k: k.strftime("%B %Y"))

        st.markdown(section_header("🏆 Personal Bests — New Business (All Time)", "trend"), unsafe_allow_html=True)
        _rc = st.columns(3)
        for _col, _title, _rec in zip(_rc, ["Best Day", "Best Week", "Best Month"], [_day, _week, _month]):
            with _col:
                with st.container(border=True):
                    st.markdown(f"<div style='font-size:.72rem;letter-spacing:.09em;color:#94a3b8;"
                                f"text-transform:uppercase;font-weight:700'>{_title}</div>", unsafe_allow_html=True)
                    if _rec:
                        st.markdown(f"<div style='font-size:1.9rem;font-weight:800;color:#fff;line-height:1.1;margin-top:6px'>"
                                    f"{_rec['pol']} <span style='font-size:.85rem;color:#22c55e;font-weight:700'>policies</span></div>"
                                    f"<div style='font-size:.78rem;color:#94a3b8'>{_rec['pol_when']}</div>"
                                    f"<div style='font-size:1.5rem;font-weight:800;color:#fff;line-height:1.1;margin-top:10px'>"
                                    f"{_rec['mem']} <span style='font-size:.85rem;color:#60a5fa;font-weight:700'>members</span></div>"
                                    f"<div style='font-size:.78rem;color:#94a3b8'>{_rec['mem_when']}</div>",
                                    unsafe_allow_html=True)
                    else:
                        st.markdown("—")
        st.markdown("<br>", unsafe_allow_html=True)

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

    # ── KPI row (icon stat-cards) ─────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(stat_card("Total Policies Submitted", f"{total_pol:,}", "file", BLUE), unsafe_allow_html=True)
    with k2:
        st.markdown(stat_card("Total Heads Sold", f"{total_heads:,}", "users", ELEC), unsafe_allow_html=True)
    with k3:
        st.markdown(stat_card(f"Daily Avg ({days_elapsed} days elapsed)", daily_avg, "trend", CYAN), unsafe_allow_html=True)
    with k4:
        st.markdown(stat_card(f"Days with Activity ({pct_month}% of {dim})", days_active, "calendar", PURPLE), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Chart + table (glass cards) ───────────────────────────────────────────
    col_chart, col_table = st.columns([3, 2])

    with col_chart:
        with st.container(border=True):
            st.markdown(chart_head("Submissions by Day", "Policies submitted per day this month", "bars"),
                        unsafe_allow_html=True)
            chart_df = daily_df.copy()
            chart_df["Day"] = chart_df["Date"].dt.strftime("%b %d")
            max_pol = max(int(daily_df["Policies"].max()), 1)
            # Single trace, per-bar color (gold = best day) so bars stay in date
            # order. Using px color= would split into separate traces and push the
            # gold bars to the end of the axis.
            _day_order = chart_df["Day"].tolist()
            _bar_colors = [GOLD if int(p) == max_pol else GREEN for p in chart_df["Policies"]]

            fig = px.bar(chart_df, x="Day", y="Policies", text="Policies")
            fig.update_traces(marker_color=_bar_colors, marker_cornerradius=4,
                              textposition="outside", textfont_size=9,
                              hovertemplate="%{x}: %{y} policies<extra></extra>")
            fig.update_layout(**_chart_layout(
                showlegend=False, height=430,
                xaxis=dict(gridcolor="rgba(0,0,0,0)", showgrid=False, tickangle=-45, tickfont=dict(size=9),
                           categoryorder="array", categoryarray=_day_order),
                yaxis=dict(title="Policies", gridcolor="rgba(96,165,250,0.10)", showgrid=True, zeroline=False),
                margin=dict(t=14, b=10, l=10, r=10),
            ))
            # Clickable: capture which bar (day) was clicked for the drill-down below.
            fig.update_xaxes(fixedrange=True); fig.update_yaxes(fixedrange=True)
            fig.update_layout(dragmode=False)
            _evt = st.plotly_chart(fig, use_container_width=True, on_select="rerun",
                                   key=f"daily_chart_{selected_m}",
                                   config={"displayModeBar": False, "scrollZoom": False, "doubleClick": False})
            st.caption("💡 Click any bar to see that day's policies below.")

    with col_table:
        with st.container(border=True):
            st.markdown(chart_head("Day-by-Day Breakdown", "Daily policies & members", "calendar"),
                        unsafe_allow_html=True)
            max_hd = max(int(daily_df["Members"].max()), 1)

            tbl = daily_df.copy()
            tbl["Day"] = tbl["Date"].dt.strftime("%b %d")

            # Flag today
            if today.year == year and today.month == mnum:
                today_str = today.strftime("%b %d")
                tbl["Day"] = tbl["Day"].apply(lambda d: f"→ {d}" if d == today_str else d)

            st.dataframe(
                tbl[["Day", "Policies", "Members"]],
                use_container_width=True,
                hide_index=True,
                height=430,
                column_config={
                    "Policies": st.column_config.ProgressColumn(
                        "Policies", min_value=0, max_value=max_pol, format="%d"
                    ),
                    "Members": st.column_config.ProgressColumn(
                        "Members", min_value=0, max_value=max_hd, format="%d"
                    ),
                },
            )

    # ── Drill-down: policies for the clicked day ──────────────────────────────
    _clicked_day = None
    try:
        _pts = _evt.selection.points if (_evt and getattr(_evt, "selection", None)) else []
        if _pts:
            _clicked_day = _pts[0].get("x")
    except Exception:
        _clicked_day = None

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(border=True):
        if not _clicked_day:
            st.markdown(chart_head("Policies for a Day", "Click a bar above to see who you signed that day", "users"),
                        unsafe_allow_html=True)
        else:
            _det = _load_daily_detail()
            if _det is None or _det.empty:
                st.markdown(chart_head(f"Policies — {_clicked_day}", "Detail not generated yet — run a report", "users"),
                            unsafe_allow_html=True)
            else:
                _det = _det[_det["Month"].astype(str) == selected_m].copy()
                _det["_lbl"] = pd.to_datetime(_det["Date"], errors="coerce").dt.strftime("%b %d")
                _rows = _det[_det["_lbl"] == _clicked_day]
                _mem = int(pd.to_numeric(_rows["Members"], errors="coerce").fillna(0).sum())
                st.markdown(chart_head(f"Policies submitted — {_clicked_day}",
                                       f"{len(_rows)} policies · {_mem} members", "users"),
                            unsafe_allow_html=True)
                if _rows.empty:
                    st.info("No policies recorded for that day.")
                else:
                    _show = pd.DataFrame({
                        "Name": (_rows["First Name"].fillna("") + " " + _rows["Last Name"].fillna("")).str.strip().str.title(),
                        "Members": pd.to_numeric(_rows["Members"], errors="coerce").fillna(1).astype(int),
                        "Carrier": _rows["Carrier"],
                        "State": _rows["State"],
                    }).sort_values("Name")
                    st.dataframe(_show, use_container_width=True, hide_index=True,
                                 height=min(80 + len(_show) * 35, 460))


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT ROSTER
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Book":
    st.title("Book of Business")
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

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
        st.markdown(stat_card("Total Policies", f"{len(df):,}", "file", ELEC), unsafe_allow_html=True)
    with m2:
        st.markdown(stat_card("Active Policies", f"{active_ct:,}", "shield", GREEN), unsafe_allow_html=True)
    with m3:
        st.markdown(stat_card("Inactive Policies", f"{inactive_ct:,}", "minus", RED), unsafe_allow_html=True)
    with m4:
        st.markdown(stat_card("Active Members", f"{total_mem:,}", "users", CYAN), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Household size breakdown (active policies) ────────────────────────────
    # How many singles vs 2/3/4/5+ member families make up the book. Uses the
    # active policies in the current filtered view, so carrier/state filters flow
    # through; with no filter it's the whole book.
    with st.container(border=True):
        st.markdown(chart_head("Household Size", "Active policies by number of members on the plan", "users"),
                    unsafe_allow_html=True)
        _adf = df[df["status"].isin(active_sts)]
        _sz = pd.to_numeric(_adf.get("applicant_count", pd.Series(dtype=float)), errors="coerce").fillna(1).astype(int).clip(lower=1)
        if len(_sz):
            _b = _sz.where(_sz < 6, 6)   # bucket 6+ together
            _lbl = {1: "1 (single)", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6+"}
            _hh = (pd.DataFrame({"n": _b, "mem": _sz.values})
                     .groupby("n").agg(Policies=("n", "size"), Members=("mem", "sum")).reset_index())
            _hh["Size"] = _hh["n"].map(_lbl)
            _hht = _hh[["Size", "Policies", "Members"]].copy()
            _hht.loc[len(_hht)] = ["Total", int(_hht["Policies"].sum()), int(_hht["Members"].sum())]
            st.dataframe(_hht, use_container_width=True, hide_index=True)
        else:
            st.caption("No active policies in the current view.")

    st.markdown("<br>", unsafe_allow_html=True)

    # Duplicate detection — only among policies still in force. A Terminated/
    # Cancelled row alongside an active one is a plan switch, not a duplicate
    # (Ethan 2026-07-10: "if its terminated dont worry about a duplicate").
    _live = all_clients[~all_clients["status"].isin(["Terminated", "Cancelled"])]
    dup_mask = _live.duplicated(subset=["first_name", "last_name"], keep=False)
    dups = _live[dup_mask][["first_name", "last_name", "carrier", "state", "status", "effective_date"]].copy()
    dups = dups.sort_values(["last_name", "first_name"])
    if not dups.empty:
        st.warning(f"⚠️ {len(dups)} duplicate client names detected ({dups.groupby(['first_name','last_name']).ngroups} unique names appear more than once)")
        with st.expander("View duplicates"):
            dups.columns = [c.replace("_", " ").title() for c in dups.columns]
            st.dataframe(dups, use_container_width=True, hide_index=True,
                column_config={"Effective Date": st.column_config.DateColumn("Effective Date", format="MMM D, YYYY")})

    st.markdown("<br>", unsafe_allow_html=True)

    # Table (glass card)
    display_cols = [
        "first_name", "last_name", "carrier", "state", "status_display",
        "effective_date", "term_date", "months_on_book", "applicant_count", "net_premium",
    ]
    disp = df[[c for c in display_cols if c in df.columns]].copy()
    disp = disp.rename(columns={"status_display": "status"})
    disp.columns = [c.replace("_", " ").title() for c in disp.columns]

    # Supplemental coverage columns (dental/vision/STM/etc.), matched by name.
    _enriched = _attach_supplemental(df, dd.get("supp_df"))
    disp["Supplemental"]    = _enriched["_supp_products"].values
    disp["Supp Premium"]    = _enriched["_supp_premium"].values
    disp["Supp Status"]     = _enriched["_supp_status"].values
    disp["Supp Term Date"]  = pd.to_datetime(_enriched["_supp_term"], errors="coerce").values

    with st.container(border=True):
        st.markdown(chart_head("Client Roster", f"{len(df):,} policies in current view", "book"),
                    unsafe_allow_html=True)
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
                "Supp Premium":    st.column_config.NumberColumn("Supp Premium", format="$%.2f"),
                "Supp Term Date":  st.column_config.DateColumn("Supp Term Date", format="MMM D, YYYY"),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT LOOKUP
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Client Lookup":
    import re as _re

    st.title("Client Lookup")
    st.caption("Start typing — the list narrows with every letter. Pick a client to open their profile.")

    _people_all = (all_clients["first_name"].fillna("").astype(str).str.title().str.strip() + " " +
                   all_clients["last_name"].fillna("").astype(str).str.title().str.strip()).str.strip()
    _names = sorted({p for p in _people_all if p})
    with st.container(key="lookup_hero"):
        person = st.selectbox("Find a client", _names, index=None, key="lookup_select",
                              placeholder="🔎  Type a name…  (e.g. “br” → every Brandon, Brittney, Bryan…)",
                              label_visibility="collapsed")
    st.markdown("""<style>
      .st-key-lookup_hero {max-width:620px;}
      .st-key-lookup_hero div[data-baseweb="select"] > div {
          font-size:1.08rem; padding:8px 12px; border-radius:14px;
          background:rgba(15,23,42,.65); border:1.5px solid rgba(96,165,250,.4);
          box-shadow:0 6px 22px rgba(0,0,0,.3);}
      /* Selected client's name: bright and bold in the box — never placeholder-dim */
      .st-key-lookup_hero div[data-baseweb="select"] > div > div:first-child,
      .st-key-lookup_hero div[data-baseweb="select"] > div > div:first-child > div {
          color:#f8fafc !important; font-weight:700 !important; opacity:1 !important;}
      .st-key-lookup_hero div[data-baseweb="select"] input {
          color:#f8fafc !important; font-weight:600 !important; -webkit-text-fill-color:#f8fafc !important;}
    </style>""", unsafe_allow_html=True)

    if not person:
        st.info("🔎 Pick a client to see everything — policies, payments, contact, and alerts.")
    else:
        rows = all_clients[_people_all == person].copy()
        if len(rows):
            rows["_eff"] = pd.to_datetime(rows["effective_date"], errors="coerce")
            rows = rows.sort_values("_eff", ascending=False)
            r = rows.iloc[0]   # newest policy is the headline

            _ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
            is_active = r.get("status") in _ACTIVE
            _mem_n = pd.to_numeric(r.get("applicant_count"), errors="coerce")
            _mem = 1 if pd.isna(_mem_n) else max(int(_mem_n), 1)

            # ── Header ────────────────────────────────────────────────────────
            _pill_bg, _pill_tx = (("rgba(34,197,94,.15)", "#4ade80") if is_active
                                  else ("rgba(239,68,68,.15)", "#f87171"))
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:14px;margin:6px 0 2px;'>"
                f"<span style='font-size:1.6rem;font-weight:800;color:#f8fafc;'>{person}</span>"
                f"<span style='background:{_pill_bg};color:{_pill_tx};padding:3px 12px;border-radius:999px;"
                f"font-size:.8rem;font-weight:700;'>{r.get('status','?')}</span></div>"
                f"<div style='color:#94a3b8;font-size:.95rem;margin-bottom:10px;'>"
                f"{r.get('carrier','—')} · {r.get('state','—')}"
                + (f" · Policy ID: <span style='color:#e2e8f0;font-weight:600;'>{_pid_hdr}</span>"
                   if (_pid_hdr := str(r.get('policy_number') or '').strip()) and _pid_hdr.lower() not in ('nan', 'none')
                   else "")
                + "</div>",
                unsafe_allow_html=True)

            # ── Agent-of-record banner ────────────────────────────────────────
            _aor = str(r.get("policy_aor") or "")
            _mine = (_AGENT_NPN in _aor) or (_AGENT_FN in _aor.lower() and _AGENT_LN in _aor.lower())
            if _aor.strip().lower() in ("", "none", "nan"):
                st.caption("Agent of record: not recorded (usually fine — carrier book shows you).")
            elif _mine:
                st.success("✓ You are the agent of record.", icon="🛡️")
            else:
                _who = _re.sub(r"\s*\(NPN.*\)", "", _aor).strip().title()
                st.error(f"⚠️ Agent of record is **{_who}** — this client is on your AOR Defense page.",
                         icon="🚨")

            # ── Stat cards ────────────────────────────────────────────────────
            _prem = pd.to_numeric(r.get("net_premium"), errors="coerce")
            _mob = pd.to_numeric(r.get("months_on_book"), errors="coerce")
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.markdown(stat_card("Members", f"{_mem}", "users", CYAN), unsafe_allow_html=True)
            with k2:
                st.markdown(stat_card("Net Premium / Mo",
                                      f"${_prem:,.0f}" if pd.notna(_prem) else "—",
                                      "dollar", GREEN), unsafe_allow_html=True)
            with k3:
                st.markdown(stat_card("Months on Book",
                                      ("<1" if int(_mob) == 0 else f"{int(_mob)}") if pd.notna(_mob) else "—",
                                      "calendar", ELEC), unsafe_allow_html=True)
            with k4:
                st.markdown(stat_card("Est Commission / Yr",
                                      f"${_mem * 23 * 12:,.0f}" if is_active else "$0",
                                      "trend", GOLD), unsafe_allow_html=True)

            # ── Contact ───────────────────────────────────────────────────────
            _ph = _re.sub(r"\D", "", str(r.get("phone") or ""))
            _ph_fmt = f"({_ph[:3]}) {_ph[3:6]}-{_ph[6:10]}" if len(_ph) >= 10 else (_ph or "—")
            _em = str(r.get("email") or "").strip() or "—"
            _cs = pd.to_datetime(r.get("client_since"), errors="coerce")
            _cs_fmt = _cs.strftime("%b %-d, %Y") if pd.notna(_cs) else "—"
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown(f"**📞 Phone:** {_ph_fmt}")
            with c2:
                st.markdown(f"**✉️ Email:** {_em}")
            with c3:
                st.markdown(f"**🗓️ Client since:** {_cs_fmt}")

            # ── Why they left (if churned) ────────────────────────────────────
            _reason = str(r.get("cancel_reason") or "").strip()
            if not is_active and _reason:
                st.warning(f"**Why they left:** {_reason}", icon="📋")

            # ── Verification flags ────────────────────────────────────────────
            _dmi_n = pd.to_numeric(r.get("dmi_outstanding"), errors="coerce")
            _dmi = 0 if pd.isna(_dmi_n) else int(_dmi_n)
            _svi_n = pd.to_numeric(r.get("svi_outstanding"), errors="coerce")
            _svi = 0 if pd.isna(_svi_n) else int(_svi_n)
            if _dmi or _svi:
                st.warning(f"📎 Outstanding verification docs: {_dmi} DMI, {_svi} SVI — "
                           "their subsidy is at risk until submitted (see Follow-ups).", icon="⚠️")

            # ── Policies (history) ────────────────────────────────────────────
            with st.container(border=True):
                st.markdown(chart_head("Policies", f"{len(rows)} on record for {person}", "file"),
                            unsafe_allow_html=True)
                _pc = [c for c in ["carrier", "policy_number", "status", "effective_date", "term_date",
                                   "net_premium", "applicant_count", "cancel_reason"] if c in rows.columns]
                _pt = rows[_pc].rename(columns={
                    "carrier": "Carrier", "policy_number": "Policy ID", "status": "Status",
                    "effective_date": "Effective", "term_date": "Term Date", "net_premium": "Premium",
                    "applicant_count": "Members", "cancel_reason": "Why Ended"})
                st.dataframe(_pt, use_container_width=True, hide_index=True,
                             column_config={
                                 "Effective": st.column_config.DateColumn("Effective", format="MMM D, YYYY"),
                                 "Term Date": st.column_config.DateColumn("Term Date", format="MMM D, YYYY"),
                             })

            # ── Payment history (commissions received for this client) ───────
            with st.container(border=True):
                st.markdown(chart_head("Commission Payments",
                                       "What carriers have paid YOU for this client", "dollar"),
                            unsafe_allow_html=True)
                pay = _load_payments()
                if pay is None or pay.empty:
                    st.caption("Payments sheet not available.")
                else:
                    def _norm_name(s):
                        return _re.sub(r"[^a-z]", "", str(s).lower())
                    _fn = _norm_name(r.get("first_name")); _ln = _norm_name(r.get("last_name"))
                    _keys = {_fn + _ln, _ln + _fn}
                    _pm = pay[pay["member"].map(_norm_name).isin(_keys)
                              | pay["member"].map(_norm_name).str.replace(",", "").isin(_keys)]
                    if _pm.empty:
                        st.caption("No commission payments found under this client's name — "
                                   "if they're active and 2+ months in, check Money Owed.")
                    else:
                        _pm = _pm.copy()
                        _pm["Month"] = pd.to_datetime(_pm["payment_month"], errors="coerce").dt.strftime("%b %Y")
                        _amt = pd.to_numeric(_pm["amount"], errors="coerce").fillna(0)
                        s1, s2, s3 = st.columns(3)
                        with s1:
                            st.markdown(stat_card("Total Paid to You", f"${_amt.sum():,.2f}", "dollar", GREEN),
                                        unsafe_allow_html=True)
                        with s2:
                            st.markdown(stat_card("Payments", f"{len(_pm)}", "file", CYAN),
                                        unsafe_allow_html=True)
                        with s3:
                            _last = pd.to_datetime(_pm["payment_month"], errors="coerce").max()
                            st.markdown(stat_card("Last Paid",
                                                  _last.strftime("%b %Y") if pd.notna(_last) else "—",
                                                  "calendar", ELEC), unsafe_allow_html=True)
                        _pv = _pm[["Month", "carrier", "amount"]].rename(
                            columns={"carrier": "Carrier", "amount": "Amount"})
                        st.dataframe(_pv, use_container_width=True, hide_index=True,
                                     height=min(46 + 35 * len(_pv), 320))

            # ── Supplemental coverage (one line per policy, with policy #) ────
            _supp = dd.get("supp_df")
            if _supp is not None and not getattr(_supp, "empty", True):
                _skey = _supp_name_key(r.get("first_name", ""), r.get("last_name", ""))
                _mine_supp = [t for t in _supp.itertuples(index=False)
                              if _supp_name_key(getattr(t, "first_name", ""),
                                                getattr(t, "last_name", "")) == _skey]
                for _t in _mine_supp:
                    _pn = str(getattr(_t, "policy_number", "") or "").strip()
                    _pn_txt = f" · Policy #{_pn}" if _pn and _pn.lower() != "nan" else ""
                    _prem_s = float(getattr(_t, "premium", 0) or 0)
                    st.markdown(f"**🦷 Supplemental:** {getattr(_t, 'product', '')} "
                                f"({_supp_carrier_label(getattr(_t, 'carrier', ''))}){_pn_txt} — "
                                f"${_prem_s:,.2f}/mo ({getattr(_t, 'status', '')})")

# ══════════════════════════════════════════════════════════════════════════════
# COMMISSIONS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Commissions":
    from tracker.commissions import (carrier_timing, monthly_summary,
                                     carrier_summary, reconcile_book, build_gaps)
    st.title("Commissions")
    st.caption("Actual payments from your Insurance PAYMENTS sheet — income, when each carrier pays, "
               "and clients who've stopped getting paid.")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    pay = _load_payments()
    if pay is None or pay.empty:
        _sa_email = None
        try:
            _sa_email = dict(st.secrets["gcp_service_account"]).get("client_email")
        except Exception:
            pass
        st.warning("Couldn't read the **Insurance PAYMENTS** sheet yet.")
        if _sa_email:
            st.markdown("Share that Google Sheet — **Viewer** access — with this exact account, then hit **Refresh data**:")
            st.code(_sa_email, language=None)
        else:
            st.markdown("Open your **main** Commission Tracker sheet → **Share** to find the "
                        "`…@…iam.gserviceaccount.com` account that has access, then share the **Insurance "
                        "PAYMENTS** sheet with that same email (Viewer) and hit **Refresh data**.")
    else:
        msum = monthly_summary(pay)
        latest = msum.iloc[-1]
        _lm = latest["Month"]
        ytd = pay[pay["payment_month"].dt.year == _lm.year]["amount"].sum()
        avg = msum["Net"].mean()

        # ── KPIs ──────────────────────────────────────────────────────────────
        st.markdown(section_header("Income", "dollar"), unsafe_allow_html=True)
        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(stat_card(f"{_lm.strftime('%b %Y')} Net", f"${latest['Net']:,.0f}", "dollar", GREEN), unsafe_allow_html=True)
        with k2:
            st.markdown(stat_card(f"{_lm.year} YTD", f"${ytd:,.0f}", "calendar", ELEC), unsafe_allow_html=True)
        with k3:
            st.markdown(stat_card("Avg / Month", f"${avg:,.0f}", "trend", CYAN), unsafe_allow_html=True)
        with k4:
            st.markdown(stat_card("Chargebacks (mo)", f"${latest['Chargebacks']:,.0f}", "minus", RED), unsafe_allow_html=True)

        # ── Monthly net chart ────────────────────────────────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(chart_head("Commission by month", "Net paid (after chargebacks)", "trend"), unsafe_allow_html=True)
            mc = msum.copy()
            mc["Label"] = mc["Month"].dt.strftime("%b %Y")
            fig = px.bar(mc, x="Label", y="Net", text="Net")
            fig.update_traces(marker_color=GREEN, marker_cornerradius=8,
                              texttemplate="$%{text:,.0f}", textposition="outside")
            fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10),
                              paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              xaxis_title=None, yaxis_title=None, font_color="#cbd5e1")
            show_chart(fig)

        # ── Each carrier by month (trend + exact numbers) ─────────────────────
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(chart_head("Each carrier by month",
                                   "Net paid per carrier — spot who's growing and who's shrinking",
                                   "trend"), unsafe_allow_html=True)
            cbm = pay.copy()
            cbm["Month"] = cbm["payment_month"].dt.to_period("M").dt.to_timestamp()
            piv = cbm.pivot_table(index="carrier", columns="Month",
                                  values="amount", aggfunc="sum", fill_value=0)
            piv = piv.reindex(sorted(piv.columns), axis=1)
            # biggest carriers first (by all-time net)
            piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]

            # exact-dollar matrix: carrier × month, with a Total column and an
            # all-carriers row so the monthly grand total is visible too.
            _mat = piv.copy()
            _mat["Total"] = _mat.sum(axis=1)
            _mat.loc["All carriers"] = _mat.sum(axis=0)
            _mat.columns = [c.strftime("%b %y") if hasattr(c, "strftime") else c for c in _mat.columns]
            _disp = _mat.apply(lambda col: col.map(lambda v: f"${v:,.0f}"))
            _disp.insert(0, "Carrier", _disp.index)
            st.dataframe(_disp, use_container_width=True, hide_index=True,
                         height=(len(_disp) + 1) * 35 + 8)

        # ── Carrier breakdown + payment timing ────────────────────────────────
        cc1, cc2 = st.columns(2)
        with cc1:
            with st.container(border=True):
                st.markdown(chart_head("By carrier", "Net commission, all months", "shield"), unsafe_allow_html=True)
                cs = carrier_summary(pay).copy()
                # Avg per member per month = net ÷ total member-months (sum of
                # subscribers across payment lines), NOT ÷ payment count — a single
                # large household would otherwise skew the average.
                _subs = (pay.assign(_s=pd.to_numeric(pay["subscribers"], errors="coerce").fillna(1).clip(lower=1))
                            .groupby("carrier")["_s"].sum())
                _mm = cs["Carrier"].map(_subs).fillna(0)
                cs["Avg / Member"] = (cs["Net"] / _mm.replace(0, pd.NA)).map(
                    lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
                cs["Net"] = cs["Net"].map(lambda v: f"${v:,.0f}")
                st.dataframe(cs[["Carrier", "Net", "Payments", "Avg / Member"]],
                             use_container_width=True, hide_index=True, height=380)
        with cc2:
            with st.container(border=True):
                st.markdown(chart_head("When each carrier pays", "Lag from coverage month to payment", "calendar"), unsafe_allow_html=True)
                tim = carrier_timing(pay)
                trows = [{"Carrier": c, "Pays": ("Same month" if lag == 0 else f"+{lag} month" + ("s" if lag > 1 else ""))}
                         for c, lag in sorted(tim.items())]
                st.dataframe(pd.DataFrame(trows), use_container_width=True, hide_index=True, height=380)

        # ── Commission gaps: active but not getting paid (with pay history) ───
        _ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
        _active = all_clients[all_clients["status"].isin(_ACTIVE)] if "status" in all_clients.columns else pd.DataFrame()
        rec = reconcile_book(_active, pay)

        st.caption(f"Matched {rec['matched']} of {len(_active)} active clients to a payment "
                   f"({len(_active) - rec['matched']} unmatched — mostly new business not yet paid or name variants).")

elif page == "Money Owed":
    from tracker.commissions import reconcile_book, build_gaps
    st.title("Money Owed")
    st.caption("Money you should be collecting but aren't yet — active clients you're not being "
               "paid on and Ambetter disputes. Chase these to get paid. "
               "(Members behind on premium now live on the **Past Due** page.)")
    st.markdown(color_legend(), unsafe_allow_html=True)
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    # Jump to the section the user clicked from the Dashboard (?sec=...), once.
    _sec = st.query_params.get("sec")
    if _sec and st.session_state.get("_mo_scrolled") != _sec:
        st.session_state["_mo_scrolled"] = _sec
        _anchor = {"gaps": "mo-gaps", "disputes": "mo-disputes", "pastdue": "mo-pastdue"}.get(_sec)
        if _anchor:
            import streamlit.components.v1 as _c
            # Re-align to the section while the page is still rendering (tables above
            # load late and would otherwise leave the scroll short of the target).
            _c.html(
                "<script>"
                "const d=window.parent.document,id='" + _anchor + "',end=Date.now()+2800;"
                "function go(){const e=d.getElementById(id);if(e)e.scrollIntoView({block:'start'});}"
                "const o=new MutationObserver(()=>{Date.now()<end?go():o.disconnect();});"
                "o.observe(d.body,{childList:true,subtree:true});go();"
                "setTimeout(()=>{go();o.disconnect();},2900);"
                "</script>", height=0)
    pay = _load_payments()
    _ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
    _active = all_clients[all_clients["status"].isin(_ACTIVE)] if "status" in all_clients.columns else pd.DataFrame()
    _mo_ready = pay is not None and not pay.empty
    rec = reconcile_book(_active, pay) if _mo_ready else {"matched": 0, "chargebacks": pd.DataFrame(columns=["member","carrier","payment_month","amount"])}
    # Gaps = clients YOU'RE the agent for but not paid on. Exclude any whose AOR
    # moved to another agent — you're correctly not paid on those (they're AOR
    # losses, not disputes). Blank AOR is kept (could be yours, just unpopulated).
    _gap_active = _filter_aor_mine(_active)
    gaps = build_gaps(_gap_active, pay) if _mo_ready else None
    # Policy-number audit: who's truly never paid (carrier policy # never on a
    # statement) vs paid under a different member. Local computes it from the
    # carrier books; cloud reads the columns the report wrote to the sheet.
    if gaps is not None and not gaps.empty:
        try:
            from tracker.commissions import audit_gaps
            _books = Path(__file__).parent / "carrier_books"
            if _books.exists() and any(_books.glob("*.csv")):
                gaps = audit_gaps(gaps, pay, str(_books))
            else:
                _au = _gap_audit_from_sheet()
                if _au:
                    import re as _re2
                    def _nkk(r): return _re2.sub(r"[^a-z0-9]", "", (str(r["Last Name"]) + str(r["First Name"])).lower())[:12]
                    gaps["Policy #"] = gaps.apply(lambda r: _au.get(_nkk(r), {}).get("Policy #", ""), axis=1)
                    gaps["Ever Paid"] = gaps.apply(lambda r: _au.get(_nkk(r), {}).get("Ever Paid", "?"), axis=1)
                    gaps["Dispute"] = gaps.apply(lambda r: _au.get(_nkk(r), {}).get("Dispute", ""), axis=1)
        except Exception:
            pass
    if not _mo_ready:
        st.warning("Couldn't read the Insurance PAYMENTS sheet yet — the 'not getting paid' list needs it. Disputes & past-due below still work after a refresh.")
    if True:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div id="mo-gaps"></div>', unsafe_allow_html=True)
        st.markdown(section_header("Active — Not Getting Paid", "shield"), unsafe_allow_html=True)
        st.caption("Clients confirmed **active** on your book (carrier portals + HealthSherpa) that you're **not "
                   "being paid on**, with each one's full payment history so you can take it to your commissions "
                   "team. June is excluded (its second check is still pending) — gaps are based on complete months only.")
        if gaps is None or gaps.empty:
            st.success("No commission gaps — every active client (past the new-business window) is being paid. 🎉")
        else:
            _never = int((gaps["Gap"] == "Never paid").sum())
            _stop = int((gaps["Gap"] == "Stopped").sum())
            _disp_ct = int(gaps["Dispute"].astype(str).str.contains("Dispute").sum()) if "Dispute" in gaps.columns else 0
            gk1, gk2, gk3, gk4 = st.columns(4)
            with gk1:
                st.markdown(stat_card("Total Gaps", f"{len(gaps):,}", "minus", RED), unsafe_allow_html=True)
            with gk2:
                st.markdown(stat_card("Never Paid", f"{_never:,}", "shield", GOLD), unsafe_allow_html=True)
            with gk3:
                st.markdown(stat_card("Stopped", f"{_stop:,}", "clock", ELEC), unsafe_allow_html=True)
            with gk4:
                st.markdown(stat_card("Dispute-Ready", f"{_disp_ct:,}", "dollar", GREEN), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            # Reindex so a missing column (e.g. an older cached module) can't crash the page.
            gx = gaps.reindex(columns=["First Name", "Last Name", "Carrier", "State", "Gap",
                                       "Client Since", "Months Paid", "Last Paid", "Total Paid", "Premium",
                                       "Policy #", "Ever Paid", "Dispute"])
            gx["_prem"] = pd.to_numeric(gx["Premium"], errors="coerce").fillna(0)
            gx["Dispute"] = gx["Dispute"].fillna("")

            # ── Filters ───────────────────────────────────────────────────────
            ff1, ff2, ff3, ff4, ff5 = st.columns(5)
            with ff1:
                f_gap = st.selectbox("Gap type", ["All", "Never paid", "Stopped"], key="gap_type")
            with ff2:
                f_carrier = st.selectbox("Carrier", ["All"] + sorted(gx["Carrier"].dropna().unique().tolist()), key="gap_carrier")
            with ff3:
                f_state = st.selectbox("State", ["All"] + sorted(gx["State"].dropna().astype(str).unique().tolist()), key="gap_state")
            with ff4:
                f_prem = st.selectbox("Premium", ["All", "Paying ($0+ premium)", "$0 (fully subsidized)"], key="gap_prem")
            with ff5:
                f_ver = st.selectbox("Verified", ["All", "✅ Dispute-ready", "Were paid", "Needs portal"], key="gap_verified")

            gv = gx.copy()
            if f_gap != "All":
                gv = gv[gv["Gap"] == f_gap]
            if f_carrier != "All":
                gv = gv[gv["Carrier"] == f_carrier]
            if f_state != "All":
                gv = gv[gv["State"].astype(str) == f_state]
            if f_prem == "$0 (fully subsidized)":
                gv = gv[gv["_prem"] <= 0]
            elif f_prem == "Paying ($0+ premium)":
                gv = gv[gv["_prem"] > 0]
            if f_ver == "✅ Dispute-ready":
                gv = gv[gv["Dispute"].astype(str).str.contains("Dispute", na=False)]
            elif f_ver == "Were paid":
                gv = gv[gv["Ever Paid"].astype(str) == "Yes"]
            elif f_ver == "Needs portal":
                gv = gv[gv["Dispute"].astype(str).str.contains("needs portal", na=False)]
            # Group by $0 vs paying (paying first, highest premium on top), then carrier.
            gv = gv.sort_values(["_prem", "Carrier"], ascending=[False, True])

            _cs = pd.to_datetime(gv["Client Since"], errors="coerce")
            gd = pd.DataFrame({
                "Name": (gv["First Name"].fillna("") + " " + gv["Last Name"].fillna("")).str.strip().str.title(),
                "Carrier": gv["Carrier"],
                "State": gv["State"],
                "Policy #": gv.get("Policy #", "").fillna("").replace("", "—"),
                "Dispute": gv.get("Dispute", "").fillna(""),
                "Gap": gv["Gap"],
                "Client Since": _cs.dt.strftime("%b %Y").where(_cs.notna(), "—"),
                "Months Paid": gv["Months Paid"].fillna("—"),
                "Last Paid": gv["Last Paid"].fillna("—"),
                "Total Paid": pd.to_numeric(gv["Total Paid"], errors="coerce").fillna(0).map(lambda v: f"${v:,.0f}"),
                "Premium": gv["_prem"].map(lambda v: f"${v:,.2f}" if v > 0 else "$0"),
            })
            st.caption(f"Showing **{len(gd)}** of {len(gx)} gaps"
                       + (f" · {f_gap}" if f_gap != "All" else "")
                       + (f" · {f_carrier}" if f_carrier != "All" else "")
                       + (f" · {f_state}" if f_state != "All" else "")
                       + (f" · {f_prem}" if f_prem != "All" else ""))
            st.dataframe(gd, use_container_width=True, hide_index=True, height=min(120 + len(gd) * 34, 560))
            st.caption("📄 Also saved to the **Commission Gaps** tab in your Google Sheet — export or share that with "
                       "your commissions team as the dispute report.")

        # ── Ambetter disputes: carrier's OWN export confirms owed, but unpaid ──
        disp = _load_ambetter_disputes()
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<div id="mo-disputes"></div>', unsafe_allow_html=True)
        st.markdown(section_header("Ambetter — Carrier Confirms Owed", "shield"), unsafe_allow_html=True)
        st.caption("The strongest dispute evidence: Ambetter's **own policy export** marks these "
                   "**Eligible for Commission = Yes** with the member **paid-through current**, yet your payments "
                   "sheet shows no recent check. New business (effective this month or later) is held back — "
                   "Ambetter hasn't turned it on yet.")
        if disp is None or disp.empty:
            st.success("No open Ambetter disputes — every commission-eligible, current policy is being paid. 🎉")
        else:
            _mem = pd.to_numeric(disp.get("Members"), errors="coerce").fillna(0).astype(int).sum()
            _prem = pd.to_numeric(disp.get("Monthly Premium"), errors="coerce").fillna(0).sum()
            dk1, dk2, dk3 = st.columns(3)
            with dk1:
                st.markdown(stat_card("Disputes", f"{len(disp):,}", "shield", RED), unsafe_allow_html=True)
            with dk2:
                st.markdown(stat_card("Members Owed", f"{_mem:,}", "users", GOLD), unsafe_allow_html=True)
            with dk3:
                st.markdown(stat_card("Monthly Premium", f"${_prem:,.0f}", "dollar", GREEN), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            _states = sorted(disp.get("State", pd.Series(dtype=str)).dropna().astype(str).unique().tolist())
            d_state = st.selectbox("State", ["All"] + _states, key="disp_state")
            dv = disp if d_state == "All" else disp[disp["State"].astype(str) == d_state]
            dd = pd.DataFrame({
                "Name": (dv["First Name"].fillna("") + " " + dv["Last Name"].fillna("")).str.strip().str.title(),
                "State": dv["State"],
                "Policy #": dv["Policy #"],
                "Effective": dv["Effective"],
                "Paid Through": dv["Paid Through"],
                "Eligible (carrier)": dv.get("Eligible (carrier)", "Yes"),
                "Last Paid": dv.get("Last Paid", "—"),
                "Members": pd.to_numeric(dv.get("Members"), errors="coerce").fillna(0).astype(int),
                "Monthly Premium": pd.to_numeric(dv.get("Monthly Premium"), errors="coerce").fillna(0).map(lambda v: f"${v:,.2f}"),
                "Phone": dv.get("Phone", ""),
            })
            st.caption(f"Showing **{len(dd)}** of {len(disp)} disputes" + (f" · {d_state}" if d_state != "All" else ""))
            st.dataframe(dd, use_container_width=True, hide_index=True, height=min(120 + len(dd) * 34, 560))
            st.caption("📄 Saved to the **Ambetter Disputes** tab in your Google Sheet. Each row = a policy Ambetter "
                       "itself confirms you're the broker of record and eligible to be paid on.")

        cb = rec["chargebacks"]
        if cb is not None and not cb.empty:
            st.markdown("<br>", unsafe_allow_html=True)
            with st.expander(f"Recent chargebacks / clawbacks ({len(cb)})"):
                cbd = pd.DataFrame({
                    "Member": cb["member"].str.title(),
                    "Carrier": cb["carrier"],
                    "Month": cb["payment_month"].dt.strftime("%b %Y"),
                    "Amount": cb["amount"].map(lambda v: f"-${abs(v):,.2f}"),
                })
                st.dataframe(cbd, use_container_width=True, hide_index=True, height=min(80 + len(cbd) * 35, 400))




# ══════════════════════════════════════════════════════════════════════════════
# PAST DUE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Past Due":
    st.title("Past Due")
    st.caption("Active clients **paying a premium** ($0 plans excluded) who haven't paid for a **full elapsed "
               "month** — call them before the carrier cancels for non-payment. A **May-start who never paid** "
               "shows here; a **June-start gets until end of June** (their first month isn't over yet). "
               "Covers Ambetter (paid-through passed) and Oscar (balance owed).")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    pdue = _load_pastdue()
    # Drop anyone whose HealthSherpa verification has EXPIRED — subsidy is lost,
    # they're handled as Cancelled outreach instead (not a payment reach-out).
    _exp_keys = _expired_followup_keys()
    if pdue is not None and not pdue.empty and _exp_keys:
        from tracker.carrier_status import _person_key as _pk_fn
        _pk = pdue.apply(lambda r: _pk_fn(r.get("first_name", ""), r.get("last_name", "")), axis=1)
        pdue = pdue[~_pk.isin(_exp_keys)].copy()
    if pdue is None or pdue.empty:
        st.success("No paying clients are past due right now. 🎉")
    else:
        pv = pdue.copy()
        pv["_name"] = ((pv["first_name"].fillna("").astype(str).str.strip() + " "
                        + pv["last_name"].fillna("").astype(str).str.strip()).str.strip())
        pv["premium"] = pd.to_numeric(pv.get("premium"), errors="coerce")
        pv["members"] = pd.to_numeric(pv.get("members"), errors="coerce").fillna(1).astype(int)
        pv["days_overdue"] = pd.to_numeric(pv.get("days_overdue"), errors="coerce")
        _risk = int(pv["members"].sum()) * 23
        qk1, qk2, qk3 = st.columns(3)
        with qk1:
            st.markdown(stat_card("Past-Due Policies", f"{len(pv):,}", "clock", GOLD), unsafe_allow_html=True)
        with qk2:
            st.markdown(stat_card("Commission at Risk / Mo", f"${_risk:,.0f}", "minus", RED), unsafe_allow_html=True)
        with qk3:
            st.markdown(stat_card("Members at Risk", f"{int(pv['members'].sum()):,}", "users", ELEC), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        pv["status"] = pv.get("status", pd.Series(index=pv.index, dtype=str)).fillna("Past due").replace("", "Past due")
        qf1, qf2, qf3 = st.columns(3)
        with qf1:
            _qst = ["All"] + sorted(pv["status"].dropna().unique().tolist())
            qstf = st.selectbox("Status", _qst, key="pastdue_comm_status")
        with qf2:
            _qc = ["All"] + sorted(pv["carrier"].dropna().unique().tolist())
            qcf = st.selectbox("Carrier", _qc, key="pastdue_comm_carrier")
        with qf3:
            _qs = ["All"] + sorted(pv["state"].dropna().astype(str).unique().tolist()) if "state" in pv.columns else ["All"]
            qsf = st.selectbox("State", _qs, key="pastdue_comm_state")
        if qstf != "All":
            pv = pv[pv["status"] == qstf]
        if qcf != "All":
            pv = pv[pv["carrier"] == qcf]
        if qsf != "All" and "state" in pv.columns:
            pv = pv[pv["state"].astype(str) == qsf]
        # Most-recent paid-through month first — the freshest lapses are the
        # most savable. Oscar rows (balance-based, no paid-through) fall to the
        # bottom, ordered by balance owed.
        pv["_pt"] = pd.to_datetime(pv.get("paid_through"), errors="coerce")
        pv["_bal"] = pd.to_numeric(pv.get("balance"), errors="coerce")
        pv = pv.sort_values(["_pt", "_bal"], ascending=[False, False], na_position="last").reset_index(drop=True)

        qd = pd.DataFrame({
            "Name": pv["_name"].str.title(),
            "Status": pv["status"],
            "Carrier": pv["carrier"],
            "State": pv["state"] if "state" in pv.columns else "",
            "Premium": pv["premium"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—"),
            "Members": pv["members"],
            "Behind Since / Owed": pv["reason"] if "reason" in pv.columns else "",
            "Phone": pv["phone"] if "phone" in pv.columns else "",
        })
        st.caption(f"Showing **{len(qd)}** past-due paying clients"
                   + (f" · {qstf}" if qstf != "All" else "")
                   + (f" · {qcf}" if qcf != "All" else "") + (f" · {qsf}" if qsf != "All" else ""))
        st.dataframe(qd, use_container_width=True, hide_index=True, height=min(120 + len(qd) * 34, 560))
        st.caption("**Status** tells you the kind of call: *Grace period / Delinquent* = an existing client "
                   "lapsing (catch them up before cancellation); *Unpaid binder* = a brand-new sale that hasn't "
                   "paid to activate yet (call to turn coverage on). Covers Ambetter & Oscar — Anthem and "
                   "UHC-medical exports don't include payment data.")


# ══════════════════════════════════════════════════════════════════════════════
# AOR DEFENSE
# ══════════════════════════════════════════════════════════════════════════════
elif page == "AOR Defense":
    st.title("AOR Defense")
    st.caption("Clients HealthSherpa flags as at risk of walking out the door. **Taken** = another "
               "agent filed an Agent-of-Record change — call them, most don't know they were switched. "
               "**Disconnected** = usually still yours — just click Reconnect in HealthSherpa to confirm.")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    _adf = _load_aor_defense()
    if _adf is None or _adf.empty:
        st.warning("No AOR-at-risk data yet. Ask Claude to re-pull the AOR list from HealthSherpa, "
                   "then run the report.")
    else:
        _taken = _adf[_adf["Type"] == "Taken"]
        _disc  = _adf[_adf["Type"] == "Disconnected"]
        _open_taken = _taken[_taken["Handled"].fillna("") == ""]
        _stake = int(pd.to_numeric(_open_taken["Est $/yr"], errors="coerce").fillna(0).sum())

        k1, k2, k3, k4 = st.columns(4)
        with k1:
            st.markdown(stat_card("Taken by Another Agent", f"{len(_taken):,}", "minus", RED), unsafe_allow_html=True)
        with k2:
            st.markdown(stat_card("Still To Fight", f"{len(_open_taken):,}", "bell", GOLD), unsafe_allow_html=True)
        with k3:
            st.markdown(stat_card("Disconnected (reconnect)", f"{len(_disc):,}", "refresh", CYAN), unsafe_allow_html=True)
        with k4:
            st.markdown(stat_card("$/yr At Stake", f"${_stake:,}", "dollar", GREEN), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        _show_handled = st.toggle("Show handled clients", value=False)

        _cols_taken = [c for c in ["Client", "Taken By", "Detected", "Days Ago", "Carrier", "State",
                                   "Members", "Est $/yr", "Phone", "Policy Status", "Handled"]
                       if c in _adf.columns]
        _cols_disc = [c for c in _cols_taken if c != "Taken By"]

        with st.container(border=True):
            st.markdown(chart_head("Taken — call these first",
                                   "Another agent holds the AOR. Newest steals first — freshest are the most winnable. "
                                   "Script: “I saw your plan got moved to a different agent — did you mean to do that?”",
                                   "minus"), unsafe_allow_html=True)
            _t = (_taken if _show_handled else _open_taken)[_cols_taken]
            if "Days Ago" in _t.columns:
                _t = _t.sort_values("Days Ago", ascending=True, na_position="last")
            _t = _t.rename(columns={"Detected": "Taken On"})
            st.dataframe(_t, use_container_width=True, hide_index=True,
                         height=min(46 + 35 * max(len(_t), 1), 560))

        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(chart_head("Disconnected — reconnect these",
                                   "Usually still your clients. Open each in HealthSherpa and click Reconnect; "
                                   "only worry if the reconnect shows another agent.",
                                   "refresh"), unsafe_allow_html=True)
            _d = (_disc if _show_handled else _disc[_disc["Handled"].fillna("") == ""])[_cols_disc]
            _d = _d.rename(columns={"Detected": "Disconnected On"})
            st.dataframe(_d, use_container_width=True, hide_index=True,
                         height=min(46 + 35 * max(len(_d), 1), 560))

        st.caption("✅ Handled someone? Tell Claude (e.g. “won back Kayla Martinez” or “Sue Meyer is "
                   "really gone”) and they'll drop off the open list. The list itself refreshes when "
                   "Claude re-pulls HealthSherpa's AOR-at-risk tab.")

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
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    st.markdown(section_header("Set your goal", "target"), unsafe_allow_html=True)
    _prev_goal_members = st.session_state.get("goal_members", 2000)
    _prev_goal_date     = st.session_state.get("goal_date", dt.date(2027, 2, 1))

    # Goal input cards — dark filled tiles with gradient borders, icon squares,
    # big values. Native number/date widgets underneath (steppers + picker keep
    # working); everything here is styling only.
    _USERS_SVG = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
                  "fill='none' stroke='%23a78bfa' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
                  "<path d='M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2'/><circle cx='9' cy='7' r='4'/>"
                  "<path d='M23 21v-2a4 4 0 0 0-3-3.87'/><path d='M16 3.13a4 4 0 0 1 0 7.75'/></svg>")
    _CAL_SVG = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
                "fill='none' stroke='%23a78bfa' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'>"
                "<rect x='3' y='4' width='18' height='18' rx='2' ry='2'/><line x1='16' y1='2' x2='16' y2='6'/>"
                "<line x1='8' y1='2' x2='8' y2='6'/><line x1='3' y1='10' x2='21' y2='10'/></svg>")
    st.markdown(f"""
    <style>
      .st-key-goal_card_members, .st-key-goal_card_date {{
        position:relative; border-radius:20px; background:#0c1424;
        padding:30px 30px 30px 112px; min-height:132px; justify-content:center;
      }}
      .st-key-goal_card_members {{ box-shadow:0 0 24px rgba(139,92,246,.16); }}
      .st-key-goal_card_members::before {{
        content:""; position:absolute; inset:0; border-radius:20px; padding:1.5px;
        background:linear-gradient(120deg,#4285F4,#8b5cf6 55%,#b06ef7);
        -webkit-mask:linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
        -webkit-mask-composite:xor; mask-composite:exclude; pointer-events:none;
      }}
      .st-key-goal_card_date {{ box-shadow:0 0 20px rgba(66,133,244,.10); }}
      .st-key-goal_card_date::before {{
        content:""; position:absolute; inset:0; border-radius:20px; padding:1.5px;
        background:linear-gradient(120deg,rgba(66,133,244,.55),rgba(96,120,200,.30));
        -webkit-mask:linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
        -webkit-mask-composite:xor; mask-composite:exclude; pointer-events:none;
      }}
      .st-key-goal_card_members::after, .st-key-goal_card_date::after {{
        content:""; position:absolute; left:28px; top:50%; transform:translateY(-50%);
        width:56px; height:56px; border-radius:16px; background-color:#161f38;
        background-repeat:no-repeat; background-position:center; background-size:28px 28px;
        box-shadow:inset 0 0 0 1px rgba(139,92,246,.28); pointer-events:none;
      }}
      .st-key-goal_card_members::after {{ background-image:url("{_USERS_SVG}"); }}
      .st-key-goal_card_date::after    {{ background-image:url("{_CAL_SVG}"); }}

      .st-key-goal_card_members [data-testid="stWidgetLabel"] p,
      .st-key-goal_card_date [data-testid="stWidgetLabel"] p {{
        font-size:.85rem !important; color:#8aacd6 !important;
      }}
      .st-key-goal_card_members [data-testid="stNumberInputContainer"],
      .st-key-goal_card_members div[data-baseweb="input"],
      .st-key-goal_card_members div[data-baseweb="input"] > div,
      .st-key-goal_card_members [data-baseweb="base-input"],
      .st-key-goal_card_date div[data-baseweb="input"],
      .st-key-goal_card_date div[data-baseweb="input"] > div,
      .st-key-goal_card_date [data-baseweb="base-input"] {{
        background:transparent !important; border:none !important; box-shadow:none !important;
      }}
      .st-key-goal_card_members input, .st-key-goal_card_date input {{
        font-size:1.9rem !important; font-weight:700 !important; color:#f2f5fb !important;
        background:transparent !important; padding-left:0 !important;
      }}
      /* minus / plus steppers with divider lines */
      .st-key-goal_card_members button[data-testid*="StepDown"],
      .st-key-goal_card_members button[data-testid*="StepUp"] {{
        background:transparent !important; border-radius:0 !important;
        border-left:1px solid rgba(138,172,214,.18) !important;
        width:54px !important; color:#e8edf5 !important;
      }}
      .st-key-goal_card_members button[data-testid*="StepDown"]:hover,
      .st-key-goal_card_members button[data-testid*="StepUp"]:hover {{
        background:rgba(139,92,246,.12) !important;
      }}
      /* purple calendar glyph on the far right of the date card */
      .st-key-goal_card_date div[data-baseweb="input"] {{ position:relative; }}
      .st-key-goal_card_date div[data-baseweb="input"]::after {{
        content:""; position:absolute; right:6px; top:50%; transform:translateY(-50%);
        width:26px; height:26px; background:url("{_CAL_SVG}") no-repeat center/26px 26px;
        pointer-events:none; opacity:.9;
      }}
    </style>
    """, unsafe_allow_html=True)

    gi1, gi2 = st.columns(2, gap="large")
    with gi1:
        with st.container(key="goal_card_members"):
            GOAL = st.number_input("Member goal", min_value=1, value=_prev_goal_members, step=50)
        st.session_state["goal_members"] = GOAL
    with gi2:
        with st.container(key="goal_card_date"):
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

    # ── LTV — all-time churn across your entire tracked history ──
    # Aggregate monthly churn = total members lost / total active member-months
    # over every tracked month (standard churn calc). More stable and
    # representative than the trailing few months.
    if not mom_df.empty and "Members Lost" in mom_df.columns and "Total Members" in mom_df.columns:
        _total_lost   = mom_df["Members Lost"].sum()
        _member_months = mom_df["Total Members"].sum()
        monthly_churn_rate = _total_lost / max(_member_months, 1)
        _churn_label = "all-time avg"
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
    st.markdown(section_header("Revenue — where you are now", "dollar"), unsafe_allow_html=True)
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
    st.markdown(section_header("Pace needed to hit goal", "target"), unsafe_allow_html=True)
    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(_goal_kpi("New members / day", f"+{needed_per_day}", f"{days_left:,} days remaining"), unsafe_allow_html=True)
    with k2:
        st.markdown(_goal_kpi("New members / week", f"+{needed_per_week:.0f}", f"{weeks_left:.0f} weeks remaining"), unsafe_allow_html=True)
    with k3:
        st.markdown(_goal_kpi("New members / month", f"+{needed_per_mo:.0f}", f"{months_left:.0f} months remaining"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── At your current pace ──────────────────────────────────────────────────
    st.markdown(section_header("At your current pace", "trend"), unsafe_allow_html=True)
    # Same cadence as "pace needed" so you can compare directly.
    cur_per_month = recent_mo_growth
    cur_per_week  = recent_mo_growth * 12 / 52
    cur_per_day   = recent_mo_growth * 12 / 365
    c1, c2, c3 = st.columns(3)
    with c1:
        _d_lbl = "ahead of pace ✓" if cur_per_day >= needed_per_day else "below pace"
        st.markdown(_goal_kpi("New members / day", f"+{cur_per_day:.2f}", f"vs +{needed_per_day} needed · {_d_lbl}",
                              "green" if cur_per_day >= needed_per_day else "red"), unsafe_allow_html=True)
    with c2:
        _w_lbl = "ahead of pace ✓" if cur_per_week >= needed_per_week else "below pace"
        st.markdown(_goal_kpi("New members / week", f"+{cur_per_week:.0f}", f"vs +{needed_per_week:.0f} needed · {_w_lbl}",
                              "green" if cur_per_week >= needed_per_week else "red"), unsafe_allow_html=True)
    with c3:
        _m_lbl = "ahead of pace ✓" if cur_per_month >= needed_per_mo else "below pace"
        st.markdown(_goal_kpi("New members / month", f"+{cur_per_month:.0f}", f"vs +{needed_per_mo:.0f} needed · {_m_lbl}",
                              "green" if cur_per_month >= needed_per_mo else "red"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

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
    st.markdown(section_header("Growth vs. required pace", "bars"), unsafe_allow_html=True)

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
            fig.add_vline(x=TODAY.isoformat(), line_color="#94a3b8", line_dash="dot", line_width=1, annotation_text="Today", annotation_position="top right", annotation_font_color="#94a3b8")
            fig.update_layout(**_chart_layout(
                xaxis=dict(showgrid=False, title=""),
                yaxis=dict(showgrid=True, gridcolor="rgba(96,165,250,0.10)", title="Active members"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=30, b=0), height=360))
            show_chart(fig)

        with tab_revenue:
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=hist["month"], y=hist["arr"], mode="lines+markers", name="Actual ARR", line=dict(color=GREEN, width=3), marker=dict(size=7)))
            fig2.add_trace(go.Scatter(x=pace_df["month"], y=pace_df["required_arr"], mode="lines", name="Required pace", line=dict(color=GOLD, width=2, dash="dash")))
            fig2.add_hline(y=goal_arr, line_color=GREEN, line_dash="dot", line_width=1.5, annotation_text=f"Goal ARR: ${goal_arr:,.0f}", annotation_position="top left", annotation_font_color=GREEN)
            fig2.add_vline(x=TODAY.isoformat(), line_color="#94a3b8", line_dash="dot", line_width=1, annotation_text="Today", annotation_position="top right", annotation_font_color="#94a3b8")
            fig2.update_layout(**_chart_layout(
                xaxis=dict(showgrid=False, title=""),
                yaxis=dict(showgrid=True, gridcolor="rgba(96,165,250,0.10)", title="Annual Revenue ($)", tickprefix="$", tickformat=",.0f"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                margin=dict(l=0, r=0, t=30, b=0), height=360))
            show_chart(fig2)
    else:
        st.info("Not enough history to plot growth chart.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Weekly callout ────────────────────────────────────────────────────────
    _week_policies = round(needed_per_week / max(_avg_hh, 1))
    st.markdown(
        f'<div style="background:linear-gradient(90deg,rgba(245,158,11,0.13),rgba(245,158,11,0.04));'
        f'border:1px solid rgba(245,158,11,0.4);border-left:4px solid {GOLD};padding:16px 20px;'
        f'border-radius:14px;margin-bottom:20px;">'
        f'<div style="font-size:0.78rem;color:{T["kpi_lbl"]};text-transform:uppercase;letter-spacing:0.08em;font-weight:600;">This week\'s target</div>'
        f'<div style="font-size:1.9rem;font-weight:800;color:{GOLD};margin-top:4px;">+{needed_per_week:.0f} members</div>'
        f'<div style="font-size:0.9rem;color:{T["kpi_lbl"]};margin-top:3px;">≈ {_week_policies} policies &nbsp;·&nbsp; {weeks_left:.0f} weeks remaining to reach {GOAL:,} members by {GOAL_DATE.strftime("%b %d, %Y")}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Monthly targets table ─────────────────────────────────────────────────
    st.markdown(section_header("Monthly targets", "calendar"), unsafe_allow_html=True)
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
    with st.container(border=True):
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

    # Health-plan policies behind on payment but still active (grace period).
    _pd_df = dd.get("health_pastdue_df")
    _pd_df = _pd_df.copy() if (_pd_df is not None and len(_pd_df)) else pd.DataFrame()
    # Exclude expired-verification clients (subsidy lost → Cancelled outreach, not past-due).
    _exp_keys = _expired_followup_keys()
    if not _pd_df.empty and _exp_keys:
        from tracker.carrier_status import _person_key as _pk_fn
        _pk = _pd_df.apply(lambda r: _pk_fn(r.get("first_name", ""), r.get("last_name", "")), axis=1)
        _pd_df = _pd_df[~_pk.isin(_exp_keys)].copy()

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
                st.markdown(stat_card("Need Outreach", f"{len(outreach_df):,}", "users", ELEC), unsafe_allow_html=True)
            with k2:
                st.markdown(stat_card("Lost < 30 Days", f"{last_30:,}", "clock", RED), unsafe_allow_html=True)
            with k3:
                st.markdown(stat_card("Lost < 60 Days", f"{last_60:,}", "clock", GOLD), unsafe_allow_html=True)
            with k4:
                st.markdown(stat_card("Won Back", f"{len(winbacks):,}", "refresh", GREEN), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Group the free-text Reason into filterable categories.
            def _reason_cat(v):
                s = str(v or "").strip()
                if s.startswith("AOR taken"):        return "AOR taken"
                if "Verification expired" in s:      return "Verification expired"
                if s.startswith("Lapsed"):           return "Lapsed (carrier)"
                if s in ("", "—", "nan", "None"):    return "Unknown"
                return "Carrier note"

            outreach_df["_reason_cat"] = (outreach_df["cancel_reason"].apply(_reason_cat)
                                          if "cancel_reason" in outreach_df.columns else "Unknown")

            f1, f2, f3, f4, f5 = st.columns(5)
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
            with f4:
                reason_cats = ["All"] + sorted(outreach_df["_reason_cat"].dropna().unique().tolist())
                reason_filter = st.selectbox("Reason", reason_cats)
            with f5:
                premium_filter = st.selectbox("Premium", ["All", "$0 only", "Above $0"])

            view = outreach_df[outreach_df["days_since_lost"] <= window_days].copy()
            if carrier_filter != "All" and "carrier" in view.columns:
                view = view[view["carrier"] == carrier_filter]
            if state_filter != "All" and "state" in view.columns:
                view = view[view["state"] == state_filter]
            if reason_filter != "All" and "_reason_cat" in view.columns:
                view = view[view["_reason_cat"] == reason_filter]
            if premium_filter != "All" and "net_premium" in view.columns:
                _pf = pd.to_numeric(view["net_premium"], errors="coerce").fillna(0)
                view = view[_pf <= 0] if premium_filter == "$0 only" else view[_pf > 0]
            view = view.sort_values("days_since_lost", ascending=True, na_position="last").reset_index(drop=True)

            st.caption(f"Showing **{len(view)}** clients · {window_label.lower()}"
                       + (f" · {carrier_filter}" if carrier_filter != "All" else "")
                       + (f" · {state_filter}" if state_filter != "All" else "")
                       + (f" · {reason_filter}" if reason_filter != "All" else "")
                       + (f" · {premium_filter}" if premium_filter != "All" else ""))

            if view.empty:
                st.info("No clients match the current filters.")
            else:
                disp = pd.DataFrame()
                disp["Name"]           = view["_name"]
                disp["Carrier"]        = view["carrier"] if "carrier" in view.columns else ""
                disp["State"]          = view["state"]   if "state"   in view.columns else ""
                disp["Lost"]           = view["term_date"].apply(_rel_day)
                disp["Term Date"]      = view["term_date"].dt.strftime("%b %d, %Y").where(view["term_date"].notna(), "Unknown")
                disp["Days Since Lost"]= view["days_since_lost"].fillna(0).astype(int)
                disp["Mo. on Book"]    = view["months_on_book"].fillna("?").astype(str).str.replace(r"\.0$", "", regex=True)
                disp["Members"]        = view["applicant_count"].fillna(1).astype(int) if "applicant_count" in view.columns else 1
                if "net_premium" in view.columns:
                    _prem = pd.to_numeric(view["net_premium"], errors="coerce")
                    disp["Premium"]    = _prem.apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
                disp["Reason"]         = (view["cancel_reason"].fillna("").replace("", "—")
                                          if "cancel_reason" in view.columns else "—")
                disp["Urgency"]        = view["Urgency"]

                def _row_color(row):
                    u = str(row.get("Urgency",""))
                    if "🔴" in u: return ["background-color: rgba(231,76,60,0.15)"] * len(row)
                    if "🟡" in u: return ["background-color: rgba(243,156,18,0.15)"] * len(row)
                    if "🟠" in u: return ["background-color: rgba(230,126,34,0.10)"] * len(row)
                    return [""] * len(row)

                # Lean view by default (relative "Lost" date); toggle reveals the
                # exact date + tenure/member columns.
                _show_all = st.checkbox("Show all columns", value=False, key="reengage_showall")
                _lean = ["Name", "Carrier", "State", "Lost", "Premium", "Reason", "Urgency"]
                _cols = list(disp.columns) if _show_all else [c for c in _lean if c in disp.columns]
                st.dataframe(disp[_cols].style.apply(_row_color, axis=1),
                             use_container_width=True, hide_index=True, height=520)

                # ── Quick Text — copy & paste into your CRM ────────────────
                st.markdown("<br>", unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown(
                        chart_head("Quick Text", "Pick a client, copy the message, paste into your CRM", "users"),
                        unsafe_allow_html=True,
                    )
                    _opts = view["_name"].tolist()
                    _pick = st.selectbox("Client", _opts, key="reengage_msg_pick", label_visibility="collapsed")
                    _prow = view[view["_name"] == _pick].iloc[0]
                    _first = str(_prow.get("first_name") or (_pick.split()[0] if _pick else "")).strip().title()
                    _carrier = str(_prow.get("carrier") or "").strip()
                    _plan = f"{_carrier} plan" if _carrier and _carrier.lower() not in ("none", "nan") else "health plan"
                    _msg = (
                        f"Hey {_first}, it's Ethan, your insurance guy. Looks like another agent "
                        f"took over your {_plan} and I lost access. Did you authorize that? "
                        f"Let me know ASAP!"
                    )
                    st.code(_msg, language=None)
                    st.caption("Tap the copy icon at the top-right of the box, then paste it into your CRM.")

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
# RE-ENGAGE (SUPPLEMENTAL)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Supplemental Re-Engage":
    st.title("Re-Engage (Supplemental)")
    st.caption("Two categories: clients past due (still active, behind on payment — save them before they cancel) "
               "and lapsed policies to win back.")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    today_ts = pd.Timestamp(dt.date.today())
    supp_df  = dd.get("supp_df")
    _SUPP_PAYMENT_NUMBER = "(800) 657-8205"   # call-in number to update payment / reinstate

    def _supp_quick_text(_view, _key):
        """Copy/paste win-back text pointing the client to the payment line."""
        st.markdown("<br>", unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown(chart_head("Quick Text", "Pick a client, copy the message, paste into your CRM", "users"),
                        unsafe_allow_html=True)
            _opts = _view["_name"].tolist()
            if not _opts:
                return
            _pick = st.selectbox("Client", _opts, key=_key, label_visibility="collapsed")
            _prow = _view[_view["_name"] == _pick].iloc[0]
            _first = str(_prow.get("first_name") or (_pick.split()[0] if _pick else "")).strip().title()
            _prod = str(_prow.get("product") or "").strip().lower()
            _what = _prod if _prod else "supplemental coverage"
            _msg = (
                f"Hi {_first}, Ethan here — your insurance guy. Heads up: your {_what} "
                f"lapsed from a payment issue. To get it back, just call {_SUPP_PAYMENT_NUMBER} "
                f"and update your payment. If you have questions, let me know."
            )
            st.code(_msg, language=None)
            st.caption("Tap the copy icon at the top-right of the box, then paste it into your CRM.")

    if supp_df is None or len(supp_df) == 0:
        st.info("No supplemental data found. Drop your UHOne and Allstate exports in carrier_books and run a report.")
    else:
        sdf = supp_df.copy()
        sdf["status"]   = sdf["status"].astype(str).str.strip()
        sdf["_name"]    = (sdf["first_name"].fillna("").astype(str).str.strip() + " "
                           + sdf["last_name"].fillna("").astype(str).str.strip()).str.strip()
        sdf["_carrier"] = sdf["carrier"].apply(_supp_carrier_label)
        sdf["premium"]  = pd.to_numeric(sdf["premium"], errors="coerce")

        grace  = sdf[sdf["status"] == "Grace Period"].copy()
        lapsed = sdf[sdf["status"] == "Inactive"].copy()

        tab_grace, tab_lapsed = st.tabs([
            f"⚠️ Past Due — Grace Period ({len(grace)})",
            f"Lapsed ({len(lapsed)})",
        ])

        # ── CATEGORY 1: Grace Period (still active, behind on payment) ─────────
        with tab_grace:
            if grace.empty:
                st.success("No supplemental policies are past due right now. 🎉")
            else:
                st.caption("Still active but behind on payment — a quick call to update payment keeps the policy "
                           "without re-enrolling. Work these first.")
                _gp = grace["premium"].fillna(0).sum()
                k1, k2, k3 = st.columns(3)
                with k1:
                    st.markdown(stat_card("Past-Due Policies", f"{len(grace):,}", "clock", GOLD), unsafe_allow_html=True)
                with k2:
                    st.markdown(stat_card("Premium at Risk / Mo", f"${_gp:,.0f}", "dollar", RED), unsafe_allow_html=True)
                with k3:
                    st.markdown(stat_card("Clients", f"{grace['_name'].nunique():,}", "users", ELEC), unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)
                gf1, gf2 = st.columns(2)
                with gf1:
                    g_carriers = ["All"] + sorted(grace["_carrier"].dropna().unique().tolist())
                    g_carrier = st.selectbox("Carrier", g_carriers, key="supp_grace_carrier")
                with gf2:
                    g_states = ["All"] + sorted(grace["state"].dropna().unique().tolist()) if "state" in grace.columns else ["All"]
                    g_state = st.selectbox("State", g_states, key="supp_grace_state")

                gview = grace.copy()
                if g_carrier != "All":
                    gview = gview[gview["_carrier"] == g_carrier]
                if g_state != "All" and "state" in gview.columns:
                    gview = gview[gview["state"] == g_state]
                gview = gview.sort_values(["_carrier", "_name"]).reset_index(drop=True)

                gdisp = pd.DataFrame()
                gdisp["Name"]    = gview["_name"]
                gdisp["Carrier"] = gview["_carrier"]
                gdisp["Product"] = gview["product"]
                gdisp["Premium"] = gview["premium"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
                gdisp["Detail"]  = gview["status_detail"] if "status_detail" in gview.columns else ""
                gdisp["State"]   = gview["state"] if "state" in gview.columns else ""
                st.dataframe(gdisp, use_container_width=True, hide_index=True, height=440)

                _supp_quick_text(gview, "supp_grace_pick")

        # ── CATEGORY 2: Lapsed (cancelled — win back) ──────────────────────────
        with tab_lapsed:
            if lapsed.empty:
                st.success("No lapsed supplemental policies. 🎉")
            else:
                lapsed["term_date"]       = pd.to_datetime(lapsed.get("term_date"), errors="coerce")
                lapsed["days_since_lost"] = (today_ts - lapsed["term_date"]).dt.days.clip(lower=0)

                def _urgency(days):
                    if pd.isna(days): return "Unknown"
                    if days <= 30:    return "🔴 <30 days"
                    if days <= 60:    return "🟡 30-60 days"
                    if days <= 90:    return "🟠 60-90 days"
                    return "⚪ 90+ days"
                lapsed["Urgency"] = lapsed["days_since_lost"].apply(_urgency)

                last_30 = int((lapsed["days_since_lost"] <= 30).sum())
                last_60 = int((lapsed["days_since_lost"] <= 60).sum())
                _lost_prem = float(lapsed["premium"].fillna(0).sum())

                k1, k2, k3, k4 = st.columns(4)
                with k1:
                    st.markdown(stat_card("Lapsed Policies", f"{len(lapsed):,}", "users", ELEC), unsafe_allow_html=True)
                with k2:
                    st.markdown(stat_card("Lost < 30 Days", f"{last_30:,}", "clock", RED), unsafe_allow_html=True)
                with k3:
                    st.markdown(stat_card("Lost < 60 Days", f"{last_60:,}", "clock", GOLD), unsafe_allow_html=True)
                with k4:
                    st.markdown(stat_card("Lapsed Premium / Mo", f"${_lost_prem:,.0f}", "dollar", GREEN), unsafe_allow_html=True)

                st.markdown("<br>", unsafe_allow_html=True)
                f1, f2, f3 = st.columns(3)
                with f1:
                    window_opts  = {"Last 30 days": 30, "Last 60 days": 60, "Last 90 days": 90, "All time": 99999}
                    window_label = st.selectbox("Show lost in", list(window_opts.keys()), index=3, key="supp_lapsed_window")
                    window_days  = window_opts[window_label]
                with f2:
                    carriers = ["All"] + sorted(lapsed["_carrier"].dropna().unique().tolist())
                    carrier_filter = st.selectbox("Carrier", carriers, key="supp_lapsed_carrier")
                with f3:
                    states = ["All"] + sorted(lapsed["state"].dropna().unique().tolist()) if "state" in lapsed.columns else ["All"]
                    state_filter = st.selectbox("State", states, key="supp_lapsed_state")

                view = lapsed[(lapsed["days_since_lost"] <= window_days) | (lapsed["days_since_lost"].isna() & (window_days >= 99999))].copy()
                if carrier_filter != "All":
                    view = view[view["_carrier"] == carrier_filter]
                if state_filter != "All" and "state" in view.columns:
                    view = view[view["state"] == state_filter]
                view = view.sort_values("days_since_lost", ascending=True, na_position="last").reset_index(drop=True)

                st.caption(f"Showing **{len(view)}** lapsed policies · {window_label.lower()}"
                           + (f" · {carrier_filter}" if carrier_filter != "All" else "")
                           + (f" · {state_filter}" if state_filter != "All" else ""))

                if view.empty:
                    st.info("No lapsed supplemental policies match the current filters.")
                else:
                    disp = pd.DataFrame()
                    disp["Name"]            = view["_name"]
                    disp["Carrier"]         = view["_carrier"]
                    disp["Product"]         = view["product"]
                    disp["Term Date"]       = view["term_date"].dt.strftime("%b %d, %Y").where(view["term_date"].notna(), "Unknown")
                    disp["Days Since Lost"] = view["days_since_lost"].fillna(0).astype(int)
                    disp["Premium"]         = view["premium"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
                    disp["State"]           = view["state"] if "state" in view.columns else ""
                    disp["Urgency"]         = view["Urgency"]

                    def _row_color(row):
                        u = str(row.get("Urgency", ""))
                        if "🔴" in u: return ["background-color: rgba(231,76,60,0.15)"] * len(row)
                        if "🟡" in u: return ["background-color: rgba(243,156,18,0.15)"] * len(row)
                        if "🟠" in u: return ["background-color: rgba(230,126,34,0.10)"] * len(row)
                        return [""] * len(row)

                    st.dataframe(disp.style.apply(_row_color, axis=1), use_container_width=True, hide_index=True, height=520)
                    _supp_quick_text(view, "supp_lapsed_pick")


# ══════════════════════════════════════════════════════════════════════════════
# FOLLOW-UPS  (HealthSherpa DMI/SVI verifications)
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Follow-ups":
    st.title("Verifications")
    st.caption("HealthSherpa verifications your clients still owe. **DMI** = income/coverage match; "
               "**SVI** = enrollment verification. If one **expires, the client loses their premium "
               "subsidy** and usually drops. **Open** ones are still savable — reach out before they expire.")
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    fu = _load_follow_ups()
    if fu is None or fu.empty:
        st.success("No outstanding verification follow-ups right now. 🎉")
    else:
        fu = fu.copy()
        fu["Status"] = fu.get("Status", "").astype(str).str.strip()
        _open_n = int((fu["Status"] == "Open").sum())
        _exp_n = int((fu["Status"] == "Expired").sum())
        fk1, fk2, fk3 = st.columns(3)
        with fk1:
            st.markdown(stat_card("Open — Save the Subsidy", f"{_open_n:,}", "clock", GOLD), unsafe_allow_html=True)
        with fk2:
            st.markdown(stat_card("Expired — Lost", f"{_exp_n:,}", "minus", RED), unsafe_allow_html=True)
        with fk3:
            st.markdown(stat_card("Total Follow-ups", f"{len(fu):,}", "shield", ELEC), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        ef1, ef2, ef3 = st.columns(3)
        with ef1:
            _fs = st.selectbox("Status", ["Open first", "Open only", "Expired only", "All"], key="fu_status")
        with ef2:
            _fc = st.selectbox("Carrier", ["All"] + sorted(fu["Carrier"].dropna().astype(str).unique().tolist()), key="fu_carrier")
        with ef3:
            _fst = st.selectbox("State", ["All"] + sorted(fu["State"].dropna().astype(str).unique().tolist()), key="fu_state")

        fv = fu.copy()
        if _fs == "Open only":
            fv = fv[fv["Status"] == "Open"]
        elif _fs == "Expired only":
            fv = fv[fv["Status"] == "Expired"]
        if _fc != "All":
            fv = fv[fv["Carrier"].astype(str) == _fc]
        if _fst != "All":
            fv = fv[fv["State"].astype(str) == _fst]
        # Sort by due date: soonest (most urgent) first, undated (expired) last.
        fv["_due"] = pd.to_datetime(fv.get("Due Date"), errors="coerce")
        fv = fv.sort_values("_due", ascending=True, na_position="last").reset_index(drop=True)

        fd = pd.DataFrame({
            "Name": (fv["First Name"].fillna("") + " " + fv["Last Name"].fillna("")).str.strip().str.title(),
            "Due Date": fv["_due"].dt.strftime("%b %d, %Y").fillna("—"),
            "Status": fv["Status"],
            "Follow-up": fv.get("Follow-up", ""),
            "Detail": fv.get("Detail", ""),
            "Carrier": fv["Carrier"],
            "State": fv["State"],
            "Phone": fv.get("Phone", ""),
        })
        st.caption(f"Showing **{len(fd)}** follow-ups · soonest due first"
                   + (f" · {_fs}" if _fs not in ('Open first', 'All') else "")
                   + (f" · {_fc}" if _fc != 'All' else "") + (f" · {_fst}" if _fst != 'All' else ""))
        st.dataframe(fd, use_container_width=True, hide_index=True, height=min(120 + len(fd) * 34, 620))
        st.caption("📅 **Due Date** = the deadline to resolve the verification (pulled from your HealthSherpa "
                   "DMI/SVI exports). **Open** items show their due date — reach out before it passes. **Expired** "
                   "items have no date (already lost) and are moved to Cancelled → Re-Engage.")


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
            c1.markdown(stat_card("Renewed", f"{_renewed}", "refresh", GREEN), unsafe_allow_html=True)
            c2.markdown(stat_card("Contacted", f"{_contacted}", "users", ELEC), unsafe_allow_html=True)
            c3.markdown(stat_card("Not Started", f"{_not_start}", "clock", GOLD), unsafe_allow_html=True)
            c4.markdown(stat_card("Lost", f"{_lost}", "minus", RED), unsafe_allow_html=True)

            st.markdown("<br>", unsafe_allow_html=True)

            # Progress bar (custom gradient)
            _prog_val = (_renewed + _lost) / max(_total, 1)
            st.markdown(
                f'<div class="tp-head"><span class="tp-title">Overall progress — '
                f'{_renewed + _contacted} of {_total} clients touched ({_done_pct}% fully resolved)</span></div>'
                f'<div class="target-track"><div class="target-fill" style="width:{min(_prog_val,1.0)*100:.1f}%;'
                f'background:linear-gradient(90deg,{BLUE},{GREEN});box-shadow:0 0 18px rgba(34,197,94,0.4);"></div></div>',
                unsafe_allow_html=True,
            )
            st.markdown("<br>", unsafe_allow_html=True)

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

            # ── Editable table (auto-saves on each edit) ───────────────────
            # Re-apply the active filters to map edited view-rows back to the
            # original rows positionally (name keys fail on duplicate names).
            def _aep_autosave():
                _state = st.session_state.get("aep_editor", {})
                _changes = _state.get("edited_rows", {}) if isinstance(_state, dict) else {}
                if not _changes:
                    return
                _merged = st.session_state.aep_df.copy()
                _mask = pd.Series([True] * len(_merged), index=_merged.index)
                _fs  = st.session_state.get("aep_f_state",   "All States")
                _fc  = st.session_state.get("aep_f_carrier", "All Carriers")
                _fst = st.session_state.get("aep_f_status",  "All Statuses")
                if _fs  != "All States":   _mask &= _merged["State"]   == _fs
                if _fc  != "All Carriers": _mask &= _merged["Carrier"] == _fc
                if _fst != "All Statuses": _mask &= _merged["Status"]  == _fst
                _orig = _merged.index[_mask].tolist()
                for _pos_str, _vals in _changes.items():
                    _pos = int(_pos_str)
                    if _pos < len(_orig):
                        _oi = _orig[_pos]
                        if "Status" in _vals: _merged.at[_oi, "Status"] = _vals["Status"]
                        if "Notes"  in _vals: _merged.at[_oi, "Notes"]  = _vals["Notes"]
                if _save_aep_tab(_aep_tab, _merged):
                    st.session_state.aep_df = _merged
                    st.toast("Saved ✓")
                else:
                    st.toast("Save failed — check connection", icon="⚠️")

            _view_display = _view.reset_index(drop=True).copy()
            st.data_editor(
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
                on_change=_aep_autosave,
            )
            st.caption("✓ Changes save automatically.")


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Settings":
    st.title("Settings")
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Data freshness: when each source was last pulled ─────────────────────
    st.markdown(section_header("Data Freshness", "clock"), unsafe_allow_html=True)
    st.markdown("When each data source was last pulled into the site.")
    _fresh = pd.DataFrame()
    try:
        # Local mode only: build live from file mtimes — but only if the source
        # files actually exist here. On Streamlit Cloud they don't (the repo has
        # no data/input files), so fall through to the sheet tab instead of
        # showing a wall of "never".
        from tracker.freshness import build_freshness as _bf, _SOURCES as _fsrc
        if any(Path(p).exists() for _, p in _fsrc):
            _fresh = _bf()   # local: live file times
    except Exception:
        pass
    if _fresh.empty:
        try:   # cloud: the tab the report wrote
            _v = _tab_values("Data Freshness")
            if len(_v) > 1:
                _fresh = pd.DataFrame(_v[1:], columns=_v[0])
        except Exception:
            pass
    if not _fresh.empty:
        def _age_icon(a):
            a = str(a)
            if a in ("today", "yesterday", "—"):
                return "🟢 " + a
            try:
                d = int(a.split()[0])
            except Exception:
                return a
            return ("🟢 " if d <= 3 else "🟡 " if d <= 7 else "🔴 ") + a
        _fd = _fresh.drop(columns=["_days"], errors="ignore").copy()
        _fd["Age"] = _fd["Age"].map(_age_icon)
        st.dataframe(_fd, use_container_width=True, hide_index=True,
                     height=46 + 35 * len(_fd))
        st.caption("🟢 3 days or fresher · 🟡 up to a week · 🔴 stale — ask Claude to re-pull it. "
                   "\"Website push\" is when the site's numbers were last refreshed.")
    else:
        st.caption("Freshness data appears after the next report run.")

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.markdown(section_header("Carrier Appointments", "gear"), unsafe_allow_html=True)
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
