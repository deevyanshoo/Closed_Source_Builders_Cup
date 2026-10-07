"""Synthetic hand calculations for the pure production-period material screen."""
from __future__ import annotations

from procure.production import screen_production_schedules


AS_OF = "2026-10-09T00:00:00Z"
P1_START = "2026-10-10T09:00:00Z"
P1_END = "2026-10-10T10:00:00Z"
P2_START = P1_END
P2_END = "2026-10-10T11:00:00Z"


def case():
    snapshot = {
        "workspace_id": "W-A", "plan_version": "v7", "snapshot_id": "snap-1",
        "as_of": AS_OF, "synthetic": True,
        "inventory": [{"component_id": "C", "unit": "kg", "starting_stock": "2",
                       "stock_as_of": AS_OF}],
        "products": [{"product_id": "A", "output_unit": "piece",
                      "bom": [{"component_id": "C", "unit": "kg", "quantity_per_output_unit": "3"}]}],
        "routings": [{"product_id": "A", "resource_id": "R", "output_unit": "piece",
                      "run_seconds_per_output_unit": "900"}],
        "resources": [{"resource_id": "R", "capacity_model": "single_machine"}],
        "periods": [{"period_id": "P1", "resource_id": "R", "start": P1_START, "end": P1_END},
                    {"period_id": "P2", "resource_id": "R", "start": P2_START, "end": P2_END}],
        "maintenance": [],
        "receipts": [{"receipt_id": "rec-1", "component_id": "C", "unit": "kg",
                      "quantity": "4", "arrival_at": P1_START}],
        "orders": [{"order_id": "O-A", "product_id": "A", "output_unit": "piece",
                    "output_quantity": "2", "resource_id": "R", "due_at": P1_END}],
        "baseline_assignments": [{"order_id": "O-A", "period_id": "P1"}],
    }
    alternative = {"workspace_id": "W-A", "expected_plan_version": "v7",
                   "source_snapshot_id": "snap-1", "scenario_id": "alt-1",
                   "synthetic": True,
                   "assignments": [{"order_id": "O-A", "period_id": "P1"}]}
    return snapshot, alternative


def order(result, schedule="baseline", order_id="O-A"):
    return next(row for row in result[schedule]["orders"] if row["order_id"] == order_id)


def capacity(result, schedule="baseline", period_id="P1"):
    return next(row for row in result[schedule]["capacity"] if row["period_id"] == period_id)


def test_receipt_at_period_start_covers_bom_and_exact_capacity():
    snapshot, alternative = case()
    result = screen_production_schedules(snapshot, alternative)
    assert result["status"] == "screened"
    assert order(result)["demands"][0]["quantity"] == "6"
    assert order(result)["demands"][0]["shortfall"] == "0"
    assert capacity(result)["nominal_seconds"] == "3600"
    assert capacity(result)["workload_seconds"] == "1800"
    assert capacity(result)["remaining_seconds"] == "1800"
    assert result["baseline"] == result["alternative"]


def unsupported(snapshot, alternative, reason):
    result = screen_production_schedules(snapshot, alternative)
    assert result["status"] == "unsupported"
    assert result["reason"] == reason
    assert "baseline" not in result and "alternative" not in result


def test_delayed_receipt_and_supplied_reassignment_keep_due_risk_separate():
    snapshot, alternative = case()
    snapshot["receipts"][0]["arrival_at"] = P2_START
    alternative["assignments"][0]["period_id"] = "P2"
    result = screen_production_schedules(snapshot, alternative)
    assert order(result)["demands"][0]["shortfall"] == "4"
    assert order(result, "alternative")["demands"][0]["shortfall"] == "0"
    assert order(result, "alternative")["modeled_completion_at"] == P2_END.replace("Z", "+00:00")
    assert order(result, "alternative")["modeled_due_pass"] is False
    assert order(result, "alternative")["late_by_seconds"] == "3600"
    assert capacity(result, "alternative", "P2")["workload_seconds"] == "1800"
    assert result["alternative"]["modeled_checks_pass"] is False


