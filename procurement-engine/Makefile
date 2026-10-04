# Convenience targets (macOS / Linux). On Windows use the python commands in README.md.
PY := .venv/bin/python

.PHONY: setup run api ui test smoke uicheck refresh offline

setup:
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt
	$(PY) -m pip install -e .

run:
	$(PY) scripts/run_all.py

api:
	$(PY) -m uvicorn procure.api:app --port 8000

ui:
	$(PY) -m streamlit run ui/app.py

test:
	$(PY) -m pytest

smoke:
	$(PY) scripts/smoke_test.py

uicheck:
	$(PY) scripts/ui_check.py

refresh:
	$(PY) -m procure.ingest.refresh

offline:
	OFFLINE=1 $(PY) scripts/run_all.py
