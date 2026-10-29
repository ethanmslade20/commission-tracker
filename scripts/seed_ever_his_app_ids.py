"""
One-time seed of data/ever_his_app_ids.json — every ffm_app_id whose policy_aor
EVER named the agent, with the carrier he held it on — from every HealthSherpa
client export still sitting in Downloads (on_ex_applications-export-*.csv).

Why: the ingest ownership filter keeps a row whose application was ever his
(tracker/ingest.py load_ever_his_app_ids). The monthly snapshots only keep the
LAST export of each month, so an app that was his for a week mid-month and then
flipped can be missing from them. The raw exports have every pull since February.
After this seed, every ingest appends to the file on its own; run this once.

It also dates each steal it can see: for an application that was his and then
showed another agent as AOR, the first export after his LAST export as AOR is
merged into data/aor_first_seen.json (the frozen "Taken On" date AOR Defense and
Re-Engage share; only ever moved EARLIER). Without it, a steal the ownership filter
used to hide (Leak A) is dated by its last Marketplace sync, ~today, and shows as a
fresh "call today" loss in this month's churn although it is weeks old.

Reads the exports only (never moves or edits them). Writes ONLY to the data/
folder of the checkout this script lives in — run it from the copy you mean to
change. Append-only: existing ids are never removed.

usage:
  python scripts/seed_ever_his_app_ids.py [--downloads DIR] [--dry-run]

Downloads folder: --downloads, else $TRACKER_DOWNLOADS_DIR, else settings.yaml
`downloads_dir`, else ~/Downloads.
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402

from tracker.config import get_agent, load_full_carrier_config, load_settings  # noqa: E402
from tracker.ingest import (_EVER_HIS_PATH, _aor_names_agent, _normalize_carrier,  # noqa: E402
                            clean_app_id, load_ever_his_app_ids, record_ever_his_app_ids)


def _downloads_dir(cli_value):
    if cli_value:
        return Path(cli_value).expanduser()
    if os.environ.get("TRACKER_DOWNLOADS_DIR"):
        return Path(os.environ["TRACKER_DOWNLOADS_DIR"]).expanduser()
    try:
        s = load_settings().get("downloads_dir")
        if s:
            return Path(s).expanduser()
    except Exception:
        pass
    return Path.home() / "Downloads"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--downloads", help="folder holding on_ex_applications-export-*.csv")
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    args = ap.parse_args()

    dl = _downloads_dir(args.downloads)
    settings = load_settings()
    agent = get_agent()
    npn, name = str(agent["npn"]), f"{agent['first_name']} {agent['last_name']}"
    parts = [p.lower() for p in name.split()]
    aliases = load_full_carrier_config(settings["carrier_config_path"]).get("carrier_aliases", {}) or {}

    files = sorted(dl.glob("on_ex_applications-export-*.csv"), key=lambda p: p.stat().st_mtime)
    print(f"Downloads: {dl} — {len(files)} HealthSherpa export(s)")
    print(f"Ledger:    {_EVER_HIS_PATH}")

    found, used, skipped = {}, 0, []
    last_his, flip = {}, {}
    for f in files:                                   # oldest → newest: newest held carrier wins
        try:
            df = pd.read_csv(f, dtype=str, encoding_errors="replace",
                             usecols=lambda c: c in ("ffm_app_id", "policy_aor", "issuer", "last_ede_sync"))
        except Exception as e:
            skipped.append(f"{f.name} ({type(e).__name__})")
            continue
        if not {"ffm_app_id", "policy_aor"}.issubset(df.columns):
            skipped.append(f"{f.name} (no ffm_app_id/policy_aor)")
            continue
        used += 1
        his = df["policy_aor"].apply(lambda a: _aor_names_agent(a, npn, parts))
        # Steal dating: his → remember this export; another agent after his last
        # export → that is the first sight of the steal (the row's own sync, when it
        # falls after his last export, else this export's time).
        ts = pd.Timestamp(datetime.fromtimestamp(f.stat().st_mtime))
        sync = pd.to_datetime(df.get("last_ede_sync"), format="%m/%d/%Y %H:%M:%S", errors="coerce") \
            if "last_ede_sync" in df.columns else pd.Series(pd.NaT, index=df.index)
        aor = df["policy_aor"].fillna("").astype(str).str.strip()
        foreign = ~his & ~aor.str.lower().isin(["", "none", "nan"])
        for app, is_his, is_for, sy in zip(df["ffm_app_id"], his, foreign, sync):
            app = clean_app_id(app)
            if not app:
                continue
            if is_his:
                last_his[app] = ts
                flip.pop(app, None)
            elif is_for and app in last_his and app not in flip:
                flip[app] = sy if pd.notna(sy) and last_his[app] < sy <= ts else ts
        iss = df["issuer"] if "issuer" in df.columns else pd.Series("", index=df.index)
        for app, car in zip(df.loc[his, "ffm_app_id"], iss[his]):
            app = clean_app_id(app)
            if not app:
                continue
            car = "" if pd.isna(car) else str(car).strip()
            car = (_normalize_carrier(car, aliases) or car) if car else ""
            if car:
                found[app] = car
            else:
                found.setdefault(app, "")

    known = load_ever_his_app_ids(Path(settings["snapshot_dir"]), npn, name)
    new_vs_known = sorted(set(found) - set(known))
    print(f"Read {used} export(s){f', skipped {len(skipped)}: ' + '; '.join(skipped[:5]) if skipped else ''}")
    print(f"Applications his AOR in any export: {len(found)} | already known from ledger+snapshots: "
          f"{len(set(found) & set(known))} | new: {len(new_vs_known)}")
    fs_path = _ROOT / "data" / "aor_first_seen.json"
    try:
        first_seen = json.loads(fs_path.read_text())
    except Exception:
        first_seen = {}
    moved = {}
    for app, d in flip.items():
        d = pd.Timestamp(d).strftime("%Y-%m-%d")
        if app not in first_seen or d < str(first_seen[app]):
            moved[app] = d
    print(f"Steals dated from the exports: {len(flip)} | earlier than aor_first_seen.json had: {len(moved)}")
    if args.dry_run:
        print("Dry run — nothing written.")
        return
    added = record_ever_his_app_ids({**known, **found})
    print(f"Wrote {_EVER_HIS_PATH} (+{added} new id(s)).")
    if moved:
        first_seen.update(moved)
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = fs_path.with_name(fs_path.name + ".tmp")
        tmp.write_text(json.dumps(first_seen, indent=1))
        os.replace(tmp, fs_path)
        print(f"Wrote {fs_path} ({len(moved)} steal date(s) moved earlier).")


if __name__ == "__main__":
    main()
