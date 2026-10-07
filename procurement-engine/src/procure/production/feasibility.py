"""Pure exact material, single-machine capacity and modeled-due schedule screen."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from fractions import Fraction

from ._exact import ScreenError, add, amount, instant, iso, multiply, seconds, subtract, text

_SNAPSHOT_FIELDS = {"workspace_id", "plan_version", "snapshot_id", "as_of", "synthetic",
                    "inventory", "products", "routings", "resources", "periods", "maintenance",
                    "receipts", "orders", "baseline_assignments"}
_ALTERNATIVE_FIELDS = {"workspace_id", "expected_plan_version", "source_snapshot_id",
                       "scenario_id", "synthetic", "assignments"}
_MODELED = ("material_at_period_start", "aggregate_single_machine_capacity", "modeled_period_end_due")
_UNMODELED = (
    "Setup/changeover and intra-period sequencing are not modeled.",
    "Work in process, parallel lanes beyond the declared single machine, and multistage BOM/routing are not modeled.",
    "Cash, alternate supply, optimization, dispatch and customer promises are not modeled.",
    "Ingestion, permissions, persisted approval and replay are not modeled.",
)


def _record(raw: object, keys: set[str], reason: str) -> Mapping:
    if not isinstance(raw, Mapping):
        raise ScreenError(reason)
    if any(not isinstance(key, str) for key in raw) or set(raw) - keys:
        raise ScreenError("unsupported_constraint")
    if keys - set(raw):
        raise ScreenError(reason, *sorted(keys - set(raw)))
    return raw


def _rows(raw: object, reason: str, *, nonempty: bool = False) -> list:
    if not isinstance(raw, (list, tuple)) or (nonempty and not raw):
        raise ScreenError(reason)
    return list(raw)


def _id(raw: object, reason: str) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ScreenError(reason)
    return raw


def _put(table: dict, key: object, value: object) -> None:
    if key in table and table[key] != value:
        raise ScreenError("conflicting_snapshot", str(key))
    table[key] = value


def _scope(snapshot: Mapping, alternative: Mapping) -> datetime:
    _record(snapshot, _SNAPSHOT_FIELDS, "incomplete_snapshot")
    _record(alternative, _ALTERNATIVE_FIELDS, "incomplete_assignment")
    _id(snapshot["workspace_id"], "invalid_scope")
    _id(snapshot["plan_version"], "invalid_scope")
    _id(snapshot["snapshot_id"], "invalid_scope")
    _id(alternative["scenario_id"], "invalid_scope")
    if snapshot["workspace_id"] != alternative["workspace_id"]:
        raise ScreenError("workspace_mismatch")
    if snapshot["plan_version"] != alternative["expected_plan_version"]:
        raise ScreenError("stale_plan_version")
    if snapshot["snapshot_id"] != alternative["source_snapshot_id"]:
        raise ScreenError("snapshot_mismatch")
    if snapshot["synthetic"] is not True or alternative["synthetic"] is not True:
        raise ScreenError("invalid_scope")
    return instant(snapshot["as_of"])


def _normalize(snapshot: Mapping, alternative: Mapping, as_of: datetime):
    inventory = {}
    for raw in _rows(snapshot["inventory"], "incomplete_inventory", nonempty=True):
        row = _record(raw, {"component_id", "unit", "starting_stock", "stock_as_of"},
                      "incomplete_inventory")
        component = _id(row["component_id"], "incomplete_inventory")
        unit = _id(row["unit"], "incomplete_inventory")
        if instant(row["stock_as_of"]) != as_of:
            raise ScreenError("invalid_snapshot", component)
        _put(inventory, component, (unit, amount(row["starting_stock"])))

    products = {}
    for raw in _rows(snapshot["products"], "incomplete_bom", nonempty=True):
        row = _record(raw, {"product_id", "output_unit", "bom"}, "incomplete_bom")
        product = _id(row["product_id"], "incomplete_bom")
        output_unit = _id(row["output_unit"], "incomplete_bom")
        bom = {}
        for item in _rows(row["bom"], "incomplete_bom", nonempty=True):
            line = _record(item, {"component_id", "unit", "quantity_per_output_unit"},
                           "incomplete_bom")
            component = _id(line["component_id"], "incomplete_bom")
            unit = _id(line["unit"], "incomplete_bom")
            if component not in inventory:
                raise ScreenError("incomplete_bom", component)
            if unit != inventory[component][0]:
                raise ScreenError("incompatible_unit", component)
            _put(bom, component, (unit, amount(line["quantity_per_output_unit"], positive=True)))
        _put(products, product, (output_unit, bom))
    if set(products) & set(inventory):
        raise ScreenError("incomplete_bom", *sorted(set(products) & set(inventory)))

    resources = {}
    for raw in _rows(snapshot["resources"], "incomplete_resource", nonempty=True):
        row = _record(raw, {"resource_id", "capacity_model"}, "incomplete_resource")
        resource = _id(row["resource_id"], "incomplete_resource")
        if row["capacity_model"] != "single_machine":
            raise ScreenError("unsupported_constraint", resource)
        _put(resources, resource, "single_machine")

    routings = {}
    for raw in _rows(snapshot["routings"], "incomplete_routing", nonempty=True):
        row = _record(raw, {"product_id", "resource_id", "output_unit",
                            "run_seconds_per_output_unit"}, "incomplete_routing")
        product = _id(row["product_id"], "incomplete_routing")
        resource = _id(row["resource_id"], "incomplete_routing")
        unit = _id(row["output_unit"], "incomplete_routing")
        if product not in products or resource not in resources or unit != products[product][0]:
            raise ScreenError("incomplete_routing", product, resource)
        _put(routings, (product, resource),
             (unit, amount(row["run_seconds_per_output_unit"], positive=True)))
    if any(not any(key[0] == product for key in routings) for product in products):
        raise ScreenError("incomplete_routing")

    periods = {}
    for raw in _rows(snapshot["periods"], "incomplete_period", nonempty=True):
        row = _record(raw, {"period_id", "resource_id", "start", "end"}, "incomplete_period")
        period = _id(row["period_id"], "incomplete_period")
        resource = _id(row["resource_id"], "incomplete_period")
        if resource not in resources:
            raise ScreenError("incomplete_resource", resource)
        start, end = instant(row["start"]), instant(row["end"])
        if start < as_of:
            raise ScreenError("pre_as_of", period)
        if start >= end:
            raise ScreenError("invalid_period", period)
        _put(periods, period, (resource, start, end))
    for resource in resources:
        resource_periods = sorted((start, end, period) for period, (res, start, end)
                                  in periods.items() if res == resource)
        if not resource_periods:
            raise ScreenError("incomplete_period", resource)
        for previous, current in zip(resource_periods, resource_periods[1:]):
            if previous[1] > current[0]:
                raise ScreenError("overlapping_periods", previous[2], current[2])

    maintenance = {}
    for raw in _rows(snapshot["maintenance"], "incomplete_maintenance"):
        row = _record(raw, {"maintenance_id", "resource_id", "start", "end"},
                      "incomplete_maintenance")
        maintenance_id = _id(row["maintenance_id"], "incomplete_maintenance")
        resource = _id(row["resource_id"], "incomplete_maintenance")
        if resource not in resources:
            raise ScreenError("incomplete_resource", resource)
        start, end = instant(row["start"]), instant(row["end"])
        if start >= end:
            raise ScreenError("invalid_period", maintenance_id)
        _put(maintenance, maintenance_id, (resource, start, end))

    receipts = {}
    for raw in _rows(snapshot["receipts"], "incomplete_receipt"):
        row = _record(raw, {"receipt_id", "component_id", "unit", "quantity", "arrival_at"},
                      "incomplete_receipt")
        receipt = _id(row["receipt_id"], "incomplete_receipt")
        component = _id(row["component_id"], "incomplete_receipt")
        unit = _id(row["unit"], "incomplete_receipt")
        if component not in inventory:
            raise ScreenError("incomplete_receipt", component)
        if unit != inventory[component][0]:
            raise ScreenError("incompatible_unit", component)
        arrival = instant(row["arrival_at"])
        if arrival < as_of:
            raise ScreenError("pre_as_of", receipt)
        _put(receipts, receipt, (component, unit, amount(row["quantity"]), arrival))

    orders = {}
    for raw in _rows(snapshot["orders"], "incomplete_order", nonempty=True):
        row = _record(raw, {"order_id", "product_id", "output_unit", "output_quantity",
                            "resource_id", "due_at"}, "incomplete_order")
        order = _id(row["order_id"], "incomplete_order")
        product = _id(row["product_id"], "incomplete_order")
        unit = _id(row["output_unit"], "incomplete_order")
        resource = _id(row["resource_id"], "incomplete_order")
        if product not in products or (product, resource) not in routings:
            raise ScreenError("incomplete_routing", order)
        if unit != products[product][0]:
            raise ScreenError("incompatible_unit", order)
        _put(orders, order, (product, unit, amount(row["output_quantity"], positive=True),
                             resource, instant(row["due_at"])))

    baseline = _assignments(snapshot["baseline_assignments"], orders, periods)
    candidate = _assignments(alternative["assignments"], orders, periods)
    return inventory, products, routings, resources, periods, maintenance, receipts, orders, baseline, candidate


def _assignments(raw, orders, periods):
    assigned = {}
    for item in _rows(raw, "incomplete_assignment", nonempty=True):
        row = _record(item, {"order_id", "period_id"}, "incomplete_assignment")
        order, period = _id(row["order_id"], "incomplete_assignment"), _id(row["period_id"], "incomplete_assignment")
        if order not in orders or period not in periods:
            raise ScreenError("incomplete_assignment", order, period)
        if periods[period][0] != orders[order][3]:
            raise ScreenError("resource_mismatch", order, period)
        _put(assigned, order, period)
    if set(assigned) != set(orders):
        raise ScreenError("incomplete_assignment", *sorted(set(orders) ^ set(assigned)))
    return assigned


def _blocked(resource: str, period: tuple, maintenance: dict) -> Fraction:
    _, start, end = period
    clipped = []
    for other_resource, outage_start, outage_end in maintenance.values():
        if other_resource == resource:
            left, right = max(start, outage_start), min(end, outage_end)
            if left < right:
                clipped.append((left, right))
    clipped.sort()
    merged = []
    for left, right in clipped:
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    blocked = Fraction(0)
    for left, right in merged:
        blocked = add(blocked, seconds(left, right))
    return blocked


def _schedule(data, assignments):
    inventory, products, routings, resources, periods, maintenance, receipts, orders = data
    demanded = {}
    for order_id, (product, unit, quantity, resource, due) in orders.items():
        demands = {}
        for component, (component_unit, rate) in products[product][1].items():
            demands[component] = (component_unit, multiply(quantity, rate))
        demanded[order_id] = demands
    material_rows = {}
    balances = []
    end_at = max([at for _, _, _, at in receipts.values()] +
                 [periods[assignments[order]][1] for order in orders])
    for component, (unit, opening) in sorted(inventory.items()):
        events = []
        for receipt_id, (receipt_component, _, quantity, at) in receipts.items():
            if receipt_component == component:
                events.append((at, 0, receipt_id, quantity))
        for order_id in orders:
            if component in demanded[order_id]:
                events.append((periods[assignments[order_id]][1], 1, order_id,
                               demanded[order_id][component][1]))
        events.sort(key=lambda event: (event[0], event[1], event[2]))
        stock = opening
        for _, kind, identifier, quantity in events:
            if kind == 0:
                stock = add(stock, quantity)
            else:
                used = min(stock, quantity)
                stock = subtract(stock, used)
                material_rows[(identifier, component)] = {
                    "component_id": component, "unit": unit, "quantity": text(quantity),
                    "used": text(used), "shortfall": text(subtract(quantity, used)),
                    "remaining_stock": text(stock),
                }
        balances.append({"component_id": component, "unit": unit,
                         "starting_stock": text(opening), "ending_stock": text(stock)})

    order_rows = []
    failures = []
    for order_id in sorted(orders):
        product, unit, quantity, resource, due = orders[order_id]
        period_id = assignments[order_id]
        _, start, end = periods[period_id]
        demand_rows = [material_rows[(order_id, component)] for component in sorted(demanded[order_id])]
        material_pass = all(row["shortfall"] == "0" for row in demand_rows)
        due_pass = end <= due
        if not material_pass:
            failures.extend(f"material:{order_id}:{row['component_id']}" for row in demand_rows
                            if row["shortfall"] != "0")
        if not due_pass:
            failures.append(f"due:{order_id}")
        order_rows.append({
            "order_id": order_id, "product_id": product, "output_unit": unit,
            "output_quantity": text(quantity), "resource_id": resource, "period_id": period_id,
            "material_required_at": iso(start), "modeled_completion_at": iso(end),
            "due_at": iso(due), "demands": demand_rows, "material_pass": material_pass,
            "modeled_due_pass": due_pass,
            "late_by_seconds": text(seconds(due, end)) if not due_pass else "0",
        })

    capacity_rows = []
    for period_id in sorted(periods, key=lambda key: (periods[key][0], periods[key][1], key)):
        resource, start, end = periods[period_id]
        nominal = seconds(start, end)
        blocked = _blocked(resource, periods[period_id], maintenance)
        available = subtract(nominal, blocked)
        workload = Fraction(0)
        for order_id, (product, _, quantity, _, _) in orders.items():
            if assignments[order_id] == period_id:
                workload = add(workload, multiply(quantity, routings[(product, resource)][1]))
        passed = workload <= available
        if not passed:
            failures.append(f"capacity:{resource}:{period_id}")
        capacity_rows.append({
            "resource_id": resource, "period_id": period_id,
            "nominal_seconds": text(nominal), "blocked_seconds": text(blocked),
            "available_seconds": text(available), "workload_seconds": text(workload),
            "excess_seconds": text(subtract(workload, available)) if not passed else "0",
            "remaining_seconds": text(subtract(available, workload)) if passed else "0",
            "capacity_pass": passed,
        })
    return {"orders": order_rows, "capacity": capacity_rows,
            "material_balances": balances, "material_balance_end_at": iso(end_at),
            "modeled_checks_pass": not failures,
            "failed_checks": sorted(failures)}


def screen_production_schedules(snapshot: Mapping, alternative: Mapping) -> dict:
    """Compare two caller-supplied assignments without choosing or applying either."""
    try:
        if not isinstance(snapshot, Mapping) or not isinstance(alternative, Mapping):
            raise ScreenError("incomplete_snapshot")
        as_of = _scope(snapshot, alternative)
        normalized = _normalize(snapshot, alternative, as_of)
        data = normalized[:8]
        baseline, candidate = normalized[8:]
        return {
            "status": "screened", "synthetic": True,
            "workspace_id": snapshot["workspace_id"], "plan_version": snapshot["plan_version"],
            "snapshot_id": snapshot["snapshot_id"], "scenario_id": alternative["scenario_id"],
            "as_of": iso(as_of), "modeled_checks": list(_MODELED),
            "unmodeled_constraints": list(_UNMODELED),
            "baseline": _schedule(data, baseline),
            "alternative": _schedule(data, candidate),
        }
    except ScreenError as rejected:
        return {"status": "unsupported", "reason": rejected.reason, "details": []}
