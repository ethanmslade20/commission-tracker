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
_HS_MIN_ROWS = 1000      # full-book export sanity floor (partial = ~170)


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

    # HealthSherpa client export — validate size before it can touch the book.
    hs = _newest("on_ex_applications-export-*.csv")
    if fresh(hs):
        rows = sum(1 for _ in open(hs, errors="replace")) - 1
        if rows >= _HS_MIN_ROWS:
            shutil.copy(hs, _ROOT / "input" / "healthsherpa.csv")
            staged.append(f"HealthSherpa ({rows} rows)")
        else:
            _log(f"REJECTED partial HealthSherpa export: {hs.name} ({rows} rows)")
            _text(f"⚠️ Your HealthSherpa export only has {rows} clients — looks like "
                  f"the Date Range was 'Last 30 days'. Re-export with Custom "
                  f"01/01/2025 → today (both boxes). Nothing was uploaded.")
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
