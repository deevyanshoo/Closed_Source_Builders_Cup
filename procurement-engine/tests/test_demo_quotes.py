"""The hosted synthetic quote boundary, exercised without a provider call."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

import pytest

TODAY = date(2026, 10, 14)


def service():
    from procure.demo import quote_service
    return quote_service


def test_allowlist_has_two_pdfs_one_csv_and_rejects_altered_bytes():
    api = service()
    fixtures = api.list_fixtures()
    assert {f["id"] for f in fixtures} == {"late-copper", "express-copper", "eu-copper"}
    assert sorted(f["filename"].rsplit(".", 1)[-1] for f in fixtures) == ["csv", "pdf", "pdf"]
    for fixture in fixtures:
        filename, data = api.fixture_bytes(fixture["id"])
        assert api.match_fixture(filename, data) == fixture["id"]
        with pytest.raises(api.DemoError) as err:
            api.match_fixture(filename, data + b"altered")
        assert err.value.code == "fixture_not_allowed"


def test_missing_settlement_requires_reviewer_correction_without_fake_evidence():
    api = service()
    payload = api.extract_fixture("late-copper", mode="rules", as_of=TODAY)
    offer = api.new_offer(payload, offer_id="late-1", today=TODAY)
    assert offer["synthetic"] is True
    assert offer["extractor"] == "rules"
    assert offer["fields"]["settlement_currency"]["status"] == "missing"
    assert offer["fields"]["valid_until"]["value"] == "2026-12-31"
    assert offer["items"][0]["pricing"]["evidence"]
    assert offer["status"] == "needs_review"
    assert [r["field"] for r in offer["blocking_reasons"]] == ["settlement_currency"]
    with pytest.raises(api.DemoError) as err:
        api.save_offer(offer, today=TODAY)
    assert err.value.code == "save_blocked"
    corrected = api.correct_offer(offer, field="settlement_currency", value="USD", today=TODAY)
    field = corrected["fields"]["settlement_currency"]
    assert field["status"] == "corrected"
    assert field["source"] == "user" and field["evidence"] == []
    assert field["evidence_status"] == "user_entered_without_document_evidence"
    assert corrected["version"] == 2
    assert api.save_offer(corrected, today=TODAY)["status"] == "saved"


def test_correction_recomputes_normalized_price_and_rejects_invalid_type():
    api = service()
    offer = api.new_offer(api.extract_fixture("late-copper", mode="rules", as_of=TODAY),
                          offer_id="late-2", today=TODAY)
    price = {"type": "fixed", "unit_price": {"amount": "9000", "currency": "USD"}, "per_unit": "tonne"}
    updated = api.correct_offer(offer, field="items[0].pricing", value=price, today=TODAY)
    assert updated["normalized"]["items"][0]["pricing"]["per_kg"] == {"amount": "9", "currency": "USD"}
    assert offer["normalized"]["items"][0]["pricing"]["per_kg"]["amount"] == "9.4125"
    with pytest.raises(api.DemoError) as err:
        api.correct_offer(updated, field="settlement_currency", value="usd", today=TODAY)
    assert err.value.code == "invalid_value"


def test_first_pricing_correction_preserves_original_extracted_field_with_evidence():
    api = service()
    offer = api.new_offer(api.extract_fixture("express-copper", mode="rules", as_of=TODAY),
                          offer_id="price-first", today=TODAY)
    extracted = deepcopy(offer["items"][0]["pricing"])
    assert extracted["value"]["unit_price"] == {"amount": "10.50", "currency": "USD"}
    revised = api.correct_offer(offer, field="items[0].pricing", value={
        "type": "fixed", "unit_price": {"amount": "11.00", "currency": "USD"}, "per_unit": "kg"},
        today=TODAY)
    pricing = revised["items"][0]["pricing"]
    assert pricing["original"] == {key: extracted[key] for key in ("value", "status", "source", "evidence")}
    assert pricing["status"] == "corrected" and pricing["evidence"] == []
    assert pricing["original"]["evidence"] and pricing["original"]["source"] == "rules"
    assert offer["items"][0]["pricing"] == extracted


def test_repeated_pricing_corrections_keep_first_document_snapshot():
    api = service()
    offer = api.new_offer(api.extract_fixture("express-copper", mode="rules", as_of=TODAY),
                          offer_id="price-repeated", today=TODAY)
    original = deepcopy(offer["items"][0]["pricing"])
    first = api.correct_offer(offer, field="items[0].pricing", value={
        "type": "fixed", "unit_price": {"amount": "11.00", "currency": "USD"}, "per_unit": "kg"},
        today=TODAY)
    second = api.correct_offer(first, field="items[0].pricing", value={
        "type": "fixed", "unit_price": {"amount": "12.00", "currency": "USD"}, "per_unit": "kg"},
        today=TODAY)
    pricing = second["items"][0]["pricing"]
    assert pricing["original"] == {key: original[key] for key in ("value", "status", "source", "evidence")}
    assert pricing["previous"]["value"]["unit_price"]["amount"] == "11.00"
    assert pricing["value"]["unit_price"]["amount"] == "12.00"
    assert api.save_offer(second, today=TODAY)["items"][0]["pricing"]["original"] == pricing["original"]


def test_settlement_and_fixed_price_currency_conflict_blocks_save():
    api = service()
    offer = api.new_offer(api.extract_fixture("express-copper", mode="rules", as_of=TODAY),
                          offer_id="express-conflict", today=TODAY)
    changed = api.correct_offer(offer, field="settlement_currency", value="EUR", today=TODAY)
    assert {r["code"] for r in changed["blocking_reasons"]} == {"currency_conflict"}
    with pytest.raises(api.DemoError) as err:
        api.save_offer(changed, today=TODAY)
    assert err.value.code == "save_blocked"


def test_expired_quote_cannot_be_saved_even_after_valid_correction():
    api = service()
    later = date(2027, 1, 1)
    offer = api.new_offer(api.extract_fixture("late-copper", mode="rules", as_of=later),
                          offer_id="expired-1", today=later)
    assert offer["status"] == "expired"
    with pytest.raises(api.DemoError) as err:
        api.correct_offer(offer, field="settlement_currency", value="USD", today=later)
    assert err.value.code == "invalid_transition"


def test_malformed_capacity_period_is_a_typed_correction_error():
    api = service()
    offer = api.new_offer(api.extract_fixture("express-copper", mode="rules", as_of=TODAY),
                          offer_id="bad-capacity", today=TODAY)
    with pytest.raises(api.DemoError) as err:
        api.correct_offer(offer, field="capacity",
                          value={"amount": "60", "unit": "tonne", "period": []}, today=TODAY)
    assert err.value.code == "invalid_value" and err.value.status_code == 422


def test_live_mode_refuses_rules_fallback(monkeypatch):
    api = service()
    monkeypatch.setenv("OFFLINE", "1")
    monkeypatch.setenv("RUN_LIVE_GEMINI", "0")
    monkeypatch.setenv("MAX_LIVE_CALLS", "0")
    with pytest.raises(api.DemoError) as err:
        api.extract_fixture("late-copper", mode="gemini", as_of=TODAY)
    assert err.value.code == "gemini_unavailable"
