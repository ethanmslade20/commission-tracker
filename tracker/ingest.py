"""
Ingests CSV exports into normalized snapshots.

HealthSherpa: standard read, carrier column within the file.
Access portal (IL/GA): offset header, state extracted from address,
  status remapped, unenrolled and no-carrier rows dropped, deduped
  against same-month HealthSherpa snapshot before writing.
"""

import fnmatch
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

# Every ffm_app_id whose policy_aor EVER named the agent, with the carrier he held it
# on. Append-only (ids are never removed) — see load_ever_his_app_ids.
_EVER_HIS_PATH = Path(__file__).resolve().parent.parent / "data" / "ever_his_app_ids.json"


# Columns that every snapshot must have
CANONICAL_COLS = [
    "policy_id",
    "client_name",
    "carrier",
    "plan_name",
    "effective_date",
    "commission",
    "status",
]

# Extra columns carried through if present
EXTRA_COLS = [
    "first_name",
    "last_name",
    "ffm_app_id",
    "ffm_subscriber_id",
    "state",
    "metal_level",
    "net_premium",
    "subsidy",
    "applicant_count",
    # Tax household size (drives the FPL threshold for subsidy eligibility) — used by
    # the "$0 Plan Review" page to flag clients near the 100%-FPL floor. (Ethan 2026-08-26.)
    "household_size",
    "missing_count_flag",
    "submission_date",
    "created_date",
    "date_effectuated",
    "term_date",
    # HealthSherpa expiration_date = the real coverage-end date (see diff.build_all_clients)
    "coverage_end",
    "plan_start",
    "agent",
    "email",
    "phone",
    # Carrier-assigned policy ID (what's on the member's ID card)
    "policy_number",
    "cancel_notes",
    # HealthSherpa verification follow-ups (DMI/SVI)
    "dmi_outstanding",
    "dmi_expired",
    "svi_outstanding",
    "svi_expired",
    "followup_docs",
    # Current agent of record — used to flag AOR-taken clients on Re-Engage
    "policy_aor",
    # Ownership signal — did Ethan ever enroll/submit this? Gates AOR-taken to
    # clients he actually had, so never-mine noise isn't counted (Ethan 2026-08-15).
    "npn_used",
    "submitting_agent_name",
    # Last Marketplace sync — approximates WHEN the AOR change registered
    "last_ede_sync",
    # Which export this row came from ("healthsherpa" / "access"). Lets the
    # carrier-truth overlay skip state-based-marketplace (access) clients, who
    # are legitimately absent from the FFM carrier-portal files.
    "source",
    # GA/IL (access) rows only — see tracker/state_roster.resolve_access_rows: the
    # application year, the date of the pull the row's status came from, where its
    # carrier came from ("pull", "book Sep 13", "HealthSherpa", ...), and the real
    # application day (effective_date is that day floored to the 1st).
    "application_year",
    "export_date",
    "fill_source",
    "application_day",
]

# Last result of each GA/IL book ingest (kept prior copy? possible new sales?), read
# by run_report for the upload text. data/ is gitignored.
_ACCESS_STATUS_PATH = Path(__file__).resolve().parent.parent / "data" / "access_book_status.json"


def normalize_name(name: str) -> str:
    """Lowercase, strip accents, collapse whitespace, remove punctuation."""
    if not isinstance(name, str):
        return ""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def detect_source(filename: str, source_configs: dict) -> Optional[str]:
    """Match filename against source filename_patterns (glob-style, case-insensitive)."""
    fname = Path(filename).name.lower()
    for source, cfg in source_configs.items():
        for pattern in cfg.get("filename_patterns", []):
            if fnmatch.fnmatch(fname, pattern.lower()):
                return source
    return None


def _find_col(df_columns: list, name: str) -> Optional[str]:
    """Case-insensitive column lookup."""
    lower_map = {c.lower(): c for c in df_columns}
    return lower_map.get(name.lower())


def _extract_state(addr: str) -> str:
    """Extract 2-letter state code from US address string."""
    m = re.search(r'\b([A-Z]{2})\s+\d{5}', str(addr))
    return m.group(1) if m else ""


def clean_app_id(v) -> str:
    """ffm_app_id as a plain digit string ("1234567890.0" -> "1234567890"); "" when blank."""
    v = re.sub(r"\.0$", "", str(v if v is not None else "").strip())
    return "" if v.lower() in ("", "nan", "none") else v


def _aor_names_agent(aor, npn: str, name_parts: list) -> bool:
    """policy_aor names the agent: his NPN, or every part of his name."""
    a = str(aor or "")
    if npn and npn in a:
        return True
    al = a.lower()
    return bool(name_parts) and all(p in al for p in name_parts)


