"""
Ingests CSV exports into normalized snapshots.

HealthSherpa: standard read, carrier column within the file.
Access portal (IL/GA): offset header, state extracted from address,
  status remapped, unenrolled and no-carrier rows dropped, deduped
  against same-month HealthSherpa snapshot before writing.
"""

import fnmatch
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


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
    "missing_count_flag",
    "submission_date",
    "created_date",
    "date_effectuated",
    "term_date",
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
    # Last Marketplace sync — approximates WHEN the AOR change registered
    "last_ede_sync",
]


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
    # OR the enrolling agent (their NPN / name on the submission). Anyone else is
    # another agent's client merely surfacing in the account — drop them.
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

        before = len(df)
        df = df[df.apply(_ever_mine, axis=1)].copy()
        dropped = before - len(df)
        if dropped:
            print(f"    (ownership filter: removed {dropped} clients the broker was never agent for)")

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
            df = df[df.apply(_allowed, axis=1)].copy()
            dropped = before - len(df)
            if dropped:
                print(f"    (matrix filter: removed {dropped} unlicensed state/carrier combos)")

        # Canonicalize carrier names using the alias table
        if aliases and "carrier" in df.columns:
            df["carrier"] = df["carrier"].apply(
                lambda c: _normalize_carrier(str(c), aliases) or c
            )

    # Generate policy_id for sources that have no native ID field
    if "policy_id" not in df.columns or df.get("policy_id", pd.Series()).isna().all():
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

    df = normalize_dataframe(df, source, source_configs, full_config=full_config)

    if month is None:
        month = date.today().replace(day=1)

    month_str = month.strftime("%Y-%m")

    if dry_run:
        return None, df

    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Non-HealthSherpa sources: dedup against HS before saving
    if source != "healthsherpa":
        df = _dedup_against_healthsherpa(df, month_str, snapshot_dir)

    # Use the CSV stem so multiple files of the same source type don't overwrite
    # each other. Normalize whitespace/case so accidental filename variations
    # (e.g. "healthsherpa .csv" with a stray space) don't create a duplicate
    # snapshot alongside the real one, which would double-count clients.
    stem = re.sub(r"\s+", "", csv_path.stem.strip().lower())
    snapshot_path = snapshot_dir / f"{month_str}_{stem}.parquet"
    df.to_parquet(snapshot_path, index=False)

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
