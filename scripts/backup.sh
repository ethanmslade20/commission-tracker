#!/bin/bash
# Daily backup — invoked by launchd. Snapshots local state + exports the sheets.
cd /Users/ethanslade/commission-tracker || exit 1
LOG="$HOME/Library/Logs/commission-tracker-backup.log"
echo "--- $(date) --- daily backup" >> "$LOG"
.venv/bin/track backup >> "$LOG" 2>&1
echo "--- $(date) --- done (exit $?)" >> "$LOG"