def load_ever_his_app_ids(snapshot_dir: Path, npn: str, name: str,
                          path: Path = _EVER_HIS_PATH) -> dict:
    """{ffm_app_id: carrier he held it on} for every application whose policy_aor
    EVER named the agent. Union of data/ever_his_app_ids.json (append-only, so an
    app that was his and was taken inside ONE month survives the current-month
    snapshot being rewritten on every ingest) and every *healthsherpa*.parquet.
    The json's carrier wins: it is refreshed on every ingest, so it is at least as
    new as the newest snapshot. (Leak A, Ethan 2026-10-28.)"""
    npn = str(npn or "").strip()
    name_parts = [p.strip().lower() for p in str(name or "").split() if p.strip()]
    ids = {}
    for sp in sorted(Path(snapshot_dir).glob("*healthsherpa*.parquet")):   # oldest → newest
        try:
            p = pd.read_parquet(sp)
        except Exception:
            continue
        if not {"ffm_app_id", "policy_aor"}.issubset(p.columns):
            continue
        his = p["policy_aor"].apply(lambda a: _aor_names_agent(a, npn, name_parts))
        cars = p["carrier"] if "carrier" in p.columns else pd.Series("", index=p.index)
        for app, car in zip(p.loc[his, "ffm_app_id"], cars[his]):
            app = clean_app_id(app)
            if not app:
                continue
            car = "" if pd.isna(car) else str(car).strip()
            if car:
                ids[app] = car
            else:
                ids.setdefault(app, "")
    stored = _read_ledger(path)
    for app, car in (stored or {}).items():
        app = clean_app_id(app)
        if not app:
            continue
        if car:
            ids[app] = str(car)
        else:
            ids.setdefault(app, "")
    return ids


class LedgerUnreadable(Exception):
    """data/ever_his_app_ids.json exists but can't be parsed."""


def _read_ledger(path: Path, strict: bool = False) -> dict:
    """The ledger as a dict; {} when it doesn't exist. An unreadable file reads as {}
    (so the ownership filter still has the snapshots), or raises LedgerUnreadable
    with strict=True — a writer must never overwrite it with a smaller set."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text())
        if not isinstance(d, dict):
            raise ValueError("not a JSON object")
        return d
    except Exception as e:
        print(f"  !! {path.name} is unreadable ({type(e).__name__}: {e}) — using the snapshots only")
        if strict:
            raise LedgerUnreadable(str(e))
        return {}


def record_ever_his_app_ids(ids: dict, path: Path = _EVER_HIS_PATH) -> int:
    """Merge {app_id: carrier} into data/ever_his_app_ids.json. Append-only: an id
    is never removed; a non-blank carrier refreshes the held carrier. Returns the
    number of app ids the file did not have before.

    The ids seeded from old Downloads exports exist nowhere else, so an unreadable
    file is NEVER overwritten: it is copied aside to .corrupt_<ts> first and this
    raises LedgerUnreadable (the caller says so loudly). The write goes to a temp
    file in data/ and is renamed into place, so a crash can't leave half a file."""
    import os
    import tempfile
    path = Path(path)
    try:
        stored = _read_ledger(path, strict=True)
    except LedgerUnreadable:
        import shutil
        bad = path.with_name(path.name + pd.Timestamp.now().strftime(".corrupt_%Y%m%d_%H%M%S"))
        try:
            shutil.copy2(path, bad)
        except Exception:
            pass
        raise LedgerUnreadable(f"{path.name} unreadable — left as is, copy at {bad.name}; "
                               f"restore it from a backup")
    before = set(stored)
    for app, car in ids.items():
        app = clean_app_id(app)
        if not app:
            continue
        car = "" if car is None or (isinstance(car, float) and pd.isna(car)) else str(car).strip()
        if car or app not in stored:
            stored[app] = car or stored.get(app, "")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ever_his_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(dict(sorted(stored.items())), indent=1))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return len(set(stored) - before)


def _read_csv(csv_path: Path, cfg: dict) -> pd.DataFrame:
    """
    Read the CSV using config-driven parameters.
    If 'true_columns' is set, the file has an offset header — read with
    header=None, skip the broken header row, and assign the real column names.
    """
    true_columns = cfg.get("true_columns")
    if true_columns:
        return pd.read_csv(
            csv_path,
            header=None,
            skiprows=1,
            dtype=str,
            encoding_errors="replace",
            names=true_columns,
            index_col=False,
            usecols=range(len(true_columns)),
        )
    return pd.read_csv(csv_path, dtype=str, encoding_errors="replace")


