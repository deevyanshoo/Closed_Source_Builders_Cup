"""Offer lifecycle in the Python service: statuses, save gate, corrections, audit and idempotency.

Uses the isolated test database from conftest and the synthetic quote fixtures. Extraction runs
with the rules extractor; nothing calls Google or sends anything.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

import pytest

from procure import db
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
    return correct(offer, "settlement_currency", "USD")


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


def test_price_line_cannot_document_verify_settlement_but_uncited_reviewer_assertion_can_save():
    offer = fresh("Q01")
    with pytest.raises(OfferError) as err:
        correct(offer, "settlement_currency", "USD", [{"locator": "p1:l10", "quote": "USD"}])
    assert err.value.code == "evidence_not_verified"
    unchanged = store.get_offer(offer["offer_id"], today=TODAY)
    assert unchanged["status"] == "needs_review" and unchanged["version"] == 1
    assert "save" not in unchanged["allowed_actions"]
    asserted = correct(offer, "settlement_currency", "USD")
    field = asserted["fields"]["settlement_currency"]
    assert field["evidence_status"] == "user_entered_without_document_evidence" and field["evidence"] == []
    assert save(asserted)["status"] == "saved"


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


@pytest.mark.parametrize("broken_table", ["offer_events", "offer_requests"])
def test_save_failure_rolls_back_state_event_and_receipt_then_retries(monkeypatch, broken_table):
    offer = ready_q01()
    request_key = key()
    original = db.insert_row

    def fail_once(table, row):
        if table == broken_table:
            raise RuntimeError("injected write failure")
        return original(table, row)

    with monkeypatch.context() as patch:
        patch.setattr(db, "insert_row", fail_once)
        with pytest.raises(RuntimeError, match="injected write failure"):
            save(offer, idempotency_key=request_key)
    unchanged = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
    assert unchanged["status"] == "corrected" and unchanged["version"] == offer["version"]
    assert [e["action"] for e in unchanged["events"]] == ["created", "correct"]
    assert store._rows("SELECT * FROM offer_requests WHERE idempotency_key = ?", [request_key]) == []
    saved = save(offer, idempotency_key=request_key)
    assert saved["status"] == "saved" and saved["replayed"] is False
    db.close()
    assert save(offer, idempotency_key=request_key)["replayed"] is True
    assert [e["action"] for e in store.get_offer(offer["offer_id"], include_events=True, today=TODAY)["events"]].count("save") == 1


def test_second_offer_failure_rolls_back_entire_planning_batch(monkeypatch):
    first, second = save(ready_q01()), save(ready_q01())
    original = store._write
    writes = 0

    def fail_second(row, payload, status):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("second offer failed")
        return original(row, payload, status)

    with monkeypatch.context() as patch:
        patch.setattr(store, "_write", fail_second)
        with pytest.raises(RuntimeError, match="second offer failed"):
            store.planning_input([first["offer_id"], second["offer_id"]], buyer=BUYER, actor="planner",
                                 idempotency_key=key(), today=TODAY)
    for offer in (first, second):
        after = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
        assert after["status"] == "saved" and after["version"] == offer["version"]
        assert [e["action"] for e in after["events"]].count("use_in_plan") == 0


def test_as_of_preview_does_not_persist_expiry_or_resurrect_offer():
    offer = ready_q01()
    future = store.get_offer(offer["offer_id"], include_events=True, today=dt.date(2099, 1, 1))
    assert future["status"] == "expired" and future["version"] == offer["version"]
    assert future["normalized"]["as_of"] == "2099-01-01"
    assert future["normalized"]["expiry"] == "expired"
    assert [e["action"] for e in future["events"]].count("expire") == 0
    past = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
    assert past["status"] == "corrected" and past["version"] == offer["version"]
    assert past["normalized"]["as_of"] == TODAY.isoformat()
    assert past["normalized"]["expiry"] == "valid"
    assert past["normalized"]["items"] == future["normalized"]["items"]
    assert [e["action"] for e in past["events"]].count("expire") == 0


def test_concurrent_default_upload_of_same_document_deduplicates_inside_lock(monkeypatch):
    # Both callers must have passed the fast precheck before either inserts.
    content = (f"supplier,Unique {key()}\nmaterial,Steel\nprice,USD 10 per tonne\n").encode()
    barrier = threading.Barrier(2, timeout=10)
    original = store.extract_offer
    results, errors = [], []

    def synced_extract(*args, **kwargs):
        barrier.wait()
        return original(*args, **kwargs)

    def create():
        try:
            results.append(store.create_from_upload(content, "same.csv", mode="rules", as_of=TODAY))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with monkeypatch.context() as patch:
        patch.setattr(store, "extract_offer", synced_extract)
        threads = [threading.Thread(target=create) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    assert len(results) == 2
    assert len({result["offer"]["offer_id"] for result in results}) == 1
    assert sorted(result["deduplicated"] for result in results) == [False, True]
    duplicate = store.create_from_upload(content, "same.csv", mode="rules", as_of=TODAY,
                                         allow_duplicate=True)
    assert duplicate["deduplicated"] is False
    assert duplicate["offer"]["offer_id"] != results[0]["offer"]["offer_id"]


def test_concurrent_authoritative_expiry_writes_one_event():
    offer = ready_q01()
    results = []

    def expire():
        results.append(store.expire_due(offer["offer_id"], today=dt.date(2099, 1, 1)))

    threads = [threading.Thread(target=expire) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 2
    after = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
    assert after["status"] == "expired" and after["version"] == offer["version"] + 1
    assert [e["action"] for e in after["events"]].count("expire") == 1


def test_changed_actor_cannot_replay_a_mutation_receipt():
    offer = ready_q01()
    request_key = key()
    save(offer, idempotency_key=request_key)
    with pytest.raises(OfferError) as err:
        store.save_offer(offer["offer_id"], expected_version=offer["version"], actor="another-reviewer",
                         idempotency_key=request_key, today=TODAY)
    assert err.value.code == "idempotency_conflict"


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "1e400", "-1", "oops"])
def test_invalid_buyer_numbers_are_rejected_before_planning_effects(bad):
    offer = save(ready_q01())
    with pytest.raises(OfferError) as err:
        store.planning_input([offer["offer_id"]], buyer={**BUYER, "current_stock_kg": bad}, actor="planner",
                             idempotency_key=key(), today=TODAY)
    assert err.value.code == "invalid_value"
    after = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
    assert after["status"] == "saved" and [e["action"] for e in after["events"]].count("use_in_plan") == 0


def test_duplicate_ids_rejected_before_any_planning_effect():
    offer = save(ready_q01())
    with pytest.raises(OfferError) as err:
        store.planning_input([offer["offer_id"], offer["offer_id"]], buyer=BUYER, actor="planner",
                             idempotency_key=key(), today=TODAY)
    assert err.value.code == "invalid_request"
    assert store.get_offer(offer["offer_id"], today=TODAY)["status"] == "saved"


@pytest.mark.parametrize("change", [
    {"averaging_period": None}, {"averaging_period": "  "},
    {"premium": {"amount": "1e400", "currency": "USD"}},
    {"fx_basis": "  "},
    {"per_unit": []},
    {"premium": {"amount": "95.00", "currency": []}},
])
def test_incomplete_or_unrepresentable_index_correction_is_rejected(change):
    offer = fresh("Q10")
    original = offer["items"][0]["pricing"]["value"]
    corrected = {**original, **change}
    if change.get("averaging_period") is None:
        corrected.pop("averaging_period")
    with pytest.raises(OfferError) as err:
        correct(offer, "items[0].pricing", corrected)
    assert err.value.code == "invalid_value"
    assert store.get_offer(offer["offer_id"], today=TODAY)["version"] == offer["version"]


def test_complete_signed_index_correction_with_fx_basis_can_save_without_rate():
    offer = fresh("Q10")
    price = {**offer["items"][0]["pricing"]["value"],
             "premium": {"amount": "-95.00", "currency": "EUR"},
             "fx_basis": "ECB reference rate on invoice date"}
    corrected = correct(offer, "items[0].pricing", price)
    assert corrected["normalized"]["items"][0]["pricing"]["status"] == "ok"
    assert corrected["items"][0]["pricing"]["value"]["premium"]["amount"] == "-95.00"
    corrected = correct(corrected, "settlement_currency", "USD")
    assert save(corrected)["status"] == "saved"


def test_planning_entry_distinguishes_reviewer_assertion_from_document_evidence():
    corrected = correct(fresh("Q01"), "settlement_currency", "USD")
    saved = save(corrected)
    plan = store.planning_input([saved["offer_id"]], buyer=BUYER, actor="planner", idempotency_key=key(), today=TODAY)
    entry = plan["offers"][0]
    asserted = entry["field_provenance"]["settlement_currency"]
    assert asserted["source"] == "user" and asserted["corrected_by"] == "reviewer@example.test"
    assert asserted["evidence_status"] == "user_entered_without_document_evidence"
    assert asserted["evidence_refs"] == []
    documented = entry["field_provenance"]["items[0].pricing"]
    assert documented["source"] == "rules" and documented["evidence_refs"]


def test_rfq_receipt_failure_rolls_back_recommendation_action_and_offer(monkeypatch, seeded_db):
    offer = save(ready_q01())
    before = {table: len(store._rows(f"SELECT * FROM {table}")) for table in ("recommendations", "actions")}
    original = db.insert_row
    request_key = key()

    def fail_receipt(table, row):
        if table == "offer_requests" and row["idempotency_key"] == request_key:
            raise RuntimeError("RFQ receipt failed")
        return original(table, row)

    with monkeypatch.context() as patch:
        patch.setattr(db, "insert_row", fail_receipt)
        with pytest.raises(RuntimeError, match="RFQ receipt failed"):
            store.draft_rfq(offer["offer_id"], buyer=BUYER, actor="planner", idempotency_key=request_key, today=TODAY)
    after = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
    assert after["status"] == "saved" and after["version"] == offer["version"]
    assert [e["action"] for e in after["events"]].count("draft_rfq") == 0
    assert {table: len(store._rows(f"SELECT * FROM {table}")) for table in before} == before
    assert store._rows("SELECT * FROM offer_requests WHERE idempotency_key = ?", [request_key]) == []


def test_failed_system_expiry_event_rolls_back_expiry(monkeypatch):
    offer = ready_q01()
    original = db.insert_row

    def fail_expiry(table, row):
        if table == "offer_events" and row["action"] == "expire":
            raise RuntimeError("expiry event failed")
        return original(table, row)

    with monkeypatch.context() as patch:
        patch.setattr(db, "insert_row", fail_expiry)
        with pytest.raises(RuntimeError, match="expiry event failed"):
            store.expire_due(offer["offer_id"], today=dt.date(2099, 1, 1))
    after = store.get_offer(offer["offer_id"], include_events=True, today=TODAY)
    assert after["status"] == "corrected" and after["version"] == offer["version"]
    assert [e["action"] for e in after["events"]].count("expire") == 0


def test_replay_keeps_accepted_response_after_clock_advances():
    offer = ready_q01()
    request_key = key()
    first = save(offer, idempotency_key=request_key)
    replay = store.save_offer(offer["offer_id"], expected_version=offer["version"], actor="reviewer@example.test",
                              idempotency_key=request_key, today=dt.date(2099, 1, 1))
    assert replay["replayed"] is True and replay["status"] == first["status"] == "saved"
    assert replay["version"] == first["version"]
    # A replay is the historical response, not a fresh eligibility check or a new expiry transition.
    assert [e["action"] for e in store.get_offer(offer["offer_id"], include_events=True, today=TODAY)["events"]].count("expire") == 0


def test_upload_event_failure_rolls_back_document_extraction_and_offer(monkeypatch):
    store.ensure_schema()
    before = {table: len(store._rows(f"SELECT * FROM {table}"))
              for table in ("offer_documents", "offer_extractions", "offers", "offer_events")}
    original = db.insert_row

    def fail_created(table, row):
        if table == "offer_events" and row["action"] == "created":
            raise RuntimeError("created event failed")
        return original(table, row)

    with monkeypatch.context() as patch:
        patch.setattr(db, "insert_row", fail_created)
        with pytest.raises(RuntimeError, match="created event failed"):
            upload("Q01", allow_duplicate=True)
    assert {table: len(store._rows(f"SELECT * FROM {table}")) for table in before} == before
