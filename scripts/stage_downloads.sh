#!/bin/bash
# Triggered by launchd whenever ~/Downloads changes — stages recognized exports.
cd /Users/ethanslade/commission-tracker || exit 1
/Users/ethanslade/commission-tracker/.venv/bin/python -m tracker.stage_downloads
