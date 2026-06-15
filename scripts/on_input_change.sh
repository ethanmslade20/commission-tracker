#!/bin/bash
# Triggered by launchd WatchPaths when input/ folder changes.
# Runs ingest + report so snapshots and Google Sheet stay current.

TRACKER_DIR="/Users/ethanslade/commission-tracker"
TRACK="$TRACKER_DIR/.venv/bin/track"
LOG="$TRACKER_DIR/logs/watcher.log"

mkdir -p "$TRACKER_DIR/logs"
echo "$(date): input change detected — running ingest + report" >> "$LOG"

cd "$TRACKER_DIR" || exit 1
"$TRACK" ingest >> "$LOG" 2>&1 && "$TRACK" report >> "$LOG" 2>&1

echo "$(date): done" >> "$LOG"
