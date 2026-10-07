"""Synthetic, hand-solvable tests for the public receipt recovery comparison."""
from __future__ import annotations

from copy import deepcopy
from decimal import getcontext

from procure.production.recovery import compare_receipt_recovery


AS_OF = "2026-10-09T00:00:00Z"
P1 = "2026-10-10T09:00:00Z"
P1_END = "2026-10-10T10:00:00Z"
P2 = P1_END
P2_END = "2026-10-10T11:00:00Z"


def case():
    snapshot = {
        "workspace_id": "W", "plan_version": "v7", "snapshot_id": "s1",
        "as_of": AS_OF, "synthetic": True,
        "inventory": [
            {"component_id": "C", "unit": "kg", "starting_stock": "2", "stock_as_of": AS_OF},
            {"component_id": "D", "unit": "litre", "starting_stock": "5", "stock_as_of": AS_OF},
        ],
        "products": [
            {"product_id": "A", "output_unit": "piece", "bom": [
                {"component_id": "C", "unit": "kg", "quantity_per_output_unit": "3"}]},
            {"product_id": "U", "output_unit": "piece", "bom": [
                {"component_id": "D", "unit": "litre", "quantity_per_output_unit": "2"}]},
        ],
        "routings": [
            {"product_id": "A", "resource_id": "R", "output_unit": "piece",
             "run_seconds_per_output_unit": "900"},
            {"product_id": "U", "resource_id": "R", "output_unit": "piece",
             "run_seconds_per_output_unit": "600"},
        ],
        "resources": [{"resource_id": "R", "capacity_model": "single_machine"}],
        "periods": [
            {"period_id": "P1", "resource_id": "R", "start": P1, "end": P1_END},
            {"period_id": "P2", "resource_id": "R", "start": P2, "end": P2_END},
        ],
        "maintenance": [],
        "receipts": [{"receipt_id": "r-C", "component_id": "C", "unit": "kg",
                      "quantity": "4", "arrival_at": P1}],
        "orders": [
            {"order_id": "O-A", "product_id": "A", "output_unit": "piece",
             "output_quantity": "2", "resource_id": "R", "due_at": P1_END},
            {"order_id": "O-B", "product_id": "A", "output_unit": "piece",
             "output_quantity": "1", "resource_id": "R", "due_at": P2_END},
            {"order_id": "O-U", "product_id": "U", "output_unit": "piece",
             "output_quantity": "1", "resource_id": "R", "due_at": P1_END},
        ],
        "baseline_assignments": [
            {"order_id": "O-A", "period_id": "P1"},
            {"order_id": "O-B", "period_id": "P2"},
            {"order_id": "O-U", "period_id": "P1"},
        ],
    }
    scenario = {
        "workspace_id": "W", "expected_plan_version": "v7", "source_snapshot_id": "s1",
        "scenario_id": "delay-1", "synthetic": True,
        "arrival_replacements": [
            {"receipt_id": "r-C", "expected_arrival_at": P1, "arrival_at": P2}],
    }
    recovery = {
        "workspace_id": "W", "expected_plan_version": "v7", "source_snapshot_id": "s1",
        "scenario_id": "delay-1", "synthetic": True,
        "assignments": [
            {"order_id": "O-A", "period_id": "P2"},
            {"order_id": "O-B", "period_id": "P2"},
            {"order_id": "O-U", "period_id": "P1"},
        ],
    }
    return snapshot, scenario, recovery


def order(schedule, order_id):
    return next(row for row in schedule["orders"] if row["order_id"] == order_id)


def demand(schedule, order_id):
    return order(schedule, order_id)["demands"][0]


def capacity(schedule, period_id):
    return next(row for row in schedule["capacity"] if row["period_id"] == period_id)


def unsupported(snapshot, scenario, recovery):
    result = compare_receipt_recovery(snapshot, scenario, recovery)
    assert result["status"] == "unsupported"
    assert "original_baseline" not in result
    assert "disrupted_baseline" not in result
    assert "recovery" not in result
    return result


