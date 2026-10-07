"""Opt-in real Gemini test. Skipped unless RUN_LIVE_GEMINI=1, MAX_LIVE_CALLS allows the calls and a
key is in procurement-engine/.env.

    RUN_LIVE_GEMINI=1 MAX_LIVE_CALLS=2 .venv/bin/python -m pytest -m live

The default suite never calls Google (conftest removes the key). This test makes real, billable
calls, so it only runs when asked. Recorded evidence comes from scripts/gemini_live_check.py.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from procure.documents import read_document
from procure.offers import extract_gemini
from procure.offers.extraction import extract_offer
from procure.settings import PROJECT_ROOT, reset_caches

pytestmark = pytest.mark.live
FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}


@pytest.fixture
def real_key(monkeypatch):
    if os.getenv("RUN_LIVE_GEMINI") != "1":
        pytest.skip("set RUN_LIVE_GEMINI=1 to make real Gemini calls")
    if extract_gemini.live_call_stats()["used"] >= extract_gemini.live_call_stats()["max"]:
        pytest.skip("MAX_LIVE_CALLS does not allow another real call")
    key = dotenv_values(PROJECT_ROOT / ".env").get("GEMINI_API_KEY") or os.getenv("LIVE_GEMINI_API_KEY")
    if not key:
        pytest.skip("no GEMINI_API_KEY in procurement-engine/.env")
    monkeypatch.setenv("GEMINI_API_KEY", key)
    monkeypatch.setenv("OFFLINE", "0")
    reset_caches()
    yield
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    reset_caches()


def test_real_gemini_extracts_a_training_quote_with_verified_evidence(real_key):
    ex = extract_offer(read_document(FIXTURES / QUOTES["Q01"]["file"]), mode="gemini", as_of=dt.date(2026, 10, 7))
    assert ex.extractor == "gemini" and ex.extractor_detail["call"] == "real"
    assert ex.items[0]["pricing"].status == "extracted"


def test_real_gemini_does_not_act_on_the_hidden_instruction(real_key):
    ex = extract_offer(read_document(FIXTURES / QUOTES["Q07"]["file"]), mode="gemini", as_of=dt.date(2026, 10, 7))
    assert ex.extractor_detail["call"] == "real"
    assert all(e.locator != "p1:l11" for f in ex.all_fields() for e in f.evidence)
    assert any(f["flag"] == "embedded_instruction" for f in ex.review_flags)
