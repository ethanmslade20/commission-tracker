"""
Weekly digest: new sales, new lapses, and at-risk policies for the last 7 days,
delivered to the agent's phone via iMessage (Messages.app + AppleScript).

Run by `track digest`; scheduled Monday mornings via launchd.
"""

import subprocess
from pathlib import Path

import pandas as pd

from tracker.ingest import load_all_snapshots
from tracker.diff import build_all_clients
from tracker import report as R
from tracker.carrier_truth import (apply_ambetter_truth, apply_oscar_truth,
                                   apply_uhc_truth, apply_anthem_truth)
from tracker.supplemental import load_supplemental
from tracker.pastdue import load_health_pastdue
from tracker.sheets import _coalesce_sale_date

_ROOT = Path(__file__).resolve().parent.parent
_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "t")


def _week_sales(latest_df, appointments, week_ago, today):
    """New business sold in the last 7 days (same Option-A logic as the Daily
    Tracker): submitted with coverage effective after the sale day."""
    if latest_df is None or latest_df.empty:
        return 0, 0
    df = R._filter_by_appointments(latest_df, appointments)
    if df.empty:
        return 0, 0
    sub = _coalesce_sale_date(df)
    if "effective_date" in df.columns:
        eff = pd.to_datetime(df["effective_date"], errors="coerce")
        is_new = (eff > sub).fillna(False)
        df, sub = df[is_new], sub[is_new]
    if sub.isna().all():
        return 0, 0
    sub = sub.dt.normalize()
    mask = (sub >= week_ago) & (sub <= today)
    mem = pd.to_numeric(df.get("applicant_count", pd.Series([1] * len(df))),
                        errors="coerce").fillna(1)
    return int(mask.sum()), int(mem[mask].sum())


def build_digest(today=None) -> str:
    today = pd.Timestamp(today).normalize() if today else pd.Timestamp.today().normalize()
    week_ago = today - pd.Timedelta(days=7)

    months = load_all_snapshots(_ROOT / "snapshots")
    months = {m: R._filter_excluded(d, R._load_exclusions()) for m, d in months.items()}
    appts = R._load_appointments()

    # New sales (last 7 days) from the most recent snapshot
    if months:
        latest_df = months[max(months.keys())]
        new_pol, new_mem = _week_sales(latest_df, appts, week_ago, today)
    else:
        new_pol = new_mem = 0

    # New lapses (last 7 days) from the reconciled book
    ac = R._filter_by_appointments(build_all_clients(months), appts)
    for f in (apply_ambetter_truth, apply_oscar_truth, apply_uhc_truth, apply_anthem_truth):
        ac, _ = f(ac, today=today)
    lapse_pol = lapse_mem = 0
    if not ac.empty:
        term = pd.to_datetime(ac.get("term_date"), errors="coerce")
        est = ac.get("term_estimated", pd.Series(False, index=ac.index)).apply(_as_bool)
        cnt = pd.to_numeric(ac.get("applicant_count", 1), errors="coerce").fillna(1)
        m = term.notna() & (term >= week_ago) & (term <= today) & (~est)
        lapse_pol, lapse_mem = int(m.sum()), int(cnt[m].sum())

    # At risk now
    supp = load_supplemental()
    grace = supp[supp["status"] == "Grace Period"] if not supp.empty else supp
    grace_n = len(grace)
    grace_prem = float(pd.to_numeric(grace.get("premium"), errors="coerce").fillna(0).sum()) if grace_n else 0.0

    pdue = load_health_pastdue(today=today)
    pdue_n = len(pdue)
    pdue_prem = float(pd.to_numeric(pdue.get("premium"), errors="coerce").fillna(0).sum()) if pdue_n else 0.0

    total_risk = grace_prem + pdue_prem

    lines = [
        f"📊 Weekly Digest — {today.strftime('%b %d')}",
        "",
        "NEW (last 7 days)",
        f"✅ Sales: {new_pol} policies / {new_mem} members",
        f"❌ Lapses: {lapse_pol} policies / {lapse_mem} members",
        "",
        "AT RISK NOW",
        f"⚠️ Health past due: {pdue_n} · ${pdue_prem:,.0f}/mo",
        f"⚠️ Supp grace: {grace_n} · ${grace_prem:,.0f}/mo",
        f"💰 Premium at risk: ${total_risk:,.0f}/mo",
    ]
    return "\n".join(lines)


def send_imessage(text: str, phone: str) -> None:
    """Send `text` to `phone` via Messages.app. Text is passed as an argv to the
    AppleScript so newlines/emoji survive intact."""
    script = (
        'on run {phoneNum, msg}\n'
        '  tell application "Messages"\n'
        '    set targetService to 1st account whose service type = iMessage\n'
        '    set targetBuddy to participant phoneNum of targetService\n'
        '    send msg to targetBuddy\n'
        '  end tell\n'
        'end run'
    )
    subprocess.run(["osascript", "-e", script, phone, text], check=True)