def test_delay_recovery_preserves_original_and_shows_material_due_tradeoff():
    snapshot, scenario, recovery = case()
    result = compare_receipt_recovery(snapshot, scenario, recovery)
    assert result["status"] == "compared"
    assert result["synthetic"] is True
    assert result["as_of"] == "2026-10-09T00:00:00+00:00"
    assert result["receipt_changes"] == [{"receipt_id": "r-C",
        "before_arrival_at": "2026-10-10T09:00:00+00:00",
        "after_arrival_at": "2026-10-10T10:00:00+00:00"}]
    assert demand(result["original_baseline"], "O-A")["shortfall"] == "0"
    assert demand(result["disrupted_baseline"], "O-A")["shortfall"] == "4"
    assert demand(result["recovery"], "O-A")["shortfall"] == "0"
    assert order(result["recovery"], "O-A")["late_by_seconds"] == "3600"
    assert order(result["recovery"], "O-A")["modeled_due_pass"] is False
    assert capacity(result["original_baseline"], "P1")["workload_seconds"] == "2400"
    assert capacity(result["recovery"], "P2")["workload_seconds"] == "2700"
    assert result["potentially_exposed_order_ids"] == ["O-A", "O-B"]
    assert result["disruption_changed_order_ids"] == ["O-A", "O-B"]
    assert result["recovery_changed_order_ids"] == ["O-A", "O-B"]
    assert order(result["original_baseline"], "O-U") == order(result["recovery"], "O-U")
    assert result["modeled_checks"] and result["unmodeled_constraints"]


def test_shared_reservations_conserve_stock_and_unrelated_component_stays_separate():
    snapshot, scenario, recovery = case()
    result = compare_receipt_recovery(snapshot, scenario, recovery)
    assert demand(result["original_baseline"], "O-B")["shortfall"] == "3"
    assert demand(result["disrupted_baseline"], "O-B")["used"] == "3"
    assert demand(result["disrupted_baseline"], "O-B")["remaining_stock"] == "1"
    assert demand(result["recovery"], "O-B")["shortfall"] == "3"
    assert order(result["recovery"], "O-U")["demands"][0]["quantity"] == "2"
    assert order(result["recovery"], "O-U")["demands"][0]["unit"] == "litre"
    assert result["disrupted_baseline"]["material_balances"] == [
        {"component_id": "C", "unit": "kg", "starting_stock": "2", "ending_stock": "1"},
        {"component_id": "D", "unit": "litre", "starting_stock": "5", "ending_stock": "3"},
    ]


def test_maintenance_is_unchanged_and_workload_remains_assignment_specific():
    snapshot, scenario, recovery = case()
    snapshot["maintenance"] = [
        {"maintenance_id": "m1", "resource_id": "R", "start": P1,
         "end": "2026-10-10T09:30:00Z"},
        {"maintenance_id": "m2", "resource_id": "R",
         "start": "2026-10-10T09:15:00Z", "end": "2026-10-10T09:45:00Z"},
        {"maintenance_id": "m3", "resource_id": "R",
         "start": "2026-10-10T09:20:00Z", "end": "2026-10-10T09:25:00Z"},
        {"maintenance_id": "m4", "resource_id": "R", "start": P1_END,
         "end": "2026-10-10T10:15:00Z"},
    ]
    result = compare_receipt_recovery(snapshot, scenario, recovery)
    assert capacity(result["original_baseline"], "P1")["blocked_seconds"] == "2700"
    assert capacity(result["disrupted_baseline"], "P1")["available_seconds"] == "900"
    assert capacity(result["disrupted_baseline"], "P1")["workload_seconds"] == "2400"
    assert capacity(result["recovery"], "P1")["workload_seconds"] == "600"
    assert capacity(result["recovery"], "P2")["blocked_seconds"] == "900"


