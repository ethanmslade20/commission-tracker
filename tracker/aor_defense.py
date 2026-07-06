"""
AOR Defense — turn HealthSherpa's "AOR at risk" list into a workable call list.

Data sources (all local, PII-safe — data/ is gitignored):
  data/aor_at_risk.json   scraped from HealthSherpa's Clients → "AOR at risk" tab
                          [{name, exchange_id, type: changed|disconnected|both, last_synced}]
  data/aor_handled.json   {exchange_id: {outcome, date}} — what Ethan has dealt with
  input/healthsherpa.csv  the latest full client export (phone, carrier, premium, status)

Two very different situations on that list (Ethan's rule, 2026-07-01):
  "AOR was changed"          → another agent actually took the client. Fight/win back.
  "Marketplace disconnected" → usually STILL his client; he just clicks Reconnect to
                               confirm. Never treat these as lost.
The cloud app can't read local files, so run_report writes this table to the
"AOR Defense" sheet tab and the app falls back to reading that.
"""
import json
import re
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_RISK_PATH = _ROOT / "data" / "aor_at_risk.json"
_HANDLED_PATH = _ROOT / "data" / "aor_handled.json"
_BASELINE_PATH = _ROOT / "data" / "known_aor_risk.json"
_HS_PATH = _ROOT / "input" / "healthsherpa.csv"

# Blended per-member commission (net ÷ member-months across carriers ≈ $22-23).
_PMPM = 23.0

from tracker.config import get_agent
_AGENT = get_agent()
_ETHAN_NPN = _AGENT["npn"]


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def build_aor_defense(risk_path=_RISK_PATH, hs_path=_HS_PATH, handled_path=_HANDLED_PATH):
    """Merge the scraped at-risk list with the HealthSherpa export into the
    defense table. Returns None if the scraped list isn't available."""
    risk = _load_json(risk_path, None)
    if not risk:
        return None

    hs = pd.DataFrame()
    if Path(hs_path).exists():
        hs = pd.read_csv(hs_path, dtype=str, low_memory=False).fillna("")
        hs["_xid"] = hs["ffm_app_id"].str.strip()
        hs = hs.drop_duplicates("_xid", keep="first").set_index("_xid")

    handled = _load_json(handled_path, {})

    # SECOND DETECTOR: data/known_aor.json — the "active but credited to another
    # agent" set the report maintains from the FULLY-PROCESSED book (carrier
    # truth applied). It catches steals HealthSherpa's at-risk tab hasn't listed
    # yet, and clients whose policies vanished from the current export once the
    # steal completed (Tammy, Maurice). Union in anyone the scrape didn't have.
    def _fl_key(name):
        # first + last token only, so "Christopher Cody Lokey" == "Christopher Lokey"
        p = str(name).split()
        return re.sub(r"[^a-z]", "", (p[0] + p[-1]).lower()) if p else ""
    scraped_names = {_fl_key(r.get("name", "")) for r in risk}
    known_aor = _load_json(_ROOT / "data" / "known_aor.json", {})
    for k, disp in known_aor.items():
        if _fl_key(disp) in scraped_names:
            continue
        entry = {"name": disp, "exchange_id": "", "type": "changed", "last_synced": ""}
        if len(hs):
            parts = str(disp).split()
            fn, ln = (parts[0], parts[-1]) if len(parts) >= 2 else (str(disp), "")
            m = hs[(hs["first_name"].str.lower() == fn.lower())
                   & (hs["last_name"].str.lower().str.replace(r"[^a-z]", "", regex=True)
                      == re.sub(r"[^a-z]", "", ln.lower()))]
            if len(m):
                a = m["policy_aor"].fillna("").astype(str)
                foreign = m[a.str.strip().ne("") & ~a.str.contains("None")
                            & ~a.str.contains(_ETHAN_NPN) & ~a.str.contains(_AGENT["last_name"], case=False)]
                row = (foreign.iloc[0] if len(foreign) else m.iloc[-1])
                entry.update({
                    "exchange_id": str(row.get("ffm_app_id", "")).strip(),
                    "last_synced": str(row.get("last_ede_sync", ""))[:10],
                    "taken_by": (re.sub(r"\s*\(NPN.*\)", "", str(row.get("policy_aor", ""))).strip().title()
                                 if len(foreign) else ""),
                    "carrier": str(row.get("issuer", ""))[:34],
                    "state": str(row.get("state", "")),
                    "members": (lambda _n: 1 if pd.isna(_n) else max(int(_n), 1))(pd.to_numeric(row.get("applicant_count"), errors="coerce")),
                })
        risk.append(entry)

    rows = []
    for r in risk:
        xid = str(r.get("exchange_id", "")).strip()
        typ = r.get("type", "")
        kind = "Taken" if typ in ("changed", "both") else "Disconnected"
        m = hs.loc[xid] if (len(hs) and xid in hs.index) else None

        # Snapshot-sourced entries carry their own details (the client may be
        # gone from today's export); the export row overrides when present.
        taken_by = r.get("taken_by", "")
        carrier = r.get("carrier", "")
        state = r.get("state", "")
        members = int(r.get("members", 1) or 1)
        phone = status = ""
        if m is not None:
            aor = str(m.get("policy_aor", ""))
            if aor.strip() and _ETHAN_NPN not in aor and _AGENT["last_name"].lower() not in aor.lower():
                taken_by = re.sub(r"\s*\(NPN.*\)", "", aor).strip().title()
            carrier = str(m.get("issuer", ""))[:34]
            state = str(m.get("state", ""))
            phone = str(m.get("phone", ""))
            status = str(m.get("policy_status", ""))
            _mn = pd.to_numeric(m.get("applicant_count"), errors="coerce")
            members = 1 if pd.isna(_mn) else max(int(_mn), 1)

        h = handled.get(xid, {})
        # When it happened: the HealthSherpa sync date that detected the change —
        # the closest thing to "day they were taken" (clusters on the real dates).
        ts = pd.to_datetime(r.get("last_synced", ""), errors="coerce")
        rows.append({
            "Client": r.get("name", ""),
            "Type": kind,
            "Taken By": taken_by,
            "Detected": ts.strftime("%b %d, %Y") if pd.notna(ts) else "",
            "Days Ago": int((pd.Timestamp.today().normalize() - ts).days) if pd.notna(ts) else None,
            "Carrier": carrier,
            "State": state,
            "Members": members,
            "Est $/yr": round(members * _PMPM * 12),
            "Phone": phone,
            "Policy Status": status,
            "Handled": h.get("outcome", ""),
            "Exchange ID": xid,
        })

    df = pd.DataFrame(rows)
    # Fires first: taken, unhandled, NEWEST steal first (freshest = most
    # winnable). Unknown dates sink to the bottom, never masquerade as day 0.
    df["_open"] = (df["Handled"] == "").astype(int)
    df["_taken"] = (df["Type"] == "Taken").astype(int)
    df = (df.sort_values(["_taken", "_open", "Days Ago", "Est $/yr"],
                         ascending=[False, False, True, False], na_position="last")
            .drop(columns=["_open", "_taken"]).reset_index(drop=True))
    return df


