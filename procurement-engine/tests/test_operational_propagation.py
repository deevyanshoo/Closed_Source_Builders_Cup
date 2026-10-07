"""Hand-solvable synthetic material-arrival screens; no operational feasibility claim."""
from __future__ import annotations

from procure.events.propagation import project_arrival_change


AS_OF = "2026-10-09T00:00:00Z"
DAY = "2026-10-10T"
LATER = "2026-10-11T09:00:00Z"


def case(*, stock="2", arrival="6", baseline=DAY + "09:00:00Z",
         changed=LATER, demands=(("K1", "P1", "4", DAY + "10:00:00Z"),
                                 ("K2", "P2", "3", DAY + "11:00:00Z"))):
    nodes = [
        {"id": "S1", "kind": "shipment"}, {"id": "PO1", "kind": "purchase_order"},
        {"id": "C-copper", "kind": "component"},
    ]
    edges = [{"from": "S1", "to": "PO1"}, {"from": "PO1", "to": "C-copper"}]
    commitments = []
    for commitment_id, production_id, quantity, required_at in demands:
        nodes.extend(({"id": production_id, "kind": "production_order"},
                      {"id": commitment_id, "kind": "commitment"}))
        edges.extend(({"from": "C-copper", "to": production_id},
                      {"from": production_id, "to": commitment_id}))
        commitments.append({"commitment_id": commitment_id,
                            "production_order_id": production_id,
                            "required_at": required_at, "due_at": required_at,
                            "demands": [{"component_id": "C-copper", "quantity": quantity, "unit": "kg"}]})
    snapshot = {
        "workspace_id": "W-A", "plan_version": "v7", "snapshot_id": "snap-1",
        "as_of": AS_OF, "synthetic": True, "nodes": nodes, "edges": edges,
        "inventory": [{"component_id": "C-copper", "unit": "kg", "starting_stock": stock}],
        "arrival_lines": [{"line_id": "L1", "shipment_id": "S1", "purchase_order_id": "PO1",
                           "component_id": "C-copper", "quantity": arrival,
                           "unit": "kg", "arrival_at": baseline}],
        "commitments": commitments,
    }
    scenario = {"workspace_id": "W-A", "expected_plan_version": "v7",
                "source_snapshot_id": "snap-1", "event_id": "event-1", "scenario_id": "scenario-1",
                "synthetic": True, "changes": [{"line_id": "L1", "arrival_at": changed}]}
    return snapshot, scenario


def demand(result, commitment_id):
    commitment = next(item for item in result["affected_commitments"]
                      if item["commitment_id"] == commitment_id)
    return commitment["demands"][0]


def test_delayed_receipt_exposes_both_dependent_orders_without_backlog():
    snapshot, scenario = case()
    result = project_arrival_change(snapshot, scenario)
    assert result["status"] == "screened"
    assert [row["commitment_id"] for row in result["affected_commitments"]] == ["K1", "K2"]
    assert [demand(result, key)["baseline"]["shortfall"] for key in ("K1", "K2")] == ["0", "0"]
    assert [demand(result, key)["changed"]["shortfall"] for key in ("K1", "K2")] == ["2", "3"]
    assert [demand(result, key)["comparison"] for key in ("K1", "K2")] == ["new_shortage", "new_shortage"]
    assert result["component_balances"][0]["changed_ending_stock"] == "6"


def assert_unsupported(snapshot, scenario, reason):
    result = project_arrival_change(snapshot, scenario)
    assert result["status"] == "unsupported"
    assert result["reason"] == reason
    assert "affected_commitments" not in result
    assert "component_balances" not in result


def test_starting_stock_covers_both_reachable_orders():
    snapshot, scenario = case(stock="6", arrival="4", demands=(
        ("K1", "P1", "2", DAY + "10:00:00Z"),
        ("K2", "P2", "3", DAY + "11:00:00Z")))
    result = project_arrival_change(snapshot, scenario)
    assert [row["commitment_id"] for row in result["affected_commitments"]] == ["K1", "K2"]
    assert [demand(result, key)["changed"]["shortfall"] for key in ("K1", "K2")] == ["0", "0"]
    assert [row["material_at_risk"] for row in result["affected_commitments"]] == [False, False]
    assert result["component_balances"][0]["changed_ending_stock"] == "5"


