"""Source geometry required before structured quote values become verified."""
import json
import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from procure.documents import read_document
from procure.documents.models import Document, make_segment
from procure.offers import extract_rules
from procure.offers.evidence import resolve_field
from procure.offers.extraction import assemble


FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}


def _fixture(quote_id):
    return read_document(FIXTURES / QUOTES[quote_id]["file"])


def _csv(text):
    return read_document(text.encode(), filename="quote.csv")


def _result(doc, kind, value, *locators):
    proposal = {"value": value, "evidence": [{"locator": locator} for locator in locators]}
    return resolve_field(doc, kind, kind, [proposal])


def _quantity(amount, unit="tonne"):
    return {"amount": amount, "unit": unit}


def _price(amount, currency="EUR", unit="kg"):
    return {"type": "fixed", "unit_price": {"amount": amount, "currency": currency}, "per_unit": unit}


def test_explicit_payment_currency_label_binds_its_value_but_payment_acronym_does_not():
    doc = _csv("Payment currency,USD\nPayment,CAD\n")
    assert _result(doc, "currency", "USD", "r1:c2").status == "extracted"
    assert _result(doc, "currency", "CAD", "r2:c2").status == "unverified"


@pytest.mark.parametrize("row,amount", [(9, "3.42"), (10, "3.65"), (11, "4.15")])
def test_shared_xlsx_price_header_binds_each_numeric_row(row, amount):
    doc = _fixture("Q08")
    assert _result(doc, "pricing", _price(amount), f"Offers!D{row}", "Offers!D8").status == "extracted"


@pytest.mark.parametrize("row,amount", [(9, "12000"), (10, "18000"), (11, "8000")])
def test_shared_xlsx_quantity_header_binds_each_numeric_row(row, amount):
    doc = _fixture("Q08")
    assert _result(doc, "quantity", _quantity(amount, "kg"), f"Offers!C{row}", "Offers!C8").status == "extracted"


def test_csv_quantity_label_to_left_supplies_unit_for_numeric_cell():
    doc = _fixture("Q03")
    assert _result(doc, "quantity", _quantity("80000"), "r9:c1", "r9:c2").status == "extracted"
    assert _result(doc, "quantity", _quantity("50000"), "r9:c1", "r10:c2").status == "unverified"


def test_quantity_cannot_borrow_a_price_header_unit():
    doc = _fixture("Q08")
    assert _result(doc, "quantity", _quantity("3.42", "kg"), "Offers!D9", "Offers!D8").status == "unverified"


def test_hidden_intervening_xlsx_row_blocks_shared_header():
    original = _fixture("Q08")
    segments = tuple(replace(s, flags=(*s.flags, "hidden_row")) if s.locator == "Offers!C10" else s
                     for s in original.segments)
    doc = replace(original, segments=segments)
    assert _result(doc, "quantity", _quantity("8000", "kg"), "Offers!C11", "Offers!C8").status == "unverified"


def test_aggregate_requires_two_labeled_same_unit_rows():
    doc = _fixture("Q09")
    refs = ("r2:c7", "r3:c7", "r3:c8", "r4:c7", "r4:c8")
    assert _result(doc, "quantity", _quantity("100"), *refs).status == "extracted"
    assert _result(doc, "quantity", _quantity("100"), *[x for x in refs if x != "r4:c8"]).status == "unverified"


def test_q09_rules_candidate_with_repeated_shared_header_verifies():
    doc = _fixture("Q09")
    candidate = extract_rules.extract(doc)["items"][0]["quantity"]
    result = resolve_field(doc, "items[0].quantity", "quantity", candidate)
    assert result.status == "extracted"
    assert result.value == _quantity("100")


@pytest.mark.parametrize("source,refs", [
    ("qty,qty_unit,price\n50,t,50\n", ("r1:c1", "r1:c2", "r2:c1", "r2:c2", "r2:c3")),
    ("qty,qty_unit\n50,t\n50,kg\n", ("r1:c1", "r1:c2", "r2:c1", "r2:c2", "r3:c1", "r3:c2")),
    ("qty,qty_unit,min_order,min_order_unit\n50,t,50,t\n", ("r1:c1", "r1:c2", "r1:c3", "r1:c4", "r2:c1", "r2:c2", "r2:c3", "r2:c4")),
    ("qty,qty_unit\n50,t\nmin_order,qty_unit\n50,t\n", ("r1:c1", "r1:c2", "r2:c1", "r2:c2", "r3:c1", "r3:c2", "r4:c1", "r4:c2")),
])
def test_aggregate_cannot_borrow_price_other_unit_minimum_or_changed_header(source, refs):
    assert _result(_csv(source), "quantity", _quantity("100"), *refs).status == "unverified"


def test_aggregate_cannot_double_count_an_explicit_total():
    doc = _csv("qty,qty_unit,total,total_unit\n50,t,100,t\n50,t,,\n")
    refs = ("r1:c1", "r1:c2", "r1:c3", "r1:c4", "r2:c1", "r2:c2", "r2:c3", "r2:c4", "r3:c1", "r3:c2")
    assert _result(doc, "quantity", _quantity("100"), *refs).status == "unverified"


