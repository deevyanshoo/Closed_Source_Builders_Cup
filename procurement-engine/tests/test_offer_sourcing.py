"""Sourcing candidates read model: accepted, unexpired offers become per-item sourcing candidates.

Uses the isolated test database from conftest and the synthetic quote fixtures (rules extractor), plus hand-written
synthetic offer views. Read-only: nothing changes an offer's status or version, and nothing calls a network service.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path

import pytest

from procure.offers import store
from procure.offers.context import OfferContext
from procure.offers.sourcing import CONTRACT, incoterm_cost_split, sourcing_candidates

CONTEXT = OfferContext("synthetic-reviewer", "synthetic-workspace", "editor")
SYSTEM_CONTEXT = OfferContext("system", "synthetic-workspace", "system")

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
TODAY = dt.date(2026, 10, 7)
_keys = iter(range(10_000))


def key() -> str:
    return f"sourcing-key-{next(_keys):05d}"


def fresh(quote_id):
    path = FIXTURES / QUOTES[quote_id]["file"]
    return store.create_from_upload(path.read_bytes(), path.name, mode="rules", as_of=TODAY,
                                    allow_duplicate=True, context=CONTEXT)["offer"]


def saved_q01():
    offer = fresh("Q01")
    offer = store.correct_field(offer["offer_id"], field="settlement_currency", value="USD", evidence=None,
                                expected_version=offer["version"],
                                idempotency_key=key(), today=TODAY, context=CONTEXT)
    return store.save_offer(offer["offer_id"], expected_version=offer["version"],
                            idempotency_key=key(), today=TODAY, context=CONTEXT)


def test_saved_offer_becomes_a_candidate_with_price_basis_validity_terms_and_evidence():
    offer = saved_q01()
    result = sourcing_candidates([store.get_offer(offer["offer_id"], today=TODAY, context=CONTEXT)], as_of=TODAY)
    assert (result["contract"], result["as_of"], result["synthetic"]) == (CONTRACT, "2026-10-07", True)
    [c] = result["candidates"]
    assert c["candidate_id"] == f"{offer['offer_id']}@v{offer['version']}#item0"
    assert (c["offer_status"], c["item_index"], c["synthetic"]) == ("saved", 0, True)
    assert c["supplier"] == {"name": "Atacama Red Metals SpA [SYNTHETIC]", "origin_country": "Chile",
                             "origin_country_code": "CL"}
    assert c["material"] == "copper"
    assert c["quantity"] == {"amount": "250", "unit": "tonne", "kg": "250000"}
    assert c["incoterm"] == {"code": "CIF", "place": "Nhava Sheva, India"}
    assert c["settlement_currency"] == "USD"
    landed = c["landed_price"]
    assert (landed["status"], landed["basis"]) == ("incoterm_price_only", "quoted_price_on_incoterm")
    assert landed["per_kg"] == {"amount": "9.4125", "currency": "USD"}
    assert landed["reporting"] == {"currency": "USD", "status": "not_needed", "per_kg": {"amount": "9.4125",
                                                                                         "currency": "USD"},
                                   "fx": None}
    assert "main_freight" in landed["includes"] and "insurance" in landed["includes"]
    assert "import_duty_and_taxes" in landed["not_included"]
    assert c["validity"]["state"] == "valid" and c["validity"]["valid_until"] == "2026-10-31"
    assert c["validity"]["days_left"] == 24
    assert c["payment_terms"]["status"] == "parsed"
    assert [i["percent"] for i in c["payment_terms"]["instalments"]] == ["30", "70"]
    locators = {(e["field"], e["locator"]) for e in c["evidence"]}
    assert {("items[0].pricing", "p1:l10"), ("valid_until", "p1:l16"), ("payment_terms", "p1:l15")} <= locators
    assert all(e["document_id"] == offer["document"]["document_id"] and len(e["text_sha256"]) == 64
               for e in c["evidence"])
    assert c["field_sources"]["settlement_currency"] == {"status": "corrected", "source": "user"}


def test_offers_that_are_not_accepted_or_have_expired_are_excluded_with_reasons():
    unsaved = store.get_offer(fresh("Q02")["offer_id"], today=TODAY, context=CONTEXT)
    saved = saved_q01()
    late = dt.date(2026, 11, 1)
    result = sourcing_candidates([unsaved, store.get_offer(saved["offer_id"], today=late, context=CONTEXT)], as_of=late)
    assert result["candidates"] == []
    assert [(e["offer_id"], e["reason"]) for e in result["excluded"]] == [
        (unsaved["offer_id"], "not_accepted"), (saved["offer_id"], "expired")]
    assert result["excluded"][0]["detail"] == "status needs_review"


def test_reading_candidates_changes_nothing():
    offer = saved_q01()
    before = store.get_offer(offer["offer_id"], today=TODAY, context=CONTEXT)
    snapshot = copy.deepcopy(before)
    sourcing_candidates([before], as_of=TODAY)
    store.sourcing_candidates(today=TODAY, context=CONTEXT)
    after = store.get_offer(offer["offer_id"], today=TODAY, context=CONTEXT)
    assert before == snapshot
    assert (after["status"], after["version"]) == ("saved", offer["version"])


def test_store_lists_candidates_for_every_saved_offer():
    offer = saved_q01()
    result = store.sourcing_candidates(today=TODAY, context=CONTEXT)
    assert offer["offer_id"] in {c["offer_id"] for c in result["candidates"]}
    assert all(c["offer_status"] in ("saved", "used_in_plan") for c in result["candidates"])


# Hand-written synthetic views for paths the fixtures cannot reach offline


def view(**over):
    field = lambda name, value, kind="text": {"name": name, "kind": kind, "status": "extracted", "value": value,
                                              "source": "rules",
                                              "evidence": [{"locator": f"r1:{name}", "quote": str(value),
                                                            "text_sha256": "a" * 64}]}
    base = {
        "offer_id": "syn-1", "status": "saved", "version": 3, "synthetic": True,
        "document": {"document_id": "sha256:" + "b" * 64},
        "fields": {"supplier_name": field("supplier_name", "Synthetic Smelter [SYNTHETIC]"),
                   "origin_country": field("origin_country", "Germany"),
                   "incoterm": field("incoterm", {"code": "FOB", "place": "Hamburg"}, "incoterm"),
                   "settlement_currency": field("settlement_currency", "EUR", "currency"),
                   "payment_terms": field("payment_terms", "Net 45 days"),
                   "valid_until": field("valid_until", "2026-10-31", "date"),
                   "lead_time_days": field("lead_time_days", 30, "integer"),
                   "min_order": {"status": "missing", "value": None, "evidence": []},
                   "capacity": {"status": "missing", "value": None, "evidence": []}},
        "items": [{"material": field("material", "aluminium"), "description": field("description", "P1020"),
                   "grade": {"status": "missing", "value": None, "evidence": []},
                   "quantity": field("quantity", {"amount": "40", "unit": "tonne"}, "quantity"),
                   "pricing": field("pricing", {"type": "fixed", "unit_price": {"amount": "2400", "currency": "EUR"},
                                                "per_unit": "tonne"}, "pricing")}],
        "normalized": {"origin_country_code": "DE", "min_order": None, "capacity": None, "items": [{
            "quantity": {"original": {"amount": "40", "unit": "tonne"}, "kg": "40000", "status": "ok"},
            "pricing": {"type": "fixed", "status": "ok", "per_kg": {"amount": "2.4", "currency": "EUR"},
                        "original": {"type": "fixed", "unit_price": {"amount": "2400", "currency": "EUR"},
                                     "per_unit": "tonne"},
                        "reporting": {"currency": "USD", "status": "ok",
                                      "per_kg": {"amount": "2.7936", "currency": "USD"},
                                      "fx": {"base": "EUR", "quote": "USD", "rate": "1.164", "rate_date": "2026-10-06",
                                             "source": "frankfurter", "retrieved_at": "2026-10-07T08:00:00Z",
                                             "status": "ok"}}}}]},
    }
    for path, value in over.items():
        target = base
        *parents, last = path.split("__")
        for p in parents:
            target = target[int(p)] if isinstance(target, list) else target[p]
        target[last] = value
    return base


def test_reporting_price_keeps_its_fx_rate_and_date():
    [c] = sourcing_candidates([view()], as_of=TODAY)["candidates"]
    assert c["landed_price"]["reporting"] == {
        "currency": "USD", "status": "ok", "per_kg": {"amount": "2.7936", "currency": "USD"},
        "fx": {"base": "EUR", "quote": "USD", "rate": "1.164", "rate_date": "2026-10-06", "source": "frankfurter"}}
    assert "main_freight" in c["landed_price"]["not_included"]
    assert c["payment_terms"]["instalments"][0]["anchor"] == "not_stated"


def test_unavailable_fx_is_stated_not_filled():
    reporting = {"currency": "USD", "status": "unavailable", "per_kg": None, "fx": None}
    [c] = sourcing_candidates([view(normalized__items__0__pricing__reporting=reporting)], as_of=TODAY)["candidates"]
    assert c["landed_price"]["reporting"] == {"currency": "USD", "status": "unavailable", "per_kg": None, "fx": None}


def test_index_linked_price_has_no_landed_figure():
    pricing = {"type": "index_linked", "status": "ok", "index": "LME aluminium cash", "averaging_period": "M+1",
               "premium_per_kg": {"amount": "0.18", "currency": "USD"},
               "original": {"type": "index_linked"},
               "reporting": {"currency": "USD", "status": "not_needed",
                             "premium_per_kg": {"amount": "0.18", "currency": "USD"}, "fx": None}}
    [c] = sourcing_candidates([view(normalized__items__0__pricing=pricing)], as_of=TODAY)["candidates"]
    assert (c["landed_price"]["status"], c["landed_price"]["per_kg"]) == ("index_linked_final_price_unknown", None)
    assert c["price"] == {"type": "index_linked", "index": "LME aluminium cash", "averaging_period": "M+1",
                          "premium_per_kg": {"amount": "0.18", "currency": "USD"}}


def test_incomplete_items_are_excluded_per_item():
    result = sourcing_candidates([view(normalized__items__0__pricing=None)], as_of=TODAY)
    assert result["candidates"] == []
    assert result["excluded"] == [{"offer_id": "syn-1", "offer_version": 3, "item_index": 0,
                                   "reason": "item_incomplete", "detail": "price not normalised"}]


def test_rejected_and_unstated_validity_are_excluded():
    missing = {"status": "missing", "value": None, "evidence": []}
    result = sourcing_candidates([view(status="rejected"), view(offer_id="syn-2", fields__valid_until=missing)],
                                 as_of=TODAY)
    assert [(e["offer_id"], e["reason"]) for e in result["excluded"]] == [("syn-1", "not_accepted"),
                                                                         ("syn-2", "validity_not_stated")]


def test_candidates_are_ordered_by_material_then_offer():
    offers = [view(offer_id="syn-c"), view(offer_id="syn-a", items__0__material__value="copper"),
              view(offer_id="syn-b")]
    assert [c["offer_id"] for c in sourcing_candidates(offers, as_of=TODAY)["candidates"]] == ["syn-b", "syn-c",
                                                                                              "syn-a"]


@pytest.mark.parametrize("code,included,not_included", [
    ("EXW", set(), {"export_clearance", "main_freight", "insurance"}),
    ("FOB", {"export_clearance"}, {"main_freight", "insurance"}),
    ("CFR", {"main_freight"}, {"insurance", "import_duty_and_taxes"}),
    ("CIP", {"main_freight", "insurance"}, {"import_duty_and_taxes"}),
    ("DAP", {"main_freight", "delivery_to_named_place"}, {"import_duty_and_taxes"}),
    ("DDP", {"import_duty_and_taxes"}, {"inland_freight_beyond_named_place"}),
])
def test_incoterm_split_follows_incoterms_2020(code, included, not_included):
    includes, excludes = incoterm_cost_split(code)
    assert included <= set(includes) and not_included <= set(excludes)
    assert not set(includes) & set(excludes)


def test_unknown_incoterm_is_not_split():
    assert incoterm_cost_split(None) == ([], ["unknown_incoterm"])
