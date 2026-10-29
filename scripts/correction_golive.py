"""
Go-live helper for a tracker correction (see tracker/correction.py).

A fix that re-sorts many clients at once would otherwise make the next upload
text read like a burst of brand-new losses / win-backs, and make the AOR alert
text "call them today" for a backlog. After Ethan approves the per-person
before/after list, this writes:

  data/correction_pending.json  — the approved "out" / "in" people. The next
                                  upload text shows them on ONE "🧹 Tracker
                                  correction (not new)" line, then deletes it.
  data/aor_alerted.json         — pre-seeded with the approved "out" people who
  data/aor_alerted_ids.json       are now "AOR taken" (name + Exchange ID), so the
                                  AOR alert never texts them; a different client
                                  with the same name on another app id still alerts.

Steps:

  1. build — per-policy diff of two "All Clients" tables (a CSV exported from the
     Sheet's All Clients tab, a parquet, or a test-harness pickle holding
     'final_all'), written as a CSV for approval (default data/correction_diff.csv,
     which is git-ignored: it holds client names):
       python scripts/correction_golive.py build --before B --after A [--out data/x.csv]
     More than 100 changes is refused without --force (a wrong input, e.g. the
     summary rows of a tab export read as the header, looks like that).

  2. apply — from the APPROVED diff CSV (delete rows Ethan did not approve):
       python scripts/correction_golive.py apply data/correction_diff.csv [--note "..."] [--write]
     Dry run unless --write. Writes into THIS checkout's data/ (or --data-dir).

  3. check — is the marker + pre-seed in place? (exit code 1 when not)
       python scripts/correction_golive.py check [--data-dir DIR]

GO-LIVE ORDER (merging IS going live: within ~2 minutes the Downloads watcher's
sweep stages the GA/IL books and kicks auto_update.sh, and every report run can
send the AOR alert — not only HealthSherpa uploads):
  a. BEFORE merging, save the "before" table from the live state (the Sheet's All
     Clients tab as CSV, or a harness run of main on a copy of the live data/,
     snapshots/, input/, carrier_books/), and the "after" table from a harness run
     of the branch on the same copy. The first sweep after the merge overwrites
     the Sheet, so "before" can't be taken later.
  b. build, and get Ethan's approval of the list.
  c. apply --write into the LIVE data/ folder, then check.
  d. Only then merge. Let the launchd watcher do the report runs (never a manual
     `track report`).
"""
import argparse
import json
import pickle
import re
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
_LOST = {"Cancelled", "Terminated"}