def test_aggregate_rejects_adjacent_unit_column_with_different_field_label():
    doc = _csv("qty,min_order_unit\n50,t\n50,t\n")
    refs = ("r1:c1", "r2:c1", "r2:c2", "r3:c1", "r3:c2")
    assert _result(doc, "quantity", _quantity("100"), *refs).status == "unverified"


def test_aggregate_cannot_count_one_source_cell_twice():
    doc = _csv("qty,qty_unit\n50,t\n")
    refs = ("r1:c1", "r2:c1", "r2:c1", "r2:c2")
    assert _result(doc, "quantity", _quantity("100"), *refs).status == "unverified"


@pytest.mark.parametrize("second_row", [
    "Aluminium,Grade A,100,t,4.28,USD",
    "Copper,Grade B,100,t,4.28,USD",
    "Copper,Grade A,100,t,4.29,USD",
])
def test_aggregate_rejects_different_product_grade_or_price(second_row):
    doc = _csv("product,grade,qty,qty_unit,price,price_currency\n"
               "Copper,Grade A,50,t,4.28,USD\n" + second_row + "\n")
    refs = ("r1:c3", "r2:c3", "r2:c4", "r3:c3", "r3:c4")
    assert _result(doc, "quantity", _quantity("150"), *refs).status == "unverified"


def test_aggregate_without_product_and_grade_identity_is_unverified():
    doc = _csv("qty,qty_unit\n50,t\n100,t\n")
    refs = ("r1:c1", "r2:c1", "r2:c2", "r3:c1", "r3:c2")
    assert _result(doc, "quantity", _quantity("150"), *refs).status == "unverified"


def test_assemble_cannot_attach_other_items_aggregate_to_verified_item(monkeypatch):
    from procure.offers import fx_rates
    def no_provider(*args, **kwargs):
        raise AssertionError("provider access forbidden")
    monkeypatch.setattr(fx_rates, "http_get", no_provider)
    monkeypatch.setattr(fx_rates, "get_rate", no_provider)
    doc = _csv("product,grade,qty,qty_unit,price,price_currency,price_unit\n"
               "Aluminium,Grade B,50,t,20,USD,tonne\n"
               "Aluminium,Grade B,100,t,20,USD,tonne\n"
               "Copper,Grade A,5,t,20,USD,tonne\n")
    proposal = lambda value, *locs: {"value": value, "evidence": [{"locator": loc} for loc in locs]}
    props = {"fields": {}, "items": [{
        "description": [proposal("Copper", "r4:c1")],
        "grade": [proposal("Grade A", "r4:c2")],
        "pricing": [proposal({"type": "fixed", "unit_price": {"amount": "20", "currency": "USD"},
                              "per_unit": "tonne"}, "r1:c5", "r4:c5", "r4:c6", "r4:c7")],
        "quantity": [proposal(_quantity("150"), "r1:c3", "r2:c3", "r2:c4", "r3:c3", "r3:c4")],
    }]}
    result = assemble(doc, props, "rules", {}, as_of=dt.date(2026, 10, 7), rate_lookup=no_provider)
    item = result.items[0]
    assert item["description"].status == "extracted"
    assert item["grade"].status == "extracted"
    assert item["pricing"].status == "extracted"
    assert item["quantity"].status == "unverified"
    assert result.normalized["items"][0]["quantity"] is None


def test_assemble_requires_verified_item_identity_for_aggregate(monkeypatch):
    from procure.offers import fx_rates
    def no_provider(*args, **kwargs):
        raise AssertionError("provider access forbidden")
    monkeypatch.setattr(fx_rates, "http_get", no_provider)
    monkeypatch.setattr(fx_rates, "get_rate", no_provider)
    doc = _csv("product,grade,qty,qty_unit,price,price_currency,price_unit\n"
               "Copper,Grade A,50,t,20,USD,tonne\nCopper,Grade A,100,t,20,USD,tonne\n")
    proposal = lambda value, *locs: {"value": value, "evidence": [{"locator": loc} for loc in locs]}
    props = {"fields": {}, "items": [{
        "description": [proposal("Copper", "r9:c1")],
        "grade": [proposal("Grade A", "r2:c2")],
        "pricing": [proposal({"type": "fixed", "unit_price": {"amount": "20", "currency": "USD"},
                              "per_unit": "tonne"}, "r1:c5", "r2:c5", "r2:c6", "r2:c7")],
        "quantity": [proposal(_quantity("150"), "r1:c3", "r2:c3", "r2:c4", "r3:c3", "r3:c4")],
    }]}
    result = assemble(doc, props, "rules", {}, as_of=dt.date(2026, 10, 7), rate_lookup=no_provider)
    assert result.items[0]["description"].status == "unverified"
    assert result.items[0]["grade"].status == "extracted"
    assert result.items[0]["pricing"].status == "extracted"
    assert result.items[0]["quantity"].status == "unverified"


