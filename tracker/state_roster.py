"""
State-exchange agent books: Georgia Access / Get Covered Illinois
(book_of_business*.csv, source "access").

The portal export is the only record of WHO the exchange lists under Ethan, but it
is a poor record of coverage ("Report a Change" keeps showing after a plan ends),
and since the Oct 6 2026 renewal season it carries no carrier, plan or premium on
ANY row and lists each person's NEWEST application, which for most is the 2027
renewal. This module keeps the two jobs apart (Ethan 2026-10-28):

1. ROSTER HISTORY (write_roster). Every pull is kept as
   snapshots/roster/<st>_<YYYY-MM-DD>.parquet: every row, every status, with the
   export's own date (file mtime). It records who was listed when and how old the
   newest pull is. On its own it never decides coverage.

2. CARRIER-LESS INGEST (resolve_access_rows). Turns a pull back into current-plan-
   year coverage rows ONE PERSON AT A TIME. Never by switching the whole file
   between modes, which would flip partway through OEP once the 2027 rows get
   carriers:
     - a later-year (2027) row never decides this year's coverage. The person's last
       current-year row (its status) from the newest pull that had one stands in;
     - an earlier-year (2025) row is presence only;
     - a current-year row with no carrier takes its carrier from the person's newest
       NORMAL pull (one that still showed carriers). If that pull showed "NA" (an
       account with no plan), the person has no coverage here;
     - in no normal pull: the newest HealthSherpa snapshot's current-year plan, but
       never a prior-year app and never a row with status Unknown or AOR None;
     - nothing at all: presence only. An active-status row goes on the
       "possible new GA/IL sale — confirm carrier" list;
     - a row that HAS a carrier still loses it when that carrier's own book shows
       the plan ended and nothing of theirs active (CarrierEnded): the portal keeps
       showing "Report a Change" after a plan ends.

3. MATCHING. Every join that can REMOVE or ADD coverage uses the strict person key:
   first-name token + last-name token AND (phone OR email), or phone AND email with
   the same last-name token. Never name alone (two different clients can share a
   name), phone alone (household members) or a household member's name: spouses
   often share both phone and email, so phone + email alone is accepted only by
   PROTECTIVE lookups (any_last=True). Protective evidence (a carrier book showing a
   client still active with Ethan) may also fall back to name + state, because the
   Anthem export has no phone or email; so may CarrierEnded, but only when that
   book holds a single policy for the name + state.

4. ROSTER RULE (evaluate_roster): who left the book, and whether that may count.
   A loss it counted is kept in data/roster_lost.json (review_roster_lost) until
   the client is listed again or a later sign shows them his.
"""

import hashlib
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent

ROSTER_STATES = ("GA", "IL")          # the state exchanges whose agent book Ethan pulls
BOOK_LABELS = {"GA": "GA Access book", "IL": "IL Get Covered book"}
# Portal statuses of an enrolled (or enrolling) household; the access status_map
# turns them into Effectuated / PendingEffectuation / PendingFollowups.
PORTAL_ACTIVE = {"Report a Change", "Complete and Submit Application", "Finish Enrolling Household"}
_ACTIVE = {"Effectuated", "PendingEffectuation", "PendingFollowups"}
# A pull "shows carriers" for a plan year when >=10% of that year's rows carry one
# (Sep 13 GA: 186 of 275; Oct 11 and Oct 28: 0). Below that, a blank carrier says
# nothing about coverage; at or above it, "NA" means an account with no plan.
NORMAL_PULL_SHARE = 0.10
STALE_DAYS = 14                       # a pull older than this cannot count anyone lost
# A pull looks PARTIAL next to the pull before it when it lists < 85% of that pull's
# people, or > 5% of them vanished at once (roster rule gate b; the stager and the
# ingest keep the last full book on the same test, pull_partial). Every GA/IL pull
# Apr-Oct 2026 judged that way: whole ones listed 97.5-104.3% of the prior pull's
# people, at most 4.6% vanished (GA Sep 13 -> Oct 11: 13 of 281; Oct 11 -> Oct 28: 4
# of 274). Partial: GA Jul 10, 89 days after the Apr 12 pull (11 of 163 gone; the
# Jul 18 pull then passed), and the Oct 11 GA pull filtered to 2026 (22 of 281).
PARTIAL_PEOPLE = 0.85
PARTIAL_GONE = 0.05
_NA = {"", "na", "n/a", "nan", "none", "nat"}
_FILL_COLS = ["carrier", "plan_name", "premium", "aptc", "net_premium"]

ROSTER_COLS = ["first_name", "last_name", "f_tok", "l_tok", "phone", "email", "state",
               "status", "application_year", "application_date", "carrier", "plan_name",
               "premium", "aptc", "net_premium", "export_date", "source_file", "source_md5"]


# ── small helpers ──────────────────────────────────────────────────────────────

