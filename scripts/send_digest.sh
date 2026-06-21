#!/bin/bash
# Weekly digest sender — invoked by launchd Monday mornings.
# The phone number is passed as $1 by the launchd plist, so it never lives in
# the repo.
cd /Users/ethanslade/commission-tracker || exit 1
LOG="$HOME/Library/Logs/commission-tracker-digest.log"
echo "--- $(date) --- sending weekly digest to ${1:-<none>}" >> "$LOG"
if [ -z "$1" ]; then
    echo "ERROR: no phone number argument" >> "$LOG"
    exit 1
fi
.venv/bin/track digest --to "$1" >> "$LOG" 2>&1
echo "--- $(date) --- done (exit $?)" >> "$LOG"