def test_assemble_cannot_attach_another_price_tier_same_product_grade(monkeypatch):
    from procure.offers import fx_rates
    def no_provider(*args, **kwargs):
        raise AssertionError("provider access forbidden")
    monkeypatch.setattr(fx_rates, "http_get", no_provider)
    monkeypatch.setattr(fx_rates, "get_rate", no_provider)
    doc = _csv("product,grade,qty,qty_unit,price,price_currency,price_unit\n"
               "Copper,Grade A,50,t,10,USD,tonne\n"
               "Copper,Grade A,100,t,10,USD,tonne\n"
               "Copper,Grade A,5,t,20,USD,tonne\n")
    proposal = lambda value, *locs: {"value": value, "evidence": [{"locator": loc} for loc in locs]}
    props = {"fields": {}, "items": [{
        "description": [proposal("Copper", "r4:c1")],
        "grade": [proposal("Grade A", "r4:c2")],
        "pricing": [proposal({"type": "fixed", "unit_price": {"amount": "20", "currency": "USD"},
                              "per_unit": "tonne"}, "r1:c5", "r4:c5", "r4:c6", "r4:c7")],
        "quantity": [proposal(_quantity("150"), "r1:c3", "r2:c3", "r2:c4", "r3:c3", "r3:c4")],
    }]}
    result = assemble(doc, props, "rules", {}, as_of=dt.date(2026, 10, 7), rate_lookup=no_provider)
    assert result.items[0]["pricing"].status == "extracted"
    assert result.items[0]["quantity"].status == "unverified"


def test_q09_aggregate_survives_verified_item_context(monkeypatch):
    from procure.offers import fx_rates
    def no_provider(*args, **kwargs):
        raise AssertionError("provider access forbidden")
    monkeypatch.setattr(fx_rates, "http_get", no_provider)
    monkeypatch.setattr(fx_rates, "get_rate", no_provider)
    doc = _fixture("Q09")
    result = assemble(doc, extract_rules.extract(doc), "rules", {}, as_of=dt.date(2026, 10, 7),
                      rate_lookup=no_provider)
    assert result.items[0]["description"].value == "Copper cathode"
    assert result.items[0]["grade"].value == "Grade A"
    assert result.items[0]["quantity"].value == _quantity("100")


def test_aggregate_uses_exact_decimal_sum_above_default_precision():
    doc = _csv("product,grade,qty,qty_unit\n"
               "Copper,Grade A,10000000000000000000000000000,t\n"
               "Copper,Grade A,1,t\n")
    refs = ("r1:c3", "r2:c3", "r2:c4", "r3:c3", "r3:c4")
    assert _result(doc, "quantity", _quantity("10000000000000000000000000001"), *refs).status == "extracted"
    assert _result(doc, "quantity", _quantity("10000000000000000000000000000"), *refs).status == "unverified"


@pytest.mark.parametrize("word", ["from", "after", "following"])
def test_relative_validity_anchor_is_not_a_verified_expiry_even_with_short_quote(word):
    doc = _csv(f"Label,Value\nValidity,30 days {word} 1 October 2026\n")
    proposal = {"value": "2026-10-01", "evidence": [{"locator": "r2:c2", "quote": "1 October 2026"}]}
    result = resolve_field(doc, "valid_until", "date", [proposal])
    assert result.status == "unverified"
    assert result.value is None


def test_separate_explicit_end_claim_in_same_segment_supports_only_end_date():
    doc = _csv('Label,Value\nValidity,"30 days after 1 October 2026; valid until 31 October 2026"\n')
    assert _result(doc, "date", "2026-10-01", "r2:c2").status == "unverified"
    assert _result(doc, "date", "2026-10-31", "r2:c2").status == "extracted"
    direct = _csv("Label,Value\nValid until,31 October 2026\n")
    assert _result(direct, "date", "2026-10-31", "r2:c2").status == "extracted"


def test_large_distinct_verified_quantities_remain_a_conflict():
    document_id = "sha256:" + "a" * 64
    lines = ("Quantity: 10000000000000000000000000000 tonne",
             "Quantity: 10000000000000000000000000001 tonne")
    segments = tuple(make_segment(document_id, "pdf_line", f"p1:l{i}", line, page=1, line=i)
                     for i, line in enumerate(lines, 1))
    doc = Document(document_id, "synthetic.pdf", "application/pdf", 0, segments)
    proposals = [{"value": _quantity(amount), "evidence": [{"locator": f"p1:l{i}"}]}
                 for i, amount in enumerate(("10000000000000000000000000000",
                                             "10000000000000000000000000001"), 1)]
    result = resolve_field(doc, "quantity", "quantity", proposals)
    assert [candidate.verified for candidate in result.candidates] == [True, True]
    assert (result.status, result.value) == ("conflict", None)


def test_canonical_quantity_equates_only_trailing_decimal_zeros():
    doc = _csv("quote,other\n1.00 tonne,x\n1 tonne,x\n")
    proposals = [{"value": _quantity(amount), "evidence": [{"locator": f"r{i}:c1"}]}
                 for i, amount in enumerate(("1.00", "1"), 2)]
    result = resolve_field(doc, "quantity", "quantity", proposals)
    assert result.status == "extracted"