def _normalize_carrier(carrier: str, aliases: dict) -> Optional[str]:
    """
    Map a full carrier name to its canonical short name using the alias table.
    aliases is an ordered dict: canonical_name -> [keyword, ...].
    Returns None if no alias matches.
    """
    c = str(carrier).lower()
    for canonical, keywords in aliases.items():
        if any(kw in c for kw in keywords):
            return canonical
    return None


def normalize_dataframe(
    df: pd.DataFrame,
    source: str,
    source_configs: dict,
    full_config: Optional[dict] = None,
    ever_his_ids: Optional[set] = None,
) -> pd.DataFrame:
    cfg = source_configs[source]
    col_map        = cfg.get("column_map", {})
    name_concat    = cfg.get("name_concat", [])
    extra_columns  = cfg.get("extra_columns", {})
    date_cols      = cfg.get("date_columns", [])
    amount_cols    = cfg.get("amount_columns", [])
    skip_statuses  = set(cfg.get("skip_statuses", []))
    status_map     = cfg.get("status_map", {})
    require_carrier= cfg.get("require_carrier", False)
    require_agent  = cfg.get("require_agent")          # e.g. "Ethan Slade"
    require_ever_mine = cfg.get("require_ever_mine")    # keep only clients ever ours
    state_from_addr= cfg.get("state_from_address", False)
    default_ac     = cfg.get("default_applicant_count")
    flag_missing_ac= cfg.get("flag_missing_applicant_count", False)

    rename = {}

    # Map canonical columns from column_map
    for canonical, source_col in col_map.items():
        found = _find_col(list(df.columns), source_col)
        if found:
            rename[found] = canonical
        else:
            df[canonical] = None

    df = df.rename(columns=rename)

    # Build client_name from name_concat
    if name_concat:
        parts = []
        for col in name_concat:
            if col in df.columns:
                parts.append(df[col].fillna("").astype(str).str.strip())
            else:
                parts.append(pd.Series([""] * len(df), index=df.index))
        df["client_name"] = parts[0]
        for p in parts[1:]:
            df["client_name"] = df["client_name"].str.cat(p, sep=" ").str.strip()

    # Map extra columns
    for canonical_extra, source_col in extra_columns.items():
        found = _find_col(list(df.columns), source_col)
        if found and found != canonical_extra:
            df[canonical_extra] = df[found]
        elif not found:
            df[canonical_extra] = None

    # Extract state from address if no state column
    if state_from_addr and "address" in df.columns:
        df["state"] = df["address"].apply(_extract_state).replace("", None)

    # Apply status filters and remapping
    if skip_statuses and "status" in df.columns:
        df = df[~df["status"].isin(skip_statuses)].copy()
    if status_map and "status" in df.columns:
        df["status"] = df["status"].map(status_map).fillna(df["status"])

    # Filter to rows where the agent is the specified broker
    if require_agent and "agent" in df.columns:
        # Accept "Ethan Slade", "Slade, Ethan", any capitalisation
        parts = [p.strip().lower() for p in require_agent.split()]
        def _agent_match(val: str) -> bool:
            v = str(val).lower()
            return all(p in v for p in parts)
        before = len(df)
        df = df[df["agent"].apply(_agent_match)].copy()
        dropped = before - len(df)
        if dropped:
            print(f"    (agent filter: removed {dropped} rows not written by {require_agent})")

    # Keep ONLY clients the broker was ever the agent for: current agent of record
    # OR the enrolling agent (their NPN / name on the submission) OR an application
    # whose policy_aor named him in an earlier export (ever_his_ids). Anyone else is
    # another agent's client merely surfacing in the account — drop them.
    # The ever_his_ids test is Leak A (Ethan 2026-10-28): a client with a blank
    # npn_used whose AOR was his for months, then flipped, failed the other three
    # tests, so the flipped row was dropped and the stale "Ethan, active" row from
    # an older month kept counting them (one for ~8 weeks, 11 people in all).
    # Same "was the AOR ever his" test the never-mine guard uses, so it cannot let
    # in another agent's client that was never his.
    if require_ever_mine:
        npn        = str(require_ever_mine.get("npn", "")).strip()
        name_parts = [p.strip().lower() for p in str(require_ever_mine.get("name", "")).split()]
        aor_c      = require_ever_mine.get("aor_col", "policy_aor")
        npn_c      = require_ever_mine.get("npn_used_col", "npn_used")
        sub_c      = require_ever_mine.get("submitting_agent_col", "submitting_agent_name")

        def _name_match(val) -> bool:
            v = str(val or "").lower()
            return bool(name_parts) and all(p in v for p in name_parts)

        def _ever_mine(row) -> bool:
            aor = str(row.get(aor_c) or "")
            if npn and npn in aor:                 # I am the current agent of record
                return True
            if _name_match(aor):
                return True
            if npn and npn in str(row.get(npn_c) or ""):   # I enrolled it (my NPN)
                return True
            if _name_match(row.get(sub_c)):                # I submitted it
                return True
            return False

        def _was_his(row) -> bool:                         # AOR named me in an earlier export
            return bool(ever_his_ids) and clean_app_id(row.get("ffm_app_id")) in ever_his_ids

        before = len(df)
        _mine_now = df.apply(_ever_mine, axis=1) if len(df) else pd.Series(False, index=df.index)
        _was = (df.apply(_was_his, axis=1) & ~_mine_now) if len(df) else _mine_now
        df = df[_mine_now | _was].copy()
        dropped = before - len(df)
        if dropped:
            print(f"    (ownership filter: removed {dropped} clients the broker was never agent for)")
        if int(_was.sum()):
            print(f"    (ownership filter: kept {int(_was.sum())} row(s) whose application named the "
                  f"broker as AOR in an earlier export — their current AOR/status decides taken vs lost)")

    # Drop rows with no carrier when required
    if require_carrier and "carrier" in df.columns:
        blank = df["carrier"].isna() | df["carrier"].isin(["", "NA", "N/A", "nan"])
        df = df[~blank].copy()

    # State + carrier licensing matrix filter
    if full_config:
        aliases = full_config.get("carrier_aliases", {})
        matrix  = full_config.get("state_carrier_matrix", {})
        if aliases and matrix and "carrier" in df.columns and "state" in df.columns:
            def _allowed(row) -> bool:
                state = str(row.get("state") or "").strip().upper()
                if not state or state not in matrix:
                    return True   # no state data — let it through, don't silently drop
                canonical = _normalize_carrier(str(row.get("carrier") or ""), aliases)
                if canonical is None:
                    return True   # unrecognised carrier — let it through
                return canonical in matrix[state]

            before = len(df)
            # (On an EMPTY frame apply returns an untyped Series, which df[...] reads
            # as a column list and drops every column — guard it.)
            _ok = (df.apply(_allowed, axis=1).astype(bool) if len(df)
                   else pd.Series(True, index=df.index, dtype=bool))
            df = df[_ok].copy()
            dropped = before - len(df)
            if dropped:
                print(f"    (matrix filter: removed {dropped} unlicensed state/carrier combos)")

        # Canonicalize carrier names using the alias table
        if aliases and "carrier" in df.columns:
            df["carrier"] = df["carrier"].apply(
                lambda c: _normalize_carrier(str(c), aliases) or c
            )

    # Generate policy_id for sources that have no native ID field. An empty frame
    # (every row of a carrier-less GA/IL export dropped) used to crash here with
    # UFuncTypeError; it now comes back empty and the caller keeps the prior book.
    if len(df) == 0:
        df["policy_id"] = pd.Series(dtype=str)
    elif "policy_id" not in df.columns or df.get("policy_id", pd.Series()).isna().all():
        nk = df.apply(
            lambda r: normalize_name(
                str(r.get("first_name", "") or "") + " " +
                str(r.get("last_name", "") or "")
            ), axis=1
        )
        df["policy_id"] = source.upper() + "_" + nk

    # Default applicant_count and flag
    if default_ac is not None:
        if "applicant_count" not in df.columns:
            df["applicant_count"] = float(default_ac)
        else:
            df["applicant_count"] = pd.to_numeric(df["applicant_count"], errors="coerce")
            df["applicant_count"] = df["applicant_count"].fillna(float(default_ac))
        if flag_missing_ac:
            df["missing_count_flag"] = True

    # Clean amount columns
    for col in amount_cols:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r"[\$,\s]", "", regex=True)
                .replace({"": "0", "nan": "0", "NA": "0"})
            )
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # Parse date columns
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    # Normalize effective_date to the 1st of the month
    if "effective_date" in df.columns:
        df["effective_date"] = df["effective_date"].dt.to_period("M").dt.to_timestamp()

    # Normalized name key
    if "client_name" in df.columns:
        df["name_key"] = df["client_name"].apply(normalize_name)

    # Stamp the originating export so the carrier-truth overlay can tell FFM
    # (healthsherpa) clients apart from state-based-marketplace (access) clients.
    df["source"] = source

    # Keep only canonical + extra + name_key
    keep = [c for c in CANONICAL_COLS + EXTRA_COLS + ["name_key"] if c in df.columns]
    df = df[keep].copy()

    df = df.dropna(subset=["policy_id", "client_name"], how="all")
    df["policy_id"] = df["policy_id"].astype(str).str.strip()

    return df