def test_empty_and_same_instant_replacements_are_noops_without_mutation():
    snapshot, scenario, recovery = case()
    scenario["arrival_replacements"] = []
    recovery["assignments"] = deepcopy(snapshot["baseline_assignments"])
    originals = deepcopy((snapshot, scenario, recovery))
    result = compare_receipt_recovery(snapshot, scenario, recovery)
    assert result["status"] == "compared"
    assert result["original_baseline"] == result["disrupted_baseline"] == result["recovery"]
    assert result["receipt_changes"] == []
    assert result["potentially_exposed_order_ids"] == []
    assert result["disruption_changed_order_ids"] == []
    assert result["recovery_changed_order_ids"] == []
    assert (snapshot, scenario, recovery) == originals
    scenario["arrival_replacements"] = [{"receipt_id": "r-C",
        "expected_arrival_at": "2026-10-10T14:30:00+05:30",
        "arrival_at": "2026-10-10T04:00:00-05:00"}]
    same = compare_receipt_recovery(snapshot, scenario, recovery)
    assert same["receipt_changes"] == []
    assert same["original_baseline"] == same["disrupted_baseline"] == same["recovery"]


def test_identical_original_receipt_duplicates_all_change_and_conflicts_reject():
    snapshot, scenario, recovery = case()
    equivalent = deepcopy(snapshot["receipts"][0])
    equivalent["quantity"] = "4.0"
    equivalent["arrival_at"] = "2026-10-10T14:30:00+05:30"
    snapshot["receipts"].append(equivalent)
    result = compare_receipt_recovery(snapshot, scenario, recovery)
    assert result["status"] == "compared"
    assert demand(result["disrupted_baseline"], "O-A")["shortfall"] == "4"
    assert demand(result["disrupted_baseline"], "O-B")["used"] == "3"
    snapshot["receipts"][1]["quantity"] = "5"
    unsupported(snapshot, scenario, recovery)


def test_recovery_derived_numeric_bound_rejects_without_partial_schedules():
    snapshot, scenario, recovery = case()
    huge = "9" * 1024
    snapshot["orders"][0]["output_quantity"] = huge
    snapshot["orders"][1]["output_quantity"] = huge
    snapshot["routings"][0]["run_seconds_per_output_unit"] = huge
    result = unsupported(snapshot, scenario, recovery)
    assert result["reason"] == "invalid_quantity"


def test_duplicate_unknown_and_old_time_mismatch_targets_reject_atomically():
    snapshot, scenario, recovery = case()
    scenario["arrival_replacements"].append(deepcopy(scenario["arrival_replacements"][0]))
    unsupported(snapshot, scenario, recovery)
    scenario["arrival_replacements"] = [{"receipt_id": "missing",
        "expected_arrival_at": P1, "arrival_at": P2}]
    unsupported(snapshot, scenario, recovery)
    scenario["arrival_replacements"][0]["receipt_id"] = "r-C"
    scenario["arrival_replacements"][0]["expected_arrival_at"] = P2
    unsupported(snapshot, scenario, recovery)


def test_invalid_date_scope_override_schema_and_incomplete_recovery_reject():
    variants = [
        ("scenario", "workspace_id", "other"),
        ("scenario", "expected_plan_version", "old"),
        ("scenario", "source_snapshot_id", "other"),
        ("scenario", "synthetic", False),
        ("recovery", "workspace_id", "other"),
        ("recovery", "expected_plan_version", "old"),
        ("recovery", "source_snapshot_id", "other"),
        ("recovery", "scenario_id", "other"),
        ("recovery", "synthetic", False),
        ("snapshot", "synthetic", False),
    ]
    for where, key, value in variants:
        snapshot, scenario, recovery = case()
        {"snapshot": snapshot, "scenario": scenario, "recovery": recovery}[where][key] = value
        unsupported(snapshot, scenario, recovery)
    for key, value in (("expected_arrival_at", "2026-10-10T09:00:00+00:99"),
                       ("arrival_at", "2026-10-10T10:00:00+00:99"),
                       ("arrival_at", "2026-10-08T23:59:59Z"),
                       ("arrival_at", "2026-10-10T10:00:00.0000001Z")):
        snapshot, scenario, recovery = case()
        scenario["arrival_replacements"][0][key] = value
        unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    recovery["assignments"].pop()
    unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    scenario["arrival_replacements"][0]["quantity"] = "1"
    unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    scenario["arrival_replacements"][0]["component_id"] = "D"
    unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    scenario["extra_constraint"] = "0"
    unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    scenario[47] = "bad"
    unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    recovery["quantity"] = "8"
    unsupported(snapshot, scenario, recovery)
    snapshot, scenario, recovery = case()
    snapshot["receipts"][0]["quantity"] = "9" * 5000
    assert unsupported(snapshot, scenario, recovery)["reason"] == "invalid_quantity"


