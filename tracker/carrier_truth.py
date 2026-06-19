"""
Carrier-portal truth overlay (Phase 2).

Applies a carrier's own book of business as the source of truth on top of the
HealthSherpa-built client list, for that carrier only:

  * In the carrier portal (active)         -> stays active
  * In the carrier portal (termed)         -> marked Cancelled (carrier term date)
  * NOT in the portal, coverage started    -> marked Cancelled (dropped off portal)
  * NOT in the portal, coverage not started-> kept active (safety net for new sales
                                              that lag a few days in the portal)
  * In the portal but missing from tracker -> added to the active book

HealthSherpa stays the source for new-business / daily-tracker timing; this only
adjusts the book/status side. Currently implemented for Ambetter.
"""

import re
import unicodedata
from pathlib import Path

import pandas as pd

_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}


def _clean_id(x) -> str:
    return re.sub(r"[^0-9]", "", str(x))


def _name_key(first, last) -> str:
    s = f"{first} {last}".lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s)


def _months_on_book(eff, today) -> float:
    if pd.isna(eff):
        return 0.0
    return round(max((today - eff).days, 0) / 30.44, 1)


def apply_ambetter_truth(all_clients: pd.DataFrame,
                         carrier_books_dir: str = "carrier_books",
                         today=None):
    """Return (adjusted_all_clients, summary_dict). No-op if no Ambetter book."""
    today = pd.Timestamp(today) if today else pd.Timestamp.today().normalize()
    book = Path(carrier_books_dir) / "ambetter.csv"
    if all_clients.empty or not book.exists():
        return all_clients, {"applied": False}

    amb = pd.read_csv(book)
    amb["sid"] = amb["Exchange Subscriber ID"].apply(_clean_id)
    amb["nm"] = amb.apply(lambda r: _name_key(r["Insured First Name"], r["Insured Last Name"]), axis=1)
    amb["eff"] = pd.to_datetime(amb["Policy Effective Date"], errors="coerce")
    amb["term"] = pd.to_datetime(amb["Policy Term Date"], errors="coerce")
    amb["termed"] = amb["term"] < today
    amb_active, amb_termed = amb[~amb["termed"]], amb[amb["termed"]]

    aa_sid, aa_nm = set(amb_active["sid"]) - {""}, set(amb_active["nm"])
    at_sid, at_nm = set(amb_termed["sid"]) - {""}, set(amb_termed["nm"])

    ac = all_clients.copy()
    ac["_sid"] = ac["ffm_subscriber_id"].apply(_clean_id) if "ffm_subscriber_id" in ac.columns else ""
    ac["_nm"] = ac.apply(lambda r: _name_key(r.get("first_name", ""), r.get("last_name", "")), axis=1)
    ac["_eff"] = pd.to_datetime(ac.get("effective_date"), errors="coerce")
    is_amb = ac["carrier"].astype(str).str.contains("ambetter", case=False, na=False)
    is_active = ac["status"].isin(_ACTIVE)

    n_cancel_termed = n_cancel_dropped = n_protected = 0
    for idx in ac.index[is_amb & is_active]:
        sid, nm = ac.at[idx, "_sid"], ac.at[idx, "_nm"]
        if (sid and sid in aa_sid) or nm in aa_nm:
            continue  # confirmed active in portal
        eff = ac.at[idx, "_eff"]
        if (sid and sid in at_sid) or nm in at_nm:
            ac.at[idx, "status"] = "Cancelled"          # portal says termed
            tmatch = amb_termed[(amb_termed["sid"] == sid) | (amb_termed["nm"] == nm)]
            if not tmatch.empty and "term_date" in ac.columns and pd.notna(tmatch.iloc[0]["term"]):
                ac.at[idx, "term_date"] = tmatch.iloc[0]["term"]
            n_cancel_termed += 1
        elif pd.notna(eff) and eff > today:
            n_protected += 1                            # safety net: new sale, not yet in portal
        else:
            ac.at[idx, "status"] = "Cancelled"          # established but absent from portal
            if "term_date" in ac.columns and pd.isna(ac.at[idx, "term_date"]):
                ac.at[idx, "term_date"] = today
            n_cancel_dropped += 1

    # Add portal-active clients the tracker doesn't have
    t_sid = set(ac.loc[is_amb, "_sid"]) - {""}
    t_nm = set(ac.loc[is_amb, "_nm"])
    missing = amb_active[~amb_active["sid"].isin(t_sid) & ~amb_active["nm"].isin(t_nm)]
    new_rows = []
    for _, r in missing.iterrows():
        eff = r["eff"]
        new_rows.append({
            "first_name": r["Insured First Name"],
            "last_name": r["Insured Last Name"],
            "carrier": "Ambetter",
            "effective_date": eff,
            "term_date": pd.NaT,
            "status": "Effectuated",
            "state": r.get("State"),
            "ffm_app_id": "",
            "ffm_subscriber_id": r["sid"],
            "net_premium": pd.to_numeric(r.get("Member Responsibility"), errors="coerce"),
            "applicant_count": pd.to_numeric(r.get("Number of Members"), errors="coerce"),
            "months_on_book": _months_on_book(eff, today),
        })

    ac = ac.drop(columns=["_sid", "_nm", "_eff"])
    if new_rows:
        ac = pd.concat([ac, pd.DataFrame(new_rows)], ignore_index=True)

    summary = {
        "applied": True,
        "portal_active": len(amb_active),
        "portal_termed": len(amb_termed),
        "cancelled_termed": n_cancel_termed,
        "cancelled_dropped": n_cancel_dropped,
        "protected_new_sales": n_protected,
        "added_from_portal": len(new_rows),
    }
    return ac, summary
