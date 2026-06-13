.PHONY: setup ingest report auth-check diff help

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
TRACK := $(VENV)/bin/track

help:
	@echo "commission-tracker"
	@echo ""
	@echo "  make setup       Create venv and install dependencies"
	@echo "  make auth-check  Verify ADC + service account impersonation"
	@echo "  make ingest      Process all CSVs in input/"
	@echo "  make report      Rebuild all Google Sheet tabs"
	@echo "  make diff M1=2024-01 M2=2024-02  Ad-hoc month diff"

setup: $(VENV)/bin/activate

$(VENV)/bin/activate: requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e .
	@echo ""
	@echo "✓ Setup complete. Activate with: source .venv/bin/activate"

auth-check:
	$(TRACK) auth-check

ingest:
	$(TRACK) ingest

report:
	$(TRACK) report

diff:
	@if [ -z "$(M1)" ] || [ -z "$(M2)" ]; then \
		echo "Usage: make diff M1=2024-01 M2=2024-02"; exit 1; \
	fi
	$(TRACK) diff $(M1) $(M2)
