#!/bin/bash
# Auto-ingest: triggered by launchd WatchPaths when input/ changes.
# Runs only when healthsherpa.csv is present; skips otherwise.

INPUT_DIR="$HOME/commission-tracker/input"
TRACK="$HOME/commission-tracker/.venv/bin/track"
LOG="$HOME/commission-tracker/logs/auto_ingest.log"

mkdir -p "$(dirname "$LOG")"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

if [ ! -f "$INPUT_DIR/healthsherpa.csv" ]; then
    log "No healthsherpa.csv found — skipping."
    exit 0
fi

MONTH=$(date '+%Y-%m')
log "healthsherpa.csv detected. Starting ingest for $MONTH..."

"$TRACK" ingest --month "$MONTH" >> "$LOG" 2>&1
INGEST_STATUS=$?

if [ $INGEST_STATUS -ne 0 ]; then
    log "ERROR: ingest failed (exit $INGEST_STATUS). Aborting report."
    exit $INGEST_STATUS
fi

"$TRACK" report >> "$LOG" 2>&1
REPORT_STATUS=$?

if [ $REPORT_STATUS -ne 0 ]; then
    log "ERROR: report failed (exit $REPORT_STATUS)."
    exit $REPORT_STATUS
fi

log "Done. Google Sheet updated."