def test_split_arrival_moves_only_named_line_and_preserves_earlier_demand():
    snapshot, scenario = case(stock="1", arrival="3", baseline=DAY + "08:00:00Z",
                              changed=DAY + "08:00:00Z", demands=(
                                  ("K1", "P1", "3", DAY + "09:00:00Z"),
                                  ("K2", "P2", "4", DAY + "11:00:00Z")))
    snapshot["arrival_lines"].append({**snapshot["arrival_lines"][0], "line_id": "L2",
                                      "quantity": "4", "arrival_at": DAY + "10:00:00Z"})
    scenario["changes"] = [{"line_id": "L2", "arrival_at": DAY + "12:00:00Z"}]
    result = project_arrival_change(snapshot, scenario)
    assert demand(result, "K1")["changed"]["shortfall"] == "0"
    assert demand(result, "K1")["comparison"] == "unchanged_shortage"
    assert demand(result, "K2")["baseline"]["shortfall"] == "0"
    assert demand(result, "K2")["changed"]["shortfall"] == "3"
    assert result["component_balances"][0]["changed_ending_stock"] == "4"


def test_arrival_at_exact_requirement_instant_is_credited_first():
    snapshot, scenario = case(stock="0", arrival="5", changed=DAY + "10:00:00Z",
                              demands=(("K1", "P1", "5", DAY + "10:00:00Z"),))
    equality = project_arrival_change(snapshot, scenario)
    assert demand(equality, "K1")["changed"]["shortfall"] == "0"
    scenario["changes"][0]["arrival_at"] = DAY + "10:00:01Z"
    late = project_arrival_change(snapshot, scenario)
    assert demand(late, "K1")["changed"]["shortfall"] == "5"
    assert demand(late, "K1")["comparison"] == "new_shortage"


def test_noop_reorder_duplicate_identical_inputs_and_immutability():
    from copy import deepcopy

    snapshot, scenario = case(stock="0", arrival="4", changed=DAY + "09:00:00Z",
                              demands=(("K1", "P1", "4", DAY + "10:00:00Z"),))
    original_snapshot, original_scenario = deepcopy(snapshot), deepcopy(scenario)
    baseline = project_arrival_change(snapshot, scenario)
    assert baseline["status"] == "screened"
    assert demand(baseline, "K1")["comparison"] == "unchanged_shortage"
    assert demand(baseline, "K1")["changed"]["shortfall"] == "0"
    assert project_arrival_change(snapshot, scenario) == baseline
    assert snapshot == original_snapshot and scenario == original_scenario
    snapshot["arrival_lines"].append(deepcopy(snapshot["arrival_lines"][0]))
    scenario["changes"].append(deepcopy(scenario["changes"][0]))
    for key in ("nodes", "edges", "inventory", "arrival_lines", "commitments"):
        snapshot[key].reverse()
    scenario["changes"].reverse()
    assert project_arrival_change(snapshot, scenario) == baseline


def test_equal_time_demands_use_commitment_id_tiebreak_without_negative_stock():
    snapshot, scenario = case(stock="3", arrival="0", changed=DAY + "09:00:00Z",
                              demands=(("K2", "P2", "2", DAY + "10:00:00Z"),
                                       ("K1", "P1", "2", DAY + "10:00:00Z")))
    result = project_arrival_change(snapshot, scenario)
    assert [r["commitment_id"] for r in result["affected_commitments"]] == ["K1", "K2"]
    assert demand(result, "K1")["changed"] == {"used": "2", "shortfall": "0", "remaining_stock": "1"}
    assert demand(result, "K2")["changed"] == {"used": "1", "shortfall": "1", "remaining_stock": "0"}


def test_large_fractional_amounts_are_exact_under_low_ambient_decimal_precision():
    from decimal import getcontext

    stock = "123456789012345678901234567890.12345678901234567890"
    snapshot, scenario = case(stock=stock, arrival="0.00000000000000000001",
                              changed=DAY + "10:00:01Z", demands=((
                                  "K1", "P1", "123456789012345678901234567890.12345678901234567891",
                                  DAY + "10:00:00Z"),))
    context = getcontext()
    original = context.prec
    try:
        context.prec = 4
        result = project_arrival_change(snapshot, scenario)
    finally:
        context.prec = original
    assert demand(result, "K1")["baseline"]["shortfall"] == "0"
    assert demand(result, "K1")["changed"]["shortfall"] == "0.00000000000000000001"
    assert result["component_balances"][0]["changed_ending_stock"] == "0.00000000000000000001"


