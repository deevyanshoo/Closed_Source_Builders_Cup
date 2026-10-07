"""Offer lifecycle in the Python service: statuses, save gate, corrections, audit and idempotency.

Uses the isolated test database from conftest and the synthetic quote fixtures. Extraction runs
with the rules extractor; nothing calls Google or sends anything.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from procure.offers import store
from procure.offers.lifecycle import TRANSITIONS, OfferError

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
TODAY = dt.date(2026, 10, 7)
_keys = iter(range(10_000))


def key() -> str:
    return f"test-key-{next(_keys):05d}"


def upload(quote_id, **kw):
    path = FIXTURES / QUOTES[quote_id]["file"]
    return store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=TODAY, actor="tester", **kw)


def fresh(quote_id):
    """A new offer for this fixture, even if another test already uploaded the same file."""
    return upload(quote_id, allow_duplicate=True)["offer"]


def correct(offer, field, value, evidence=None, **kw):
    return store.correct_field(offer["offer_id"], field=field, value=value, evidence=evidence,
                               expected_version=offer["version"], actor="reviewer@example.test",
                               idempotency_key=kw.pop("idempotency_key", key()), today=TODAY, **kw)


def save(offer, **kw):
    return store.save_offer(offer["offer_id"], expected_version=offer["version"], actor="reviewer@example.test",
                            idempotency_key=kw.pop("idempotency_key", key()), today=TODAY, **kw)


def ready_q01():
    """Q01 needs only its settlement currency confirmed before it can be saved."""
    offer = fresh("Q01")
    return correct(offer, "settlement_currency", "USD", [{"locator": "p1:l10", "quote": "USD"}])


# ---------- statuses ----------

def test_lifecycle_statuses_and_moves_are_explicit():
    statuses = {s for sources, target in TRANSITIONS.values() for s in (*sources, target)}
    assert statuses == {"extracted", "needs_review", "corrected", "saved", "used_in_plan", "rfq_drafted",
                        "rejected", "expired"}
    assert TRANSITIONS["save"] == ({"extracted", "needs_review", "corrected"}, "saved")


def test_new_offer_with_a_missing_critical_field_needs_review():
    offer = fresh("Q01")
    assert offer["status"] == "needs_review"
    assert offer["version"] == 1 and offer["synthetic"] is True
    assert [r["field"] for r in offer["blocking_reasons"]] == ["settlement_currency"]
    assert offer["blocking_reasons"][0]["code"] == "field_missing"


def test_expired_quote_starts_expired_and_cannot_be_saved():
    offer = fresh("Q06")
    assert offer["status"] == "expired"
    with pytest.raises(OfferError) as err:
        save(offer)
    assert err.value.code == "invalid_transition"
    assert "expired" in {r["code"] for r in err.value.details["blocking_reasons"]}


# ---------- save gate ----------

def test_save_is_blocked_with_clear_reasons():
    offer = fresh("Q01")
    with pytest.raises(OfferError) as err:
        save(offer)
    assert err.value.code == "save_blocked"
    assert err.value.details["blocking_reasons"] == [{"code": "field_missing", "field": "settlement_currency",
                                                       "message": "settlement_currency is missing"}]
    assert store.get_offer(offer["offer_id"])["status"] == "needs_review"


def test_conflicting_price_blocks_save_until_a_reviewer_chooses():
    offer = fresh("Q18")
    reasons = {(r["field"], r["code"]) for r in offer["blocking_reasons"]}
    assert ("items[0].pricing", "field_conflict") in reasons
    chosen = {"type": "fixed", "unit_price": {"amount": "9420.00", "currency": "USD"}, "per_unit": "tonne"}
    offer = correct(offer, "items[0].pricing", chosen,
                    [{"locator": "Terms!A5", "quote": "price confirmed at USD 9,420.00 per tonne"}])
    assert offer["items"][0]["pricing"]["status"] == "corrected"
    assert ("items[0].pricing", "field_conflict") not in {(r["field"], r["code"]) for r in offer["blocking_reasons"]}


def test_saved_offer_after_review():
    offer = save(ready_q01())
    assert offer["status"] == "saved" and offer["version"] == 3
    assert offer["blocking_reasons"] == []


# ---------- corrections ----------

def test_correction_is_checked_against_the_document():
    offer = fresh("Q01")
    with pytest.raises(OfferError) as err:
        correct(offer, "settlement_currency", "EUR", [{"locator": "p1:l10", "quote": "USD"}])
    assert err.value.code == "evidence_not_verified"
    assert store.get_offer(offer["offer_id"])["version"] == 1


def test_correction_value_must_be_well_formed():
    offer = fresh("Q01")
    for field, value in [("settlement_currency", "usd"), ("valid_until", "31/10/2026"),
                         ("items[0].quantity", {"amount": "-5", "unit": "tonne"}),
                         ("items[0].quantity", {"amount": "5", "unit": "ton"}),
                         ("items[0].material", "unobtainium")]:
        with pytest.raises(OfferError) as err:
            correct(offer, field, value)
        assert err.value.code == "invalid_value", (field, value)


def test_unknown_field_is_rejected():
    with pytest.raises(OfferError) as err:
        correct(fresh("Q01"), "approve_offer", True)
    assert err.value.code == "unknown_field"


def test_user_entered_value_without_evidence_is_labelled():
    offer = correct(fresh("Q01"), "settlement_currency", "USD")
    field = offer["fields"]["settlement_currency"]
    assert (field["status"], field["source"], field["evidence"]) == ("corrected", "user", [])
    assert field["evidence_status"] == "user_entered_without_document_evidence"


def test_every_change_is_recorded_with_who_when_old_and_new():
    offer = save(ready_q01())
    events = store.get_offer(offer["offer_id"], include_events=True)["events"]
    assert [e["action"] for e in events] == ["created", "correct", "save"]
    corr = events[1]
    assert corr["actor"] == "reviewer@example.test" and corr["field"] == "settlement_currency"
    assert corr["old"] == {"status": "missing", "value": None}
    assert corr["new"]["status"] == "corrected" and corr["new"]["value"] == "USD"
    assert corr["from_status"] == "needs_review" and corr["to_status"] == "corrected"
    assert all(e["at"] for e in events)


def test_correction_recomputes_normalised_values():
    offer = fresh("Q01")
    new_price = {"type": "fixed", "unit_price": {"amount": "9412.50", "currency": "USD"}, "per_unit": "tonne"}
    offer = correct(offer, "items[0].pricing", new_price)
    assert offer["normalized"]["items"][0]["pricing"]["per_kg"] == {"amount": "9.4125", "currency": "USD"}


# ---------- transitions, versions and repeats ----------

def test_invalid_moves_are_rejected():
    saved = save(ready_q01())
    with pytest.raises(OfferError) as err:
        correct(saved, "settlement_currency", "USD")
    assert err.value.code == "invalid_transition"
    rejected = store.reject_offer(fresh("Q04")["offer_id"], expected_version=1, actor="r", reason="not needed",
                                  idempotency_key=key(), today=TODAY)
    assert rejected["status"] == "rejected"
    with pytest.raises(OfferError) as err:
        save(rejected)
    assert err.value.code == "invalid_transition"


def test_stale_version_is_rejected():
    offer = ready_q01()
    stale = dict(offer, version=offer["version"] - 1)
    with pytest.raises(OfferError) as err:
        save(stale)
    assert err.value.code == "stale_version"


def test_repeated_request_has_no_second_effect():
    offer = ready_q01()
    k = key()
    first = save(offer, idempotency_key=k)
    again = save(offer, idempotency_key=k)
    assert again["version"] == first["version"] and again["replayed"] is True
    events = store.get_offer(offer["offer_id"], include_events=True)["events"]
    assert [e["action"] for e in events].count("save") == 1


def test_same_key_with_a_different_request_conflicts():
    offer = fresh("Q01")
    k = key()
    correct(offer, "settlement_currency", "USD", idempotency_key=k)
    with pytest.raises(OfferError) as err:
        correct(offer, "settlement_currency", "EUR", idempotency_key=k)
    assert err.value.code == "idempotency_conflict"


def test_uploading_the_same_document_twice_returns_the_same_offer():
    first, second = upload("Q05"), upload("Q05")
    assert second["offer"]["offer_id"] == first["offer"]["offer_id"]
    assert second["deduplicated"] is True


def test_offers_survive_a_new_connection():
    from procure import db

    offer = ready_q01()
    db.close()
    assert store.get_offer(offer["offer_id"])["fields"]["settlement_currency"]["value"] == "USD"


def test_list_offers_filters_by_status():
    saved = save(ready_q01())
    listed = store.list_offers(status="saved", today=TODAY)
    assert saved["offer_id"] in {o["offer_id"] for o in listed}
    assert all(o["status"] == "saved" for o in listed)


# ---------- planning input ----------

BUYER = {"need_by_month": 3, "current_stock_kg": "20000", "monthly_consumption_kg": "15000", "risk_appetite": "medium"}


def test_saved_offers_become_a_planning_input_with_decimal_strings():
    offer = save(ready_q01())
    plan = store.planning_input([offer["offer_id"]], buyer=BUYER, actor="planner", idempotency_key=key(),
                                today=TODAY)
    entry = plan["offers"][0]
    assert entry["offer_id"] == offer["offer_id"] and entry["synthetic"] is True
    item = entry["items"][0]
    assert item["material"] == "copper"
    assert item["quantity_kg"] == "250000"
    assert item["price"]["per_kg"] == {"amount": "9.4125", "currency": "USD"}
    legacy = plan["legacy_recommend_requests"][0]
    assert legacy["status"] == "ready"
    assert legacy["request"]["material"] == "copper" and legacy["request"]["supplier_lead_time_months"] == 2
    assert "lead_time_days=45 rounded up to whole months" in legacy["assumptions"][0]
    assert store.get_offer(offer["offer_id"])["status"] == "used_in_plan"


def test_planning_input_rejects_offers_that_are_not_saved():
    with pytest.raises(OfferError) as err:
        store.planning_input([fresh("Q01")["offer_id"]], buyer=BUYER, actor="planner", idempotency_key=key(),
                             today=TODAY)
    assert err.value.code == "invalid_transition"


def test_missing_buyer_inputs_make_the_legacy_request_unavailable():
    offer = save(ready_q01())
    plan = store.planning_input([offer["offer_id"]], buyer={}, actor="planner", idempotency_key=key(), today=TODAY)
    legacy = plan["legacy_recommend_requests"][0]
    assert legacy["status"] == "unavailable" and legacy["request"] is None
    assert "need_by_month" in legacy["reason"]
