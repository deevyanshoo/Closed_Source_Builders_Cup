"""End to end in the Python service: saved offer -> planning input -> existing recommend() -> existing local RFQ
draft. Nothing is sent. Uses the seeded offline database (synthetic market data) and synthetic quotes."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from procure import actions, db
from procure.offers import store
from procure.offers.lifecycle import OfferError

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
TODAY = dt.date(2026, 10, 7)
BUYER = {"need_by_month": 3, "current_stock_kg": "20000", "monthly_consumption_kg": "15000", "risk_appetite": "medium"}
_k = iter(range(10_000))


def key():
    return f"e2e-key-{next(_k):05d}"


def saved_q01():
    path = FIXTURES / QUOTES["Q01"]["file"]
    offer = store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=TODAY, actor="buyer",
                                     allow_duplicate=True)["offer"]
    offer = store.correct_field(offer["offer_id"], field="settlement_currency", value="USD",
                                evidence=[{"locator": "p1:l10", "quote": "USD"}], expected_version=1,
                                actor="reviewer", idempotency_key=key(), today=TODAY)
    return store.save_offer(offer["offer_id"], expected_version=offer["version"], actor="reviewer",
                            idempotency_key=key(), today=TODAY)


def rfq_count():
    return int(db.query_df("SELECT COUNT(*) AS n FROM actions WHERE type = 'rfq'")["n"].iloc[0])


def test_saved_offer_to_planning_input_to_local_rfq_draft(seeded_db):
    offer = saved_q01()
    plan = store.planning_input([offer["offer_id"]], buyer=BUYER, actor="planner", idempotency_key=key(), today=TODAY)
    assert plan["legacy_recommend_requests"][0]["status"] == "ready"
    before = rfq_count()
    result = store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=key(), today=TODAY)
    assert result["sent"] is False
    assert result["offer"]["status"] == "rfq_drafted"
    action = actions.get_action(result["rfq"]["action_id"])
    assert action["type"] == "rfq" and action["status"] == "draft"
    assert action["recommendation_id"] == result["recommendation"]["id"]
    assert result["rfq"]["subject"].startswith("Request for quotation")
    assert rfq_count() == before + 1
    events = store.get_offer(offer["offer_id"], include_events=True)["events"]
    assert [e["action"] for e in events] == ["created", "correct", "save", "use_in_plan", "draft_rfq"]
    assert events[-1]["new"]["sent"] is False and events[-1]["new"]["action_id"] == action["id"]


def test_repeating_the_rfq_request_creates_no_second_draft(seeded_db):
    offer = saved_q01()
    k = key()
    first = store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=k, today=TODAY)
    count = rfq_count()
    again = store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=k, today=TODAY)
    assert again["replayed"] is True and again["rfq"]["action_id"] == first["rfq"]["action_id"]
    assert rfq_count() == count


def test_a_drafted_offer_cannot_draft_again_with_a_new_key(seeded_db):
    offer = saved_q01()
    store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=key(), today=TODAY)
    with pytest.raises(OfferError) as err:
        store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=key(), today=TODAY)
    assert err.value.code == "invalid_transition"


def test_unsaved_offer_cannot_reach_an_rfq(seeded_db):
    path = FIXTURES / QUOTES["Q01"]["file"]
    offer = store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=TODAY, actor="buyer",
                                     allow_duplicate=True)["offer"]
    before = rfq_count()
    with pytest.raises(OfferError) as err:
        store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=key(), today=TODAY)
    assert err.value.code == "invalid_transition"
    assert rfq_count() == before


def test_missing_buyer_inputs_stop_before_any_recommendation(seeded_db):
    offer = saved_q01()
    recs = int(db.query_df("SELECT COUNT(*) AS n FROM recommendations")["n"].iloc[0])
    with pytest.raises(OfferError) as err:
        store.draft_rfq(offer["offer_id"], buyer={}, actor="planner", idempotency_key=key(), today=TODAY)
    assert err.value.code == "planning_unavailable"
    assert int(db.query_df("SELECT COUNT(*) AS n FROM recommendations")["n"].iloc[0]) == recs
    assert store.get_offer(offer["offer_id"])["status"] == "saved"


def test_rfq_draft_route(seeded_db):
    from fastapi.testclient import TestClient
    from procure.api import app

    offer = saved_q01()
    with TestClient(app) as client:
        r = client.post(f"/offers/{offer['offer_id']}/rfq-draft",
                        json={"buyer": BUYER, "actor": "planner", "idempotency_key": key(), "as_of": "2026-10-07"})
    assert r.status_code == 200
    assert r.json()["sent"] is False and r.json()["offer"]["status"] == "rfq_drafted"
