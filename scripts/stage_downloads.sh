#!/bin/bash
# Runs on ~/Downloads changes (WatchPaths) AND every 2 min (StartInterval sweeper).
cd /Users/ethanslade/commission-tracker || exit 1
/Users/ethanslade/commission-tracker/.venv/bin/python -m tracker.stage_downloads
