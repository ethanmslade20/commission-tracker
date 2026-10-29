"""
One-time backfill of the GA Access / Get Covered IL roster history
(snapshots/roster/<st>_<YYYY-MM-DD>.parquet) from every agent-book export still
sitting in Downloads (book_of_business*.csv).

Why: the roster rule and the carrier-less ingest (tracker/state_roster.py) need to
know who each earlier pull listed, and which pulls still showed carriers. From now
on the Downloads watcher (tracker/stage_downloads.py) writes every new pull on its
own; this records the pulls made before that existed (GA Apr 12 → Oct 28, IL
Jul 18 → Oct 11).

Reads the exports only (never moves, edits or stages them; input/ and
data/.staged_downloads.json are not touched). Writes ONLY to the snapshots/roster/
folder of the checkout this script lives in — run it from the copy you mean to
change. Idempotent: re-running replaces each file's own rows. Header-only files
and files with no GA/IL address majority are skipped.

usage:
  python scripts/backfill_access_roster.py [--downloads DIR] [--dry-run]

Downloads folder: --downloads, else $TRACKER_DOWNLOADS_DIR, else settings.yaml
`downloads_dir`, else ~/Downloads.
"""
import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from tracker import state_roster as SR  # noqa: E402
from tracker.config import load_settings  # noqa: E402


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
    ap.add_argument("--downloads", help="folder holding book_of_business*.csv")
    ap.add_argument("--dry-run", action="store_true", help="list only, write nothing")
    args = ap.parse_args()

    dl = _downloads_dir(args.downloads)
    snap = Path(load_settings()["snapshot_dir"])
    files = sorted(dl.glob("book_of_business*.csv"), key=lambda p: p.stat().st_mtime)
    if not files:
        print(f"No book_of_business*.csv in {dl}")
        return
    print(f"{len(files)} export(s) in {dl}{' (dry run)' if args.dry_run else ''} → {SR.roster_dir(snap)}")
    written = {}
    for p in files:
        try:
            raw = SR.read_book(p)
        except Exception as e:
            print(f"  skip {p.name}: unreadable ({type(e).__name__}: {e})")
            continue
        if raw.empty:
            print(f"  skip {p.name}: header only")
            continue
        st = SR.majority_state(raw)
        if st not in SR.ROSTER_STATES:
            print(f"  skip {p.name}: no GA/IL address majority ({st or 'none'})")
            continue
        ts = SR.export_time(p)
        cars = int(raw["carrier"].map(SR.has_carrier).sum()) if "carrier" in raw.columns else 0
        yrs = raw.get("application_year")
        mix = (yrs.map(lambda v: str(SR.app_year(v) or "blank")).value_counts().to_dict()
               if yrs is not None else {})
        print(f"  {st} {ts:%Y-%m-%d %H:%M}  {p.name:28s} {len(raw):4d} rows, {cars:4d} with a carrier, "
              f"years {mix}")
        if not args.dry_run:
            out = SR.write_roster(raw, st, ts, p.name, SR.file_md5(p), snap)
            written[out.name] = written.get(out.name, 0) + len(raw)
    if written:
        print(f"Wrote {len(written)} roster file(s): " + ", ".join(f"{k} ({v})" for k, v in sorted(written.items())))


if __name__ == "__main__":
    main()
