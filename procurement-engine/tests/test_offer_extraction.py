"""Offer extraction: rules extractor, Gemini with mocked responses, fallback and evidence checks.

No test calls Google. A fake client stands in for Gemini and every result it produces is
labelled `mocked`, never `real`.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from procure.documents import read_document
from procure.offers.extraction import extract_offer
from procure.offers import extract_gemini
from procure.offers import fx_rates

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text())
QUOTES = {q["id"]: q for q in MANIFEST["quotes"]}
AS_OF = dt.date(2026, 10, 7)


def _deny_provider(*args, **kwargs):
    raise AssertionError("provider access forbidden in extraction tests")


def _no_rate(base, quote, on):
    return fx_rates.FxRate(base, quote, None, None, None, None, "unavailable", "offline test")


@pytest.fixture(autouse=True)
def _stub_fx_provider(monkeypatch):
    monkeypatch.setattr(fx_rates, "http_get", _deny_provider)
    monkeypatch.setattr(fx_rates, "get_rate", _deny_provider)


def doc(quote_id):
    return read_document(FIXTURES / QUOTES[quote_id]["file"])


def rules(quote_id):
    return extract_offer(doc(quote_id), mode="rules", as_of=AS_OF, rate_lookup=_no_rate)


# ---------- rules extractor ----------

@pytest.mark.parametrize("quote_id", sorted(QUOTES))
def test_rules_extractor_runs_on_every_fixture_and_only_keeps_verified_values(quote_id):
    ex = rules(quote_id)
    assert ex.extractor == "rules"
    for f in ex.all_fields():
        if f.status == "extracted":
            assert f.value is not None and f.evidence, f.name
        else:
            assert f.value is None, f.name
    assert ex.synthetic is True


def test_rules_extract_a_simple_pdf_quote():
    ex = rules("Q01")
    assert ex.fields["supplier_name"].value == "Atacama Red Metals SpA [SYNTHETIC]"
    assert ex.fields["valid_until"].value == "2026-10-31"
    assert ex.fields["incoterm"].value == {"code": "CIF", "place": "Nhava Sheva, India"}
    assert ex.fields["lead_time_days"].value == 45
    item = ex.items[0]
    assert item["pricing"].value == {"type": "fixed", "unit_price": {"amount": "9412.50", "currency": "USD"},
                                     "per_unit": "tonne"}
    assert item["pricing"].evidence[0].locator == "p1:l10"
    assert item["material"].value == "copper"
    assert ex.fields["origin_country"].value == "Chile"
    assert ex.normalized["origin_country_code"] == "CL"


def test_rules_read_decimal_comma_csv():
    item = rules("Q11").items[0]
    assert item["pricing"].value["unit_price"] == {"amount": "2598.00", "currency": "USD"}
    assert item["pricing"].value["per_unit"] == "tonne"


def test_rules_find_every_line_item():
    ex = rules("Q08")
    assert [i["description"].value for i in ex.items] == ["Aluminium sheet", "Aluminium coil",
                                                         "Aluminium extrusion profile"]
    assert [i["pricing"].value["unit_price"]["amount"] for i in ex.items] == ["3.42", "3.65", "4.15"]


def test_rules_keep_index_linked_price_as_index_plus_premium():
    pricing = rules("Q10").items[0]["pricing"]
    assert pricing.status == "extracted"
    assert pricing.value["type"] == "index_linked"
    assert pricing.value["premium"] == {"amount": "95.00", "currency": "USD"}
    assert "unit_price" not in pricing.value


def test_rules_report_two_prices_as_conflict():
    pricing = rules("Q18").items[0]["pricing"]
    assert (pricing.status, pricing.value) == ("conflict", None)
    assert "price_conflict" in {f["flag"] for f in rules("Q18").review_flags}


def test_rules_do_not_invent_a_missing_validity_date():
    ex = rules("Q17")
    assert (ex.fields["valid_until"].status, ex.fields["valid_until"].value) == ("missing", None)


def test_hidden_sheet_price_is_never_used():
    ex = rules("Q12")
    assert ex.items[0]["pricing"].value["unit_price"]["amount"] == "128.5"
    assert all("121" not in json.dumps(c.value) for c in ex.items[0]["pricing"].candidates)
    assert "hidden_content" in {f["flag"] for f in ex.review_flags}


def test_expired_quote_is_flagged():
    ex = rules("Q06")
    assert ex.normalized["expiry"] == "expired"
    assert "expired" in {f["flag"] for f in ex.review_flags}


def test_normalised_values_use_decimal_strings():
    ex = rules("Q09")
    norm = ex.normalized["items"][0]
    assert norm["pricing"]["per_kg"] == {"amount": "9.4357848215", "currency": "USD"}
    assert norm["quantity"]["kg"] == "100000"


# ---------- Gemini (mocked) ----------

class FakeModels:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.calls = payload, error, []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error:
            raise self.error
        return type("Resp", (), {"text": json.dumps(self.payload), "usage_metadata": None})()


class FakeClient:
    def __init__(self, payload=None, error=None):
        self.models = FakeModels(payload, error)


def ev(locator, quote):
    return [{"locator": locator, "quote": quote}]


def q01_payload(**override):
    payload = {
        "supplier_name": [{"value": "Atacama Red Metals SpA [SYNTHETIC]", "evidence": ev("p1:l2", "Atacama Red Metals SpA")}],
        "origin_country": [{"value": "Chile", "evidence": ev("p1:l8", "Chile")}],
        "incoterm": [{"code": "CIF", "place": "Nhava Sheva, India", "evidence": ev("p1:l11", "CIF Nhava Sheva, India")}],
        "settlement_currency": [{"value": "USD", "evidence": ev("p1:l10", "USD 9,412.50")}],
        "payment_terms": [{"value": "30% advance by TT, 70% against copy of bill of lading",
                           "evidence": ev("p1:l15", "30% advance by TT, 70% against copy of bill of lading")}],
        "valid_until": [{"value": "2026-10-31", "evidence": ev("p1:l16", "31 October 2026")}],
        "lead_time_days": [{"value": 45, "evidence": ev("p1:l14", "45 days")}],
        "min_order": [{"amount": "25", "unit": "tonne", "evidence": ev("p1:l12", "25 MT")}],
        "capacity": [{"amount": "500", "unit": "tonne", "period": "month", "evidence": ev("p1:l13", "500 MT per month")}],
        "shipments": [],
        "items": [{
            "description": [{"value": "Copper cathode", "evidence": ev("p1:l7", "Copper cathode")}],
            "grade": [{"value": "Grade A, min 99.99% Cu", "evidence": ev("p1:l7", "Grade A, min 99.99% Cu")}],
            "quantity": [{"amount": "250", "unit": "tonne", "evidence": ev("p1:l9", "250 metric tonnes")}],
            "pricing": [{"type": "fixed", "amount": "9412.50", "currency": "USD", "per_unit": "tonne",
                         "index": None, "averaging_period": None,
                         "evidence": ev("p1:l10", "USD 9,412.50 per metric tonne")}],
        }],
    }
    payload.update(override)
    return payload


@pytest.fixture
def with_key(monkeypatch):
    from procure.settings import reset_caches

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OFFLINE", "0")
    reset_caches()
    yield
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OFFLINE", "1")
    reset_caches()


def test_mocked_gemini_result_is_labelled_mocked_and_evidence_checked(with_key):
    client = FakeClient(q01_payload())
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF, client=client, rate_lookup=_no_rate)
    assert ex.extractor == "gemini"
    assert ex.extractor_detail["call"] == "mocked"
    assert ex.items[0]["pricing"].value["unit_price"]["amount"] == "9412.50"
    assert ex.items[0]["pricing"].source == "gemini"
    assert ex.fields["valid_until"].status == "extracted"


def test_gemini_receives_located_segments_not_the_raw_file(with_key):
    client = FakeClient(q01_payload())
    extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF, client=client, rate_lookup=_no_rate)
    prompt = client.models.calls[0]["contents"]
    assert "[p1:l10] Unit price: USD 9,412.50 per metric tonne" in prompt
    assert "%PDF" not in prompt
    config = client.models.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert "untrusted" in config.system_instruction.lower()


def test_gemini_hallucination_is_unverified(with_key):
    payload = q01_payload()
    payload["items"][0]["pricing"][0].update({"amount": "8999.00"})
    payload["valid_until"] = [{"value": "2026-12-31", "evidence": ev("p1:l16", "31 December 2026")}]
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF, client=FakeClient(payload), rate_lookup=_no_rate)
    assert ex.items[0]["pricing"].status == "unverified"
    assert ex.fields["valid_until"].status == "unverified"
    assert ex.fields["valid_until"].value is None


def test_gemini_failure_falls_back_to_rules(with_key):
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF,
                       client=FakeClient(error=RuntimeError("quota")), rate_lookup=_no_rate)
    assert ex.extractor == "rules"
    assert ex.extractor_detail["requested"] == "gemini"
    assert ex.extractor_detail["fallback_reason"] == "call_failed"
    assert ex.extractor_detail["call"] == "mocked"
    assert ex.extractor_detail["attempted"] is True


def test_gemini_invalid_json_falls_back_to_rules(with_key):
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF,
                       client=FakeClient({"supplier_name": "not a list"}), rate_lookup=_no_rate)
    assert ex.extractor == "rules"
    assert ex.extractor_detail["fallback_reason"] == "invalid_response"
    assert ex.extractor_detail["call"] == "mocked"
    assert ex.extractor_detail["stage"] == "response_validation"


def test_no_key_uses_rules(monkeypatch):
    from procure.settings import reset_caches

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OFFLINE", "1")
    reset_caches()
    ex = extract_offer(doc("Q01"), mode="auto", as_of=AS_OF, rate_lookup=_no_rate)
    assert ex.extractor == "rules"
    assert ex.extractor_detail["fallback_reason"] == "no_api_key"
    reset_caches()


def test_offline_blocks_gemini_even_with_a_key(monkeypatch):
    from procure.settings import reset_caches

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("OFFLINE", "1")
    reset_caches()
    try:
        client = FakeClient(q01_payload())
        ex = extract_offer(doc("Q01"), mode="auto", as_of=AS_OF, client=client, rate_lookup=_no_rate)
        assert ex.extractor == "rules" and ex.extractor_detail["fallback_reason"] == "offline"
        assert client.models.calls == []
    finally:
        monkeypatch.delenv("GEMINI_API_KEY")
        reset_caches()


def test_material_comes_from_the_catalog_not_the_model(with_key):
    payload = q01_payload()
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF, client=FakeClient(payload), rate_lookup=_no_rate)
    assert ex.items[0]["material"].value == "copper"
    assert ex.items[0]["material"].evidence[0].locator == "p1:l7"


def test_extraction_serialises_to_json():
    data = rules("Q02").to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["document"]["document_id"].startswith("sha256:")
    assert data["fields"]["supplier_name"]["status"] == "extracted"


def test_gemini_schema_has_no_free_form_action_fields():
    names = set(extract_gemini.GeminiOffer.model_fields)
    assert names == {"supplier_name", "origin_country", "incoterm", "settlement_currency", "payment_terms",
                     "valid_until", "lead_time_days", "min_order", "capacity", "shipments", "items"}


def test_separate_laycan_cell_keeps_candidate_without_verifying_a_quantity_pair():
    ex = rules("Q03")
    shipments = ex.fields["shipments"]
    assert (shipments.status, shipments.value) == ("unverified", None)
    assert shipments.candidates[0].value == [
        {"quantity": {"amount": "80000", "unit": "tonne"}, "window": "2026-11-15/2026-11-25"}
    ]
    assert {e.locator for e in shipments.candidates[0].evidence} == {"r9:c1", "r9:c2", "r12:c2"}
    assert ex.items[0]["pricing"].status == "missing"  # separate settlement code is not a price currency


class QuotaError(Exception):
    code = 429


def test_quota_failure_is_recorded_without_exception_text(with_key):
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF,
                       client=FakeClient(error=QuotaError("429 RESOURCE_EXHAUSTED secret-ish detail")))
    assert ex.extractor == "rules"
    assert ex.extractor_detail["fallback_reason"] == "call_failed"
    assert ex.extractor_detail["error_kind"] == "quota_exhausted"
    assert "secret-ish" not in json.dumps(ex.extractor_detail)


def test_other_provider_failures_are_recorded_as_provider_error(with_key):
    ex = extract_offer(doc("Q01"), mode="gemini", as_of=AS_OF, client=FakeClient(error=RuntimeError("boom")))
    assert ex.extractor_detail["error_kind"] == "provider_error"
