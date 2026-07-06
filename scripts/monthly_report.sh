#!/bin/bash
# Auto-generates last month's Book & Performance PDF on the 1st (launchd).
cd /Users/ethanslade/commission-tracker || exit 1
/Users/ethanslade/commission-tracker/.venv/bin/python -m tracker.monthly_report >> "$HOME/Library/Logs/commission-tracker-monthly.log" 2>&1
