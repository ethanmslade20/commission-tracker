"""
One-time tracker-correction marker (data/correction_pending.json, gitignored).

When a fix changes who the tracker counts — e.g. 14 stale "active" clients become
"AOR taken" all at once — the next upload text would read like a burst of brand-new
losses and wins, and the AOR alert would text "call them today" for a backlog that
is weeks old. The marker lists the approved before/after people so those surfaces
can say "tracker correction (not new)" instead, once:

  {"out":  [...],   # moved OUT of the active book by the correction
   "in":   [...],   # moved back IN
   "note": "..."}   # optional, shown in the text

Each item is an ffm_app_id ("1234567890"), a name ("Jane Doe"), or a dict
{"ffm_app_id": ..., "name": ..., "state": ...}. An item with an app id matches by
app id only (so a same-name stranger is never swallowed); an item without one
matches by first+last name. _upload_summary deletes the file after the text that
carries the correction line actually sends. scripts/correction_golive.py writes it.
"""
import json
import re
import unicodedata
from pathlib import Path

CORRECTION_PATH = Path(__file__).resolve().parent.parent / "data" / "correction_pending.json"


def name_key(name) -> str:
    """first+last letters only, accent-folded: 'Jane Doe' -> 'janedoe'."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", s)


def _item_parts(item):
    """(app_id, name, state) of one marker item."""
    if isinstance(item, dict):
        app = re.sub(r"\.0$", "", str(item.get("ffm_app_id") or "").strip())
        return (app if app.lower() not in ("nan", "none") else "",
                str(item.get("name") or "").strip(), str(item.get("state") or "").strip())
    s = re.sub(r"\.0$", "", str(item or "").strip())
    return (s, "", "") if s.isdigit() else ("", s, "")


def load_correction(path: Path = CORRECTION_PATH):
    """The pending correction marker, or None when there is none (or it is unreadable)."""
    try:
        m = json.loads(Path(path).read_text())
    except Exception:
        return None
    if not isinstance(m, dict):
        return None
    m.setdefault("out", [])
    m.setdefault("in", [])
    return m if (m["out"] or m["in"]) else None


def match_sets(marker, side=None):
    """(app ids, name keys) the marker covers — both lists, or just side 'out'/'in'."""
    ids, names = set(), set()
    if not marker:
        return ids, names
    for s in ((side,) if side else ("out", "in")):
        for it in marker.get(s) or []:
            app, nm, _ = _item_parts(it)
            if app:
                ids.add(app)
            elif nm:
                names.add(name_key(nm))
    return ids, names


def labels(marker, side, name_by_app=None):
    """Display names for one side of the marker (app ids resolved via name_by_app)."""
    out = []
    for it in (marker or {}).get(side) or []:
        app, nm, st = _item_parts(it)
        lbl = nm or (name_by_app or {}).get(app) or app
        if lbl:
            out.append(f"{lbl} ({st})" if st and nm else lbl)
    return out
