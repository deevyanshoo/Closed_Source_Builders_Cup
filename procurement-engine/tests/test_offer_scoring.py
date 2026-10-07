"""Scoring an extraction against the synthetic manifest answer key."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from procure.documents import read_document
from procure.offers import scoring
from procure.offers.extraction import extract_offer

FIXTURES = Path(__file__).parent / "fixtures" / "offers"
QUOTES = {q["id"]: q for q in json.loads((FIXTURES / "manifest.json").read_text())["quotes"]}
AS_OF = dt.date(2026, 10, 7)


def rules(quote_id):
    return extract_offer(read_document(FIXTURES / QUOTES[quote_id]["file"]), mode="rules", as_of=AS_OF)


def by_name(score):
    return {f["field"]: f for f in score["fields"]}


def test_rules_extraction_of_a_simple_quote_scores_all_but_settlement_currency():
    # Since PR #24 a price currency is not taken as the settlement (invoice) currency. Q01 never
    # states how it will be invoiced, so the field stays missing while the answer key assumes USD.
    score = scoring.score_quote(rules("Q01"), QUOTES["Q01"])
    assert score["total"] == 15  # 10 offer fields + 5 item fields
    assert [(f["field"], f["error"]) for f in score["fields"] if not f["correct"]] == [
        ("settlement_currency", "not_extracted")]


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


def test_scoring_run_over_the_heldout_set_records_how_it_ran():
    import importlib.util

    path = Path(__file__).parents[1] / "scripts" / "score_offer_extraction.py"
    spec = importlib.util.spec_from_file_location("score_offer_extraction", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = module.run(mode="rules", split="heldout")
    assert report["split"] == "heldout" and report["mode"] == "rules"
    assert report["summary"]["quotes"] == sum(q["split"] == "heldout" for q in QUOTES.values())
    assert report["extractors_used"] == {"rules": report["summary"]["quotes"]}
    assert report["calls"] == {"none": report["summary"]["quotes"]}
    assert report["synthetic_inputs"] is True and report["revision"]
    assert {"missing", "conflict", "expired", "embedded_instruction"} <= set(report["summary"]["flags"])