def test_maintenance_union_nesting_duplicates_crossing_and_full_outage():
    snapshot, alternative = case()
    snapshot["maintenance"] = [
        {"maintenance_id": "m1", "resource_id": "R", "start": P1_START,
         "end": "2026-10-10T09:30:00Z"},
        {"maintenance_id": "m2", "resource_id": "R", "start": "2026-10-10T09:15:00Z",
         "end": "2026-10-10T09:45:00Z"},
        {"maintenance_id": "m3", "resource_id": "R", "start": "2026-10-10T09:20:00Z",
         "end": "2026-10-10T09:25:00Z"},
        {"maintenance_id": "m4", "resource_id": "R", "start": P1_END,
         "end": "2026-10-10T10:15:00Z"},
    ]
    snapshot["maintenance"].append(dict(snapshot["maintenance"][0]))
    result = screen_production_schedules(snapshot, alternative)
    p1 = capacity(result)
    assert (p1["blocked_seconds"], p1["available_seconds"], p1["excess_seconds"]) == (
        "2700", "900", "900")
    assert capacity(result, period_id="P2")["blocked_seconds"] == "900"
    assert p1["capacity_pass"] is False
    snapshot["maintenance"].append({"maintenance_id": "m5", "resource_id": "R",
                                    "start": "2026-10-10T09:45:00Z", "end": P1_END})
    full = screen_production_schedules(snapshot, alternative)
    assert capacity(full)["available_seconds"] == "0"
    assert capacity(full)["excess_seconds"] == "1800"


def test_cross_period_maintenance_is_clipped_and_endpoint_contact_is_half_open():
    snapshot, alternative = case()
    snapshot["maintenance"] = [
        {"maintenance_id": "before", "resource_id": "R", "start": "2026-10-10T08:00:00Z",
         "end": P1_START},
        {"maintenance_id": "cross", "resource_id": "R", "start": "2026-10-10T09:30:00Z",
         "end": "2026-10-10T10:30:00Z"},
        {"maintenance_id": "after", "resource_id": "R", "start": P2_END,
         "end": "2026-10-10T12:00:00Z"},
    ]
    result = screen_production_schedules(snapshot, alternative)
    assert capacity(result)["blocked_seconds"] == "1800"
    assert capacity(result, period_id="P2")["blocked_seconds"] == "1800"


def test_two_orders_share_stock_and_capacity_without_reuse_or_backlog():
    snapshot, alternative = case()
    snapshot["orders"].append({"order_id": "O-B", "product_id": "A", "output_unit": "piece",
                               "output_quantity": "1", "resource_id": "R", "due_at": P2_END})
    snapshot["baseline_assignments"].append({"order_id": "O-B", "period_id": "P1"})
    alternative["assignments"].append({"order_id": "O-B", "period_id": "P1"})
    result = screen_production_schedules(snapshot, alternative)
    assert [row["order_id"] for row in result["baseline"]["orders"]] == ["O-A", "O-B"]
    assert order(result, order_id="O-A")["demands"][0]["shortfall"] == "0"
    assert order(result, order_id="O-B")["demands"][0]["shortfall"] == "3"
    assert order(result, order_id="O-B")["demands"][0]["remaining_stock"] == "0"
    assert capacity(result)["workload_seconds"] == "2700"
    assert result["baseline"]["material_balances"][0]["ending_stock"] == "0"
    alternative["assignments"][1]["period_id"] = "P2"
    snapshot["receipts"].append({"receipt_id": "rec-2", "component_id": "C", "unit": "kg",
                                 "quantity": "3", "arrival_at": P2_START})
    moved = screen_production_schedules(snapshot, alternative)
    assert order(moved, "alternative", "O-B")["demands"][0]["shortfall"] == "0"


def test_fractional_bom_workload_and_microsecond_duration_are_exact():
    from decimal import getcontext

    snapshot, alternative = case()
    snapshot["inventory"][0]["starting_stock"] = "0.02"
    snapshot["receipts"] = []
    snapshot["products"][0]["bom"][0]["quantity_per_output_unit"] = "0.1"
    snapshot["routings"][0]["run_seconds_per_output_unit"] = "0.3"
    snapshot["orders"][0]["output_quantity"] = "0.2"
    snapshot["periods"][0]["end"] = "2026-10-10T09:00:01.000001Z"
    snapshot["periods"][1]["start"] = "2026-10-10T09:00:01.000001Z"
    snapshot["orders"][0]["due_at"] = snapshot["periods"][0]["end"]
    context = getcontext()
    previous_precision = context.prec
    try:
        context.prec = 3
        result = screen_production_schedules(snapshot, alternative)
    finally:
        context.prec = previous_precision
    assert order(result)["demands"][0]["quantity"] == "0.02"
    assert order(result)["demands"][0]["shortfall"] == "0"
    assert capacity(result)["nominal_seconds"] == "1.000001"
    assert capacity(result)["workload_seconds"] == "0.06"
    assert capacity(result)["remaining_seconds"] == "0.940001"
    assert order(result)["modeled_due_pass"] is True