def _cell(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _ascii_lower(s) -> str:
    s = unicodedata.normalize("NFKD", _cell(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def name_tokens(first, last) -> tuple:
    """(first token of the first name, last token of the last name)."""
    f = re.sub(r"[^a-z ]", " ", _ascii_lower(first)).split()
    l = re.sub(r"[^a-z ]", " ", _ascii_lower(last)).split()
    return (f[0] if f else ""), (l[-1] if l else "")


def phone10(v) -> str:
    d = re.sub(r"\D", "", re.sub(r"\.0$", "", _cell(v)))
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return d if len(d) == 10 else ""


def email_l(v) -> str:
    e = _cell(v).lower()
    return e if "@" in e else ""


def has_carrier(v) -> bool:
    return _cell(v).lower() not in _NA


def app_year(v):
    """Application year as an int, None when blank."""
    m = re.match(r"^\s*(\d{4})", _cell(v))
    return int(m.group(1)) if m else None


def _his(text, npn: str, first: str, last: str) -> bool:
    t = _cell(text)
    tl = t.lower()
    return bool((npn and npn in t) or (first and last and first in tl and last in tl))


def _foreign(aor, npn, first, last) -> bool:
    a = _cell(aor).lower()
    return a not in _NA and not _his(aor, npn, first, last)


def _utc_to_local(ts):
    """A UTC time (naive or tz-aware) as this Mac's local wall-clock time, naive and
    DST-aware: the clock export_time reads file mtimes in. NaT stays NaT."""
    if ts is None or pd.isna(ts):
        return pd.NaT
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return pd.Timestamp(datetime.fromtimestamp(t.timestamp()))


def _sync_ts(v):
    """HealthSherpa last_ede_sync as LOCAL time. HealthSherpa writes it in UTC (the
    Oct 28 export stamped T191324 holds syncs up to 19:04, saved at 13:15 MDT), while
    roster pulls and carrier books are dated by local file mtimes: an evening sync
    is the next day in UTC."""
    return _utc_to_local(pd.to_datetime(_cell(v) or None, format="%m/%d/%Y %H:%M:%S", errors="coerce"))


def _app_date(v):
    """Portal application date ("28-Oct-26") as a day, NaT when blank."""
    s = _cell(v)
    if not s:
        return pd.NaT
    d = pd.to_datetime(s, format="%d-%b-%y", errors="coerce")
    return d if pd.notna(d) else pd.to_datetime(s, errors="coerce")


def add_keys(df: pd.DataFrame, first="first_name", last="last_name",
             phone="phone", email="email") -> pd.DataFrame:
    """Copy of df with the person-key columns _f/_l (name tokens), _ph, _em."""
    out = df.copy()
    blank = pd.Series("", index=out.index)
    toks = [name_tokens(a, b) for a, b in zip(out.get(first, blank), out.get(last, blank))]
    out["_f"] = [t[0] for t in toks]
    out["_l"] = [t[1] for t in toks]
    out["_ph"] = out.get(phone, blank).map(phone10)
    out["_em"] = out.get(email, blank).map(email_l)
    return out


class PersonIndex:
    """Strict person-key lookup over a keyed frame (see add_keys). With loose=True a
    name + state lookup is also kept, for PROTECTIVE evidence only."""

    def __init__(self, df: pd.DataFrame, loose: bool = False):
        self._by = {}
        self._last = {}
        st = df["state"].map(lambda s: _cell(s).upper()) if (loose and "state" in df.columns) \
            else pd.Series("", index=df.index)
        for i, f, l, ph, em, s in zip(df.index, df["_f"], df["_l"], df["_ph"], df["_em"], st):
            self._last[i] = l
            if f and l and ph:
                self._by.setdefault(("np", f, l, ph), []).append(i)
            if f and l and em:
                self._by.setdefault(("ne", f, l, em), []).append(i)
            if ph and em:
                self._by.setdefault(("pe", ph, em), []).append(i)
            if loose and f and l and s:
                self._by.setdefault(("ns", f, l, s), []).append(i)

    def match(self, f, l, ph, em, state="", loose=False, any_last=False) -> list:
        """Rows of the same person. A phone + email hit also needs the same last-name
        token unless any_last=True (protective lookups only: spouses share both)."""
        keys = []
        if f and l and ph:
            keys.append(("np", f, l, ph))
        if f and l and em:
            keys.append(("ne", f, l, em))
        if ph and em:
            keys.append(("pe", ph, em))
        if loose and f and l and state:
            keys.append(("ns", f, l, _cell(state).upper()))
        out = {}
        for k in keys:
            for i in self._by.get(k, ()):
                if k[0] == "pe" and not any_last and self._last.get(i, "") != l:
                    continue
                out[i] = True
        return list(out)


# ── roster history (C1) ────────────────────────────────────────────────────────

def roster_dir(snapshot_dir) -> Path:
    return Path(snapshot_dir) / "roster"


def file_md5(path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


def export_time(path) -> pd.Timestamp:
    """The export's own time = the downloaded file's mtime, in LOCAL time (as
    freshness.py does; pd.Timestamp(mtime, unit="s") would be UTC)."""
    return pd.Timestamp(datetime.fromtimestamp(Path(path).stat().st_mtime))


def _access_cfg() -> dict:
    from tracker.config import load_carrier_configs, load_settings
    return load_carrier_configs(load_settings()["carrier_config_path"])["access"]


def read_book(path, cfg: dict = None) -> pd.DataFrame:
    """A portal export as raw rows (the access source's true_columns, all strings)."""
    from tracker.ingest import _read_csv
    return _read_csv(Path(path), cfg or _access_cfg())


def majority_state(raw: pd.DataFrame) -> str:
    """The state most rows' addresses are in ("" when no state has a majority)."""
    from tracker.ingest import _extract_state
    if raw is None or raw.empty or "address" not in raw.columns:
        return ""
    st = raw["address"].map(_extract_state)
    st = st[st != ""]
    if not len(st):
        return ""
    vc = st.value_counts()
    return str(vc.index[0]) if vc.iloc[0] > 0.5 * len(st) else ""


def book_agent(raw: pd.DataFrame) -> tuple:
    """(is it Ethan's book, the agent name(s) on it). A portal export names its agent
    on every row (agent first / last name); at least 90% of rows must name the
    configured agent. Another agent's book (a Phase-B audit file) must never be
    recorded or staged as his: every client in it would count as his."""
    from tracker.config import get_agent
    ag = get_agent()
    fn, ln = str(ag["first_name"]).strip().lower(), str(ag["last_name"]).strip().lower()
    if raw is None or raw.empty:
        return False, ""
    blank = pd.Series("", index=raw.index)
    who = (raw.get("agent_first_name", blank).map(_cell) + " " + raw.get("agent_last_name", blank).map(_cell)).str.strip()
    wl = who.str.lower()
    mine = wl.str.contains(fn, regex=False) & wl.str.contains(ln, regex=False)
    names = ", ".join(who[~mine].value_counts().index[:3]) if (~mine).any() else str(ag.get("name") or "")
    return bool(fn and ln and mine.mean() >= 0.9), names


def roster_frame(raw: pd.DataFrame, export_ts, source_file: str, source_md5: str) -> pd.DataFrame:
    from tracker.ingest import _extract_state
    blank = pd.Series("", index=raw.index)
    col = lambda c: raw.get(c, blank).map(_cell)
    toks = [name_tokens(a, b) for a, b in zip(raw.get("first_name", blank), raw.get("last_name", blank))]
    return pd.DataFrame({
        "first_name": col("first_name"), "last_name": col("last_name"),
        "f_tok": [t[0] for t in toks], "l_tok": [t[1] for t in toks],
        "phone": raw.get("phone", blank).map(phone10), "email": raw.get("email", blank).map(email_l),
        "state": raw.get("address", blank).map(_extract_state),
        "status": col("status"),
        "application_year": raw.get("application_year", blank).map(lambda v: str(app_year(v) or "")),
        "application_date": col("application_date"),
        "carrier": col("carrier"), "plan_name": col("plan_name"), "premium": col("premium"),
        "aptc": col("aptc"), "net_premium": col("net_premium"),
        "export_date": pd.Timestamp(export_ts), "source_file": source_file, "source_md5": source_md5,
    }, index=raw.index)[ROSTER_COLS]


def write_roster(raw: pd.DataFrame, state: str, export_ts, source_file: str, source_md5: str,
                 snapshot_dir) -> Path:
    """Write (or merge into) snapshots/roster/<st>_<YYYY-MM-DD>.parquet. Two pulls on
    the same day are BOTH kept (Oct 11: the unfiltered (24) and the 2026-only (26)
    differ by 9 people), and re-writing the same file replaces its own rows, so
    running this twice changes nothing."""
    rdir = roster_dir(snapshot_dir)
    rdir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp(export_ts)
    path = rdir / f"{state.lower()}_{ts.strftime('%Y-%m-%d')}.parquet"
    new = roster_frame(raw, ts, source_file, source_md5)
    if path.exists():
        old = pd.read_parquet(path)
        old = old[old["source_md5"] != source_md5]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values("export_date", kind="stable").reset_index(drop=True)
    new.to_parquet(path, index=False)
    return path


def load_roster(snapshot_dir, state: str) -> pd.DataFrame:
    """Every recorded pull for a state, keyed (_f/_l/_ph/_em), with _day = pull date."""
    files = sorted(roster_dir(snapshot_dir).glob(f"{state.lower()}_*.parquet"))
    frames = []
    for p in files:
        try:
            frames.append(pd.read_parquet(p))
        except Exception as e:
            print(f"  !! unreadable roster file {p.name}: {e}")
    if not frames:
        return pd.DataFrame(columns=ROSTER_COLS + ["_f", "_l", "_ph", "_em", "_day"])
    r = pd.concat(frames, ignore_index=True)
    r["export_date"] = pd.to_datetime(r["export_date"], errors="coerce")
    r["_f"], r["_l"] = r["f_tok"].fillna(""), r["l_tok"].fillna("")
    r["_ph"], r["_em"] = r["phone"].fillna(""), r["email"].fillna("")
    r["_day"] = r["export_date"].dt.normalize()
    r = r.sort_values("export_date", kind="stable").reset_index(drop=True)
    r["_filtered"] = r["source_md5"].isin(filtered_pulls(r))
    return r


def filtered_pulls(roster: pd.DataFrame) -> set:
    """source_md5 of pulls filtered to ONE application year (the Oct 11 "2026 only" GA
    and IL exports), told apart by content: every dated row shares one year while the
    pull before it mixed years. Such a pull leaves out everyone listed only by another
    year's row, so it can't say who left the book. An unfiltered pull that happens to
    hold one year (IL before the renewal season) follows a one-year pull, so it is
    never marked."""
    if roster is None or not len(roster):
        return set()
    out, prev_years = set(), None
    order = roster.groupby("source_md5")["export_date"].min().sort_values(kind="stable")
    yrs = roster.groupby("source_md5")["application_year"].agg(
        lambda s: {y for y in (app_year(v) for v in s) if y is not None})
    for md5 in order.index:
        ys = yrs.get(md5, set())
        if len(ys) == 1 and prev_years is not None and len(prev_years) >= 2:
            out.add(md5)
        else:
            prev_years = ys
    return out


def partial_counts(new_rows: pd.DataFrame, prev_rows: pd.DataFrame, count_rows: pd.DataFrame = None) -> tuple:
    """(people in the new pull, people in the prior pull, prior people the new pull no
    longer lists, partial?). Keyed frames (_f/_l/_ph/_em); a person matches on the
    strict key. count_rows: the new rows to count (default new_rows)."""
    _k = ["_f", "_l", "_ph", "_em"]
    prev_people = prev_rows.drop_duplicates(_k)
    n_prev = len(prev_people)
    n_new = len((new_rows if count_rows is None else count_rows).drop_duplicates(_k))
    nidx = PersonIndex(new_rows)
    gone = sum(1 for f, l, ph, em in zip(prev_people["_f"], prev_people["_l"], prev_people["_ph"],
                                         prev_people["_em"]) if not nidx.match(f, l, ph, em))
    return n_new, n_prev, gone, bool(n_prev and (n_new < PARTIAL_PEOPLE * n_prev or gone > PARTIAL_GONE * n_prev))


def keyed_pull(raw: pd.DataFrame, export_ts, source_file: str = "", source_md5: str = "") -> pd.DataFrame:
    """One pull as roster rows with the person-key columns, as load_roster keys them."""
    r = roster_frame(raw, export_ts, source_file, source_md5)
    r["_f"], r["_l"], r["_ph"], r["_em"] = r["f_tok"], r["l_tok"], r["phone"], r["email"]
    return r


def pull_partial(roster: pd.DataFrame, pull: pd.DataFrame, export_ts) -> str:
    """Does ONE pull look partial next to the roster rule's prior pull (the newest
    earlier day with an unfiltered pull)? The reason ("184 people vs 276 in the Oct 28
    pull; 91 vanished at once"), or "" when it looks whole or nothing came before.
    pull: keyed_pull rows. Judged alone, not merged with other pulls of its day: the
    stager and the ingest stage this one file. A pull on a later day that matches a
    rejected one passes (it is compared with that one), as it does in the rule."""
    if roster is None or not len(roster) or pull is None or not len(pull):
        return ""
    day = pd.Timestamp(export_ts).normalize()
    _filt = roster["_filtered"] if "_filtered" in roster.columns else pd.Series(False, index=roster.index)
    earlier = sorted(roster.loc[~_filt & (roster["_day"] < day), "_day"].dropna().unique())
    if not earlier:
        return ""
    prev_day = pd.Timestamp(earlier[-1])
    n_new, n_prev, gone, partial = partial_counts(pull, roster[(roster["_day"] == prev_day) & ~_filt])
    return (f"{n_new} people vs {n_prev} in the {prev_day:%b %d} pull; {gone} vanished at once"
            if partial else "")


def _in_roster(snapshot_dir, state: str, md5: str) -> bool:
    for p in roster_dir(snapshot_dir).glob(f"{state.lower()}_*.parquet"):
        try:
            if (pd.read_parquet(p, columns=["source_md5"])["source_md5"] == md5).any():
                return True
        except Exception:
            continue
    return False


def newest_pull_time(snapshot_dir, state: str):
    """Export time of the newest recorded pull for a state, or None."""
    files = sorted(roster_dir(snapshot_dir).glob(f"{state.lower()}_*.parquet"))
    for p in reversed(files):
        try:
            v = pd.to_datetime(pd.read_parquet(p, columns=["export_date"])["export_date"]).max()
        except Exception:
            continue
        if pd.notna(v):
            return pd.Timestamp(v)
    return None


def book_age(snapshot_dir, input_dir, state: str, now=None):
    """(timestamp, age in days) of the newest pull of a state's book: the newer of the
    newest roster export_date and the staged input file's mtime (a book staged by
    hand never passes through the Downloads stager). (None, None) if neither."""
    now = pd.Timestamp(now) if now else pd.Timestamp.now()
    cands = [newest_pull_time(snapshot_dir, state)]
    p = Path(input_dir) / f"book_of_business_{state.lower()}.csv"
    if p.exists() and not _in_roster(snapshot_dir, state, file_md5(p)):
        cands.append(export_time(p))      # a pull the roster never saw: its mtime is all we have
    cands = [c for c in cands if c is not None]
    if not cands:
        return None, None
    ts = max(cands)
    return ts, (now - ts).total_seconds() / 86400


# ── carrier-book evidence ─────────────────────────────────────────────────────

_CIGNA_ACTIVE = {"active", "future active", "pending", "effectuated"}


def carrier_evidence(books_dir=None, today=None) -> pd.DataFrame:
    """One row per policy in the carrier portal books on file (Ambetter, Oscar, Anthem,
    Cigna, UHC): keyed person, state, whether it is active WITH ETHAN (Anthem writing
    agent / Ambetter broker / Cigna writing agent; the Oscar and UHC exports are his
    own book), whether it ended, and the book's own date (file mtime). A book that
    won't parse is skipped loudly; it never stops the run."""
    from tracker.config import get_agent
    ag = get_agent()
    npn, fn, ln = str(ag["npn"]), ag["first_name"].lower(), ag["last_name"].lower()
    today = pd.Timestamp(today).normalize() if today else pd.Timestamp.today().normalize()
    books_dir = Path(books_dir) if books_dir else _ROOT / "carrier_books"
    frames = []

    def _add(book, path, first, last, phone, email, state, active, his, end):
        n = len(first)
        frames.append(pd.DataFrame({
            "book": book, "book_date": export_time(path),
            "first_name": list(first), "last_name": list(last),
            "phone": list(phone) if phone is not None else [""] * n,
            "email": list(email) if email is not None else [""] * n,
            "state": [(_cell(s).upper()) for s in state] if state is not None else [""] * n,
            "active": list(active), "his": list(his), "end": list(end),
        }))

    def _d(s):
        import warnings
        with warnings.catch_warnings():               # mixed date formats across books
            warnings.simplefilter("ignore")
            return pd.to_datetime(s, errors="coerce")

    p = books_dir / "ambetter.csv"
    if p.exists():
        try:
            a = pd.read_csv(p, dtype=str)
            pterm = _d(a["Policy Term Date"])
            bterm = _d(a["Broker Term Date"])          # 12/31/9999 → NaT = open-ended
            his = (a["Broker NPN"].fillna("").str.contains(npn, regex=False)
                   | a["Broker Name"].map(lambda v: _his(v, npn, fn, ln))) & (bterm.isna() | (bterm >= today))
            _add("Ambetter", p, a["Insured First Name"], a["Insured Last Name"], a["Member Phone Number"],
                 a["Member Email"], a["State"], pterm.isna() | (pterm >= today), his, pterm)
        except Exception as e:
            print(f"  !! Ambetter book unreadable for GA/IL evidence: {type(e).__name__}: {e}")
    p = books_dir / "oscar.csv"
    if p.exists():
        try:
            o = pd.read_csv(p, dtype=str)
            nm = o["Member name"].fillna("").astype(str).str.split()
            _add("Oscar", p, nm.map(lambda x: x[0] if x else ""), nm.map(lambda x: x[-1] if x else ""),
                 o["Phone number"], o["Email"], o["State"], o["Policy status"].fillna("") != "Inactive",
                 [True] * len(o), _d(o["Coverage end date"]))
        except Exception as e:
            print(f"  !! Oscar book unreadable for GA/IL evidence: {type(e).__name__}: {e}")
    p = books_dir / "anthem.csv"
    if p.exists():
        try:
            an = pd.read_csv(p, skiprows=1, dtype=str)
            cn = an["Client Name"].fillna("").astype(str)
            last = cn.map(lambda n: n.split(",", 1)[0] if "," in n else n)
            first = cn.map(lambda n: n.split(",", 1)[1] if "," in n else "")
            _add("Anthem", p, first, last, None, None, an["State"],
                 an["Status"].isin(["Active", "Future Active"]),
                 an["Writing Agent"].map(lambda v: _his(v, npn, fn, ln)), _d(an["Cancellation Date"]))
        except Exception as e:
            print(f"  !! Anthem book unreadable for GA/IL evidence: {type(e).__name__}: {e}")
    p = books_dir / "cigna.xlsx"
    if p.exists():
        try:
            c = pd.read_excel(p, dtype=str)
            end = _d(c.get("Termination Date"))
            act = c["Policy Status"].fillna("").str.strip().str.lower().isin(_CIGNA_ACTIVE) & ~(end < today)
            his = (c.get("Writing Agent NPN", pd.Series("", index=c.index)).fillna("").str.contains(npn, regex=False)
                   | c.get("Writing Agent", pd.Series("", index=c.index)).map(lambda v: _his(v, npn, fn, ln)))
            _add("Cigna", p, c["Primary First Name"], c["Primary Last Name"],
                 c.get("Customer Phone Number"), c.get("Customer Email Address"), c.get("State"), act, his, end)
        except Exception as e:
            print(f"  !! Cigna book unreadable for GA/IL evidence: {type(e).__name__}: {e}")
    p = books_dir / "uhc_source.xlsx"
    if p.exists():
        try:
            u = pd.read_excel(p, header=2, dtype=str).dropna(subset=["memberFirstName", "memberLastName"])
            his = u.get("agentNpn", pd.Series("", index=u.index)).fillna("")
            his = (his == "") | his.str.contains(npn, regex=False)
            _add("UHC", p, u["memberFirstName"], u["memberLastName"], u.get("memberPhone"),
                 u.get("memberEmail"), u.get("memberState"), u["planStatus"].fillna("") == "A", his,
                 _d(u.get("policyTermDate")))
        except Exception as e:
            print(f"  !! UHC book unreadable for GA/IL evidence: {type(e).__name__}: {e}")
    if not frames:
        return pd.DataFrame(columns=["book", "book_date", "first_name", "last_name", "phone", "email",
                                     "state", "active", "his", "end", "_f", "_l", "_ph", "_em"])
    ev = pd.concat(frames, ignore_index=True)
    ev["end"] = pd.to_datetime(ev["end"], errors="coerce")
    return add_keys(ev)


# The carrier names a GA/IL row can carry, per carrier book.
_BOOK_BRAND = {"Ambetter": r"ambetter|peach state", "Oscar": r"oscar",
               "Anthem": r"anthem|wellpoint|amgp", "Cigna": r"cigna", "UHC": r"united|uhc"}


class CarrierEnded:
    """Did a carrier book END this person's plan? (Ethan 2026-10-28.)

    GA Access and Get Covered IL policies DO appear in the carrier portal books: of
    the 187 active Sep 13 GA book rows, 164 match an active policy with Ethan in the
    Ambetter / Oscar / Anthem / Cigna books. So when the carrier book for the row's
    own carrier shows the person's policy with Ethan ended and NO policy of theirs is
    active in any book, the state-exchange book's "Report a Change" is stale (it keeps
    showing after a plan ends) and the plan is over: the carrier book and the
    commission payments agree on that, the portal export does not. Matched on the
    strict person key; name + state only when that book holds a single policy for the
    name + state (Anthem has no phone or email). Never fires when:
      - the book was pulled before the application (it can't know about that plan);
      - the application was filed after the policy ended (a new application);
      - HealthSherpa shows an active plan for the person that starts after the end
        (a new plan already on the way)."""

    def __init__(self, evidence: pd.DataFrame, hs: pd.DataFrame = None, plan_year: int = None):
        self.ev = evidence if evidence is not None and len(evidence) else None
        self.idx = PersonIndex(self.ev) if self.ev is not None else None
        self.ns = {}
        if self.ev is not None:
            for i, b, f, l, s in zip(self.ev.index, self.ev["book"], self.ev["_f"], self.ev["_l"],
                                     self.ev["state"].map(lambda v: _cell(v).upper())):
                if f and l and s:
                    self.ns.setdefault((b, f, l, s), []).append(i)
        self.hs, self.hidx = None, None
        if hs is not None and len(hs):
            h = add_keys(hs)
            h["_eff"] = pd.to_datetime(h.get("effective_date"), errors="coerce")
            h = h[h.get("status", pd.Series("", index=h.index)).map(_cell).isin(_ACTIVE)]
            self.hs, self.hidx = h, PersonIndex(h)

    def _hits(self, f, l, ph, em, state):
        hit = set(self.idx.match(f, l, ph, em))
        st = _cell(state).upper()
        for b in _BOOK_BRAND:
            rows = self.ns.get((b, f, l, st), [])
            if len(rows) == 1:                    # a single policy for that name + state
                i = rows[0]
                if not (self.ev.at[i, "_ph"] or self.ev.at[i, "_em"]):   # a book with no contact data
                    hit.add(i)
        return self.ev.loc[sorted(hit)]

    def check(self, f, l, ph, em, state, carrier, app_day=None, app_day_latest=None) -> tuple:
        """(detail, end date) when the carrier book shows the plan ended, else ("", NaT).
        app_day: the application day (or its earliest estimate); app_day_latest: its
        latest estimate when only the month is known (the new-application test)."""
        if self.ev is None or not (f and l):
            return "", pd.NaT
        m = self._hits(f, l, ph, em, state)
        if not len(m) or m["active"].any():
            return "", pd.NaT
        car = _cell(carrier).lower()
        brand = m["book"].map(lambda b: bool(re.search(_BOOK_BRAND.get(b, "^$"), car)))
        e = m[brand & m["his"] & m["end"].notna()]
        app_day = pd.Timestamp(app_day).normalize() if app_day is not None and pd.notna(app_day) else None
        if app_day is not None:
            e = e[e["book_date"].dt.normalize() >= app_day]
        if not len(e):
            return "", pd.NaT
        end = pd.Timestamp(e["end"].max())
        _late = pd.Timestamp(app_day_latest).normalize() if app_day_latest is not None \
            and pd.notna(app_day_latest) else app_day
        if _late is not None and _late > end:
            return "", pd.NaT                     # a new application after that plan ended
        if self.hidx is not None:
            hm = self.hs.loc[self.hidx.match(f, l, ph, em)]
            if len(hm) and (hm["_eff"] > end).any():
                return "", pd.NaT                 # HealthSherpa: a newer plan starts after it
        b = e.sort_values("end").iloc[-1]
        return f"{b['book']} book ({b['book_date']:%b %d}) shows it ended {end:%m/%d/%Y}", end


# ── carrier-less ingest (C2) ───────────────────────────────────────────────────

def _pull_normal(rows: pd.DataFrame, plan_year: int) -> bool:
    """Does this pull still show carriers for the plan year?"""
    yr = rows["application_year"].map(app_year)
    cur = yr.isna() | (yr == plan_year)
    n = int(cur.sum())
    return n > 0 and int((cur & rows["carrier"].map(has_carrier)).sum()) >= NORMAL_PULL_SHARE * n


def resolve_access_rows(raw: pd.DataFrame, export_ts, history: pd.DataFrame, hs: pd.DataFrame,
                        plan_year: int, evidence: pd.DataFrame = None, source_md5: str = ""):
    """Resolve ONE pull of a portal export into current-plan-year coverage rows.

    raw      : the pull, as read_book returns it (true_columns, strings)
    export_ts: the pull's own time
    history  : load_roster rows; only EARLIER pulls are used (export_date < export_ts,
               and never this file's own rows, matched by source_md5)
    hs       : newest HealthSherpa snapshot (normalized) or None
    evidence : carrier_evidence() or None

    Every row that ends up with a carrier (this pull's own, carried or filled) is
    checked against the carrier books (CarrierEnded): a plan the carrier shows ended
    is blanked like a "no plan" row.

    Returns (rows, report). rows has raw's columns plus export_date (the pull each
    row's status came from), fill_source and application_day. A row that must not
    count as coverage keeps carrier blank, so the access config's require_carrier
    drops it at normalize. report: counts per outcome, "carried"/"filled"/"no_plan"/
    "ended" name lists, and "unfilled" = active-status rows with no carrier anywhere
    (possible new sales)."""
    out = raw.copy().reset_index(drop=True)
    for c in _FILL_COLS + ["application_year", "application_date", "status"]:
        if c not in out.columns:
            out[c] = None
    out = add_keys(out)
    from tracker.ingest import _extract_state
    out["_st"] = out.get("address", pd.Series("", index=out.index)).map(lambda a: _extract_state(_cell(a)))
    export_ts = pd.Timestamp(export_ts)
    out["export_date"] = export_ts
    out["fill_source"] = ""
    yrs = pd.Series([app_year(v) for v in out["application_year"]], index=out.index, dtype=object)
    rep = {"pull": 0, "filled_book": 0, "filled_hs": 0, "carried": 0, "no_plan": 0,
           "earlier_year": 0, "later_year_only": 0, "ended_in_carrier_book": 0, "unfilled": [],
           "carried_names": [], "filled_names": [], "no_plan_names": [], "ended_names": [],
           "normal_pull": False}
    normal = _pull_normal(out, plan_year)
    rep["normal_pull"] = normal

    # History, keyed. A pull is "normal" for this plan year when it still showed carriers.
    h = history if history is not None else pd.DataFrame(columns=ROSTER_COLS)
    if len(h):
        h = h[(pd.to_datetime(h["export_date"], errors="coerce") < export_ts)
              & (h["source_md5"] != source_md5)].copy()
    if len(h):
        h["_f"], h["_l"] = h["f_tok"].fillna(""), h["l_tok"].fillna("")
        h["_ph"], h["_em"] = h["phone"].fillna(""), h["email"].fillna("")
        h["_yr"] = h["application_year"].map(app_year)
        h["_car"] = h["carrier"].map(has_carrier)
        _norm = {m: _pull_normal(g, plan_year) for m, g in h.groupby("source_md5")}
        h["_normal"] = h["source_md5"].map(_norm).fillna(False).astype(bool)
    hidx = PersonIndex(h) if len(h) else None

    # HealthSherpa: this plan year's plans only, never Unknown status or a None AOR.
    hsv = None
    if hs is not None and len(hs):
        hsv = add_keys(hs)
        _eff = pd.to_datetime(hsv.get("effective_date"), errors="coerce")
        _st = hsv.get("status", pd.Series("", index=hsv.index)).map(_cell)
        _aor = hsv.get("policy_aor", pd.Series("", index=hsv.index)).map(_cell).str.lower()
        hsv = hsv[(_eff.dt.year == plan_year) & ~_st.isin(["", "Unknown"]) & ~_aor.isin(_NA)].copy()
        hsv["_eff"] = pd.to_datetime(hsv["effective_date"], errors="coerce")
        hsv["_act"] = hsv["status"].isin(_ACTIVE)
    hsidx = PersonIndex(hsv) if hsv is not None and len(hsv) else None
    ended_by = CarrierEnded(evidence, hs)

    def _hist(f, l, ph, em):
        return h.loc[hidx.match(f, l, ph, em)] if hidx is not None else h.iloc[0:0]

    def _from_hs(f, l, ph, em):
        if hsidx is None:
            return None
        m = hsv.loc[hsidx.match(f, l, ph, em)]
        if not len(m):
            return None
        r = m.sort_values(["_act", "_eff"]).iloc[-1]
        return {"carrier": r.get("carrier"), "plan_name": r.get("plan_name"),
                "premium": r.get("commission"), "aptc": r.get("subsidy"),
                "net_premium": r.get("net_premium")}

    def _carrier_source(f, l, ph, em, app_date, status):
        """(fill dict, how) or (None, why). The person's newest normal pull decides
        when it describes THIS application: pulled on/after the application date, and
        either showing a plan or showing the same status. Otherwise its "NA" says
        nothing about it: an application filed after that pull (one GA client filed
        Oct 21), or one that was still in progress then and is enrolled now ("Complete
        and Submit", NA on Sep 13; "Report a Change" on Oct 28 — a normal pull never
        shows a carrier on an in-progress application). Those go to
        HealthSherpa first, with the old pull's plan as the fallback."""
        hh = _hist(f, l, ph, em)
        hn = hh[hh["_normal"]] if len(hh) else hh
        book, day = None, ""
        if len(hn):
            newest = hn[hn["export_date"] == hn["export_date"].max()]
            ok = newest[(newest["_yr"].isna() | (newest["_yr"] == plan_year)) & newest["_car"]]
            day = pd.Timestamp(newest["export_date"].max()).strftime("%b %d")
            book = {c: ok.iloc[-1].get(c) for c in _FILL_COLS} if len(ok) else None
            described = ((pd.isna(app_date) or app_date <= pd.Timestamp(newest["export_date"].max()).normalize())
                         and (book is not None or (newest["status"] == status).any()))
            if described:
                return (book, f"book {day}") if book else (None, f"no plan in the {day} pull")
        fill = _from_hs(f, l, ph, em)
        if fill:
            return fill, "HealthSherpa"
        if book:
            return book, f"book {day}"
        return None, ("no history" if not day else f"no history (no plan on file since the {day} pull)")

    def _ended(i, f, l, ph, em):
        """Blank the row when its carrier's book shows the plan ended (CarrierEnded)."""
        how, _ = ended_by.check(f, l, ph, em, out.at[i, "_st"], out.at[i, "carrier"],
                                _app_date(out.at[i, "application_date"]))
        if not how:
            return False
        rep["ended_in_carrier_book"] += 1
        rep["ended_names"].append(_name(i))
        out.loc[i, _FILL_COLS] = None
        out.at[i, "fill_source"] = (out.at[i, "fill_source"] + "; " if out.at[i, "fill_source"] else "") + how
        return True

    def _name(i):
        return f"{_cell(out.at[i, 'first_name'])} {_cell(out.at[i, 'last_name'])}".strip()

    def _contact(i):            # lets run_report tell a new sale from a known household
        return {"first_name": _cell(out.at[i, "first_name"]), "last_name": _cell(out.at[i, "last_name"]),
                "phone": out.at[i, "_ph"], "email": out.at[i, "_em"], "state": out.at[i, "_st"]}

    # People this pull ALSO lists on a current-year row: that row is the freshest word
    # on this year's coverage, so their later-year row carries nothing forward
    # (a GA client on Oct 28: 2027 Report a Change + 2026 Shop for Plans).
    _isc = pd.Series([y is None or y == plan_year for y in yrs], index=out.index)
    _cur_here = out[_isc & ~out["status"].map(_cell).isin(["", "Start New Application"])]
    curidx = PersonIndex(_cur_here)

    for i in out.index:
        f, l, ph, em = out.at[i, "_f"], out.at[i, "_l"], out.at[i, "_ph"], out.at[i, "_em"]
        y = yrs.at[i]
        if y is not None and y < plan_year:                 # 2025 app: presence only
            rep["earlier_year"] += 1
            out.loc[i, _FILL_COLS] = None
            out.at[i, "fill_source"] = "earlier-year app"
            continue
        if y is None or y == plan_year:
            if has_carrier(out.at[i, "carrier"]):           # a real plan in this pull
                out.at[i, "fill_source"] = "pull"
                if not _ended(i, f, l, ph, em):
                    rep["pull"] += 1
                continue
            if normal:                                      # "NA" in a pull that shows plans
                out.at[i, "fill_source"] = "no plan in this pull"
                continue
        else:                                               # later-year (2027) row
            if curidx.match(f, l, ph, em):
                rep["later_year_only"] += 1
                out.loc[i, _FILL_COLS] = None
                out.at[i, "fill_source"] = f"{y} app (this pull's {plan_year} row decides)"
                continue
            hh = _hist(f, l, ph, em)
            cur = hh[(hh["_yr"].isna() | (hh["_yr"] == plan_year))
                     & ~hh["status"].isin(["", "Start New Application"])] if len(hh) else hh
            if not len(cur):
                rep["later_year_only"] += 1
                out.loc[i, _FILL_COLS] = None
                out.at[i, "fill_source"] = f"{y} app only"
                # Never in any pull and not in HealthSherpa: a new enrollment made
                # straight in the portal (for next year).
                if (not len(hh) and _cell(out.at[i, "status"]) in PORTAL_ACTIVE
                        and not (hsidx is not None and hsidx.match(f, l, ph, em))):
                    rep["unfilled"].append({"name": _name(i), "status": _cell(out.at[i, "status"]),
                                            "year": y, "application_date": _cell(out.at[i, "application_date"]),
                                            **_contact(i)})
                continue
            c = cur.sort_values("export_date", kind="stable").iloc[-1]
            # The person's current-year row stands in for the later-year one: its status,
            # dates and (when its pull showed plans) its carrier.
            for col in ["status", "application_date"] + _FILL_COLS:
                out.at[i, col] = c.get(col)
            out.at[i, "application_year"] = str(plan_year)
            out.at[i, "export_date"] = c["export_date"]
            rep["carried"] += 1
            rep["carried_names"].append(_name(i))
            out.at[i, "fill_source"] = f"carried from {c['export_date']:%b %d} pull"
            if c["_normal"]:
                if c["_car"]:
                    _ended(i, f, l, ph, em)
                else:
                    rep["no_plan"] += 1
                    rep["no_plan_names"].append(_name(i))
                    out.loc[i, _FILL_COLS] = None
                    out.at[i, "fill_source"] = f"no plan in the {c['export_date']:%b %d} pull"
                continue
        # Current-year row (this pull's own, or carried) with no carrier: fill it.
        fill, how = _carrier_source(f, l, ph, em, _app_date(out.at[i, "application_date"]),
                                    _cell(out.at[i, "status"]))
        if fill is None:
            out.loc[i, _FILL_COLS] = None
            out.at[i, "fill_source"] = (out.at[i, "fill_source"] + "; " if out.at[i, "fill_source"] else "") + how
            if how.startswith("no history"):
                if _cell(out.at[i, "status"]) in PORTAL_ACTIVE:
                    rep["unfilled"].append({"name": _name(i), "status": _cell(out.at[i, "status"]),
                                            "year": plan_year,
                                            "application_date": _cell(out.at[i, "application_date"]),
                                            **_contact(i)})
            else:
                rep["no_plan"] += 1
                rep["no_plan_names"].append(_name(i))
            continue
        for col, v in fill.items():
            out.at[i, col] = v
        out.at[i, "fill_source"] = (out.at[i, "fill_source"] + "; " if out.at[i, "fill_source"] else "") + how
        if _ended(i, f, l, ph, em):
            continue
        rep["filled_hs" if how == "HealthSherpa" else "filled_book"] += 1
        rep["filled_names"].append(_name(i))
    # The real application day (effective_date is this day floored to the 1st, so the
    # HealthSherpa-ended check compares this instead).
    out["application_day"] = out["application_date"].map(
        lambda v: (lambda d: d.strftime("%Y-%m-%d") if pd.notna(d) else "")(_app_date(v)))
    return out.drop(columns=["_f", "_l", "_ph", "_em", "_st"]), rep


# ── HealthSherpa "ended" beats a book row (C3) ────────────────────────────────

def hs_ended(access_rows: pd.DataFrame, hs: pd.DataFrame, plan_year: int, today=None) -> list:
    """Access rows (active status) that a fresher HealthSherpa "ended" overrides:
    the strict-key-matched HealthSherpa rows for the person have NO active row, and
    their newest plan is from this plan year and is Cancelled, or Terminated with a
    coverage end that has passed and is not 12/31 (a 12/31 end is the plan-year
    rollover, not a loss). The book keeps showing "Report a Change" after a plan ends
    (two GA clients on Oct 28: HealthSherpa and the carrier both show it ended).

    The book row is dropped only when it is the SAME application HealthSherpa shows
    ended: its real application day (application_day; effective_date is that day
    floored to the 1st) is within 3 days of HealthSherpa's submission date. A book
    application filed on another day is a different plan (a client who moved to
    Georgia Access after the HealthSherpa plan, e.g. a termed Ambetter plan in
    HealthSherpa and an active Oscar one in the book), and for GA a HealthSherpa
    "ended" is not proof of loss: that row is kept and only flagged. With no dates to
    compare, the book's floored start after HealthSherpa's end keeps it, as before.
    Returns [(index, detail, drop)]: drop=True = no longer counts, False = kept, flag."""
    today = pd.Timestamp(today).normalize() if today else pd.Timestamp.today().normalize()
    if access_rows is None or not len(access_rows) or hs is None or not len(hs):
        return []
    a = add_keys(access_rows)
    hk = add_keys(hs)
    hk["_eff"] = pd.to_datetime(hk.get("effective_date"), errors="coerce")
    hk["_ce"] = pd.to_datetime(hk.get("coverage_end"), errors="coerce")
    hidx = PersonIndex(hk)
    out = []
    for i in a.index[a["status"].isin(_ACTIVE)]:
        m = hk.loc[hidx.match(a.at[i, "_f"], a.at[i, "_l"], a.at[i, "_ph"], a.at[i, "_em"])]
        if not len(m) or m["status"].isin(_ACTIVE).any():
            continue
        n = m.sort_values(["_eff", "_ce"], na_position="first").iloc[-1]
        if pd.isna(n["_eff"]) or n["_eff"].year != plan_year:
            continue
        st, ce = _cell(n.get("status")), n["_ce"]
        if st == "Cancelled":
            ended = n["_eff"]
            how = f"HealthSherpa: this year's plan Cancelled (start {n['_eff']:%m/%d})"
        elif (st == "Terminated" and pd.notna(ce) and ce <= today
              and not (ce.month == 12 and ce.day == 31)):
            ended = ce
            how = f"HealthSherpa: this year's plan ended {ce:%m/%d}"
        else:
            continue
        app = pd.to_datetime(a.at[i, "application_day"], errors="coerce") \
            if "application_day" in a.columns else pd.NaT
        sub = pd.to_datetime(n.get("submission_date"), errors="coerce")
        if pd.notna(sub) and sub.tzinfo is not None:
            sub = _utc_to_local(sub)
        if pd.isna(sub):                                    # created_date is a UTC time
            sub = _utc_to_local(pd.to_datetime(n.get("created_date"), errors="coerce", utc=True))
        if pd.notna(app) and pd.notna(sub):
            if abs((app.normalize() - sub.normalize()).days) > 3:
                out.append((i, f"{how}; but the book's application ({app:%m/%d/%Y}) is not HealthSherpa's "
                               f"({sub:%m/%d/%Y}) — kept, verify", False))
                continue
        else:
            start = pd.to_datetime(a.at[i, "effective_date"], errors="coerce")
            if pd.notna(start) and start > ended:
                continue                                    # a newer state-exchange plan
        out.append((i, how, True))
    return out


# ── roster rule (C7) ───────────────────────────────────────────────────────────

def in_open_enrollment(day) -> bool:
    d = pd.Timestamp(day)
    return (d.month, d.day) >= (11, 1) or (d.month, d.day) <= (1, 15)


class _Protective:
    """Signs that a client who left the agent book is still Ethan's (evaluate_roster d/e,
    and undoing a counted loss in the ledger): a HealthSherpa row with Ethan as AOR
    (policy_aor; premier_policy_aor is never used) and an active status, or a carrier-
    book policy active with Ethan. HealthSherpa matches by the same app id or the
    strict key, a carrier book also by name + state (Anthem has no phone or email);
    both protective lookups accept phone + email with any last name (any_last). Each
    sign is dated by its LOCAL day (_sync_ts; books by file mtime)."""

    def __init__(self, hs: pd.DataFrame, evidence: pd.DataFrame):
        from tracker.config import get_agent
        ag = get_agent()
        npn, fn, ln = str(ag["npn"]), ag["first_name"].lower(), ag["last_name"].lower()
        self.hk, self.hidx, self.by_app = None, None, {}
        if hs is not None and len(hs):
            hk = add_keys(hs)
            hk["_sync"] = hk.get("last_ede_sync", pd.Series("", index=hk.index)).map(_sync_ts)
            hk["_ok"] = (hk["status"].isin(_ACTIVE)
                         & hk.get("policy_aor", pd.Series("", index=hk.index)).map(lambda a: _his(a, npn, fn, ln)))
            hk["_app"] = hk.get("ffm_app_id", pd.Series("", index=hk.index)).map(
                lambda v: re.sub(r"\.0$", "", _cell(v)))
            for i, a in zip(hk.index, hk["_app"]):
                if a:
                    self.by_app.setdefault(a, []).append(i)
            self.hk, self.hidx = hk, PersonIndex(hk)
        self.ev = evidence if evidence is not None and len(evidence) else None
        self.eidx = PersonIndex(self.ev, loose=True) if self.ev is not None else None
        self.book_days = sorted(self.ev["book_date"].dt.normalize().unique()) if self.ev is not None else []

    def check(self, keys, apps, state, last, first_missing) -> tuple:
        """(protective signs, weak signs) for one person. keys: (f, l, ph, em) tuples
        (the client's own and their book rows'); apps: their app ids. A sign dated on a
        DAY after first_missing protects; one from their last listing through
        first_missing is weak; an older one says nothing about the departure."""
        kept, weak = [], []
        if self.hk is not None:
            cand = set()
            for f, l, ph, em in keys:
                cand |= set(self.hidx.match(f, l, ph, em, any_last=True))
            for a in apps:
                cand |= set(self.by_app.get(a, []))
            hm = self.hk.loc[sorted(cand)] if cand else self.hk.iloc[0:0]
            hm = hm[hm["_ok"] & (hm["_sync"].dt.normalize() >= last)]
            if (hm["_sync"].dt.normalize() > first_missing).any():
                kept.append(f"HealthSherpa synced {hm['_sync'].max():%b %d} shows them active with you")
            elif len(hm):
                weak.append(f"HealthSherpa synced {hm['_sync'].max():%b %d}")
        if self.eidx is not None:
            cand = set()
            for f, l, ph, em in keys:
                cand |= set(self.eidx.match(f, l, ph, em, state=state, loose=True, any_last=True))
            em_ = self.ev.loc[sorted(cand)] if cand else self.ev.iloc[0:0]
            em_ = em_[em_["active"] & em_["his"] & (em_["book_date"].dt.normalize() >= last)]
            if len(em_):
                b = em_.sort_values("book_date").iloc[-1]
                if b["book_date"].normalize() > first_missing:
                    kept.append(f"{b['book']} book ({b['book_date']:%b %d}) shows them active with you")
                else:
                    weak.append(f"{b['book']} book ({b['book_date']:%b %d})")
        return kept, weak


def evaluate_roster(clients: pd.DataFrame, state: str, roster: pd.DataFrame, hs: pd.DataFrame,
                    evidence: pd.DataFrame, today=None) -> pd.DataFrame:
    """Which tracker-active clients of `state` left the agent book, and may it count.

    clients : tracker rows to check (already: active status, this state, not another
              agent's AOR, not manual adds); index is kept.
    roster  : load_roster(state). hs: newest HealthSherpa snapshot. evidence:
              carrier_evidence().

    A client is a candidate only when an EARLIER pull listed them (strict key) and
    the NEWEST pull does not. Listed in the newest pull with ANY status (Shop for
    Plans, in-progress, a 2027 row) = no action; never listed = no action (some real
    GA clients never show in the book). A candidate may COUNT ("count") only when
    ALL hold:
      a. the newest pull is <= 14 days old;
      b. the newest pull is not partial: >= 85% of the prior pull's people, and
         <= 5% of the prior pull's people vanished at once;
      c. their first-missing pull is not from Nov 1 - Jan 15 (open enrollment: plan
         changes everywhere). Judged by that pull's day, not today's: a client who
         left in October is not an open-enrollment artifact on Nov 2;
      d. no HealthSherpa row (same app id or strict key) synced on a DAY strictly
         after their first-missing pull shows them active with Ethan as AOR
         (policy_aor; premier_policy_aor is never used);
      e. no carrier book pulled on a DAY strictly after their first-missing pull shows
         them active with Ethan (name + state allowed: Anthem has no phone or email);
      f. at least one carrier book was pulled on/after their first-missing pull
         (otherwise nothing outside the book was checked).
    Everything else is "flag". A sign dated after the first-missing day keeps them
    his ("kept"). A sign dated from their last listing through the first-missing day
    does NOT (Ethan 2026-10-28: off the GA list = gone): the Oct 11 Oscar book, saved
    a minute before that day's GA pull, still showed two real losses active, and a
    same-morning HealthSherpa sync still named Ethan on a real loss. When that is the
    only sign they are his, the client is flagged "only same-day or earlier evidence
    ... — check" ("check"): neither counted nor cleared. A sign older than their last
    listing says nothing about the departure and is ignored. Dates are compared by
    local DAY, never minute (HealthSherpa's UTC syncs are converted first).

    Gates a, b, c and f only stop NEW counts: a loss already counted
    stays counted through the ledger below (review_roster_lost).

    "Newest" and "earlier" pulls are days with at least one UNFILTERED pull: a pull
    filtered to one application year (filtered_pulls) leaves out everyone listed
    only by another year, so on its own it can't say who left. A filtered pull still
    counts as a listing (it can only keep someone in).

    Returns one row per candidate: decision, last_listed, first_missing, reasons,
    listed_status, kept (a later sign shows them his), evidence (those signs), check
    (only same-day or earlier signs). The frame's attrs["partial"] holds the
    partial-pull reason ("" when the newest pull looks whole): the caller reports a
    partial pull once, not per client."""
    today = pd.Timestamp(today).normalize() if today else pd.Timestamp.today().normalize()
    cols = ["decision", "last_listed", "first_missing", "reasons", "listed_status", "kept", "evidence", "check"]
    empty = pd.DataFrame(columns=cols)
    empty.attrs["partial"] = ""
    if clients is None or not len(clients) or roster is None or not len(roster):
        return empty
    _filt = roster["_filtered"] if "_filtered" in roster.columns else pd.Series(False, index=roster.index)
    days = sorted(roster.loc[~_filt, "_day"].dropna().unique())
    if not days:
        return empty
    newest = pd.Timestamp(days[-1])
    rnew = roster[roster["_day"] == newest]
    ridx = PersonIndex(roster)

    # Pull-level gates.
    gate, partial = [], ""
    if (today - newest).days > STALE_DAYS:
        gate.append(f"newest {state} pull is {(today - newest).days} days old")
    if len(days) >= 2:
        rprev = roster[(roster["_day"] == pd.Timestamp(days[-2])) & ~_filt]
        n_new, n_prev, gone, _part = partial_counts(rnew, rprev, count_rows=rnew[~_filt.loc[rnew.index]])
        if _part:
            partial = (f"newest {state} pull looks partial ({n_new} people vs {n_prev}; "
                       f"{gone} vanished at once)")
            gate.append(partial)

    prot = _Protective(hs, evidence)
    k = add_keys(clients)
    rows = {}
    for i in k.index:
        f, l, ph, em = k.at[i, "_f"], k.at[i, "_l"], k.at[i, "_ph"], k.at[i, "_em"]
        hit = roster.loc[ridx.match(f, l, ph, em)]
        if not len(hit) or (hit["_day"] >= newest).any():
            continue                                        # never listed / still listed
        last = pd.Timestamp(hit["_day"].max())
        later = [pd.Timestamp(d) for d in days if pd.Timestamp(d) > last]   # unfiltered pulls
        if not later:
            continue
        first_missing = later[0]
        lrows = hit[hit["_day"] == last]
        why = list(gate)
        if in_open_enrollment(first_missing):
            why.append("open enrollment (Nov 1 – Jan 15): flag only")
        app = re.sub(r"\.0$", "", _cell(k.at[i, "ffm_app_id"])) if "ffm_app_id" in k.columns else ""
        keys = [(f, l, ph, em)] + list(zip(lrows["_f"], lrows["_l"], lrows["_ph"], lrows["_em"]))
        kept, weak = prot.check(keys, [app] if app else [], state, last, first_missing)
        why += kept
        if weak and not kept:                               # same-day or earlier only: neither way
            why.append(f"only same-day or earlier evidence shows them yours ({'; '.join(weak)}) — check")
        if not any(pd.Timestamp(d) >= first_missing for d in prot.book_days):
            why.append(f"no carrier book pulled since {first_missing:%b %d}")
        rows[i] = {"decision": "flag" if why else "count", "last_listed": last,
                   "first_missing": first_missing, "reasons": "; ".join(why),
                   "listed_status": _cell(lrows["status"].iloc[-1]),
                   "kept": bool(kept), "evidence": "; ".join(kept), "check": bool(weak and not kept)}
    res = pd.DataFrame.from_dict(rows, orient="index", columns=cols)
    res.attrs["partial"] = partial
    return res


# ── counted roster losses: the ledger (C7b) ───────────────────────────────────
#
# evaluate_roster has no memory, and its gates (stale or partial pull, a first-
# missing day in open enrollment, no carrier book since) only stop NEW counts. So a
# loss it counted is written to data/roster_lost.json (count mode only) and every
# later run keeps it counted with the same date and reason, whatever the gates say
# then. It is undone ONLY when they are listed again in a pull after their last
# listing ("back"), or a HealthSherpa or carrier-book sign dated after their
# first-missing pull shows them his ("kept"); an undone entry stays in the file,
# marked, so the same departure is not counted again once that sign ages out.
# Keyed by app id, else strict person key + state. Same safety rules as
# data/ever_his_app_ids.json: an unreadable file is copied aside and never
# overwritten, and writes are atomic.

ROSTER_LOST_PATH = _ROOT / "data" / "roster_lost.json"


def roster_lost_key(row, state: str) -> str:
    """Ledger key of one tracker row: its app id, else its strict person key + state."""
    app = re.sub(r"\.0$", "", _cell(row.get("ffm_app_id")))
    if app and app.lower() not in _NA:
        return f"app:{app}"
    f, l = name_tokens(row.get("first_name"), row.get("last_name"))
    return f"pk:{state}:{f}:{l}:{phone10(row.get('phone'))}:{email_l(row.get('email'))}"


def roster_lost_entry(row, state: str, last_listed, first_missing, reason: str, today) -> dict:
    f, l = name_tokens(row.get("first_name"), row.get("last_name"))
    app = re.sub(r"\.0$", "", _cell(row.get("ffm_app_id")))
    return {"state": state, "first_name": _cell(row.get("first_name")), "last_name": _cell(row.get("last_name")),
            "f": f, "l": l, "ph": phone10(row.get("phone")), "em": email_l(row.get("email")),
            "ffm_app_id": app if app.lower() not in _NA else "",
            "last_listed": f"{pd.Timestamp(last_listed):%Y-%m-%d}",
            "first_missing": f"{pd.Timestamp(first_missing):%Y-%m-%d}",
            "term_date": f"{pd.Timestamp(first_missing):%Y-%m-%d}", "reason": reason,
            "counted_on": f"{pd.Timestamp(today):%Y-%m-%d}"}


def read_roster_lost(path=None) -> dict:
    """The ledger, {} when there is none. An unreadable file is copied aside once
    (.corrupt_<ts>) and LedgerUnreadable is raised: the caller must not write it."""
    import json
    import shutil
    from tracker.ingest import LedgerUnreadable
    path = Path(path) if path else ROSTER_LOST_PATH
    if not path.exists():
        return {}
    try:
        d = json.loads(path.read_text())
        if not isinstance(d, dict):
            raise ValueError("not a JSON object")
        return d
    except Exception as e:
        why = f"{type(e).__name__}: {e}"
    bad = path.with_name(path.name + pd.Timestamp.now().strftime(".corrupt_%Y%m%d_%H%M%S"))
    try:
        body = path.read_bytes()
        if not any(p.read_bytes() == body for p in path.parent.glob(path.name + ".corrupt_*")):
            shutil.copy2(path, bad)
        else:
            bad = None
    except Exception:
        bad = None
    raise LedgerUnreadable(f"{path.name} unreadable ({why}) — left as is"
                           + (f", copy at {bad.name}" if bad else "") + "; restore it from a backup")


def write_roster_lost(entries: dict, path=None) -> None:
    """Write the ledger atomically (a temp file in data/ renamed into place)."""
    import json
    import os
    import tempfile
    path = Path(path) if path else ROSTER_LOST_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".roster_lost_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(dict(sorted(entries.items())), indent=1))
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def review_roster_lost(entries: dict, clients: pd.DataFrame, state: str, roster: pd.DataFrame,
                       hs: pd.DataFrame, evidence: pd.DataFrame) -> list:
    """The live (not undone) ledger entries of `state`, each with the tracker rows it
    holds (same app id, or the strict person key, in that state) and a verdict:
      "back" — a pull dated after their last listing lists them again (strict key,
               ANY row: a 2027 or Shop for Plans row too, a filtered pull too);
      "kept" — a HealthSherpa or carrier-book sign dated on a day after their
               first-missing pull shows them his (_Protective);
      "lost" — neither: still counted lost, with the entry's own date and reason.
    Returns [(key, verdict, detail, [client index])]."""
    live = {key: e for key, e in (entries or {}).items()
            if isinstance(e, dict) and e.get("state") == state and not e.get("undone")}
    out = []
    if not live:
        return out
    ck, cidx = None, None
    if clients is not None and len(clients):
        ck = add_keys(clients)
        ck["_S"] = ck.get("state", pd.Series("", index=ck.index)).map(lambda s: _cell(s).upper())
        ck["_A"] = ck.get("ffm_app_id", pd.Series("", index=ck.index)).map(lambda v: re.sub(r"\.0$", "", _cell(v)))
        cidx = PersonIndex(ck)
    has_roster = roster is not None and len(roster)
    ridx = PersonIndex(roster) if has_roster else None
    prot = _Protective(hs, evidence)
    for key, e in live.items():
        f, l, ph, em, app = (_cell(e.get(c)) for c in ("f", "l", "ph", "em", "ffm_app_id"))
        last, fm = pd.Timestamp(e["last_listed"]), pd.Timestamp(e["first_missing"])
        rows = []
        if ck is not None:
            hit = set(cidx.match(f, l, ph, em))
            if app:
                hit |= set(ck.index[ck["_A"] == app])
            rows = [i for i in sorted(hit) if ck.at[i, "_S"] == state]
        keys = [(f, l, ph, em)] + [(ck.at[i, "_f"], ck.at[i, "_l"], ck.at[i, "_ph"], ck.at[i, "_em"]) for i in rows]
        apps = sorted({app} | {ck.at[i, "_A"] for i in rows} - {""})
        if has_roster:
            rh = set()
            for k in keys:
                rh |= set(ridx.match(*k))
            rhit = roster.loc[sorted(rh)]
            again = rhit[rhit["_day"] > last]
            if len(again):
                out.append((key, "back", f"listed again in the {again['_day'].min():%b %d} pull", rows))
                continue
            lrows = rhit[rhit["_day"] == last]
            keys += list(zip(lrows["_f"], lrows["_l"], lrows["_ph"], lrows["_em"]))
        kept, _ = prot.check(keys, apps, state, last, fm)
        out.append((key, "kept", "; ".join(kept), rows) if kept else (key, "lost", "", rows))
    return out
