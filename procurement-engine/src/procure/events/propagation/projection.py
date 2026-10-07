"""Exact, synthetic arrival-change material screen.

``project_arrival_change(snapshot, scenario)`` consumes the local schema described
in README.md. It never changes the input or a persisted plan. A screened result
contains per-component balances and every graph-reachable commitment; an
unsupported result contains a reason and no numeric projection. All stock,
arrival and demand quantities are plain nonnegative decimal strings. Internally,
integer scaled units avoid rounding under the caller's Decimal context.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import re

_AMOUNT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_KINDS = {"shipment", "purchase_order", "component", "production_order", "commitment"}
_EDGE_KINDS = {
    ("shipment", "purchase_order"),
    ("purchase_order", "component"),
    ("component", "production_order"),
    ("production_order", "commitment"),
}
_LIMITATIONS = (
    "Material availability only; BOM demand is supplied, not inferred.",
    "Capacity, maintenance, cash, work in process, dispatch and customer promises are not modeled.",
    "This pure scenario is not an applied or durably deduplicated plan change.",
)


class _Reject(Exception):
    def __init__(self, reason: str, *details: str):
        self.reason = reason
        self.details = tuple(sorted(set(details)))


def _mapping(value: object) -> Mapping:
    if not isinstance(value, Mapping):
        raise _Reject("incomplete_graph")
    return value


def _array(value: object) -> list:
    if not isinstance(value, (list, tuple)):
        raise _Reject("incomplete_graph")
    return list(value)


def _id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _Reject("incomplete_graph")
    return value


def _instant(value: object) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise _Reject("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise _Reject("invalid_timestamp")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise _Reject("invalid_timestamp") from exc


def _amount(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or not _AMOUNT.fullmatch(value):
        raise _Reject("invalid_quantity")
    # Decimal validates the decimal representation without ambient arithmetic;
    # its coefficient/exponent are then used as exact integer scaled units.
    digits = Decimal(value).as_tuple()
    coefficient = int("".join(str(d) for d in digits.digits))
    scale = max(-digits.exponent, 0)
    if digits.exponent > 0:
        coefficient *= 10 ** digits.exponent
    return coefficient, scale


def _at_or_after(value: object, as_of: datetime) -> datetime:
    instant = _instant(value)
    if instant < as_of:
        raise _Reject("pre_as_of")
    return instant


def _iso(value: datetime) -> str:
    return value.isoformat()


def _scaled(amount: tuple[int, int], scale: int) -> int:
    return amount[0] * 10 ** (scale - amount[1])


def _decimal(value: int, scale: int) -> str:
    if value == 0:
        return "0"
    if scale == 0:
        return str(value)
    whole, fractional = divmod(value, 10 ** scale)
    tail = str(fractional).rjust(scale, "0").rstrip("0")
    return f"{whole}.{tail}" if tail else str(whole)


def _same_or_conflict(table: dict, key: str, value: tuple, reason: str) -> None:
    previous = table.get(key)
    if previous is not None and previous != value:
        raise _Reject(reason, key)
    table[key] = value


def _scope(snapshot: Mapping, scenario: Mapping) -> tuple[str, str, str, datetime]:
    workspace = _id(snapshot.get("workspace_id"))
    plan_version = _id(snapshot.get("plan_version"))
    snapshot_id = _id(snapshot.get("snapshot_id"))
    if workspace != scenario.get("workspace_id"):
        raise _Reject("workspace_mismatch")
    if plan_version != scenario.get("expected_plan_version"):
        raise _Reject("stale_plan_version")
    if snapshot_id != scenario.get("source_snapshot_id"):
        raise _Reject("snapshot_mismatch")
    if snapshot.get("synthetic") is not True or scenario.get("synthetic") is not True:
        raise _Reject("invalid_scope")
    _id(scenario.get("event_id"))
    _id(scenario.get("scenario_id"))
    return workspace, plan_version, snapshot_id, _instant(snapshot.get("as_of"))


def _normalize(snapshot: Mapping, scenario: Mapping, as_of: datetime):
    nodes: dict[str, str] = {}
    for raw in _array(snapshot.get("nodes")):
        row = _mapping(raw)
        node_id, kind = _id(row.get("id")), row.get("kind")
        if not isinstance(kind, str) or kind not in _KINDS:
            raise _Reject("incomplete_graph", node_id)
        _same_or_conflict(nodes, node_id, kind, "conflicting_snapshot")
    edges: set[tuple[str, str]] = set()
    for raw in _array(snapshot.get("edges")):
        row = _mapping(raw)
        source, target = _id(row.get("from")), _id(row.get("to"))
        if source not in nodes or target not in nodes or (nodes[source], nodes[target]) not in _EDGE_KINDS:
            raise _Reject("incomplete_graph", source, target)
        edges.add((source, target))
    if not nodes or not edges:
        raise _Reject("incomplete_graph")

    inventory: dict[str, tuple[str, tuple[int, int]]] = {}
    for raw in _array(snapshot.get("inventory")):
        row = _mapping(raw)
        component = _id(row.get("component_id"))
        unit = _id(row.get("unit"))
        if nodes.get(component) != "component" or "starting_stock" not in row:
            raise _Reject("incomplete_graph", component)
        amount = _amount(row["starting_stock"])
        _same_or_conflict(inventory, component, (unit, amount), "conflicting_snapshot")

    arrivals: dict[str, tuple[str, str, str, tuple[int, int], str, datetime]] = {}
    for raw in _array(snapshot.get("arrival_lines")):
        row = _mapping(raw)
        line_id = _id(row.get("line_id"))
        shipment, po, component = (_id(row.get(name)) for name in
                                   ("shipment_id", "purchase_order_id", "component_id"))
        if nodes.get(shipment) != "shipment" or nodes.get(po) != "purchase_order" or \
                nodes.get(component) != "component" or (shipment, po) not in edges or (po, component) not in edges:
            raise _Reject("incomplete_graph", line_id)
        if "quantity" not in row:
            raise _Reject("incomplete_graph", line_id)
        amount = _amount(row["quantity"])
        unit = _id(row.get("unit"))
        arrival_at = _at_or_after(row.get("arrival_at"), as_of)
        _same_or_conflict(arrivals, line_id,
                          (shipment, po, component, amount, unit, arrival_at), "conflicting_snapshot")

    commitments: dict[str, tuple[str, datetime, datetime, dict[str, tuple[str, tuple[int, int]]]]] = {}
    for raw in _array(snapshot.get("commitments")):
        row = _mapping(raw)
        commitment, production = _id(row.get("commitment_id")), _id(row.get("production_order_id"))
        if nodes.get(commitment) != "commitment" or nodes.get(production) != "production_order" or \
                (production, commitment) not in edges:
            raise _Reject("incomplete_graph", commitment)
        required_at = _at_or_after(row.get("required_at"), as_of)
        due_at = _at_or_after(row.get("due_at"), as_of)
        if due_at < required_at:
            raise _Reject("incomplete_graph", commitment)
        demands: dict[str, tuple[str, tuple[int, int]]] = {}
        for item in _array(row.get("demands")):
            demand = _mapping(item)
            component = _id(demand.get("component_id"))
            if nodes.get(component) != "component" or (component, production) not in edges or \
                    "quantity" not in demand:
                raise _Reject("incomplete_graph", commitment, component)
            unit, amount = _id(demand.get("unit")), _amount(demand["quantity"])
            if component in demands:
                raise _Reject("conflicting_snapshot", commitment, component)
            demands[component] = (unit, amount)
        if not demands:
            raise _Reject("incomplete_graph", commitment)
        _same_or_conflict(commitments, commitment,
                          (production, required_at, due_at, demands), "conflicting_snapshot")

    # Validate the entire declared graph, not merely the changed line's closure.
    represented_arrival_edges = set()
    for shipment, po, component, _, _, _ in arrivals.values():
        represented_arrival_edges.update(((shipment, po), (po, component)))
    for node_id, kind in nodes.items():
        if kind in {"shipment", "purchase_order"} and not any(
                (kind == "shipment" and row[0] == node_id) or
                (kind == "purchase_order" and row[1] == node_id) for row in arrivals.values()):
            raise _Reject("incomplete_graph", node_id)
        if kind == "component" and node_id not in inventory:
            raise _Reject("incomplete_graph", node_id)
        if kind == "production_order" and not any(row[0] == node_id for row in commitments.values()):
            raise _Reject("incomplete_graph", node_id)
        if kind == "commitment" and node_id not in commitments:
            raise _Reject("incomplete_graph", node_id)
    for source, target in edges:
        kinds = nodes[source], nodes[target]
        if kinds[0] in {"shipment", "purchase_order"} and (source, target) not in represented_arrival_edges:
            raise _Reject("incomplete_graph", source, target)
        if kinds == ("component", "production_order"):
            for commitment, (production, _, _, demands) in commitments.items():
                if production == target and source not in demands:
                    raise _Reject("incomplete_graph", commitment, source)
        if kinds == ("production_order", "commitment") and \
                commitments[target][0] != source:
            raise _Reject("incomplete_graph", source, target)
    for _, _, component, _, unit, _ in arrivals.values():
        if unit != inventory[component][0]:
            raise _Reject("incompatible_unit", component)
    for _, _, _, demands in commitments.values():
        for component, (unit, _) in demands.items():
            if unit != inventory[component][0]:
                raise _Reject("incompatible_unit", component)

    changes: dict[str, datetime] = {}
    for raw in _array(scenario.get("changes")):
        row = _mapping(raw)
        line_id = _id(row.get("line_id"))
        if line_id not in arrivals:
            raise _Reject("incomplete_graph", line_id)
        _same_or_conflict(changes, line_id, _at_or_after(row.get("arrival_at"), as_of),
                          "conflicting_update")
    if not changes:
        raise _Reject("incomplete_graph")
    return nodes, edges, inventory, arrivals, commitments, changes


def _screen(inventory, arrivals, commitments, changes):
    per_component: dict[str, dict] = {}
    rows: dict[tuple[str, str], dict] = {}
    for component, (unit, starting) in sorted(inventory.items()):
        related_arrivals = [(line_id, row) for line_id, row in arrivals.items() if row[2] == component]
        related_demands = [(commitment, value[1], value[3][component][1])
                           for commitment, value in commitments.items() if component in value[3]]
        scale = max([starting[1]] + [row[3][1] for _, row in related_arrivals] +
                    [amount[1] for _, _, amount in related_demands])
        start = _scaled(starting, scale)
        outcomes = {}
        for mode in ("baseline", "changed"):
            events = []
            for line_id, row in related_arrivals:
                when = changes.get(line_id, row[5]) if mode == "changed" else row[5]
                events.append((when, 0, line_id, _scaled(row[3], scale)))
            for commitment, when, amount in related_demands:
                events.append((when, 1, commitment, _scaled(amount, scale)))
            events.sort(key=lambda event: (event[0], event[1], event[2]))
            stock = start
            for _, event_kind, identifier, amount in events:
                if event_kind == 0:
                    stock += amount
                else:
                    used = min(stock, amount)
                    stock -= used
                    rows.setdefault((identifier, component), {})[mode] = {
                        "used": _decimal(used, scale),
                        "shortfall": _decimal(amount - used, scale),
                        "remaining_stock": _decimal(stock, scale),
                    }
            outcomes[mode] = _decimal(stock, scale)
        per_component[component] = {
            "component_id": component, "unit": unit,
            "starting_stock": _decimal(start, scale),
            "baseline_ending_stock": outcomes["baseline"],
            "changed_ending_stock": outcomes["changed"],
        }
    return per_component, rows


def _comparison(before: str, after: str) -> str:
    # Decimal comparisons are exact and do not perform context-sensitive arithmetic.
    left, right = Decimal(before), Decimal(after)
    if left == right:
        return "unchanged_shortage"
    if left == 0 and right > 0:
        return "new_shortage"
    if right > left:
        return "increased_shortage"
    return "reduced_shortage"


def _project(snapshot: Mapping, scenario: Mapping) -> dict:
    workspace, plan_version, snapshot_id, as_of = _scope(snapshot, scenario)
    nodes, edges, inventory, arrivals, commitments, changes = _normalize(snapshot, scenario, as_of)
    affected: set[str] = set()
    for line_id in changes:
        component = arrivals[line_id][2]
        productions = {target for source, target in edges if source == component and
                       nodes[target] == "production_order"}
        affected.update(target for source, target in edges if source in productions and
                        nodes[target] == "commitment")
    if not affected:
        raise _Reject("incomplete_graph", *sorted(changes))
    balances, rows = _screen(inventory, arrivals, commitments, changes)
    affected_rows = []
    for commitment in sorted(affected, key=lambda key: (commitments[key][1], key)):
        production, required_at, due_at, demands = commitments[commitment]
        demand_rows = []
        material_at_risk = False
        for component, (unit, amount) in sorted(demands.items()):
            outcomes = rows[(commitment, component)]
            comparison = _comparison(outcomes["baseline"]["shortfall"],
                                     outcomes["changed"]["shortfall"])
            material_at_risk |= outcomes["changed"]["shortfall"] != "0"
            demand_rows.append({
                "component_id": component, "unit": unit,
                "quantity": _decimal(_scaled(amount, amount[1]), amount[1]),
                "baseline": outcomes["baseline"], "changed": outcomes["changed"],
                "comparison": comparison,
            })
        affected_rows.append({
            "commitment_id": commitment, "production_order_id": production,
            "required_at": _iso(required_at), "due_at": _iso(due_at),
            "material_at_risk": material_at_risk, "demands": demand_rows,
        })
    return {
        "status": "screened", "synthetic": True,
        "workspace_id": workspace, "plan_version": plan_version,
        "snapshot_id": snapshot_id, "event_id": scenario["event_id"],
        "scenario_id": scenario["scenario_id"], "as_of": _iso(as_of),
        "arrival_changes": [
            {"line_id": line_id, "component_id": arrivals[line_id][2],
             "quantity": _decimal(_scaled(arrivals[line_id][3], arrivals[line_id][3][1]),
                                  arrivals[line_id][3][1]),
             "unit": arrivals[line_id][4], "baseline_at": _iso(arrivals[line_id][5]),
             "changed_at": _iso(changes[line_id])}
            for line_id in sorted(changes)
        ],
        "affected_commitments": affected_rows,
        "component_balances": [balances[key] for key in sorted(balances)],
        "limitations": list(_LIMITATIONS),
    }


def project_arrival_change(snapshot: Mapping, scenario: Mapping) -> dict:
    """Compare one explicit arrival-date scenario against an immutable snapshot.

    Returns ``status=screened`` with exact decimal strings, or
    ``status=unsupported`` with a reason and no numerical screen.
    """
    try:
        return _project(_mapping(snapshot), _mapping(scenario))
    except _Reject as rejected:
        return {"status": "unsupported", "reason": rejected.reason,
                "details": list(rejected.details)}
