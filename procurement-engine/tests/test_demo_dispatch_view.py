"""Company-owned demonstration dispatch rows and recovery binding."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

from procure.demo.company import get_company, list_companies
from procure.demo.dispatch_view import build_dispatch_view, resolve_recovery_shipment
from procure.demo.planner import build_plan
from procure.demo.recovery import compare
from procure.demo.quote_service import (correct_offer, extract_fixture, fixture_review_values,
                                       new_offer, save_offer)


TODAY = date(2026, 10, 14)


def selected_copper(quantity="10000", stock="2000"):
    company = get_company("solara-cables")
    offer = save_offer(new_offer(extract_fixture("express-copper", mode="rules", as_of=TODAY),
                               offer_id="express-copper", today=TODAY), today=TODAY)
    request = {"offer_ids": [offer["offer_id"]], "expected_versions": {offer["offer_id"]: offer["version"]},
               "material": "copper", "quantity_kg": quantity, "current_stock_kg": stock,
               "need_by": "2026-11-05", "decision_date": "2026-10-14", "reporting_currency": "USD",
               "budget": "100000", "additional_cost_per_kg": "0.25"}
    return company, offer, build_plan([offer], request, today=TODAY)


def reviewed_copper(delays):
    company = get_company("solara-cables")
    offers = []
    for fixture_id in ("late-copper", "express-copper", "eu-copper"):
        offer = new_offer(extract_fixture(fixture_id, mode="rules", as_of=TODAY),
                          offer_id=fixture_id, today=TODAY)
        if fixture_id == "late-copper":
            offer = correct_offer(offer, field="settlement_currency", value="USD", today=TODAY)
        offers.append(save_offer(offer, today=TODAY))
    request = {"offer_ids": [offer["offer_id"] for offer in offers],
               "expected_versions": {offer["offer_id"]: offer["version"] for offer in offers},
               "material": "copper", "quantity_kg": "10000", "current_stock_kg": "2000",
               "need_by": "2026-11-05", "decision_date": "2026-10-14",
               "reporting_currency": "USD", "budget": "100000", "additional_cost_per_kg": "0.25"}
    baseline = build_plan(offers, request, today=TODAY)
    recovery = compare(baseline, request, offers, delays, today=TODAY)
    return company, offers, recovery


def plan_rows(view):
    return [row for row in view["shipments"] if row["source"] == "synthetic_plan_demand_scenario"]


def test_all_company_orders_are_readable_expected_scenarios_without_offer_or_recovery():
    profiles = list_companies()
    assert len(profiles) == 10
    assert sum(len(profile["materials"]) for profile in profiles) == 52
    for profile in profiles:
        view = build_dispatch_view(profile, [], None, None)
        assert view["company_id"] == profile["company_id"]
        assert view["synthetic"] is True and view["as_of"] == profile["as_of"]
        assert not plan_rows(view)
        assert {row["order_id"] for row in view["shipments"]} == {
            order["order_id"] for order in profile["orders"]}
        for row in view["shipments"]:
            assert row["company_id"] == profile["company_id"]
            assert row["source"] == "fictional_company_profile_expected_order"
            assert row["recovery_available"] is False
            assert row["offer_id"] is None and row["plan_id"] is None
            assert row["status"] == "expected_order_scenario"


def test_all_fifty_two_materials_can_bind_only_their_own_reviewed_quote_and_same_unit_plan():
    count = 0
    for company in list_companies():
        for scenario in company["quote_coverage"]["scenarios"]:
            fixture_id = scenario["fixture_ids"][1]
            offer = new_offer(extract_fixture(fixture_id, mode="rules", as_of=TODAY),
                              offer_id=fixture_id, today=TODAY)
            for field, value in fixture_review_values(fixture_id).items():
                offer = correct_offer(offer, field=field, value=value, today=TODAY)
            offer = save_offer(offer, today=TODAY)
            defaults = scenario["buyer_defaults"]
            request = {**defaults, "offer_ids": [offer["offer_id"]],
                       "expected_versions": {offer["offer_id"]: offer["version"]}}
            plan = build_plan([offer], request, today=TODAY)
            assert plan["selected_offer_id"] == offer["offer_id"]
            view = build_dispatch_view(company, [offer], plan, None)
            row, = plan_rows(view)
            assert view["shipments"][0] == row
            assert [record["order_id"] for record in view["shipments"][1:]] == [
                order["order_id"] for order in company["orders"]]
            assert row["company_id"] == company["company_id"]
            assert row["material_id"] == scenario["material_id"]
            assert row["quantity_unit"] == defaults["quantity_unit"]
            assert row["quantity"] == plan["required_purchase"]
            assert row["route"] is None and row["route_reason"] == "no_declared_shipment_route"
            assert row["recovery_available"] is True
            count += 1
    assert count == 52


def test_selected_quote_creates_exact_owned_plan_demand_row_without_inventing_inbound_order_quantity():
    company, offer, plan = selected_copper()
    view = build_dispatch_view(company, [offer], plan, None)
    row, = plan_rows(view)
    assert row["shipment_id"].startswith("demo-plan-demand:")
    assert row["company_id"] == "solara-cables"
    assert row["order_id"] == "solara-cables-order-1"
    assert row["quantity"] == "8000" and row["order_demand_quantity"] == "10000"
    assert row["quantity_unit"] == "kg" and row["need_by"] == "2026-11-05"
    assert row["offer_id"] == offer["offer_id"] and row["offer_version"] == offer["version"]
    assert row["plan_id"] == plan["plan_id"] and row["recovery_available"] is True
    assert row["expected_arrival"] == "2026-10-28"
    assert row["arrival_basis"] == "plan_decision_plus_reviewed_quote_lead_and_assumed_delay"
    assert row["timeline"] and all(event["source"] == "synthetic_plan_assumption" for event in row["timeline"])
    assert resolve_recovery_shipment(view, row["shipment_id"], plan["plan_id"], offer["version"]) == row


def test_changed_demand_uses_explicit_plan_reference_and_never_claims_a_purchase_order():
    company, offer, plan = selected_copper(quantity="9000")
    row, = plan_rows(build_dispatch_view(company, [offer], plan, None))
    assert row["order_id"] is None
    assert row["demand_reference"].startswith("plan-demand:")
    assert row["quantity"] == "7000" and row["order_demand_quantity"] == "9000"


def test_unknown_material_order_remains_readable_without_recovery_or_fabricated_material_mapping():
    company = get_company("solara-cables")
    company["orders"].append({"order_id": "solara-cables-unknown-order", "material_id": "unknown-grade",
                              "quantity": "40", "unit": "crate", "need_by": "2026-11-10", "status": "open"})
    row = next(row for row in build_dispatch_view(company, [], None, None)["shipments"]
               if row["order_id"] == "solara-cables-unknown-order")
    assert row["material_id"] == row["material_name"] == "unknown-grade"
    assert row["recovery_available"] is False
    assert row["recovery_reason"] == "material_or_unit_unavailable"


def test_foreign_stale_or_mismatched_quote_and_plan_cannot_enable_recovery():
    company, offer, plan = selected_copper()
    mutations = []
    foreign = deepcopy(company)
    foreign["company_id"] = "terrapack"
    mutations.append((foreign, [offer], plan))
    for key, value in (("status", "draft"), ("version", offer["version"] + 1)):
        changed = deepcopy(offer)
        changed[key] = value
        mutations.append((company, [changed], plan))
    wrong_material = deepcopy(plan)
    wrong_material["material"] = "polymer"
    mutations.append((company, [offer], wrong_material))
    wrong_unit = deepcopy(plan)
    wrong_unit["quantity_unit"] = "tonne"
    mutations.append((company, [offer], wrong_unit))
    wrong_company = deepcopy(plan)
    wrong_company["company_id"] = "terrapack"
    mutations.append((company, [offer], wrong_company))
    for profile, offers, candidate in mutations:
        assert not plan_rows(build_dispatch_view(profile, offers, candidate, None))


def test_zero_purchase_or_no_selected_offer_has_no_plan_shipment():
    company, offer, plan = selected_copper()
    covered = deepcopy(plan)
    covered["required_purchase"] = "0"
    assert not plan_rows(build_dispatch_view(company, [offer], covered, None))
    unselected = deepcopy(plan)
    unselected["selected_offer_id"] = None
    assert not plan_rows(build_dispatch_view(company, [offer], unselected, None))


def test_recovery_resolution_rejects_foreign_shipment_stale_plan_and_quote_version():
    company, offer, plan = selected_copper()
    view = build_dispatch_view(company, [offer], plan, None)
    row, = plan_rows(view)
    for shipment_id, plan_id, version in (("foreign", plan["plan_id"], offer["version"]),
                                          (row["shipment_id"], "changed-plan", offer["version"]),
                                          (row["shipment_id"], plan["plan_id"], offer["version"] + 1)):
        assert resolve_recovery_shipment(view, shipment_id, plan_id, version) is None
    expected = next(row for row in view["shipments"] if row["source"] == "fictional_company_profile_expected_order")
    assert resolve_recovery_shipment(view, expected["shipment_id"], plan["plan_id"], offer["version"]) is None


def test_recovery_scenario_keeps_original_arrival_and_delay_separate_and_inputs_unchanged():
    company, offer, baseline = selected_copper()
    scenario = deepcopy(baseline)
    scenario["plan_id"] = "reviewed-scenario-plan"
    scenario["alternatives"][0]["arrival_date"] = "2026-11-17"
    scenario["assumptions"]["delay_days_by_offer"] = {offer["offer_id"]: 20}
    recovery = {"baseline": baseline, "scenario": scenario, "outcome": "unchanged"}
    inputs = deepcopy((company, [offer], scenario, recovery))
    view = build_dispatch_view(company, [offer], scenario, recovery)
    row, = plan_rows(view)
    assert view["plan_id"] == "reviewed-scenario-plan"
    assert view["baseline_plan_id"] == baseline["plan_id"]
    assert row["original_expected_arrival"] == "2026-10-28"
    assert row["scenario_expected_arrival"] == "2026-11-17"
    assert row["delay_days"] == 0 and row["scenario_delay_days"] == 20
    assert row["recovery_available"] is False
    assert resolve_recovery_shipment(view, row["shipment_id"], scenario["plan_id"], offer["version"]) is None
    assert (company, [offer], scenario, recovery) == inputs


def test_stale_recovery_comparison_never_reenables_an_action_on_current_plan():
    company, offer, plan = selected_copper()
    old_scenario = deepcopy(plan)
    old_scenario["plan_id"] = "previous-scenario-plan"
    stale_recovery = {"baseline": plan, "scenario": old_scenario}
    current = deepcopy(plan)
    current["plan_id"] = "current-plan"
    view = build_dispatch_view(company, [offer], current, stale_recovery)
    row, = plan_rows(view)
    assert row["recovery_available"] is False
    assert resolve_recovery_shipment(view, row["shipment_id"], current["plan_id"], offer["version"]) is None


def test_real_infeasible_recovery_preserves_read_only_baseline_shipment_and_marks_missing_scenario_arrival():
    company, offers, recovery = reviewed_copper({"eu-copper": 20, "express-copper": 10})
    assert recovery["baseline"]["selected_offer_id"] == "eu-copper"
    assert recovery["scenario"]["selected_offer_id"] is None
    view = build_dispatch_view(company, offers, recovery["scenario"], recovery)
    row, = plan_rows(view)
    assert view["plan_id"] == recovery["scenario"]["plan_id"]
    assert row["plan_id"] == recovery["baseline"]["plan_id"]
    assert row["scenario_plan_id"] == recovery["scenario"]["plan_id"]
    assert row["offer_id"] == row["baseline_offer_id"] == "eu-copper"
    assert row["expected_arrival"] == row["original_expected_arrival"] == "2026-10-24"
    assert row["scenario_expected_arrival"] is None
    assert row["scenario_selected_offer_id"] is None
    assert row["scenario_status"] == "infeasible"
    assert row["recovery_available"] is False
    assert all(event["plan_id"] == recovery["baseline"]["plan_id"]
               and event["offer_id"] == "eu-copper" for event in row["timeline"])
    assert resolve_recovery_shipment(view, row["shipment_id"], view["plan_id"], row["offer_version"]) is None


def test_real_supplier_switch_identifies_baseline_quote_and_labels_both_plan_timelines():
    company, offers, recovery = reviewed_copper({"eu-copper": 20})
    assert recovery["baseline"]["selected_offer_id"] == "eu-copper"
    assert recovery["scenario"]["selected_offer_id"] == "express-copper"
    view = build_dispatch_view(company, offers, recovery["scenario"], recovery)
    row, = plan_rows(view)
    assert row["offer_id"] == row["scenario_selected_offer_id"] == "express-copper"
    assert row["baseline_offer_id"] == "eu-copper"
    assert row["baseline_offer_version"] == next(offer["version"] for offer in offers
                                                  if offer["offer_id"] == "eu-copper")
    assert row["baseline_supplier"] == "Europa Copper SA [SYNTHETIC]"
    assert row["supplier"] == row["scenario_supplier"] == "Swift Copper Trading Ltd [SYNTHETIC]"
    assert row["original_expected_arrival"] == "2026-10-24"
    assert row["scenario_expected_arrival"] == "2026-10-28"
    assert {(event["phase"], event["plan_id"], event["offer_id"])
            for event in row["timeline"]} == {
                ("baseline", recovery["baseline"]["plan_id"], "eu-copper"),
                ("scenario", recovery["scenario"]["plan_id"], "express-copper")}
    assert row["recovery_available"] is False
