"""
Downloads → tracker auto-stager (the hands-free half of the daily pull).

launchd watches ~/Downloads; whenever a recognized export lands, this script
validates it, stages it into the tracker, and kicks auto_update.sh (which runs
ingest + report, which in turn texts the upload summary). The human part of
the pull shrinks to: click Export on the website. Everything after the
download is automatic.

Recognized files:
  on_ex_applications-export-*.csv       HealthSherpa client export -> input/healthsherpa.csv
                                        (rejected + texted if it's a partial "Last 30 days" file)
  policies*.zip                         Ambetter book -> carrier_books/ambetter.csv
  Oscar_INDIVIDUAL_Book_*.csv           Oscar book    -> carrier_books/oscar.csv
  Producer ToolBox*Clients report.csv   Anthem book   -> carrier_books/anthem.csv
  Jarvis*BookOfBusiness*.xlsx           UHC book      -> carrier_books/uhc_source.xlsx

State: data/.staged_downloads.json remembers what's been staged so each file
is processed exactly once. Files must be >15s old (download finished).
"""
import json
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DL = Path.home() / "Downloads"
_STATE = _ROOT / "data" / ".staged_downloads.json"
_LOG = Path.home() / "Library" / "Logs" / "commission-tracker-downloads.log"
_MIN_AGE_S = 15          # let the browser finish writing
_HS_MIN_ROWS = 1000      # absolute floor: a "Last 30 days" partial is ~170
_HS_MIN_FRACTION = 0.85  # relative floor: reject an export smaller than 85% of the
                         # current book. Catches partials that clear the absolute
                         # floor — e.g. an export with "Include unsubmitted search &
                         # claimed applications" UNCHECKED drops ~29% (1,178 vs 1,656)
                         # yet is still >1000, so the fixed floor alone missed it.
_HS_ARCH_FRACTION = 0.85  # archived-retention floor. Archived clients are older/terminated,
                          # and a full book keeps essentially all of them (they rarely
                          # un-archive). A short DATE-WINDOW export ("Last 12 months") drops
                          # the OLDEST clients first, so its archived count collapses (50 vs
                          # 133 = 38%) while total rows still clear the 85% row floor because
                          # recent clients backfill. Reject when archived retention falls below
                          # this — the row floors alone can't see a windowed export. (Ethan
                          # 2026-08-25: a "Last 12 months" pull dropped 147 older clients and
                          # nearly false-texted 2 of them as AOR losses.)


def _log(msg):
    _LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


def _text(msg):
    try:
        cfg = json.loads((_ROOT / "data" / "alert_config.json").read_text())
        if cfg.get("phone") and cfg.get("lapse_alerts", True):
            from tracker.digest import send_imessage
            send_imessage(msg, cfg["phone"])
    except Exception as e:
        _log(f"(text failed: {e})")


def _newest(pattern):
    # Skip files younger than _MIN_AGE_S (browser may still be writing).
    # launchd fires instantly on appearance — too early for a fresh download —
    # so the plist ALSO runs this every 2 min (StartInterval) as a sweeper.
    # (Sleeping here doesn't work: launchd kills the sleeping child.)
    files = [p for p in _DL.glob(pattern)
             if time.time() - p.stat().st_mtime > _MIN_AGE_S]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _archived_yes(src, is_text=False):
    """How many rows have archived == 'Yes' in an HS export. Returns -1 if there's no
    'archived' column (can't tell). Used to catch a pull left on 'Archived = Not archived',
    which silently drops archived-but-active clients (found 2026-08-06: 'Not archived' =
    1,030 effectuated vs 'All' = 1,041, matching HS's live count)."""
    import csv, io
    try:
        f = io.StringIO(src) if is_text else open(src, errors="replace")
        r = csv.reader(f)
        header = next(r)
        if "archived" not in header:
            return -1
        i = header.index("archived")
        return sum(1 for row in r if len(row) > i and row[i].strip().lower() == "yes")
    except Exception:
        return -1


