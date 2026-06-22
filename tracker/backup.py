"""
Daily backup of everything that would hurt to lose:
  - Local state: data/ (dropped-tracking, vetted exclusions, win-backs) + config/
  - The Google Sheets (main book + Insurance PAYMENTS), exported tab-by-tab to CSV

Backups land in iCloud Drive when available (so they survive a dead Mac),
otherwise the home folder. Old backups are pruned to `keep_days`.
"""

import datetime
import re
import shutil
from pathlib import Path

import pandas as pd

from tracker.config import load_settings

_ROOT = Path(__file__).resolve().parent.parent


def _backup_root() -> Path:
    icloud = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    base = icloud if icloud.exists() else Path.home()
    return base / "commission-tracker-backups"


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("_") or "tab"


def _export_sheet(url, impersonation_target, dest: Path, label: str) -> int:
    from tracker.sheets import _open_sheet
    ss = _open_sheet(url, impersonation_target)
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for ws in ss.worksheets():
        try:
            pd.DataFrame(ws.get_all_values()).to_csv(
                dest / f"{_safe(ws.title)}.csv", index=False, header=False)
            n += 1
        except Exception as e:
            print(f"    tab '{ws.title}' failed: {e}")
    return n


def _prune(root: Path, keep_days: int) -> None:
    if not root.exists():
        return
    dated = sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)])
    for old in dated[:-keep_days] if keep_days > 0 else []:
        shutil.rmtree(old, ignore_errors=True)


def run_backup(keep_days: int = 30, today: str = None) -> Path:
    settings = load_settings()
    today = today or datetime.date.today().isoformat()
    bdir = _backup_root() / today
    bdir.mkdir(parents=True, exist_ok=True)

    # 1. Local state (the irreplaceable bits)
    for d in ("data", "config"):
        src = _ROOT / d
        if src.exists():
            shutil.copytree(src, bdir / d, dirs_exist_ok=True)
            print(f"  Copied {d}/")

    # 2. Google Sheets -> CSV
    imp = settings.get("impersonation_target")
    for label, url in (("book", settings.get("sheet_url")),
                       ("payments", settings.get("payments_sheet_url"))):
        if not url:
            continue
        try:
            n = _export_sheet(url, imp, bdir / f"sheet_{label}", label)
            print(f"  Exported {label} sheet: {n} tab(s)")
        except Exception as e:
            print(f"  Sheet backup '{label}' failed: {e}")

    _prune(_backup_root(), keep_days)
    print(f"Backup complete: {bdir}")
    return bdir
