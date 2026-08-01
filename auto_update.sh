#!/bin/bash
# Auto-runs whenever a new CSV is dropped into input/
# Triggered by launchd WatchPaths on the input/ folder

cd /Users/ethanslade/commission-tracker
source .venv/bin/activate

LOGFILE="$HOME/Library/Logs/commission-tracker-auto.log"
LOCKDIR="/tmp/commission-tracker-auto.lock"
COOLDOWN_FILE="/tmp/commission-tracker-auto.last_run"
COOLDOWN_SECONDS=180

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

# Input-change guard (added 2026-08-01): skip a full re-run when the staged
# inputs are byte-identical to the last SUCCESSFUL run. Stops redundant
# back-to-back runs from duplicate fs events or re-copying an unchanged file
# (which is how the double-run happened). Fingerprint = hash of every staged
# input + carrier book. To FORCE a run after a CODE change with unchanged
# inputs, delete this file first:  /tmp/commission-tracker-auto.inputs_fp
FP_FILE="/tmp/commission-tracker-auto.inputs_fp"
INPUTS_FP=$(find input carrier_books -type f \( -name '*.csv' -o -name '*.xlsx' -o -name '*.zip' \) 2>/dev/null -exec md5 -q {} \; | sort | md5 -q)
if [ -f "$FP_FILE" ] && [ -n "$INPUTS_FP" ] && [ "$(cat "$FP_FILE")" = "$INPUTS_FP" ]; then
    echo "--- $(date) --- Skipped: staged inputs unchanged since last successful run (delete $FP_FILE to force)." >> "$LOGFILE"
    exit 0
fi

mkdir "$LOCKDIR" 2>/dev/null || { echo "--- $(date) --- Skipped: could not acquire lock." >> "$LOGFILE"; exit 0; }
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

echo "--- $(date) --- New file detected, running update..." >> "$LOGFILE"

track ingest >> "$LOGFILE" 2>&1
if [ $? -eq 0 ]; then
    track report >> "$LOGFILE" 2>&1
    echo "$INPUTS_FP" > "$FP_FILE"   # remember what we just processed (input-change guard)
    echo "--- Done ---" >> "$LOGFILE"
else
    echo "--- Ingest failed, skipping report ---" >> "$LOGFILE"
fi

date +%s > "$COOLDOWN_FILE"
