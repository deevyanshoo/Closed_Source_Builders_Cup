"""Source geometry required before structured quote values become verified."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from procure.documents import read_document
from procure.offers import extract_rules
from procure.offers.evidence import resolve_field


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