def _dedup_against_healthsherpa(
    df: pd.DataFrame, month_str: str, snapshot_dir: Path
) -> pd.DataFrame:
    """
    Remove rows already present in the same-month HealthSherpa snapshot
    (matched by name_key). Prevents double-counting clients who appear in
    both HealthSherpa and an Access portal export.
    """
    hs_path = snapshot_dir / f"{month_str}_healthsherpa.parquet"
    if not hs_path.exists():
        return df
    hs = pd.read_parquet(hs_path)
    hs_keys = set(hs["name_key"].dropna()) if "name_key" in hs.columns else set()
    before = len(df)
    df = df[~df["name_key"].isin(hs_keys)].copy()
    dropped = before - len(df)
    if dropped:
        print(f"    (deduped {dropped} clients already in HealthSherpa snapshot)")
    return df


def _prior_access_snapshot(stem: str, month_str: str, snapshot_dir: Path) -> Optional[Path]:
    """Newest <month>_<stem>.parquet with month <= month_str, from ANY month."""
    best = None
    for p in Path(snapshot_dir).glob(f"*_{stem}.parquet"):
        m = p.name.split("_", 1)[0]
        if p.name == f"{m}_{stem}.parquet" and re.match(r"^\d{4}-\d{2}$", m) and m <= month_str:
            if best is None or m > best[0]:
                best = (m, p)
    return best[1] if best else None


