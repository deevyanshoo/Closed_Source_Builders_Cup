"""Scoring an extraction against the synthetic manifest answer key."""
from __future__ import annotations

import datetime as dt
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from procure.documents import read_document
from procure.offers import scoring
from procure.offers.extraction import extract_offer

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
AS_OF = dt.date(2026, 10, 7)


def rules(quote_id):
    return extract_offer(read_document(FIXTURES / QUOTES[quote_id]["file"]), mode="rules", as_of=AS_OF)


def matching_score_input(quote_id):
    """Answer-key-shaped unit input, separate from real document extraction accuracy."""
    entry = QUOTES[quote_id]
    expected = entry["expected"]
    def field(value):
        return SimpleNamespace(status="extracted" if value["status"] == "present" else value["status"],
                               value=deepcopy(value.get("value")))
    return SimpleNamespace(
        fields={n: field(expected[n]) for n in scoring.OFFER_FIELDS},
        items=[{n: field({"status": "present", "value": item["material"]} if n == "material" else item[n])
                for n in scoring.ITEM_FIELDS} for item in expected["items"]],
        normalized={"origin_country_code": expected["origin_country"]["value"]},
        review_flags=[], extractor="synthetic-unit-input")


def by_name(score):
    return {f["field"]: f for f in score["fields"]}


def test_matching_extraction_scores_every_field_correct():
    score = scoring.score_quote(matching_score_input("Q01"), QUOTES["Q01"])
    assert score["accuracy"] == 1.0
    assert score["correct"] == score["total"] == 15  # 10 offer fields + 5 item fields


def test_wrong_value_is_counted_as_wrong():
    ex = rules("Q01")
    ex.fields["lead_time_days"].value = 46
    f = by_name(scoring.score_quote(ex, QUOTES["Q01"]))["lead_time_days"]
    assert (f["correct"], f["error"]) == (False, "wrong_value")


def test_value_where_the_key_says_missing_is_an_invented_value():
    ex = rules("Q17")
    ex.fields["valid_until"].status, ex.fields["valid_until"].value = "extracted", "2026-12-31"
    f = by_name(scoring.score_quote(ex, QUOTES["Q17"]))["valid_until"]
    assert (f["correct"], f["error"]) == (False, "invented_value")


def test_amounts_compare_as_numbers():
    score = scoring.score_quote(rules("Q02"), QUOTES["Q02"])  # 2615 in the sheet, 2615.00 in the key
    assert by_name(score)["items[0].pricing"]["correct"] is True


def test_conflict_must_be_reported_as_conflict():
    score = scoring.score_quote(rules("Q18"), QUOTES["Q18"])
    assert by_name(score)["items[0].pricing"]["correct"] is True
    assert score["flags"]["conflict"] == {"expected": 1, "flagged": 1}


def test_flag_counts_cover_missing_expired_and_injection():
    flags = scoring.score_quote(rules("Q07"), QUOTES["Q07"])["flags"]
    assert flags["embedded_instruction"] == {"expected": 1, "flagged": 1}
    expired = scoring.score_quote(rules("Q06"), QUOTES["Q06"])["flags"]["expired"]
    assert expired == {"expected": 1, "flagged": 1, "false_positive": 0}


def test_set_summary_reports_per_field_accuracy():
    summary = scoring.summarize([scoring.score_quote(rules(q), QUOTES[q]) for q in ("Q01", "Q17")])
    assert summary["quotes"] == 2
    assert summary["per_field"]["valid_until"] == {"correct": 2, "total": 2, "accuracy": 1.0}
    assert summary["flags"]["missing"]["expected"] >= 2
    assert 0 <= summary["accuracy"] <= 1


def test_synthetic_label_is_not_part_of_the_supplier_name():
    ex = rules("Q01")
    ex.fields["supplier_name"].value = "Atacama Red Metals SpA"
    assert by_name(scoring.score_quote(ex, QUOTES["Q01"]))["supplier_name"]["correct"] is True


@pytest.mark.parametrize("change", [
    {"index": "LME Aluminium official cash settlement"},
    {"averaging_period": "daily cash settlement on the invoice date"},
    {"fx_basis": "ECB reference rate on the invoice date"},
    {"premium": {"amount": "95.00", "currency": "EUR"}},
    {"per_unit": "kg"},
])
def test_q10_rejects_changed_index_linked_commercial_terms(change):
    ex = rules("Q10")
    ex.items[0]["pricing"].value.update(change)
    result = by_name(scoring.score_quote(ex, QUOTES["Q10"]))["items[0].pricing"]
    assert (result["correct"], result["error"]) == (False, "wrong_value")


def test_q10_accepts_complete_equivalent_index_price():
    ex = rules("Q10")
    pricing = ex.items[0]["pricing"].value
    pricing["index"] = "  lme   copper grade a official cash settlement  "
    pricing["averaging_period"] = "Monthly   average of the month following shipment (M+1)"
    pricing["premium"]["amount"] = "95"
    assert by_name(scoring.score_quote(ex, QUOTES["Q10"]))["items[0].pricing"]["correct"] is True


def test_q14_wrong_fx_clause_cannot_hide_in_index_or_averaging_window():
    ex = rules("Q14")
    ex.items[0]["pricing"].value["averaging_period"] += " converted at an unstated FX rate"
    result = by_name(scoring.score_quote(ex, QUOTES["Q14"]))["items[0].pricing"]
    assert (result["correct"], result["error"]) == (False, "wrong_value")


def test_explicit_fx_basis_must_match_when_key_contains_it():
    entry = deepcopy(QUOTES["Q10"])
    entry["expected"]["items"][0]["pricing"]["value"]["fx_basis"] = "ECB reference rate on invoice date"
    ex = rules("Q10")
    ex.items[0]["pricing"].value["fx_basis"] = "ECB reference rate on invoice date"
    assert by_name(scoring.score_quote(ex, entry))["items[0].pricing"]["correct"] is True
    ex.items[0]["pricing"].value["fx_basis"] = "ECB reference rate on shipment date"
    assert by_name(scoring.score_quote(ex, entry))["items[0].pricing"]["correct"] is False


def test_extra_invented_item_fails_whole_quote_while_field_accuracy_keeps_its_denominator():
    ex = matching_score_input("Q01")
    ex.items.append(deepcopy(ex.items[0]))
    score = scoring.score_quote(ex, QUOTES["Q01"])
    assert (score["accuracy"], score["correct"], score["total"], score["extra_items"]) == (1.0, 15, 15, 1)
    assert score["quote_correct"] is False
    summary = scoring.summarize([scoring.score_quote(matching_score_input("Q01"), QUOTES["Q01"]), score])
    assert (summary["quotes_correct"], summary["quotes"], summary["quote_accuracy"]) == (1, 2, 0.5)


def test_missing_item_fails_whole_quote():
    ex = rules("Q01")
    ex.items.clear()
    assert scoring.score_quote(ex, QUOTES["Q01"])["quote_correct"] is False


def test_real_q01_price_currency_cannot_count_as_verified_settlement():
    score = scoring.score_quote(rules("Q01"), QUOTES["Q01"])
    settlement = by_name(score)["settlement_currency"]
    assert (settlement["correct"], settlement["error"]) == (False, "not_extracted")
    assert score["quote_correct"] is False