def test_receipt_within_period_cannot_cure_period_start_shortage():
    snapshot, alternative = case()
    snapshot["receipts"][0]["arrival_at"] = "2026-10-10T09:00:00.000001Z"
    result = screen_production_schedules(snapshot, alternative)
    assert order(result)["demands"][0]["shortfall"] == "4"
    assert result["baseline"]["material_balances"][0]["ending_stock"] == "4"
    assert result["baseline"]["material_balance_end_at"] == (
        "2026-10-10T09:00:00.000001+00:00")


def test_scope_and_pre_asof_boundaries_reject_without_numbers():
    from copy import deepcopy

    for key, value, reason in (
        ("workspace_id", "W-B", "workspace_mismatch"),
        ("expected_plan_version", "v6", "stale_plan_version"),
        ("source_snapshot_id", "other", "snapshot_mismatch"),
        ("synthetic", False, "invalid_scope"),
    ):
        snapshot, alternative = case()
        alternative[key] = value
        unsupported(snapshot, alternative, reason)
    snapshot, alternative = case()
    snapshot["inventory"][0]["stock_as_of"] = "2026-10-08T23:59:59Z"
    unsupported(snapshot, alternative, "invalid_snapshot")
    snapshot, alternative = case()
    snapshot["receipts"][0]["arrival_at"] = "2026-10-08T23:59:59Z"
    unsupported(snapshot, alternative, "pre_as_of")
    snapshot, alternative = case()
    snapshot["periods"][0]["start"] = "2026-10-08T23:59:59Z"
    unsupported(snapshot, alternative, "pre_as_of")
    snapshot, alternative = case()
    snapshot["as_of"] = "0001-01-01T00:00:00+01:00"
    unsupported(snapshot, alternative, "invalid_timestamp")


def test_complete_declarations_reject_unused_invalid_product_routing_and_order():
    snapshot, alternative = case()
    snapshot["products"].append({"product_id": "unused", "output_unit": "piece", "bom": []})
    unsupported(snapshot, alternative, "incomplete_bom")
    snapshot, alternative = case()
    snapshot["routings"].append({"product_id": "ghost", "resource_id": "R",
                                 "output_unit": "piece", "run_seconds_per_output_unit": "1"})
    unsupported(snapshot, alternative, "incomplete_routing")
    snapshot, alternative = case()
    snapshot["orders"].append({"order_id": "O-B", "product_id": "A", "output_unit": "piece",
                               "output_quantity": "1", "resource_id": "R", "due_at": P2_END})
    unsupported(snapshot, alternative, "incomplete_assignment")


def test_period_resource_and_maintenance_references_reject():
    snapshot, alternative = case()
    snapshot["periods"][1]["start"] = "2026-10-10T09:30:00Z"
    unsupported(snapshot, alternative, "overlapping_periods")
    snapshot, alternative = case()
    snapshot["resources"].append({"resource_id": "R2", "capacity_model": "single_machine"})
    snapshot["periods"][1]["resource_id"] = "R2"
    alternative["assignments"][0]["period_id"] = "P2"
    unsupported(snapshot, alternative, "resource_mismatch")
    snapshot, alternative = case()
    snapshot["maintenance"].append({"maintenance_id": "bad", "resource_id": "unknown",
                                    "start": P1_START, "end": P1_END})
    unsupported(snapshot, alternative, "incomplete_resource")


def test_unknown_constraints_units_duplicate_conflicts_and_invalid_numbers_reject():
    snapshot, alternative = case()
    snapshot["cash_limit"] = "100"
    unsupported(snapshot, alternative, "unsupported_constraint")
    snapshot, alternative = case()
    snapshot["products"][0]["bom"][0]["unit"] = "tonne"
    unsupported(snapshot, alternative, "incompatible_unit")
    snapshot, alternative = case()
    snapshot["orders"].append({**snapshot["orders"][0], "output_quantity": "3"})
    unsupported(snapshot, alternative, "conflicting_snapshot")
    for invalid in (0.2, "-1", "NaN", "1e3", "0", "9" * 5000):
        snapshot, alternative = case()
        snapshot["orders"][0]["output_quantity"] = invalid
        unsupported(snapshot, alternative, "invalid_quantity")
    snapshot, alternative = case()
    snapshot["receipts"][0]["quantity"] = "0"
    assert screen_production_schedules(snapshot, alternative)["status"] == "screened"