def alert_new_aor_changes(df, send=None) -> list:
    """Text Ethan when someone NEW shows up as Taken (vs the saved baseline).
    Baseline only updates after a successful diff, so each client alerts once.
    Returns the list of newly-taken names."""
    if df is None or df.empty:
        return []
    taken = df[df["Type"] == "Taken"]
    cur = dict(zip(taken["Exchange ID"], taken["Client"]))

    first_run = not _BASELINE_PATH.exists()
    prev = set(_load_json(_BASELINE_PATH, []))
    new = [(x, n) for x, n in cur.items() if x not in prev]

    _BASELINE_PATH.write_text(json.dumps(sorted(cur), indent=1))
    if first_run:
        print(f"  AOR Defense: baseline initialized ({len(cur)} already taken — no text).")
        return []
    if not new:
        return []

    names = [n for _, n in new]
    print(f"  AOR Defense: {len(names)} NEWLY taken by another agent → texting")
    cfg = _load_json(_ROOT / "data" / "alert_config.json", {})
    phone = cfg.get("phone") if cfg.get("lapse_alerts", True) else None
    if phone:
        shown = ", ".join(names[:8]) + (f" +{len(names) - 8} more" if len(names) > 8 else "")
        msg = (f"🚨 AOR alert: {len(names)} client(s) newly taken by another agent:\n"
               f"{shown}\nCall them today — most don't know they were switched. "
               f"Full list on the AOR Defense page.")
        try:
            if send is None:
                from tracker.digest import send_imessage as send
            send(msg, phone)
            print(f"  AOR alert texted to {phone}")
        except Exception as e:
            print(f"  (AOR alert text failed: {e})")
    return names