def test_comparison_distinguishes_increased_and_reduced_existing_shortage():
    snapshot, scenario = case(stock="1", arrival="2", baseline=DAY + "11:00:00Z",
                              changed=DAY + "12:00:00Z", demands=((
                                  "K1", "P1", "4", DAY + "10:00:00Z"),))
    increased = project_arrival_change(snapshot, scenario)
    assert demand(increased, "K1")["baseline"]["shortfall"] == "3"
    assert demand(increased, "K1")["comparison"] == "unchanged_shortage"
    snapshot["arrival_lines"][0]["arrival_at"] = DAY + "10:00:00Z"
    scenario["changes"][0]["arrival_at"] = DAY + "11:00:00Z"
    increased = project_arrival_change(snapshot, scenario)
    assert demand(increased, "K1")["baseline"]["shortfall"] == "1"
    assert demand(increased, "K1")["changed"]["shortfall"] == "3"
    assert demand(increased, "K1")["comparison"] == "increased_shortage"
    snapshot["arrival_lines"][0]["arrival_at"] = DAY + "11:00:00Z"
    scenario["changes"][0]["arrival_at"] = DAY + "10:00:00Z"
    reduced = project_arrival_change(snapshot, scenario)
    assert demand(reduced, "K1")["comparison"] == "reduced_shortage"
    assert demand(reduced, "K1")["changed"]["shortfall"] == "1"


def test_aware_offset_normalizes_across_date_boundary_and_rejects_naive():
    snapshot, scenario = case(stock="0", arrival="1",
                              baseline="2026-10-10T00:30:00+05:30",
                              changed="2026-10-09T19:00:00Z",
                              demands=(("K1", "P1", "1", "2026-10-09T19:00:00+00:00"),))
    snapshot["as_of"] = "2026-10-10T00:00:00+05:30"
    result = project_arrival_change(snapshot, scenario)
    assert result["as_of"] == "2026-10-09T18:30:00+00:00"
    assert demand(result, "K1")["changed"]["shortfall"] == "0"
    snapshot["as_of"] = "2026-10-09T18:30:00"
    assert_unsupported(snapshot, scenario, "invalid_timestamp")


def test_conflicting_duplicate_arrival_or_update_is_rejected():
    from copy import deepcopy

    snapshot, scenario = case()
    snapshot["arrival_lines"].append({**snapshot["arrival_lines"][0], "quantity": "7"})
    assert_unsupported(snapshot, scenario, "conflicting_snapshot")
    snapshot["arrival_lines"] = snapshot["arrival_lines"][:1]
    scenario["changes"].append({"line_id": "L1", "arrival_at": DAY + "12:00:00Z"})
    assert_unsupported(snapshot, scenario, "conflicting_update")
    scenario["changes"] = [deepcopy(scenario["changes"][0])]
    snapshot["nodes"].append({"id": "S1", "kind": "component"})
    assert_unsupported(snapshot, scenario, "conflicting_snapshot")


def test_incompatible_unit_and_incomplete_declared_graph_return_no_numbers():
    snapshot, scenario = case(stock="0", arrival="2", demands=((
        "K1", "P1", "500", DAY + "10:00:00Z"),))
    snapshot["arrival_lines"][0]["unit"] = "tonne"
    assert_unsupported(snapshot, scenario, "incompatible_unit")
    snapshot["arrival_lines"][0]["unit"] = "kg"
    snapshot["edges"] = [edge for edge in snapshot["edges"]
                         if edge != {"from": "C-copper", "to": "P1"}]
    assert_unsupported(snapshot, scenario, "incomplete_graph")


def test_complete_declared_graph_validation_catches_unrelated_broken_order():
    snapshot, scenario = case()
    snapshot["nodes"].extend(({"id": "P3", "kind": "production_order"},
                              {"id": "K3", "kind": "commitment"}))
    snapshot["edges"].append({"from": "P3", "to": "K3"})
    snapshot["commitments"].append({"commitment_id": "K3", "production_order_id": "P3",
                                    "required_at": DAY + "12:00:00Z",
                                    "due_at": DAY + "12:00:00Z", "demands": []})
    assert_unsupported(snapshot, scenario, "incomplete_graph")


def test_workspace_version_snapshot_and_synthetic_boundaries_reject():
    snapshot, scenario = case()
    scenario["workspace_id"] = "W-B"
    assert_unsupported(snapshot, scenario, "workspace_mismatch")
    scenario["workspace_id"] = "W-A"
    scenario["expected_plan_version"] = "v6"
    assert_unsupported(snapshot, scenario, "stale_plan_version")
    scenario["expected_plan_version"] = "v7"
    scenario["source_snapshot_id"] = "another"
    assert_unsupported(snapshot, scenario, "snapshot_mismatch")
    scenario["source_snapshot_id"] = "snap-1"
    scenario["synthetic"] = False
    assert_unsupported(snapshot, scenario, "invalid_scope")


