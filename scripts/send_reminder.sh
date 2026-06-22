#!/bin/bash
# Weekly Sunday reminder to pull carrier reports — invoked by launchd.
cd /Users/ethanslade/commission-tracker || exit 1
LOG="$HOME/Library/Logs/commission-tracker-reminder.log"
MSG="📋 Sunday reminder: pull your 4 carrier reports — Ambetter, Oscar, UHC, Anthem — and drop them in (plus grab Ambetter's Unpaid tab). ~10 min, keeps Monday's numbers fresh."
echo "--- $(date) --- sending Sunday reminder" >> "$LOG"
.venv/bin/track remind --to "+18013583482" --message "$MSG" >> "$LOG" 2>&1