def _record_access_status(stem: str, info: dict, path: Path = None) -> None:
    """Remember how the last ingest of a GA/IL book went (data/access_book_status.json)
    so run_report can put a failure or a possible new sale in the upload text."""
    path = Path(path or _ACCESS_STATUS_PATH)
    try:
        cur = json.loads(path.read_text())
    except Exception:
        cur = {}
    cur[stem] = {**info, "at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cur, indent=1, default=str))
    except Exception as e:
        print(f"  !! could not write {path.name}: {e}")


def _keep_prior_access(stem: str, month_str: str, snapshot_dir: Path, file_name: str, why: str,
                       partial: bool = False):
    """Keep the last good GA/IL book instead of a failed one: carry the newest earlier
    snapshot into this month (so the book stays dated like every other run did with a
    re-staged file) and record the failure for the upload text (partial=True: the pull
    looked partial, see state_roster.pull_partial). Returns (snapshot_path, frame), or
    None when there is no earlier snapshot."""
    prior = _prior_access_snapshot(stem, month_str, snapshot_dir)
    if prior is None:
        return None
    prev = pd.read_parquet(prior)
    cur = Path(snapshot_dir) / f"{month_str}_{stem}.parquet"
    if prior != cur:
        prev.to_parquet(cur, index=False)
    _ed = ""
    if "export_date" in prev.columns:
        _v = pd.to_datetime(prev["export_date"], errors="coerce").max()
        _ed = "" if pd.isna(_v) else f"{_v:%b %d}"
    _copy = _ed or f"{prior.name.split('_', 1)[0]} snapshot"
    print(f"  !! {why}; kept the previous {stem} book ({len(prev)} rows, the {_copy} copy). "
          f"Re-export the full book.")
    # No "possible new sale" list from a pull that was not used: the failure line is
    # the message (an all-unfillable pull would otherwise list everyone in it).
    _record_access_status(stem, {
        "file": file_name, "ok": False, "kept_prior": True, "rows": int(len(prev)),
        "kept_copy": _copy, "message": why, "unfilled": [], "partial": bool(partial),
    })
    return cur, prev


def _resolve_access(df: pd.DataFrame, csv_path: Path, snapshot_dir: Path, month: date,
                    dry_run: bool = False):
    """Run tracker.state_roster.resolve_access_rows on one GA/IL export: its own date
    (looked up in the roster by content, else the file's mtime), the roster history
    of EARLIER pulls, the newest HealthSherpa snapshot and the carrier books. A pull
    the roster has never seen (staged into input/ by hand, not via Downloads) is
    dated by its mtime and returned as rep["unrecorded"]: ingest_file records it in
    the roster once the pull is ACCEPTED (an empty or failed pull must not become
    the "newest" listing), so the roster rule and the book-age alarm judge against
    it too. rep["partial"]: why the pull looks partial next to the pull before it
    (state_roster.pull_partial, the roster rule's own test), "" when it looks whole;
    ingest_file then keeps the last good book and records this pull in the roster
    only, where the rule sees it as a partial pull."""
    from tracker import state_roster as SR
    md5 = SR.file_md5(csv_path)
    state = SR.majority_state(df)
    roster = SR.load_roster(snapshot_dir, state) if state else pd.DataFrame()
    export_ts = None
    if len(roster):
        _own = roster.loc[roster["source_md5"] == md5, "export_date"]
        if len(_own):
            export_ts = pd.Timestamp(_own.max())
    unrecorded = None
    if export_ts is None:
        export_ts = SR.export_time(csv_path)
        if state in SR.ROSTER_STATES and not dry_run:
            unrecorded = (df.copy(), state, export_ts, csv_path.name, md5)
    partial = ""
    if state in SR.ROSTER_STATES and len(roster):
        partial = SR.pull_partial(roster, SR.keyed_pull(df, export_ts, csv_path.name, md5), export_ts)
    hs_snaps = sorted(Path(snapshot_dir).glob("*healthsherpa*.parquet"),
                      key=lambda p: (p.name[:7], p.name))
    hs = pd.read_parquet(hs_snaps[-1]) if hs_snaps else None
    if hs is not None and state and "state" in hs.columns:
        hs = hs[hs["state"].astype(str).str.upper() == state]
    out, rep = SR.resolve_access_rows(df, export_ts, roster, hs, plan_year=month.year,
                                      evidence=SR.carrier_evidence(), source_md5=md5)
    rep["export_date"] = export_ts.strftime("%Y-%m-%d %H:%M")
    rep["unrecorded"] = unrecorded
    rep["partial"] = partial
    _carried = f", {rep['carried']} carried from an earlier {month.year} listing" if rep["carried"] else ""
    print(f"    ({state or 'access'} book {csv_path.name}, pulled {export_ts:%b %d %H:%M}: "
          f"{'shows carriers' if rep['normal_pull'] else 'NO carriers (renewal season)'} — "
          f"{rep['pull']} with a plan, {rep['filled_book']} filled from an earlier pull, "
          f"{rep['filled_hs']} from HealthSherpa{_carried}; {rep['no_plan']} no plan, "
          f"{rep['earlier_year']} earlier-year, {rep['later_year_only']} next-year-only, "
          f"{rep['ended_in_carrier_book']} ended in a carrier book"
          + (f": {', '.join(rep['ended_names'][:8])}" if rep.get("ended_names") else "") + ")")
    if rep["unfilled"]:
        print(f"    !! {len(rep['unfilled'])} active-status row(s) with NO carrier anywhere — possible "
              f"new {state} sale(s), confirm the carrier: "
              + ", ".join(u["name"] for u in rep["unfilled"][:10]))
    return out, rep


def ingest_file(
    csv_path: Path,
    source_configs: dict,
    snapshot_dir: Path,
    month: Optional[date] = None,
    dry_run: bool = False,
    full_config: Optional[dict] = None,
) -> tuple:
    """
    Read a CSV, detect source, normalize, optionally write snapshot.
    Returns (snapshot_path_or_None, dataframe).
    """
    source = detect_source(csv_path.name, source_configs)
    if source is None:
        raise ValueError(
            f"Could not detect source for '{csv_path.name}'. "
            "Check filename_patterns in carrier_configs.yaml."
        )

    cfg = source_configs[source]
    df = _read_csv(csv_path, cfg)

    # ACCESS-BOOK EMPTY GUARD (Ethan 2026-07-24): an empty state-exchange book
    # (book_of_business_*) is a failed/blank export (like the GA export on 7/24)
    # and would otherwise crash normalize; keep the prior snapshot instead of
    # wiping those clients. (The >50%-drop partial case is caught below the write.)
    # The prior snapshot is the newest one of this file from ANY month, so the first
    # run of a month is protected too (Ethan 2026-10-28).
    _stem0 = re.sub(r"\s+", "", csv_path.stem.strip().lower())
    if "book_of_business" in _stem0 and df.empty:
        _m0 = month or date.today().replace(day=1)
        _kept = None if dry_run else _keep_prior_access(
            _stem0, _m0.strftime("%Y-%m"), snapshot_dir, csv_path.name,
            f"{csv_path.name} is EMPTY — failed state-exchange export")
        if _kept is not None:
            return _kept

    # Fail with a human message, not a KeyError, when the file isn't the export
    # we expect (wrong download, changed format, partial file).
    if source == "healthsherpa":
        _required = {"first_name", "last_name", "policy_status", "effective_date", "policy_aor"}
        _missing = _required - set(df.columns)
        if _missing:
            raise SystemExit(
                f"!! {csv_path.name} doesn't look like a HealthSherpa client export — "
                f"missing columns: {', '.join(sorted(_missing))}.\n"
                f"   Re-export from Clients → Export (Date Range: Custom 01/01/2025 → today, "
                f"both checkboxes) and try again. Nothing was ingested.")

    # Applications that were EVER his (AOR named him in any earlier export): the
    # ownership filter keeps them even after the AOR flips, so a steal shows up as a
    # steal instead of the client freezing on an old "active, mine" row.
    _rem = cfg.get("require_ever_mine")
    ever_his = None
    if _rem:
        ever_his = load_ever_his_app_ids(snapshot_dir, _rem.get("npn", ""), _rem.get("name", ""))

    if month is None:
        month = date.today().replace(day=1)

    month_str = month.strftime("%Y-%m")

    # GA/IL (access) export → current-plan-year coverage rows, person by person
    # (tracker/state_roster.py). Since the Oct 2026 renewal season the export has no
    # carrier on any row and lists most people by their 2027 renewal; read raw, every
    # row was dropped and the whole GA/IL book silently went stale. Never raises: a
    # failure here falls back to the raw rows (whose blank carriers then drop) and
    # the empty-book guard below keeps the prior copy, loudly.
    _acc_rep = None
    if source == "access" and len(df):
        # Another agent's book (hand-staged by mistake) must never count as his: keep
        # the last good copy instead, loudly.
        from tracker.state_roster import book_agent as _book_agent
        _ok_agent, _who = _book_agent(df)
        if not _ok_agent:
            _why = f"{csv_path.name} is another agent's book ({_who}), not yours"
            _kept = None if dry_run else _keep_prior_access(
                re.sub(r"\s+", "", csv_path.stem.strip().lower()), month.strftime("%Y-%m"),
                snapshot_dir, csv_path.name, _why)
            if _kept is not None:
                return _kept
            raise ValueError(f"{_why} — nothing was ingested.")
        try:
            df, _acc_rep = _resolve_access(df, csv_path, snapshot_dir, month, dry_run=dry_run)
        except Exception as e:
            print(f"  !! {csv_path.name}: GA/IL carrier fill FAILED ({type(e).__name__}: {e}) — "
                  f"using the raw rows; carrier-less rows will drop")
            _acc_rep = {"error": f"{type(e).__name__}: {e}"}
        # A pull that looks PARTIAL next to the pull before it: GA-book-only clients
        # missing from it would lapse and be texted as fresh losses. Keep the last good
        # book; the pull goes into the roster history only, where the roster rule sees
        # it as partial (and a matching pull on a later day passes).
        if (_acc_rep or {}).get("partial"):
            _why = f"the {csv_path.name} pull looks partial ({_acc_rep['partial']})"
            _kept = None if dry_run else _keep_prior_access(
                re.sub(r"\s+", "", csv_path.stem.strip().lower()), month_str, snapshot_dir,
                csv_path.name, _why, partial=True)
            if dry_run:
                print(f"  !! {_why} — a real ingest keeps the last good book")
            elif _kept is not None:
                if _acc_rep.get("unrecorded"):
                    from tracker import state_roster as _SR
                    _raw, _st, _ts, _nm, _md5 = _acc_rep["unrecorded"]
                    try:
                        _SR.write_roster(_raw, _st, _ts, _nm, _md5, snapshot_dir)
                    except Exception as e:
                        print(f"  !! could not record {_nm} in the roster history: {e}")
                return _kept
            else:
                print(f"  !! {_why} — and there is no earlier snapshot to keep; using it")

    df = normalize_dataframe(df, source, source_configs, full_config=full_config,
                             ever_his_ids=set(ever_his) if ever_his is not None else None)

    if dry_run:
        return None, df

    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Non-HealthSherpa sources: dedup against HS before saving — EXCEPT state-
    # exchange (access) books. build_all_clients already collapses a person's HS +
    # access rows by name_key (verified: no double-count), so the dedup is redundant
    # here and actively strips the ACTIVE access signal from GA/IL clients who also
    # carry an old (often termed) HS row — shrinking the fresh book (198->16) so the
    # partial-book guard below then rejects it (16 vs 208). Keep every access row so
    # the fresh state-exchange export ingests and those clients stay counted.
    if source not in ("healthsherpa", "access"):
        df = _dedup_against_healthsherpa(df, month_str, snapshot_dir)

    # Use the CSV stem so multiple files of the same source type don't overwrite
    # each other. Normalize whitespace/case so accidental filename variations
    # (e.g. "healthsherpa .csv" with a stray space) don't create a duplicate
    # snapshot alongside the real one, which would double-count clients.
    stem = re.sub(r"\s+", "", csv_path.stem.strip().lower())
    snapshot_path = snapshot_dir / f"{month_str}_{stem}.parquet"

    # ACCESS-BOOK PARTIAL GUARD (Ethan 2026-07-24): a state-exchange book
    # (book_of_business_*) that lands empty or collapses to under half of the last
    # good snapshot is almost always a failed/partial export (like the blank GA
    # export on 7/24). Writing it would wipe real active clients. Keep the prior
    # snapshot and warn instead of overwriting. "Last good" = the newest snapshot of
    # this file from ANY month (it used to be this month's only, so the first run of
    # Nov 1 / Dec 1 / Jan 1 was unguarded).
    if "book_of_business" in stem:
        _pp = _prior_access_snapshot(stem, month_str, snapshot_dir)
        try:
            _prev = pd.read_parquet(_pp) if _pp is not None else None
        except Exception:
            _prev = None
        _why = ""
        if df.empty:
            _why = f"{csv_path.name} has no usable rows after the carrier fill"
        elif _prev is not None and len(_prev) >= 5 and len(df) < 0.5 * len(_prev):
            _why = (f"{csv_path.name} REJECTED — {len(df)} rows vs {len(_prev)} in the last good "
                    f"snapshot (>50% drop). Looks like a partial/empty state-exchange export")
        if _why:
            _kept = _keep_prior_access(stem, month_str, snapshot_dir, csv_path.name, _why)
            if _kept is not None:
                return _kept      # keep prior snapshot, hand its data back to the caller
            print(f"  !! {_why} — and there is no earlier {stem} snapshot to keep.")

    df.to_parquet(snapshot_path, index=False)
    if source == "access" and (_acc_rep or {}).get("unrecorded"):
        from tracker import state_roster as _SR
        _raw, _st, _ts, _nm, _md5 = _acc_rep["unrecorded"]
        try:
            _SR.write_roster(_raw, _st, _ts, _nm, _md5, snapshot_dir)
            print(f"    ({_nm} was not in the {_st} roster history — recorded it, dated {_ts:%b %d %H:%M} "
                  f"by its file time)")
        except Exception as e:
            print(f"  !! could not record {_nm} in the roster history: {e}")
    if source == "access":
        _record_access_status(stem, {
            "file": csv_path.name, "ok": True, "kept_prior": False, "rows": int(len(df)),
            "export_date": str((_acc_rep or {}).get("export_date") or ""),
            "unfilled": (_acc_rep or {}).get("unfilled", []),
            "message": (_acc_rep or {}).get("error", ""),
            "counts": {k: v for k, v in (_acc_rep or {}).items() if isinstance(v, (int, bool))},
        })

    # Append this export's his-AOR applications (and every one the snapshots know)
    # to data/ever_his_app_ids.json, so "ever his" survives this month's snapshot
    # being overwritten by the next ingest.
    if ever_his is not None and {"ffm_app_id", "policy_aor"}.issubset(df.columns):
        _parts = [p.lower() for p in str(_rem.get("name", "")).split()]
        _npn = str(_rem.get("npn", "")).strip()
        _his = df["policy_aor"].apply(lambda a: _aor_names_agent(a, _npn, _parts))
        _cars = df["carrier"] if "carrier" in df.columns else pd.Series("", index=df.index)
        _cur = {clean_app_id(a): ("" if pd.isna(c) else str(c)) for a, c in zip(df.loc[_his, "ffm_app_id"], _cars[_his])}
        try:
            _added = record_ever_his_app_ids({**ever_his, **_cur})
            if _added:
                print(f"    (ever-his ledger: +{_added} application(s) → data/ever_his_app_ids.json)")
        except Exception as e:
            print(f"  !! could not update data/ever_his_app_ids.json: {e}")

    return snapshot_path, df


def load_all_snapshots(snapshot_dir: Path) -> dict:
    """
    Returns dict keyed by 'YYYY-MM' -> combined DataFrame for that month.
    All parquet files matching YYYY-MM_*.parquet are combined per month.
    """
    months = {}
    for f in sorted(snapshot_dir.glob("*.parquet")):
        parts = f.stem.split("_", 1)
        if len(parts) < 2:
            continue
        month_key = parts[0]
        df = pd.read_parquet(f)
        months.setdefault(month_key, []).append(df)

    return {m: pd.concat(dfs, ignore_index=True) for m, dfs in months.items()}


def match_client_id(df: pd.DataFrame) -> pd.DataFrame:
    """
    Produce a stable client_key: policy_id if non-empty, else name_key fallback.
    """
    df = df.copy()
    df["client_key"] = df["policy_id"].where(
        df["policy_id"].notna()
        & (df["policy_id"] != "")
        & (df["policy_id"] != "nan"),
        other=df.get("name_key", ""),
    )
    return df