def test_noop_duplicates_reordering_and_input_immutability():
    from copy import deepcopy

    snapshot, alternative = case()
    original_snapshot, original_alternative = deepcopy(snapshot), deepcopy(alternative)
    expected = screen_production_schedules(snapshot, alternative)
    assert expected["status"] == "screened"
    assert snapshot == original_snapshot and alternative == original_alternative
    snapshot["receipts"].append(deepcopy(snapshot["receipts"][0]))
    snapshot["baseline_assignments"].append(deepcopy(snapshot["baseline_assignments"][0]))
    alternative["assignments"].append(deepcopy(alternative["assignments"][0]))
    for key in ("inventory", "products", "routings", "resources", "periods",
                "maintenance", "receipts", "orders", "baseline_assignments"):
        snapshot[key].reverse()
    alternative["assignments"].reverse()
    assert screen_production_schedules(snapshot, alternative) == expected
    snapshot["receipts"].append({**snapshot["receipts"][0], "quantity": "5"})
    unsupported(snapshot, alternative, "conflicting_snapshot")


def test_int_guard_and_large_exact_values_have_bounded_behavior():
    import sys

    snapshot, alternative = case()
    snapshot["inventory"][0]["starting_stock"] = "9" * 1024
    snapshot["receipts"] = []
    snapshot["orders"][0]["output_quantity"] = "1"
    snapshot["products"][0]["bom"][0]["quantity_per_output_unit"] = "1"
    prior = sys.get_int_max_str_digits() if hasattr(sys, "get_int_max_str_digits") else None
    try:
        if prior is not None:
            sys.set_int_max_str_digits(640)
        result = screen_production_schedules(snapshot, alternative)
        assert result["status"] == "screened"
        assert result["baseline"]["material_balances"][0]["ending_stock"] == (
            "9" * 1023 + "8")
    finally:
        if prior is not None:
            sys.set_int_max_str_digits(prior)


def test_malformed_timezone_offset_rejects_before_stdlib_normalization():
    for bad in ("2026-10-09T00:00:00+00:99", "2026-10-09T00:00:00+24:00"):
        snapshot, alternative = case()
        snapshot["as_of"] = bad
        snapshot["inventory"][0]["stock_as_of"] = bad
        unsupported(snapshot, alternative, "invalid_timestamp")


def test_malformed_mapping_keys_reject_without_echoing_raw_unknown_fields():
    snapshot, alternative = case()
    secret_key = "private-input-" * 100
    snapshot[42] = "hidden"
    snapshot[secret_key] = "hidden"
    result = screen_production_schedules(snapshot, alternative)
    assert result["status"] == "unsupported" and result["reason"] == "unsupported_constraint"
    assert secret_key not in str(result)
    snapshot, alternative = case()
    snapshot["inventory"][0][42] = "hidden"
    snapshot["inventory"][0][secret_key] = "hidden"
    result = screen_production_schedules(snapshot, alternative)
    assert result["status"] == "unsupported" and result["reason"] == "unsupported_constraint"
    assert secret_key not in str(result)


def test_two_resources_share_component_stock_but_keep_capacity_separate():
    snapshot, alternative = case()
    snapshot["resources"].append({"resource_id": "R2", "capacity_model": "single_machine"})
    snapshot["periods"].append({"period_id": "Q1", "resource_id": "R2",
                                "start": P1_START, "end": P1_END})
    snapshot["routings"].append({"product_id": "A", "resource_id": "R2",
                                 "output_unit": "piece", "run_seconds_per_output_unit": "900"})
    snapshot["orders"].append({"order_id": "O-B", "product_id": "A",
                               "output_unit": "piece", "output_quantity": "1",
                               "resource_id": "R2", "due_at": P1_END})
    snapshot["baseline_assignments"].append({"order_id": "O-B", "period_id": "Q1"})
    alternative["assignments"].append({"order_id": "O-B", "period_id": "Q1"})
    result = screen_production_schedules(snapshot, alternative)
    assert order(result, order_id="O-A")["demands"][0]["shortfall"] == "0"
    assert order(result, order_id="O-B")["demands"][0]["shortfall"] == "3"
    assert capacity(result, period_id="P1")["workload_seconds"] == "1800"
    assert capacity(result, period_id="Q1")["workload_seconds"] == "900"


