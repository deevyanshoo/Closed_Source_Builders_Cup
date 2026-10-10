"""Payment-term normalisation: extracted payment text becomes instalments with evidence, or stays not stated.

Wordings come from the training quotes and hand-written strings only; held-out quotes are not used to shape the
parser. Nothing here calls a network service.
"""
from __future__ import annotations

import pytest

from procure.offers.terms import parse_payment_terms


def field(text, status="extracted", locator="p1:l15", source="rules"):
    return {"name": "payment_terms", "kind": "text", "status": status, "value": text if status != "missing" else None,
            "source": source,
            "evidence": [{"locator": locator, "quote": text}] if status == "extracted" and text else []}


def shape(result):
    return [(i["percent"], i["percent_basis"], i["instrument"], i["anchor"], i["days"], i["timing"])
            for i in result["instalments"]]


@pytest.mark.parametrize("text,expected", [
    ("30% advance by TT, 70% against copy of bill of lading",
     [("30", "stated", "telegraphic_transfer", "order", 0, "on"),
      ("70", "stated", "not_stated", "bill_of_lading", 0, "on")]),
    ("Irrevocable LC at sight", [("100", "single_term", "letter_of_credit", "documents", 0, "on")]),
    ("LC 90 days from bill of lading date", [("100", "single_term", "letter_of_credit", "bill_of_lading", 90, "on")]),
    ("Net 60 days from invoice date", [("100", "single_term", "open_account", "invoice", 60, "on")]),
    ("50% TT advance, 50% before shipment",
     [("50", "stated", "telegraphic_transfer", "order", 0, "on"),
      ("50", "stated", "not_stated", "shipment", 0, "on_or_before")]),
    ("Cash against documents (CAD)", [("100", "single_term", "cash_against_documents", "documents", 0, "on")]),
    ("LC 60 days from BL", [("100", "single_term", "letter_of_credit", "bill_of_lading", 60, "on")]),
    ("LC at sight, 100%", [("100", "stated", "letter_of_credit", "documents", 0, "on")]),
    ("100% TT before shipment", [("100", "stated", "telegraphic_transfer", "shipment", 0, "on_or_before")]),
])
def test_training_wordings_become_instalments(text, expected):
    result = parse_payment_terms(field(text))
    assert result["status"] == "parsed", result
    assert shape(result) == expected
    assert result["text"] == text
    assert result["evidence"] == [{"locator": "p1:l15", "quote": text}]
    assert result["reasons"] == []


def test_unrecognised_remainder_is_kept_and_never_given_a_share():
    text = "provisional invoice 90% against bill of lading, final invoice after quotational period"
    result = parse_payment_terms(field(text))
    assert result["status"] == "partial"
    assert shape(result) == [("90", "stated", "not_stated", "bill_of_lading", 0, "on")]
    assert result["unrecognised"] == ["final invoice after quotational period"]
    assert result["reasons"] == ["unrecognised_clause", "percentages_sum_to_90"]


@pytest.mark.parametrize("text", ["Net 45 days", "LC 30 days"])
def test_days_without_a_stated_event_keep_the_event_not_stated(text):
    result = parse_payment_terms(field(text))
    assert result["status"] == "parsed"
    [only] = result["instalments"]
    assert (only["anchor"], only["days"]) == ("not_stated", int(text.split()[1]))


@pytest.mark.parametrize("text", ["30 days before shipment by TT", "30 days prior to loading by TT"])
def test_nonzero_before_shipment_keeps_a_before_event_deadline(text):
    [term] = parse_payment_terms(field(text))["instalments"]
    assert (term["anchor"], term["days"], term["timing"]) == ("shipment", 30, "on_or_before")


def test_explicit_after_shipment_event_outweighs_cad_instrument_default():
    [term] = parse_payment_terms(field("CAD 30 days after shipment"))["instalments"]
    assert (term["instrument"], term["anchor"], term["days"]) == (
        "cash_against_documents", "shipment", 30)


def test_explicit_invoice_event_outweighs_sight_instrument_default():
    [term] = parse_payment_terms(field("LC at sight 30 days from invoice"))["instalments"]
    assert (term["instrument"], term["anchor"], term["days"]) == (
        "letter_of_credit", "invoice", 30)


def test_missing_field_is_not_stated_and_nothing_is_assumed():
    result = parse_payment_terms(field(None, status="missing"))
    assert result == {"status": "not_stated", "text": None, "instalments": [], "unrecognised": [], "evidence": [],
                      "reasons": ["payment_terms_missing"], "source": "rules"}


@pytest.mark.parametrize("status", ["conflict", "unverified"])
def test_unresolved_field_is_unavailable(status):
    result = parse_payment_terms({**field("Net 60 days"), "status": status, "value": None, "evidence": []})
    assert (result["status"], result["instalments"], result["reasons"]) == ("unavailable", [], [f"field_{status}"])


@pytest.mark.parametrize("text,status,reason", [
    ("Payment on mutually agreed terms", "unrecognised", "no_recognised_terms"),
    ("60% advance by TT, 60% before shipment", "inconsistent", "percentages_exceed_100"),
    ("40% advance by TT", "partial", "percentages_sum_to_40"),
])
def test_unclear_or_inconsistent_terms_are_marked(text, status, reason):
    result = parse_payment_terms(field(text))
    assert result["status"] == status
    assert reason in result["reasons"]


def test_reviewer_entry_has_no_document_evidence():
    result = parse_payment_terms({**field("Net 30 days from invoice date"), "status": "corrected", "source": "user",
                                  "evidence": []})
    assert result["status"] == "parsed"
    assert (result["source"], result["evidence"]) == ("user", [])


def test_percentages_keep_exact_decimal_text():
    result = parse_payment_terms(field("12.5% advance by TT, 87.5% against bill of lading"))
    assert [i["percent"] for i in result["instalments"]] == ["12.5", "87.5"]
    assert result["status"] == "parsed"


# Wiring: extraction and service corrections carry the parsed terms in `normalized`


def test_extraction_normalises_payment_terms_with_their_locator():
    import datetime as dt
    import json
    from pathlib import Path

    from procure.documents import read_document
    from procure.offers.extraction import extract_offer

    fixtures = Path(__file__).parent / "fixtures" / "offers"
    q01 = next(q for q in json.loads((fixtures / "manifest.json").read_text())["quotes"] if q["id"] == "Q01")
    result = extract_offer(read_document(fixtures / q01["file"]), mode="rules", as_of=dt.date(2026, 10, 7))
    terms = result.normalized["payment_terms"]
    assert terms["status"] == "parsed"
    assert [e["locator"] for e in terms["evidence"]] == ["p1:l15"]
    assert [i["percent"] for i in terms["instalments"]] == ["30", "70"]


def test_service_correction_reparses_payment_terms_as_a_reviewer_entry():
    import datetime as dt

    from procure.offers import lifecycle

    payload = {"fields": {name: {"status": "missing", "value": None, "evidence": []}
                          for name in ("origin_country", "valid_until", "min_order", "capacity")},
               "items": []}
    payload["fields"]["payment_terms"] = {"status": "corrected", "value": "Net 60 days from invoice date",
                                          "source": "user", "evidence": []}
    terms = lifecycle.compute_normalized(payload, dt.date(2026, 10, 7))["payment_terms"]
    assert (terms["status"], terms["source"], terms["instalments"][0]["anchor"]) == ("parsed", "user", "invoice")
