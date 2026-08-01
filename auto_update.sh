#!/bin/bash
# Auto-runs whenever a new CSV is dropped into input/
# Triggered by launchd WatchPaths on the input/ folder

cd /Users/ethanslade/commission-tracker || { echo "--- $(date) --- ABORT: cannot cd to repo" >> "$HOME/Library/Logs/commission-tracker-auto.log"; exit 1; }
source .venv/bin/activate

LOGFILE="$HOME/Library/Logs/commission-tracker-auto.log"
LOCKDIR="/tmp/commission-tracker-auto.lock"
COOLDOWN_FILE="/tmp/commission-tracker-auto.last_run"
COOLDOWN_SECONDS=180
FP_FILE="/tmp/commission-tracker-auto.inputs_fp"
HS_CSV="input/healthsherpa.csv"
MARKER="data/last_upload_hash.txt"

# Skip if another run is already in progress (stale locks older than 10 min are cleared)
if [ -d "$LOCKDIR" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -lt 600 ]; then
        echo "--- $(date) --- Skipped: a run is already in progress." >> "$LOGFILE"
        exit 0
    fi
    rmdir "$LOCKDIR" 2>/dev/null
fi

# Skip if a run finished too recently (debounces multiple rapid file events / quota cooldown)
if [ -f "$COOLDOWN_FILE" ]; then
    LAST_RUN=$(cat "$COOLDOWN_FILE")
    NOW=$(date +%s)
    if [ -n "$LAST_RUN" ] && [ $(( NOW - LAST_RUN )) -lt "$COOLDOWN_SECONDS" ]; then
        echo "--- $(date) --- Skipped: last run finished less than ${COOLDOWN_SECONDS}s ago." >> "$LOGFILE"
        exit 0
    fi
fi

# --- Input/code-change guard (2026-08-01; hardened after review w245k82uk) ------
# A COMPUTE saver only: skip a redundant re-run when nothing that affects the
# report changed AND there is no upload text still owed. It must NEVER suppress a
# pending/failed text retry or a crashed report — report.py's marker
# (last_upload_hash.txt) stays the single source of truth for whether a text is
# owed. Fingerprint spans staged inputs + carrier books + code + config (so a
# code/config edit is not silently skipped) and excludes rotating *backup* files.
# `md5 {} +` includes the path, so the hash is filename-aware. To force a run,
# delete $FP_FILE.
FIND_FP() { find input carrier_books tracker config -type f \
    \( -name '*.csv' -o -name '*.xlsx' -o -name '*.zip' -o -name '*.py' -o -name '*.yaml' -o -name '*.yml' \) \
    ! -name '*backup*' ! -path '*/.venv/*' 2>/dev/null ; }
NFILES=$(FIND_FP | wc -l | tr -d ' ')
INPUTS_FP=$(find input carrier_books tracker config -type f \
    \( -name '*.csv' -o -name '*.xlsx' -o -name '*.zip' -o -name '*.py' -o -name '*.yaml' -o -name '*.yml' \) \
    ! -name '*backup*' ! -path '*/.venv/*' -exec md5 {} + 2>/dev/null | sort | md5 -q)

# text_done: report.py's HS-upload text obligation is satisfied when the marker
# equals md5 of the current input book (report.py hashes the same file).
text_done() {
    [ -f "$HS_CSV" ] && [ -f "$MARKER" ] && \
    [ "$(md5 -q "$HS_CSV" 2>/dev/null)" = "$(tr -d '[:space:]' < "$MARKER")" ]
}

if [ "$NFILES" -gt 0 ] && [ -f "$FP_FILE" ] && [ "$(cat "$FP_FILE")" = "$INPUTS_FP" ] && text_done; then
    echo "--- $(date) --- Skipped: inputs+code unchanged and no upload text pending (delete $FP_FILE to force)." >> "$LOGFILE"
    exit 0
fi

mkdir "$LOCKDIR" 2>/dev/null || { echo "--- $(date) --- Skipped: could not acquire lock." >> "$LOGFILE"; exit 0; }
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

echo "--- $(date) --- New file detected, running update..." >> "$LOGFILE"

track ingest >> "$LOGFILE" 2>&1
RC_I=$?
if [ "$RC_I" -eq 0 ]; then
    track report >> "$LOGFILE" 2>&1
    RC_R=$?
    # Only remember these inputs (and thus allow a future skip) once the report
    # fully SUCCEEDED *and* the upload text is settled. If the report crashed
    # (RC_R!=0) or left a text pending (send failed → marker still stale), do NOT
    # write the fingerprint, so the next watcher fire re-runs and retries.
    if [ "$RC_R" -eq 0 ] && text_done; then
        echo "$INPUTS_FP" > "$FP_FILE"
        echo "--- Done ---" >> "$LOGFILE"
    elif [ "$RC_R" -eq 0 ]; then
        echo "--- Report OK but upload text PENDING (send deferred) — fingerprint NOT recorded so next run retries ---" >> "$LOGFILE"
    else
        echo "--- Report FAILED (rc=$RC_R) — fingerprint NOT recorded; will retry ---" >> "$LOGFILE"
    fi
else
    echo "--- Ingest failed (rc=$RC_I), skipping report ---" >> "$LOGFILE"
fi

date +%s > "$COOLDOWN_FILE"
