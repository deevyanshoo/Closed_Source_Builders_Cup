"""Expiry and payment-timing scenarios for offers: validity at a decision date, the comparison set, the payment
schedule and the cash-timing effect of different terms. Decimal text throughout; the rate is a user input.
Offers are hand-written synthetic dicts in the stored offer shape. Nothing calls a network service.
"""
from __future__ import annotations

import datetime as dt

import pytest

from procure.offers.scenario import (cash_timing, compare_terms, comparison_set, expiry_scenario, offer_amount,
                                     offer_scenario, validity_at)
from procure.offers.terms import parse_payment_terms

AS_OF = dt.date(2026, 10, 7)


def text_field(name, value, status="extracted", locator="p1:l16"):
    return {"name": name, "kind": "date" if name == "valid_until" else "text", "status": status, "value": value,
            "source": "rules", "evidence": [{"locator": locator, "quote": str(value)}] if status == "extracted" else []}


def offer(offer_id, valid_until="2026-10-31", payment="30% advance by TT, 70% against copy of bill of lading",
          status="needs_review", items=None, valid_status=None):
    vs = valid_status or ("extracted" if valid_until else "missing")
    return {"offer_id": offer_id, "status": status, "synthetic": True,
            "fields": {"valid_until": text_field("valid_until", valid_until, vs),
                       "payment_terms": text_field("payment_terms", payment, "extracted" if payment else "missing",
                                                   "p1:l15")},
            "normalized": {"items": items if items is not None else [fixed("250", "tonne", "9412.50", "USD", "tonne")]}}


def fixed(qty, unit, price, currency, per_unit):
    return {"quantity": {"original": {"amount": qty, "unit": unit}, "status": "ok"},
            "pricing": {"type": "fixed", "status": "ok",
                        "original": {"type": "fixed", "unit_price": {"amount": price, "currency": currency},
                                     "per_unit": per_unit}}}


# Validity


@pytest.mark.parametrize("valid_until,on,state,days_left", [
    ("2026-10-31", "2026-10-07", "valid", 24),
    ("2026-10-31", "2026-10-31", "valid", 0),
    ("2026-10-31", "2026-11-01", "expired", None),
])
def test_validity_is_inclusive_of_the_last_day(valid_until, on, state, days_left):
    result = validity_at(text_field("valid_until", valid_until), dt.date.fromisoformat(on))
    assert (result["state"], result["valid_until"], result["days_left"]) == (state, valid_until, days_left)


def test_missing_validity_is_not_stated_and_never_assumed_open():
    result = validity_at(text_field("valid_until", None, "missing"), AS_OF)
    assert result == {"state": "not_stated", "valid_until": None, "days_left": None, "evidence": []}


def test_unresolved_validity_is_unavailable():
    assert validity_at(text_field("valid_until", None, "conflict"), AS_OF)["state"] == "unavailable"


def test_validity_keeps_its_evidence():
    assert validity_at(text_field("valid_until", "2026-10-31"), AS_OF)["evidence"] == [
        {"locator": "p1:l16", "quote": "2026-10-31"}]


# Comparison set and the expiry scenario


def test_expired_and_unstated_offers_stay_visible_but_leave_the_comparison():
    offers = [offer("a"), offer("b", valid_until="2026-10-01"), offer("c", valid_until=None),
              offer("d", status="rejected")]
    result = comparison_set(offers, AS_OF)
    assert result["date"] == "2026-10-07"
    assert result["included"] == ["a"]
    assert result["excluded"] == [
        {"offer_id": "b", "reason": "expired", "valid_until": "2026-10-01"},
        {"offer_id": "c", "reason": "validity_not_stated", "valid_until": None},
        {"offer_id": "d", "reason": "rejected", "valid_until": "2026-10-31"},
    ]
    assert [o["offer_id"] for o in offers] == ["a", "b", "c", "d"]


def test_moving_the_decision_date_past_expiry_lists_what_drops_out():
    offers = [offer("a", valid_until="2026-10-18"), offer("b", valid_until="2026-11-15")]
    result = expiry_scenario(offers, as_of=AS_OF, decision_date=dt.date(2026, 10, 20))
    assert result["as_of"]["included"] == ["a", "b"]
    assert result["decision"]["included"] == ["b"]
    assert result["newly_expired"] == [{"offer_id": "a", "valid_until": "2026-10-18"}]


# Amount


def test_amount_is_quantity_times_price_in_the_quote_currency():
    assert offer_amount(offer("a")) == {"amount": "2353125", "currency": "USD", "reason": None}


def test_amount_converts_units_only_when_exact():
    tonne_per_kg = offer("a", items=[fixed("40", "tonne", "9.15", "EUR", "kg")])
    assert offer_amount(tonne_per_kg) == {"amount": "366000", "currency": "EUR", "reason": None}
    per_lb = offer("b", items=[fixed("100", "tonne", "4.28", "USD", "lb")])
    assert offer_amount(per_lb) == {"amount": None, "currency": None, "reason": "unit_conversion_not_exact"}


@pytest.mark.parametrize("items,reason", [
    ([fixed("1", "tonne", "1", "USD", "tonne"), fixed("1", "tonne", "1", "EUR", "tonne")], "mixed_currencies"),
    ([{"quantity": None, "pricing": None}], "price_or_quantity_unavailable"),
    ([{"quantity": {"original": {"amount": "1", "unit": "tonne"}, "status": "ok"},
       "pricing": {"type": "index_linked", "status": "ok"}}], "price_not_fixed"),
    ([], "no_items"),
])
def test_amount_is_unavailable_rather_than_guessed(items, reason):
    assert offer_amount(offer("a", items=items)) == {"amount": None, "currency": None, "reason": reason}


# Payment schedule


