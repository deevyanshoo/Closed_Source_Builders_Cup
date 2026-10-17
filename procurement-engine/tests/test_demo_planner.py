"""Hand-calculated single-offer comparisons on the synthetic copper scenario."""
from __future__ import annotations

from datetime import date

import pytest

TODAY = date(2026, 10, 14)


def services():
    from procure.demo import planner, quote_service
    return planner, quote_service


def accepted_offers():
    _, quotes = services()
    out = []
    for fixture_id in ("late-copper", "express-copper", "eu-copper"):
        offer = quotes.new_offer(quotes.extract_fixture(fixture_id, mode="rules", as_of=TODAY),
                                 offer_id=fixture_id, today=TODAY)
        if fixture_id == "late-copper":
            offer = quotes.correct_offer(offer, field="settlement_currency", value="USD", today=TODAY)
        out.append(quotes.save_offer(offer, today=TODAY))
    return out


def request(offers, **changes):
    data = {"offer_ids": [o["offer_id"] for o in offers],
            "expected_versions": {o["offer_id"]: o["version"] for o in offers},
            "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
            "need_by": "2026-11-05", "decision_date": "2026-10-14",
            "reporting_currency": "USD", "budget": "100000",
            "additional_cost_per_kg": "0.25"}
    return {**data, **changes}


def test_stock_and_fx_cost_select_cheapest_feasible_not_cheapest_late_offer():
    planner, _ = services()
    offers = accepted_offers()
    result = planner.build_plan(offers, request(offers), today=TODAY)
    assert result["required_purchase_kg"] == "8000"
    alternatives = {a["offer_id"]: a for a in result["alternatives"]}
    assert alternatives["late-copper"]["feasible"] is False
    assert "arrives_after_need_by" in alternatives["late-copper"]["reasons"]
    assert alternatives["late-copper"]["unit_price_per_kg"] == "9.4125"
    assert alternatives["express-copper"]["feasible"] is True
    assert alternatives["express-copper"]["total_cost"] == "86000"
    assert alternatives["eu-copper"]["feasible"] is True
    assert alternatives["eu-copper"]["unit_price_per_kg"] == "10"
    assert alternatives["eu-copper"]["total_cost"] == "82000"
    assert result["selected_offer_id"] == "eu-copper"
    assert result["total_cost"] == "82000"
    assert result["forecast_action"] == "ABSTAIN" and result["customer_savings"] is None
    assert result["assumptions"]["fx_source"] == "synthetic_scenario"


def test_budget_can_make_every_offer_infeasible():
    planner, _ = services()
    offers = accepted_offers()
    result = planner.build_plan(offers, request(offers, budget="80000"), today=TODAY)
    assert result["selected_offer_id"] is None and result["total_cost"] is None
    assert all(not a["feasible"] for a in result["alternatives"])
    assert "over_budget" in {reason for a in result["alternatives"] for reason in a["reasons"]}


def test_moq_capacity_and_delay_are_real_constraints():
    planner, _ = services()
    offers = accepted_offers()
    too_small = planner.build_plan(offers, request(offers, quantity_kg="4000", current_stock_kg="0"), today=TODAY)
    alt = {a["offer_id"]: a for a in too_small["alternatives"]}
    assert "below_min_order" in alt["eu-copper"]["reasons"]
    too_large = planner.build_plan(offers, request(offers, quantity_kg="70000", current_stock_kg="0",
                                                   budget="1000000"), today=TODAY)
    alt = {a["offer_id"]: a for a in too_large["alternatives"]}
    assert "exceeds_capacity" in alt["express-copper"]["reasons"]
    delayed = planner.build_plan(offers, request(offers, delay_days_by_offer={"eu-copper": 20}), today=TODAY)
    assert delayed["selected_offer_id"] == "express-copper"


def test_unknown_fx_and_stale_version_fail_closed():
    planner, _ = services()
    offers = accepted_offers()
    result = planner.build_plan(offers, request(offers, decision_date="2026-10-15"), today=TODAY)
    alt = {a["offer_id"]: a for a in result["alternatives"]}
    assert "fx_unavailable" in alt["eu-copper"]["reasons"]
    with pytest.raises(planner.DemoError) as err:
        planner.build_plan(offers, request(offers, expected_versions={"late-copper": 1,
                                                                        "express-copper": 2,
                                                                        "eu-copper": 2}), today=TODAY)
    assert err.value.code == "stale_version"


def test_invalid_numeric_inputs_do_not_accept_nan_or_negative():
    planner, _ = services()
    offers = accepted_offers()
    for key, value in (("quantity_kg", "NaN"), ("current_stock_kg", "-1"),
                       ("budget", "Infinity"), ("additional_cost_per_kg", "-0.1")):
        with pytest.raises(planner.DemoError) as err:
            planner.build_plan(offers, request(offers, **{key: value}), today=TODAY)
        assert err.value.code == "invalid_value"


def test_large_decimal_stock_subtraction_keeps_every_digit():
    planner, _ = services()
    offers = accepted_offers()
    result = planner.build_plan(offers, request(offers, quantity_kg="100000000000000000000000000001",
                                                current_stock_kg="2", budget="0"), today=TODAY)
    assert result["required_purchase_kg"] == "99999999999999999999999999999"


def test_date_overflow_is_a_typed_validation_error():
    planner, _ = services()
    offers = accepted_offers()
    with pytest.raises(planner.DemoError) as err:
        planner.build_plan(offers, request(offers, decision_date="9999-12-31", need_by="9999-12-31"), today=TODAY)
    assert err.value.code == "invalid_value" and err.value.status_code == 422


def test_offer_expired_as_of_today_is_not_revived_by_an_older_scenario_date():
    planner, _ = services()
    offers = accepted_offers()
    result = planner.build_plan(offers, request(offers), today=date(2027, 1, 1))
    assert result["selected_offer_id"] is None
    assert all("offer_expired" in alt["reasons"] for alt in result["alternatives"])


def test_quarter_capacity_does_not_imply_available_quantity_before_need_date():
    planner, quotes = services()
    offer = quotes.new_offer(quotes.extract_fixture("express-copper", mode="rules", as_of=TODAY),
                             offer_id="quarter-capacity", today=TODAY)
    offer = quotes.correct_offer(offer, field="capacity",
                                 value={"amount": "60", "unit": "tonne", "period": "quarter"}, today=TODAY)
    offer = quotes.save_offer(offer, today=TODAY)
    result = planner.build_plan([offer], request([offer]), today=TODAY)
    assert result["selected_offer_id"] is None
    assert "capacity_period_unsupported" in result["alternatives"][0]["reasons"]