def test_diagnostics_do_not_echo_caller_identifiers():
    snapshot, scenario, recovery = case()
    secret = "private-receipt-" * 100
    scenario["arrival_replacements"][0]["receipt_id"] = secret
    result = unsupported(snapshot, scenario, recovery)
    assert secret not in str(result)


def test_permutations_and_multiple_period_crossings_are_deterministic():
    snapshot, scenario, recovery = case()
    snapshot["receipts"].append({"receipt_id": "r-D", "component_id": "D",
        "unit": "litre", "quantity": "1", "arrival_at": P2})
    scenario["arrival_replacements"].append({"receipt_id": "r-D",
        "expected_arrival_at": P2, "arrival_at": P2_END})
    expected = compare_receipt_recovery(snapshot, scenario, recovery)
    assert expected["status"] == "compared"
    assert expected["potentially_exposed_order_ids"] == ["O-A", "O-B", "O-U"]
    assert expected["disruption_changed_order_ids"] == ["O-A", "O-B"]
    assert [row["receipt_id"] for row in expected["receipt_changes"]] == ["r-C", "r-D"]
    snapshot["receipts"].reverse()
    snapshot["orders"].reverse()
    snapshot["baseline_assignments"].reverse()
    recovery["assignments"].reverse()
    scenario["arrival_replacements"].reverse()
    assert compare_receipt_recovery(snapshot, scenario, recovery) == expected


def test_fractional_public_screen_under_low_decimal_context():
    snapshot, scenario, recovery = case()
    snapshot["inventory"][0]["starting_stock"] = "0.02"
    snapshot["receipts"][0]["quantity"] = "0.01"
    snapshot["products"][0]["bom"][0]["quantity_per_output_unit"] = "0.1"
    snapshot["routings"][0]["run_seconds_per_output_unit"] = "0.3"
    snapshot["orders"][0]["output_quantity"] = "0.2"
    snapshot["periods"][0]["end"] = "2026-10-10T09:00:01.000001Z"
    snapshot["periods"][1]["start"] = snapshot["periods"][0]["end"]
    snapshot["orders"][0]["due_at"] = snapshot["periods"][0]["end"]
    scenario["arrival_replacements"][0]["arrival_at"] = snapshot["periods"][1]["start"]
    prior = getcontext().prec
    try:
        getcontext().prec = 3
        result = compare_receipt_recovery(snapshot, scenario, recovery)
    finally:
        getcontext().prec = prior
    assert result["status"] == "compared"
    assert demand(result["original_baseline"], "O-A")["quantity"] == "0.02"
    assert capacity(result["original_baseline"], "P1")["nominal_seconds"] == "1.000001"
    assert capacity(result["original_baseline"], "P1")["workload_seconds"] == "600.06"


def test_public_mapping_and_tuple_inputs_compare_without_mutation():
    from types import MappingProxyType

    snapshot, scenario, recovery = case()
    original = deepcopy((snapshot, scenario, recovery))
    snapshot["receipts"] = tuple(MappingProxyType(row) for row in snapshot["receipts"])
    snapshot["baseline_assignments"] = tuple(snapshot["baseline_assignments"])
    scenario["arrival_replacements"] = tuple(
        MappingProxyType(row) for row in scenario["arrival_replacements"])
    recovery["assignments"] = tuple(recovery["assignments"])
    result = compare_receipt_recovery(MappingProxyType(snapshot),
                                      MappingProxyType(scenario), MappingProxyType(recovery))
    assert result["status"] == "compared"
    assert demand(result["disrupted_baseline"], "O-A")["shortfall"] == "4"
    assert snapshot["receipts"][0]["arrival_at"] == original[0]["receipts"][0]["arrival_at"]