DATES = {"shipment": "2026-11-05", "bill_of_lading": "2026-11-06", "documents": "2026-11-12",
         "invoice": "2026-11-06", "delivery": None, "arrival": None}


def test_schedule_places_each_instalment_on_its_event_date():
    terms = parse_payment_terms(text_field("payment_terms", "30% advance by TT, 70% against copy of bill of lading"))
    result = cash_timing(terms, amount={"amount": "2353125", "currency": "USD"}, dates=DATES,
                         decision_date=dt.date(2026, 10, 10))
    assert [(line["due_date"], line["days_from_decision"], line["amount"]) for line in result["lines"]] == [
        ("2026-10-10", 0, {"amount": "705937.5", "currency": "USD"}),
        ("2026-11-06", 27, {"amount": "1647187.5", "currency": "USD"}),
    ]
    assert (result["complete"], result["weighted_days"], result["total_percent"]) == (True, "18.9", "100")
    assert result["missing"] == []


def test_days_after_the_event_are_added():
    terms = parse_payment_terms(text_field("payment_terms", "LC 90 days from bill of lading date"))
    [line] = cash_timing(terms, amount=None, dates=DATES, decision_date=dt.date(2026, 10, 10))["lines"]
    assert (line["due_date"], line["days_from_decision"], line["amount"]) == ("2027-02-04", 117, None)


def test_missing_event_dates_and_unstated_events_leave_the_schedule_incomplete():
    terms = parse_payment_terms(text_field("payment_terms", "Net 45 days"))
    result = cash_timing(terms, amount=None, dates=DATES, decision_date=AS_OF)
    assert result["complete"] is False and result["weighted_days"] is None
    assert result["missing"] == ["anchor_not_stated"]
    arrival = parse_payment_terms(text_field("payment_terms", "Net 30 days after arrival"))
    assert cash_timing(arrival, amount=None, dates=DATES, decision_date=AS_OF)["missing"] == ["date_not_entered:arrival"]


def test_partial_terms_never_get_a_remainder():
    terms = parse_payment_terms(text_field(
        "payment_terms", "provisional invoice 90% against bill of lading, final invoice after quotational period"))
    result = cash_timing(terms, amount={"amount": "1000", "currency": "USD"}, dates=DATES, decision_date=AS_OF)
    assert [line["amount"] for line in result["lines"]] == [{"amount": "900", "currency": "USD"}]
    assert result["complete"] is False and "terms_partial" in result["missing"]


def test_bad_dates_are_refused():
    terms = parse_payment_terms(text_field("payment_terms", "Net 60 days from invoice date"))
    with pytest.raises(ValueError):
        cash_timing(terms, amount=None, dates={**DATES, "invoice": "2026-13-01"}, decision_date=AS_OF)


# Comparing terms


def schedule(text):
    return cash_timing(parse_payment_terms(text_field("payment_terms", text)),
                       amount={"amount": "2353125", "currency": "USD"}, dates=DATES, decision_date=dt.date(2026, 10, 10))


def test_later_payment_is_valued_only_at_the_entered_rate():
    base = schedule("30% advance by TT, 70% against copy of bill of lading")
    later = schedule("LC 90 days from bill of lading date")
    result = compare_terms(base, later, amount={"amount": "2353125", "currency": "USD"}, rate_percent="8")
    assert result["weighted_days_change"] == "98.1"
    assert result["value_of_later_payment"] == {"amount": "50595.41", "currency": "USD"}
    assert (result["rate_percent"], result["day_count"], result["rounding"]) == ("8", 365, "half_even_0.01")
    assert result["reason"] is None


def test_no_rate_means_no_cost_figure():
    base = schedule("30% advance by TT, 70% against copy of bill of lading")
    later = schedule("LC 90 days from bill of lading date")
    result = compare_terms(base, later, amount={"amount": "2353125", "currency": "USD"}, rate_percent=None)
    assert result["weighted_days_change"] == "98.1"
    assert (result["value_of_later_payment"], result["reason"]) == (None, "no_rate_entered")


def test_incomplete_schedules_are_not_compared():
    base = schedule("30% advance by TT, 70% against copy of bill of lading")
    unknown = schedule("Net 45 days")
    result = compare_terms(base, unknown, amount={"amount": "2353125", "currency": "USD"}, rate_percent="8")
    assert (result["weighted_days_change"], result["value_of_later_payment"], result["reason"]) == (
        None, None, "schedule_incomplete")


@pytest.mark.parametrize("rate", ["-1", "101", "eight", "1e2", ""])
def test_bad_rates_are_refused(rate):
    base = schedule("Net 60 days from invoice date")
    with pytest.raises(ValueError):
        compare_terms(base, base, amount={"amount": "1", "currency": "USD"}, rate_percent=rate)


# The combined scenario shown on /offers and exported


def test_offer_scenario_states_assumptions_and_validity_at_both_dates():
    target = offer("a", valid_until="2026-10-18")
    others = [target, offer("b", payment="LC 90 days from bill of lading date")]
    result = offer_scenario(target, others, as_of=AS_OF, decision_date=dt.date(2026, 10, 20), dates=DATES,
                            rate_percent="8", alternative_offer_id="b")
    assert result["synthetic"] is True
    assert (result["validity"]["as_of"]["state"], result["validity"]["decision"]["state"]) == ("valid", "expired")
    assert result["comparison"]["newly_expired"] == [{"offer_id": "a", "valid_until": "2026-10-18"}]
    assert result["payment_terms"]["status"] == "parsed"
    assert result["alternative"]["offer_id"] == "b"
    assert result["terms_comparison"]["weighted_days_change"] is not None
    assert any("rate" in a for a in result["assumptions"])
    assert all("—" not in a for a in result["assumptions"])