def test_overdue_due_is_reported_independently_of_material_and_capacity():
    snapshot, alternative = case()
    snapshot["orders"][0]["due_at"] = P1_START
    result = screen_production_schedules(snapshot, alternative)
    assert order(result)["material_pass"] is True
    assert capacity(result)["capacity_pass"] is True
    assert order(result)["modeled_due_pass"] is False
    assert order(result)["late_by_seconds"] == "3600"


def test_submicrosecond_timestamp_rejects_instead_of_truncating():
    snapshot, alternative = case()
    snapshot["periods"][0]["end"] = "2026-10-10T10:00:00.0000001Z"
    unsupported(snapshot, alternative, "invalid_timestamp")


def test_derived_output_coefficient_bound_rejects_without_partial_screen():
    snapshot, alternative = case()
    huge = "9" * 1024
    snapshot["orders"][0]["output_quantity"] = huge
    snapshot["routings"][0]["run_seconds_per_output_unit"] = huge
    snapshot["orders"].append({"order_id": "O-B", "product_id": "A", "output_unit": "piece",
                               "output_quantity": huge, "resource_id": "R", "due_at": P1_END})
    snapshot["baseline_assignments"].append({"order_id": "O-B", "period_id": "P1"})
    alternative["assignments"].append({"order_id": "O-B", "period_id": "P1"})
    unsupported(snapshot, alternative, "invalid_quantity")


def test_unsupported_diagnostics_do_not_echo_large_caller_identifier():
    snapshot, alternative = case()
    secret = "private-order-" * 100
    snapshot["orders"][0]["order_id"] = secret
    snapshot["baseline_assignments"][0]["order_id"] = secret
    alternative["assignments"][0]["order_id"] = secret
    alternative["assignments"][0]["period_id"] = "missing-period"
    result = screen_production_schedules(snapshot, alternative)
    assert result["status"] == "unsupported"
    assert secret not in str(result)


def test_multicomponent_shortfall_keeps_other_component_reserved_and_load_counted():
    snapshot, alternative = case()
    snapshot["inventory"].append({"component_id": "D", "unit": "litre",
                                  "starting_stock": "1", "stock_as_of": AS_OF})
    snapshot["products"][0]["bom"].append({"component_id": "D", "unit": "litre",
                                           "quantity_per_output_unit": "1"})
    snapshot["orders"].append({"order_id": "O-B", "product_id": "A", "output_unit": "piece",
                               "output_quantity": "1", "resource_id": "R", "due_at": P1_END})
    snapshot["baseline_assignments"].append({"order_id": "O-B", "period_id": "P1"})
    alternative["assignments"].append({"order_id": "O-B", "period_id": "P1"})
    result = screen_production_schedules(snapshot, alternative)
    assert result["status"] == "screened"
    first = {d["component_id"]: d for d in order(result, order_id="O-A")["demands"]}
    second = {d["component_id"]: d for d in order(result, order_id="O-B")["demands"]}
    assert (first["C"]["unit"], first["C"]["used"], first["C"]["shortfall"]) == (
        "kg", "6", "0")
    assert (first["D"]["unit"], first["D"]["used"], first["D"]["shortfall"]) == (
        "litre", "1", "1")
    assert (second["C"]["unit"], second["C"]["used"], second["C"]["shortfall"]) == (
        "kg", "0", "3")
    assert (second["D"]["unit"], second["D"]["used"], second["D"]["shortfall"]) == (
        "litre", "0", "1")
    assert capacity(result)["workload_seconds"] == "2700"
    assert order(result, order_id="O-A")["modeled_due_pass"] is True
    assert order(result, order_id="O-B")["modeled_due_pass"] is True
    assert result["baseline"]["material_balances"] == [
        {"component_id": "C", "unit": "kg", "starting_stock": "2", "ending_stock": "0"},
        {"component_id": "D", "unit": "litre", "starting_stock": "1", "ending_stock": "0"},
    ]
    assert result["baseline"]["modeled_checks_pass"] is False
