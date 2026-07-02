"""
Data freshness — when each source file was last pulled/updated.

Local mode reads file mtimes directly; run_report also writes this table to a
"Data Freshness" sheet tab so the cloud app can show it (cloud can't see local
files). The "Website push" row is stamped at report time, so on the live site
it doubles as "when the site's data was last refreshed."
"""
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent

_SOURCES = [
    ("HealthSherpa client export", _ROOT / "input" / "healthsherpa.csv"),
    ("Ambetter book",              _ROOT / "carrier_books" / "ambetter.csv"),
    ("Oscar book",                 _ROOT / "carrier_books" / "oscar.csv"),
    ("Anthem book",                _ROOT / "carrier_books" / "anthem.csv"),
    ("UHC book (Jarvis)",          _ROOT / "carrier_books" / "uhc_source.xlsx"),
    ("Supplemental — UHOne",       _ROOT / "carrier_books" / "supp_uhc.csv"),
    ("Supplemental — NatGen",      _ROOT / "carrier_books" / "supp_natgen.csv"),
    ("AOR at-risk list",           _ROOT / "data" / "aor_at_risk.json"),
]


def _fmt_age(days: float) -> str:
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{int(days)} days ago"


def build_freshness(now=None) -> pd.DataFrame:
    """One row per data source: when it was last updated and how old that is."""
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    rows = []
    for label, path in _SOURCES:
        if Path(path).exists():
            ts = pd.Timestamp(Path(path).stat().st_mtime, unit="s")
            age = (now - ts).total_seconds() / 86400
            rows.append({"Source": label,
                         "Last Updated": ts.strftime("%b %d, %Y · %I:%M %p"),
                         "Age": _fmt_age(age),
                         "_days": round(age, 1)})
        else:
            rows.append({"Source": label, "Last Updated": "never", "Age": "—", "_days": None})
    # Stamped at report time — on the cloud site this reads as "site data as of".
    rows.append({"Source": "Website push (last report run)",
                 "Last Updated": now.strftime("%b %d, %Y · %I:%M %p"),
                 "Age": "—", "_days": 0.0})
    return pd.DataFrame(rows)
