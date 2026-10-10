"""Scoped synthetic offer review -> save -> planning input; global RFQ remains unavailable.

Uses temporary local DuckDB and the generated quote fixture only; no seeded market run."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from procure import actions, db
from procure.offers import store
from procure.offers.context import OfferContext
from procure.offers.lifecycle import OfferError

CONTEXT = OfferContext("synthetic-reviewer", "synthetic-workspace", "editor")
SYSTEM_CONTEXT = OfferContext("system", "synthetic-workspace", "system")

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
TODAY = dt.date(2026, 10, 7)
BUYER = {"need_by_month": 3, "current_stock_kg": "20000", "monthly_consumption_kg": "15000", "risk_appetite": "medium"}
_k = iter(range(10_000))


def key():
    return f"e2e-key-{next(_k):05d}"


def saved_q01():
    path = FIXTURES / QUOTES["Q01"]["file"]
    offer = store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=TODAY,
                                     allow_duplicate=True, context=CONTEXT)["offer"]
    offer = store.correct_field(offer["offer_id"], field="settlement_currency", value="USD",
                                evidence=None, expected_version=1, idempotency_key=key(), today=TODAY, context=CONTEXT)
    return store.save_offer(offer["offer_id"], expected_version=offer["version"],
                            idempotency_key=key(), today=TODAY, context=CONTEXT)


def test_q01_settlement_is_an_uncited_reviewer_assertion_and_price_keeps_its_source():
    offer = saved_q01()
    settlement = offer["fields"]["settlement_currency"]
    pricing = offer["items"][0]["pricing"]
    assert settlement["source"] == "user"
    assert settlement["evidence"] == []
    assert settlement["evidence_status"] == "user_entered_without_document_evidence"
    assert pricing["source"] == "rules"
    assert any(e["locator"] == "p1:l10" for e in pricing["evidence"])


def test_saved_offer_to_scoped_planning_without_global_rfq():
    offer = saved_q01()
    plan = store.planning_input([offer["offer_id"]], buyer=BUYER,
                                idempotency_key=key(), today=TODAY, context=CONTEXT)
    assert plan["offers"][0]["offer_id"] == offer["offer_id"]
    with pytest.raises(OfferError) as err:
        store.draft_rfq(offer["offer_id"], buyer=BUYER, idempotency_key=key(),
                        today=TODAY, context=CONTEXT)
    assert err.value.code == "service_route_unavailable"
    events = store.get_offer(offer["offer_id"], include_events=True, context=CONTEXT, today=TODAY)["events"]
    assert [event["action"] for event in events] == ["created", "correct", "save", "use_in_plan"]
