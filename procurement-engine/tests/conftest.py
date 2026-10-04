"""Shared fixtures. Tests run OFFLINE against an isolated temporary data directory, so they
exercise the synthetic-data and sample-news fallback path and never touch data/app.duckdb."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="procure_test_"))
os.environ["PROCURE_DATA_DIR"] = str(_TMP)
os.environ["PROCURE_DB_PATH"] = str(_TMP / "test.duckdb")
os.environ["OFFLINE"] = "1"
os.environ.pop("GEMINI_API_KEY", None)

from procure import db  # noqa: E402
from procure.settings import reset_caches  # noqa: E402

reset_caches()


@pytest.fixture(scope="session")
def seeded_db():
    """Ingest (offline: synthetic prices and FX, sample news) and evaluate models once."""
    from procure.ingest.refresh import refresh_all

    report = refresh_all(include_news=True, evaluate_models=True)
    yield report


def pytest_sessionfinish(session, exitstatus):
    db.close()
    shutil.rmtree(_TMP, ignore_errors=True)