def test_pre_as_of_arrival_requirement_and_changed_date_are_rejected():
    snapshot, scenario = case()
    snapshot["arrival_lines"][0]["arrival_at"] = "2026-10-08T23:59:59Z"
    assert_unsupported(snapshot, scenario, "pre_as_of")
    snapshot["arrival_lines"][0]["arrival_at"] = DAY + "09:00:00Z"
    snapshot["commitments"][0]["required_at"] = "2026-10-08T23:59:59Z"
    assert_unsupported(snapshot, scenario, "pre_as_of")
    snapshot["commitments"][0]["required_at"] = DAY + "10:00:00Z"
    scenario["changes"][0]["arrival_at"] = "2026-10-08T23:59:59Z"
    assert_unsupported(snapshot, scenario, "pre_as_of")


def test_missing_invalid_or_float_quantity_never_silently_becomes_zero():
    snapshot, scenario = case()
    del snapshot["commitments"][1]["demands"][0]["quantity"]
    assert_unsupported(snapshot, scenario, "incomplete_graph")
    snapshot, scenario = case()
    snapshot["arrival_lines"][0]["quantity"] = 6.0
    assert_unsupported(snapshot, scenario, "invalid_quantity")
    snapshot["arrival_lines"][0]["quantity"] = "NaN"
    assert_unsupported(snapshot, scenario, "invalid_quantity")
    snapshot["arrival_lines"][0]["quantity"] = "-2"
    assert_unsupported(snapshot, scenario, "invalid_quantity")


def test_screen_discloses_material_only_limits_without_feasibility_claim():
    snapshot, scenario = case()
    result = project_arrival_change(snapshot, scenario)
    assert result["status"] == "screened"
    assert result["synthetic"] is True
    assert "limitations" in result and "capacity" in " ".join(result["limitations"]).lower()
    assert "feasible" not in result and "customer_due_risk" not in result
    assert result["component_balances"][0]["unit"] == "kg"


def test_shared_production_order_screens_components_separately():
    snapshot, scenario = case(stock="0", arrival="3", changed=DAY + "11:00:00Z",
                              demands=(("K1", "P1", "3", DAY + "10:00:00Z"),))
    snapshot["nodes"].extend(({"id": "S2", "kind": "shipment"},
                              {"id": "PO2", "kind": "purchase_order"},
                              {"id": "C-resin", "kind": "component"}))
    snapshot["edges"].extend(({"from": "S2", "to": "PO2"},
                              {"from": "PO2", "to": "C-resin"},
                              {"from": "C-resin", "to": "P1"}))
    snapshot["inventory"].append({"component_id": "C-resin", "unit": "litre",
                                  "starting_stock": "1.5"})
    snapshot["arrival_lines"].append({"line_id": "L2", "shipment_id": "S2",
                                      "purchase_order_id": "PO2", "component_id": "C-resin",
                                      "quantity": "2.5", "unit": "litre",
                                      "arrival_at": DAY + "09:00:00Z"})
    snapshot["commitments"][0]["demands"].append({"component_id": "C-resin",
                                                   "quantity": "3", "unit": "litre"})
    result = project_arrival_change(snapshot, scenario)
    assert result["status"] == "screened"
    row = result["affected_commitments"][0]
    assert [(d["component_id"], d["unit"], d["changed"]["shortfall"])
            for d in row["demands"]] == [("C-copper", "kg", "3"), ("C-resin", "litre", "0")]
    assert [(b["component_id"], b["unit"], b["changed_ending_stock"])
            for b in result["component_balances"]] == [
                ("C-copper", "kg", "3"), ("C-resin", "litre", "1")]
    assert "total_shortfall" not in result


def test_declared_component_edge_without_demand_rejects_entire_screen():
    snapshot, scenario = case()
    snapshot["nodes"].extend(({"id": "S2", "kind": "shipment"},
                              {"id": "PO2", "kind": "purchase_order"},
                              {"id": "C-resin", "kind": "component"}))
    snapshot["edges"].extend(({"from": "S2", "to": "PO2"},
                              {"from": "PO2", "to": "C-resin"},
                              {"from": "C-resin", "to": "P1"}))
    snapshot["inventory"].append({"component_id": "C-resin", "unit": "litre",
                                  "starting_stock": "1"})
    snapshot["arrival_lines"].append({"line_id": "L2", "shipment_id": "S2",
                                      "purchase_order_id": "PO2", "component_id": "C-resin",
                                      "quantity": "2", "unit": "litre",
                                      "arrival_at": DAY + "09:00:00Z"})
    assert_unsupported(snapshot, scenario, "incomplete_graph")