def main():
    # macOS TCC: launchd jobs need Full Disk Access (System Settings) to read
    # ~/Downloads. Without it, log the denial loudly instead of silently seeing
    # an empty folder (root cause of the 2026-07-06 sweeper mystery).
    try:
        next(_DL.iterdir(), None)
    except PermissionError:
        _log("!! BLOCKED: no permission to read ~/Downloads — grant Full Disk "
             "Access to Python in System Settings > Privacy & Security.")
        return
    state = {}
    if _STATE.exists():
        try:
            state = json.loads(_STATE.read_text())
        except Exception:
            state = {}

    staged = []

    def fresh(p):
        return p is not None and state.get(str(p)) != p.stat().st_mtime

    def mark(p):
        state[str(p)] = p.stat().st_mtime

    # HealthSherpa client export — validate size AND ownership before it can
    # touch the book. Another agent's export (Phase-B audit files!) must never
    # auto-stage into this tracker.
    hs = _newest("on_ex_applications-export-*.csv")
    if fresh(hs):
        body = open(hs, errors="replace").read()
        rows = body.count("\n") - 1
        from tracker.config import get_agent
        _npn = get_agent()["npn"]
        # Relative floor: compare against the book we last staged. A partial export
        # that clears the absolute floor (missing claimed/AOR apps → ~29% short) is
        # caught here where a fixed number can't see it. Grows/self-heals as the book
        # grows; a bigger export always passes, so the correct full export is never
        # blocked.
        _hs_dest = _ROOT / "input" / "healthsherpa.csv"
        _base = 0
        if _hs_dest.exists():
            try:
                _base = sum(1 for _ in open(_hs_dest, errors="replace")) - 1
            except Exception:
                _base = 0
        _rel_floor = int(_base * _HS_MIN_FRACTION) if _base > 0 else 0
        # Archived-completeness: a pull left on "Archived = Not archived" is only ~57 rows
        # short (well inside the 85% floor above), so the row guards can't see it — but it
        # silently drops archived-but-active clients. Catch it by the archived column.
        _base_arch = _archived_yes(_hs_dest)           # archived clients in the current book
        _new_arch = _archived_yes(body, is_text=True)  # archived clients in the incoming export
        if _npn not in body:
            _log(f"REJECTED foreign HealthSherpa export (no NPN {_npn} inside): {hs.name}")
            _text(f"⚠️ A HealthSherpa export landed in Downloads that isn't YOUR book "
                  f"(your NPN isn't in it) — probably another agent's audit file. "
                  f"Not uploaded. Move it to the audit folder instead.")
        elif rows < _HS_MIN_ROWS:
            _log(f"REJECTED partial HealthSherpa export: {hs.name} ({rows} rows < {_HS_MIN_ROWS} floor)")
            _text(f"⚠️ Your HealthSherpa export only has {rows} clients — looks like "
                  f"the Date Range was 'Last 30 days'. Re-export with Custom "
                  f"01/01/2025 → today (both boxes checked). Nothing was uploaded.")
        elif _rel_floor and rows < _rel_floor:
            _log(f"REJECTED short HealthSherpa export: {hs.name} ({rows} rows < {_rel_floor} "
                 f"= {_HS_MIN_FRACTION:.0%} of last book {_base}) — likely missing claimed/AOR apps")
            _text(f"⚠️ Your HealthSherpa export has {rows} clients but your book was {_base} — "
                  f"missing ~{_base - rows}. You likely left 'Include unsubmitted search & "
                  f"claimed applications' UNCHECKED. Re-export with a Custom date range and "
                  f"BOTH boxes checked. Nothing was uploaded.")
        elif _base_arch > 0 and _new_arch == 0:
            _log(f"REJECTED not-archived HealthSherpa export: {hs.name} (0 archived rows vs "
                 f"{_base_arch} in last book) — 'Archived status' left on 'Not archived'")
            _text(f"⚠️ Your HealthSherpa export has 0 archived clients but your book had "
                  f"{_base_arch}. You left 'Archived status' on 'Not archived' — that drops "
                  f"archived-but-active clients from the book. Re-export with Archived = All "
                  f"(Clients page → Archived status → All). Nothing was uploaded.")
        elif _base_arch > 0 and _new_arch < int(_base_arch * _HS_ARCH_FRACTION):
            _log(f"REJECTED windowed HealthSherpa export: {hs.name} ({_new_arch} archived vs "
                 f"{_base_arch} in last book = {_new_arch/_base_arch:.0%}, below "
                 f"{_HS_ARCH_FRACTION:.0%}) — Date Range too short (drops the oldest clients)")
            _text(f"⚠️ Your HealthSherpa export only has {_new_arch} archived clients but your "
                  f"book had {_base_arch} — the Date Range looks too short (e.g. 'Last 12 "
                  f"months'), which silently drops your oldest ~{_base - rows} clients. "
                  f"Re-export with Custom 01/01/2025 → today, Archived = All, both boxes "
                  f"checked. Nothing was uploaded.")
        else:
            shutil.copy(hs, _hs_dest)
            staged.append(f"HealthSherpa ({rows} rows)")
        mark(hs)

    # Ambetter zip → newest policies_*.csv inside
    zp = _newest("policies*.zip")
    if fresh(zp):
        try:
            with zipfile.ZipFile(zp) as z:
                names = [n for n in z.namelist() if n.startswith("policies_") and n.endswith(".csv")]
                if names:
                    with z.open(sorted(names)[-1]) as src, \
                         open(_ROOT / "carrier_books" / "ambetter.csv", "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    staged.append("Ambetter book")
        except Exception as e:
            _log(f"ambetter zip failed: {e}")
        mark(zp)

    for pattern, dest, label in [
        ("Oscar_INDIVIDUAL_Book_*.csv", "carrier_books/oscar.csv", "Oscar book"),
        ("Producer ToolBox*Clients report.csv", "carrier_books/anthem.csv", "Anthem book"),
        ("Jarvis*BookOfBusiness*.xlsx", "carrier_books/uhc_source.xlsx", "UHC book"),
        ("BookOfBusinessExport*.xlsx", "carrier_books/cigna.xlsx", "Cigna book"),
    ]:
        p = _newest(pattern)
        if fresh(p):
            shutil.copy(p, _ROOT / dest)
            staged.append(label)
            mark(p)

    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(state, indent=1))

    if staged:
        _log(f"staged: {', '.join(staged)} — kicking auto_update.sh")
        # auto_update.sh has its own lock + cooldown; run_report has a global
        # flock — so this can never race a manual run.
        subprocess.Popen(["/bin/bash", str(_ROOT / "auto_update.sh")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