def _load_table(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix == ".pkl":
        with open(p, "rb") as fh:
            obj = pickle.load(fh)
        return obj["final_all"] if isinstance(obj, dict) else obj
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    # A CSV exported from the Sheet's All Clients tab has summary rows ("Active
    # Policies: …") above the header: use the first row that names first_name.
    import csv
    with open(p, newline="") as fh:
        rows = list(csv.reader(fh))
    norm = lambda c: str(c).strip().lower().replace(" ", "_")
    hdr = [i for i, r in enumerate(rows[:20]) if "first_name" in {norm(c) for c in r}]
    if not hdr:
        raise SystemExit(f"{p}: no header row with a 'first_name' column in the first 20 rows")
    h = hdr[0]
    cols = [norm(c) for c in rows[h]]
    body = [(r + [""] * len(cols))[:len(cols)] for r in rows[h + 1:] if any(str(c).strip() for c in r)]
    return pd.DataFrame(body, columns=cols).replace({"": None})


def _s(v) -> str:
    """Cell as text; blank for None/NaN."""
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def _app(v) -> str:
    v = re.sub(r"\.0$", "", _s(v))
    return "" if v.lower() in ("", "nan", "none") else v


def _rows(df: pd.DataFrame) -> dict:
    """{policy key: row} — key = ffm_app_id, else name|state (one row per policy)."""
    out = {}
    for _, r in df.iterrows():
        nm = f"{_s(r.get('first_name'))} {_s(r.get('last_name'))}".strip()
        app = _app(r.get("ffm_app_id"))
        k = app or f"{nm.lower()}|{_s(r.get('state')).upper()}"
        # an app id can only appear once after the same-policy collapse; prefer the active row
        if k in out and str(out[k].get("status")) in _ACTIVE:
            continue
        out[k] = {"name": nm, "state": _s(r.get("state")).upper(), "ffm_app_id": app,
                  "status": _s(r.get("status")), "reason": _s(r.get("cancel_reason")),
                  "members": _s(r.get("applicant_count"))}
    return out


def _category(row):
    if row is None:
        return "absent"
    if row["status"] in _ACTIVE:
        return "active"
    if row["status"] in _LOST:
        return "taken" if row["reason"].startswith("AOR taken") else "lost"
    return row["status"] or "other"


def _check_ignored(path: Path) -> bool:
    """True when git ignores `path` (client names must never be committed). Outside a
    git checkout (a test copy), only this checkout's data/ folder is accepted."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(_ROOT), "check-ignore", "-q", str(path)],
                           capture_output=True)
        if r.returncode in (0, 1):
            return r.returncode == 0
    except Exception:
        pass
    return Path(path).resolve().is_relative_to((_ROOT / "data").resolve())


def build(before: str, after: str, out_csv: str, force: bool = False) -> None:
    out = Path(out_csv).resolve()
    if not _check_ignored(out):
        raise SystemExit(f"Refusing to write {out}: git does not ignore it, and it holds client names. "
                         f"Use a path under data/ (the default).")
    b, a = _rows(_load_table(before)), _rows(_load_table(after))
    recs = []
    for k in sorted(set(b) | set(a)):
        rb, ra = b.get(k), a.get(k)
        cb, ca = _category(rb), _category(ra)
        if cb == ca:
            continue
        r = ra or rb
        # "out": no longer counted active, or newly in loss history / now marked taken.
        # "in": counted active again. "gone": a duplicate row removed (no text impact).
        if ca == "active":
            chg = "in"
        elif ca in ("lost", "taken"):
            chg = "out"
        else:
            chg = "gone"
        recs.append({"change": chg, "name": r["name"], "state": r["state"], "ffm_app_id": r["ffm_app_id"],
                     "before": cb, "before_reason": (rb or {}).get("reason", ""),
                     "after": ca, "after_reason": (ra or {}).get("reason", ""),
                     "members": r.get("members")})
    d = pd.DataFrame(recs, columns=["change", "name", "state", "ffm_app_id", "before", "before_reason",
                                    "after", "after_reason", "members"])
    if len(d) > 100 and not force:
        raise SystemExit(f"{len(d)} changes — far more than a correction should make. Check the inputs "
                         f"(both tables from the same data? a tab export read with the right header?). "
                         f"Re-run with --force only if this is really intended. Nothing written.")
    d.to_csv(out, index=False)
    print(f"Wrote {out}: {len(d)} change(s) — " + ", ".join(
        f"{c} {int((d['change'] == c).sum())}" for c in ("out", "in", "gone")))


def apply(diff_csv: str, note: str, data_dir: Path, write: bool) -> None:
    d = pd.read_csv(diff_csv, dtype=str).fillna("")
    d = d[d["change"].str.lower().isin(["out", "in", "-active", "+active"])]
    side = d["change"].str.lower().map({"out": "out", "-active": "out", "in": "in", "+active": "in"})

    def _item(r):
        it = {"name": r["name"].strip(), "state": r.get("state", "").strip()}
        if _app(r.get("ffm_app_id")):
            it["ffm_app_id"] = _app(r.get("ffm_app_id"))
        return it
    marker = {"out": [_item(r) for _, r in d[side == "out"].iterrows()],
              "in": [_item(r) for _, r in d[side == "in"].iterrows()],
              "note": note or ""}

    # aor_alerted keys = letters of the display name, exactly as alert_new_aor_changes builds
    # them; aor_alerted_ids.json ties each to the approved app id, so a later steal of a
    # DIFFERENT client with the same name (another app id) still alerts.
    taken = d[(side == "out") & d.get("after_reason", pd.Series("", index=d.index)).str.startswith("AOR taken")]
    seed = {re.sub(r"[^a-z]", "", n.lower()) for n in taken["name"]} - {""}
    seed_ids = {}
    for _, r in taken.iterrows():
        k, x = re.sub(r"[^a-z]", "", r["name"].lower()), _app(r.get("ffm_app_id"))
        if k and x:
            seed_ids.setdefault(k, set()).add(x)
    alerted_p = data_dir / "aor_alerted.json"
    ids_p = data_dir / "aor_alerted_ids.json"
    try:
        alerted = set(json.loads(alerted_p.read_text()))
    except Exception:
        alerted = set()
    try:
        ids_map = {k: set(v) for k, v in json.loads(ids_p.read_text()).items()}
    except Exception:
        ids_map = {}
    print(f"Marker: {len(marker['out'])} out / {len(marker['in'])} in"
          + (f" — note: {note}" if note else ""))
    print(f"AOR alert pre-seed: {len(seed)} taken name(s), {len(seed - alerted)} not yet in aor_alerted.json: "
          f"{', '.join(sorted(seed - alerted)[:12])}")
    if not write:
        print("Dry run — nothing written (add --write).")
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "correction_pending.json").write_text(json.dumps(marker, indent=1))
    # A name already alerted with no id on file keeps the legacy behaviour (silent);
    # give it the approved id too so the same policy stays silent.
    for k, xs in seed_ids.items():
        ids_map.setdefault(k, set()).update(xs)
    alerted_p.write_text(json.dumps(sorted(alerted | seed), indent=1))
    ids_p.write_text(json.dumps({k: sorted(v) for k, v in sorted(ids_map.items())}, indent=1))
    print(f"Wrote {data_dir / 'correction_pending.json'}, {alerted_p.name} and {ids_p.name}")


def check(data_dir: Path) -> int:
    """0 when the correction marker and the AOR alert pre-seed are in place."""
    ok = True
    mp = data_dir / "correction_pending.json"
    try:
        m = json.loads(mp.read_text())
        print(f"OK  {mp}: {len(m.get('out') or [])} out / {len(m.get('in') or [])} in")
    except Exception as e:
        print(f"!!  {mp}: missing or unreadable ({type(e).__name__}) — the next upload text would "
              f"report the correction as new losses")
        ok = False
    ip = data_dir / "aor_alerted_ids.json"
    try:
        n = sum(len(v) for v in json.loads(ip.read_text()).values())
        print(f"OK  {ip}: {n} app id(s)")
    except Exception as e:
        print(f"!!  {ip}: missing or unreadable ({type(e).__name__}) — the AOR alert would text the "
              f"corrected steals as new")
        ok = False
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="per-policy before/after diff → CSV for approval")
    b.add_argument("--before", required=True)
    b.add_argument("--after", required=True)
    b.add_argument("--out", default=str(_ROOT / "data" / "correction_diff.csv"))
    b.add_argument("--force", action="store_true", help="allow more than 100 changes")
    a = sub.add_parser("apply", help="approved diff CSV → correction marker + AOR alert pre-seed")
    a.add_argument("diff_csv")
    a.add_argument("--note", default="")
    a.add_argument("--data-dir", default=str(_ROOT / "data"))
    a.add_argument("--write", action="store_true")
    c = sub.add_parser("check", help="is the marker + AOR alert pre-seed in place?")
    c.add_argument("--data-dir", default=str(_ROOT / "data"))
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.before, args.after, args.out, args.force)
    elif args.cmd == "check":
        return check(Path(args.data_dir))
    else:
        print(f"Data dir: {args.data_dir}")
        apply(args.diff_csv, args.note, Path(args.data_dir), args.write)


if __name__ == "__main__":
    sys.exit(main())