def test_extra_production_to_commitment_edge_must_match_declared_owner():
    snapshot, scenario = case()
    snapshot["edges"].append({"from": "P2", "to": "K1"})
    assert_unsupported(snapshot, scenario, "incomplete_graph")


def test_fractional_timestamp_is_preserved_and_malformed_node_kind_is_rejected():
    snapshot, scenario = case()
    snapshot["as_of"] = "2026-10-09T00:00:00.123456Z"
    assert project_arrival_change(snapshot, scenario)["as_of"] == "2026-10-09T00:00:00.123456+00:00"
    snapshot["nodes"][0]["kind"] = ["shipment"]
    assert_unsupported(snapshot, scenario, "incomplete_graph")


def test_one_microsecond_late_receipt_is_not_available_at_demand():
    snapshot, scenario = case(stock="0", arrival="5", baseline=DAY + "10:00:00Z",
                              changed=DAY + "10:00:00.000001Z",
                              demands=(("K1", "P1", "5", DAY + "10:00:00Z"),))
    result = project_arrival_change(snapshot, scenario)
    assert demand(result, "K1")["baseline"]["shortfall"] == "0"
    assert demand(result, "K1")["changed"]["shortfall"] == "5"
    assert result["arrival_changes"] == [{"line_id": "L1", "component_id": "C-copper",
                                          "quantity": "5", "unit": "kg",
                                          "baseline_at": "2026-10-10T10:00:00+00:00",
                                          "changed_at": "2026-10-10T10:00:00.000001+00:00"}]


def test_sub_microsecond_timestamp_precision_is_rejected_not_truncated():
    snapshot, scenario = case()
    scenario["changes"][0]["arrival_at"] = DAY + "10:00:00.0000001Z"
    assert_unsupported(snapshot, scenario, "invalid_timestamp")
    scenario["changes"][0]["arrival_at"] = LATER
    snapshot["as_of"] = "2026-10-09T00:00:00.1234567Z"
    assert_unsupported(snapshot, scenario, "invalid_timestamp")


def test_utc_conversion_overflow_returns_invalid_timestamp():
    snapshot, scenario = case()
    snapshot["as_of"] = "0001-01-01T00:00:00+01:00"
    assert_unsupported(snapshot, scenario, "invalid_timestamp")
    snapshot["as_of"] = "9999-12-31T23:59:59-01:00"
    assert_unsupported(snapshot, scenario, "invalid_timestamp")


def test_oversized_plain_decimal_input_rejects_without_conversion_exception():
    snapshot, scenario = case(stock="9" * 5000)
    assert_unsupported(snapshot, scenario, "invalid_quantity")
    snapshot, scenario = case(arrival="0." + "0" * 1024 + "1")
    assert_unsupported(snapshot, scenario, "invalid_quantity")


def test_many_valid_arrivals_cannot_exceed_scaled_balance_output_bound():
    tiny = "0." + "0" * 1022 + "1"  # 1 integral + 1023 fractional digits
    snapshot, scenario = case(stock=tiny, arrival="9" * 1024,
                              demands=(("K1", "P1", "1", DAY + "10:00:00Z"),))
    prototype = snapshot["arrival_lines"][0]
    for number in range(2, 101):
        snapshot["arrival_lines"].append({**prototype, "line_id": f"L{number}"})
    assert_unsupported(snapshot, scenario, "invalid_quantity")


def test_exact_near_boundary_balance_survives_configured_640_digit_guard():
    import sys

    large = "9" * 1024
    tiny = "0." + "0" * 1022 + "1"
    snapshot, scenario = case(stock=large, arrival=large,
                              demands=(("K1", "P1", tiny, DAY + "10:00:00Z"),))
    guard_supported = hasattr(sys, "get_int_max_str_digits") and hasattr(sys, "set_int_max_str_digits")
    prior = sys.get_int_max_str_digits() if guard_supported else None
    try:
        if guard_supported:
            sys.set_int_max_str_digits(640)
        result = project_arrival_change(snapshot, scenario)
        assert result["status"] == "screened"
        assert result["component_balances"][0]["starting_stock"] == large
        assert result["component_balances"][0]["baseline_ending_stock"] == (
            "1" + "9" * 1023 + "7." + "9" * 1023)
        assert demand(result, "K1")["baseline"]["shortfall"] == "0"
    finally:
        if guard_supported:
            sys.set_int_max_str_digits(prior)
