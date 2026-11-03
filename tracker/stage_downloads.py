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
  Policies*.csv                         Ambetter book, layout used since Oct 2026: converted
                                        back to the old layout (ambetter_export) ->
                                        carrier_books/ambetter.csv; raw -> data/carrier_reports/ambetter/
  Oscar_INDIVIDUAL_Book_*.csv           Oscar book    -> carrier_books/oscar.csv
  Producer ToolBox*Clients report.csv   Anthem book   -> carrier_books/anthem.csv
  Jarvis*BookOfBusiness*.xlsx           UHC book      -> carrier_books/uhc_source.xlsx
  book_of_business*.csv                 GA Access / Get Covered IL agent book: EVERY new pull
                                        -> snapshots/roster/<st>_<date>.parquet (dated history),
                                        the newest whole one per state (not partial)
                                        -> input/book_of_business_<st>.csv

State: data/.staged_downloads.json remembers what's been staged so each file
is processed exactly once. Files must be >15s old (download finished).

The folder is ~/Downloads unless $TRACKER_DOWNLOADS_DIR or settings.yaml
`downloads_dir` points elsewhere (tests use a scratch folder).
"""
import json
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _downloads_dir() -> Path:
    import os
    if os.environ.get("TRACKER_DOWNLOADS_DIR"):
        return Path(os.environ["TRACKER_DOWNLOADS_DIR"]).expanduser()
    try:
        from tracker.config import load_settings
        _s = load_settings().get("downloads_dir")
        if _s:
            return Path(_s).expanduser()
    except Exception:
        pass
    return Path.home() / "Downloads"


_DL = _downloads_dir()
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


def _stage_access_books(fresh, mark) -> list:
    """GA Access / Get Covered IL agent books (book_of_business*.csv). Ethan 2026-10-28.

    EVERY unstaged pull is written to the dated roster history (the Oct 11 GA and IL
    pulls sat in Downloads unused because nothing picked them up, and the tracker ran
    a 45-day-old GA book labelled as this month). Then the newest pull per state is
    staged into input/book_of_business_<st>.csv, the old copy backed up as .bak_<ts>
    (his manual convention), with the export's own mtime kept so the tracker knows its
    age. A pull that looks PARTIAL next to the pull before it (state_roster.pull_partial,
    the roster rule's own test) is recorded but never staged: GA-book-only clients
    missing from it would lapse and be texted as fresh losses. The newest whole pull
    of the sweep is staged instead (or nothing, keeping the last full book), with one
    text asking for a re-pull. Header-only or unreadable downloads are skipped, with a
    text (a failed export would otherwise go unnoticed until the 14-day stale-book
    alarm). Another agent's book (its rows name a different agent) is never recorded
    or staged: the access ingest has no agent filter, so every client in it would
    count as his (the same rule as the HealthSherpa branch: Phase-B audit files must
    never auto-stage). The state comes from the address majority. A carrier-less
    renewal-season pull is fine to stage: the ingest
    (tracker/state_roster.resolve_access_rows) resolves it person by person."""
    from tracker import state_roster as SR
    try:
        from tracker.config import load_settings
        snap = Path(load_settings()["snapshot_dir"])
    except Exception:
        snap = _ROOT / "snapshots"

    def _m(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return None
    files = [p for p in _DL.glob("book_of_business*.csv")
             if _m(p) is not None and time.time() - _m(p) > _MIN_AGE_S and fresh(p)]

    def _recent(m):
        # Text only about a download from the last 2 days: the first sweep also
        # walks every old pull in Downloads (a July failed export is not news).
        return time.time() - m < 2 * 86400
    pulls = {}
    for p in sorted(files, key=lambda q: _m(q) or 0):     # oldest → newest
        m = _m(p)
        try:
            raw = SR.read_book(p)
        except Exception as e:
            _log(f"SKIPPED state-exchange book {p.name}: unreadable ({type(e).__name__}: {e})")
            if _recent(m):
                _text(f"⚠️ GA/IL book download {p.name} can't be read — nothing was staged. "
                      f"Re-export the book.")
            mark(p, m)
            continue
        if raw.empty:
            _log(f"SKIPPED header-only state-exchange book {p.name} (failed export)")
            if _recent(m):
                _text(f"⚠️ GA/IL book download {p.name} is empty (headers only — a failed export). "
                      f"Nothing was staged; re-export the book.")
            mark(p, m)
            continue
        ok, who = SR.book_agent(raw)
        if not ok:
            _log(f"REJECTED state-exchange book {p.name}: another agent's book ({who}) — NOT recorded or staged")
            if _recent(m):
                _text(f"⚠️ {p.name} is another agent's GA/IL book ({who}) — NOT staged, your book is unchanged.")
            mark(p, m)
            continue
        st = SR.majority_state(raw)
        if st not in SR.ROSTER_STATES:
            _log(f"SKIPPED state-exchange book {p.name}: no GA/IL address majority ({st or 'none'})")
            mark(p, m)
            continue
        md5 = SR.file_md5(p)
        try:
            SR.write_roster(raw, st, SR.export_time(p), p.name, md5, snap)
        except Exception as e:
            _log(f"!! roster history not written for {p.name}: {type(e).__name__}: {e}")
        pulls.setdefault(st, []).append((m, p, md5, raw))
        mark(p, m)

    staged = []
    for st, lst in sorted(pulls.items()):
        # The newest pull of the sweep that looks whole. A partial one is left in the
        # roster history (where the roster rule sees it as partial too) and texted once.
        try:
            roster = SR.load_roster(snap, st)
        except Exception as e:
            roster = None
            _log(f"!! {st} roster history unreadable, partial-pull check skipped: {type(e).__name__}: {e}")
        pick, bad = None, None
        for m, p, md5, raw in reversed(lst):
            try:
                why = SR.pull_partial(roster, SR.keyed_pull(raw, SR.export_time(p), p.name, md5), SR.export_time(p))
            except Exception as e:
                why = ""
                _log(f"!! partial-pull check failed for {p.name}: {type(e).__name__}: {e}")
            if not why:
                pick = (m, p, md5)
                break
            _log(f"NOT staged {p.name}: looks partial ({why}) — the last full {st} book stays")
            bad = bad or (m, p, why)
        if bad and _recent(bad[0]):
            _text(f"⚠️ {SR.BOOK_LABELS.get(st, st + ' book')} download {bad[1].name} looks partial ({bad[2]}) — "
                  f"not used, the tracker keeps your last full pull. Re-pull the {st} book; if it really "
                  f"shrank, a matching pull on a later day is used.")
        if pick is None:
            continue
        m, p, md5 = pick
        dest = _ROOT / "input" / f"book_of_business_{st.lower()}.csv"
        if dest.exists():
            try:
                if SR.file_md5(dest) == md5:
                    continue                                 # this pull is already staged
                if dest.stat().st_mtime >= m:
                    _log(f"NOT staged {p.name}: input/{dest.name} is newer (staged by hand?)")
                    continue
                shutil.copy2(dest, dest.with_name(dest.name + time.strftime(".bak_%Y%m%d_%H%M%S")))
            except Exception as e:
                _log(f"!! could not back up input/{dest.name}, {p.name} NOT staged: {e}")
                continue
        shutil.copy2(p, dest)
        staged.append(f"{st} book ({p.name})")
    return staged


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

    # Both of these used to call stat() unguarded, and mark() runs AFTER the file has
    # been handed to the ingest — which moves it. The window is small but real, and a
    # FileNotFoundError here kills the whole watcher run partway through, leaving later
    # files unmarked and the state file unwritten. A watcher that dies silently is the
    # worst failure mode available: the next pull looks fine and never ingests.
    def _mtime(p):
        try:
            return p.stat().st_mtime
        except (FileNotFoundError, OSError):
            return None

    def fresh(p):
        if p is None:
            return False
        m = _mtime(p)
        if m is None:
            return False                      # vanished between glob and check
        return state.get(str(p)) != m

    def mark(p, mtime=None):
        m = mtime if mtime is not None else _mtime(p)
        if m is None:
            # Already moved by the ingest. Record that we handled it so the next run
            # does not reprocess a path that no longer exists.
            state[str(p)] = state.get(str(p), 0) or -1
            return
        state[str(p)] = m

    # HealthSherpa client export — validate size AND ownership before it can
    # touch the book. Another agent's export (Phase-B audit files!) must never
    # auto-stage into this tracker.
    hs = _newest("on_ex_applications-export-*.csv")
    _hs_mtime = _mtime(hs) if hs is not None else None
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
        mark(hs, _hs_mtime)

    # Carrier books keep their DOWNLOAD's mtime (copy2 / utime): the GA/IL roster rule
    # dates a book by its file (state_roster.carrier_evidence), and a book staged after
    # midnight would otherwise count as the next day's evidence. Data Freshness then
    # shows when the book was pulled, not when the watcher copied it.
    # Ambetter zip → newest policies_*.csv inside
    zp = _newest("policies*.zip")
    if fresh(zp):
        try:
            with zipfile.ZipFile(zp) as z:
                names = [n for n in z.namelist() if n.startswith("policies_") and n.endswith(".csv")]
                if names:
                    _amb = _ROOT / "carrier_books" / "ambetter.csv"
                    with z.open(sorted(names)[-1]) as src, open(_amb, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    _zm = _mtime(zp)
                    if _zm is not None:
                        import os as _os_u
                        _os_u.utime(_amb, (_zm, _zm))
                    staged.append("Ambetter book")
        except Exception as e:
            _log(f"ambetter zip failed: {e}")
        mark(zp)

    # Ambetter's portal stopped sending the zip in Oct 2026: the book now comes as a plain
    # Policies.csv in a new layout whose term dates hide lapses, so it is never staged as
    # is. ambetter_export rebuilds the old layout; the raw file is kept beside the older
    # raw pulls in data/carrier_reports/ambetter/. "Policies.csv" is a name any portal
    # could use, so it must carry Ambetter's columns and his NPN.
    cp = _newest("[Pp]olicies*.csv")
    if fresh(cp):
        _cm = _mtime(cp)
        try:
            import pandas as pd
            from tracker.ambetter_export import SIGNATURE, normalize
            from tracker.config import get_agent
            raw = pd.read_csv(cp, dtype=str, keep_default_na=False)
            _zm = _mtime(zp) if zp is not None else None
            if not all(c in raw.columns for c in SIGNATURE):
                _log(f"SKIPPED {cp.name}: not an Ambetter book (missing Ambetter columns)")
            elif not raw["Broker NPN"].astype(str).str.contains(get_agent()["npn"], regex=False).any():
                _log(f"SKIPPED {cp.name}: no row under his NPN — not his Ambetter book")
            elif _zm is not None and _cm is not None and _zm > _cm:
                _log(f"SKIPPED {cp.name}: an Ambetter zip pulled later is already staged")
            else:
                _when = time.localtime(_cm or time.time())
                pulled = pd.Timestamp(time.strftime("%Y-%m-%d", _when))
                _raw_dir = _ROOT / "data" / "carrier_reports" / "ambetter"
                _raw_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cp, _raw_dir / time.strftime("Policies_%Y%m%d%H%M%S.csv", _when))
                _amb = _ROOT / "carrier_books" / "ambetter.csv"
                normalize(raw, pulled).to_csv(_amb, index=False)
                if _cm is not None:
                    import os as _os_u
                    _os_u.utime(_amb, (_cm, _cm))
                staged.append("Ambetter book")
        except Exception as e:
            _log(f"ambetter csv failed: {type(e).__name__}: {e}")
        mark(cp, _cm)

    # The 4th element is a header column the file MUST contain, or None to accept it on
    # the filename alone. Only the NatGen export needs one: see its entry below.
    for pattern, dest, label, marker in [
        ("Oscar_INDIVIDUAL_Book_*.csv", "carrier_books/oscar.csv", "Oscar book", None),
        ("Producer ToolBox*Clients report.csv", "carrier_books/anthem.csv", "Anthem book", None),
        ("Jarvis*BookOfBusiness*.xlsx", "carrier_books/uhc_source.xlsx", "UHC book", None),
        ("BookOfBusinessExport*.xlsx", "carrier_books/cigna.xlsx", "Cigna book", None),
        # Allstate / National General supplemental — dental, cancer, STM. supplemental.py
        # has always named "Policy List" as this book's source, but nothing ever staged it,
        # so carrier_books/supp_natgen.csv sat at its 2026-07-10 copy while fresh exports
        # piled up in Downloads unread and the site quietly reported a stale supplemental
        # book. Matched on a header rather than the filename alone, because "Policy List"
        # is generic enough that another portal could plausibly use it and silently
        # overwrite this book with someone else's data.
        ("Policy List*.csv", "carrier_books/supp_natgen.csv", "NatGen supp book", "STM Indicator"),
    ]:
        p = _newest(pattern)
        if fresh(p):
            if marker:
                try:
                    head = open(p, errors="replace").readline()
                except Exception:
                    head = ""
                if marker not in head:
                    _log(f"SKIPPED {label}: {p.name} has no {marker!r} column — not this carrier's export")
                    mark(p)
                    continue
            shutil.copy2(p, _ROOT / dest)
            staged.append(label)
            mark(p)

    try:
        staged += _stage_access_books(fresh, mark)
    except Exception as e:
        _log(f"!! GA/IL book staging failed: {type(e).__name__}: {e}")

    # Forget paths that are gone. Without this the state grows without bound and keeps
    # mtimes for files the ingest deleted months ago.
    import os as _os
    state = {k: v for k, v in state.items() if v == -1 or _os.path.exists(k)}

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
