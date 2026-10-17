"""Pure, company-owned dispatch scenarios for the fictional hosted workspace.

Profile orders describe expected demand. A plan row describes only the inbound
quantity selected by a reviewed quote; neither is a tracked physical shipment.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

from procure.offers.lifecycle import usable

from .quote_service import company_for_offer


_ORDER_SOURCE = "fictional_company_profile_expected_order"
_PLAN_SOURCE = "synthetic_plan_demand_scenario"


def _number(value):
    try:
        result = Decimal(value) if isinstance(value, str) else None
        return result if result is not None and result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _delay(plan, offer_id):
    assumptions = plan.get("assumptions") or {}
    delays = assumptions.get("delay_days_by_offer") or {}
    value = delays.get(offer_id, 0)
    return value if type(value) is int and 0 <= value <= 3650 else None


def _arrival(plan, offer_id):
    rows = [row for row in plan.get("alternatives", []) if isinstance(row, dict)
            and row.get("offer_id") == offer_id]
    if len(rows) != 1 or _date(rows[0].get("arrival_date")) is None:
        return None
    return rows[0]["arrival_date"]


def _offer_matches(company_id, material_id, unit, offer):
    if not isinstance(offer, dict) or offer.get("status") != "saved":
        return False
    if company_for_offer(offer) != company_id:
        return False
    items = offer.get("items") or []
    matching = [index for index, item in enumerate(items) if isinstance(item, dict)
                and usable(item.get("material", {})) == material_id]
    if len(matching) != 1:
        return False
    normalized_items = (offer.get("normalized") or {}).get("items") or []
    if matching[0] >= len(normalized_items):
        return False
    quantity = (normalized_items[matching[0]] or {}).get("quantity") or {}
    if quantity.get("status") != "ok":
        return False
    if unit == "kg":
        return _number(quantity.get("kg")) is not None
    return quantity.get("unit") == unit and _number(quantity.get("quantity")) is not None


def _order_row(company_id, material, order):
    material_id = order.get("material_id")
    unit = order.get("unit")
    known = material is not None and unit == material.get("quantity_unit")
    return {
        "shipment_id": f"demo-expected-order:{company_id}:{order['order_id']}",
        "company_id": company_id, "material_id": material_id,
        "material_name": material.get("name") if material else material_id,
        "order_id": order["order_id"], "demand_reference": None,
        "quantity": order.get("quantity"), "order_demand_quantity": order.get("quantity"),
        "quantity_unit": unit, "need_by": order.get("need_by"),
        "status": "expected_order_scenario", "expected_arrival": None,
        "original_expected_arrival": None, "scenario_expected_arrival": None,
        "offer_id": None, "offer_version": None, "plan_id": None,
        "baseline_plan_id": None, "baseline_offer_id": None,
        "baseline_offer_version": None, "baseline_supplier": None,
        "scenario_plan_id": None, "scenario_selected_offer_id": None,
        "scenario_offer_version": None, "scenario_supplier": None,
        "scenario_status": None, "recovery_available": False,
        "recovery_reason": "expected_order_only" if known else "material_or_unit_unavailable",
        "delay_days": None, "scenario_delay_days": None, "timeline": [],
        "route": None, "route_reason": "no_declared_shipment_route",
        "supplier": None, "arrival_basis": None, "source": _ORDER_SOURCE,
    }


def _plan_row(company, offer, plan, materials, orders):
    company_id = company["company_id"]
    material_id, unit = plan.get("material"), plan.get("quantity_unit")
    material = materials.get(material_id)
    offer_id = plan.get("selected_offer_id")
    purchase, demand = _number(plan.get("required_purchase")), _number(plan.get("quantity"))
    if (plan.get("company_id") != company_id or material is None
            or material.get("quantity_unit") != unit or not offer_id
            or purchase is None or purchase <= 0 or demand is None or demand <= 0
            or purchase > demand or not isinstance(plan.get("plan_id"), str)
            or not plan["plan_id"] or _date(plan.get("decision_date")) is None
            or _date(plan.get("need_by")) is None):
        return None
    versions = plan.get("offer_versions") or {}
    version = versions.get(offer_id)
    if type(version) is not int or version < 1 or offer.get("offer_id") != offer_id or offer.get("version") != version:
        return None
    if not _offer_matches(company_id, material_id, unit, offer):
        return None
    arrival, delay = _arrival(plan, offer_id), _delay(plan, offer_id)
    if arrival is None or delay is None:
        return None

    exact = [order for order in orders if order.get("material_id") == material_id
             and order.get("unit") == unit and order.get("need_by") == plan["need_by"]
             and _number(order.get("quantity")) == demand]
    order_id = exact[0]["order_id"] if len(exact) == 1 else None
    supplier = usable((offer.get("fields") or {}).get("supplier_name", {}))
    supplier = supplier if isinstance(supplier, str) else None
    return {
        "shipment_id": f"demo-plan-demand:{company_id}:{plan['plan_id']}:{offer_id}",
        "company_id": company_id, "material_id": material_id, "material_name": material["name"],
        "order_id": order_id, "demand_reference": None if order_id else f"plan-demand:{plan['plan_id']}",
        "quantity": plan["required_purchase"], "order_demand_quantity": plan["quantity"],
        "quantity_unit": unit, "need_by": plan["need_by"],
        "status": "planned_inbound_scenario", "expected_arrival": arrival,
        "original_expected_arrival": arrival, "scenario_expected_arrival": None,
        "offer_id": offer_id, "offer_version": version, "plan_id": plan["plan_id"],
        "baseline_plan_id": plan["plan_id"], "baseline_offer_id": offer_id,
        "baseline_offer_version": version, "baseline_supplier": supplier,
        "scenario_plan_id": None, "scenario_selected_offer_id": None,
        "scenario_offer_version": None, "scenario_supplier": None,
        "scenario_status": None,
        "recovery_available": True, "recovery_reason": None,
        "delay_days": delay, "scenario_delay_days": None,
        "timeline": [
            {"date": plan["decision_date"], "label": "Plan decision", "phase": "plan",
             "plan_id": plan["plan_id"], "offer_id": offer_id, "source": "synthetic_plan_assumption"},
            {"date": arrival, "label": "Planned arrival", "phase": "plan",
             "plan_id": plan["plan_id"], "offer_id": offer_id, "source": "synthetic_plan_assumption"},
        ],
        "route": None, "route_reason": "no_declared_shipment_route",
        "supplier": supplier,
        "arrival_basis": "plan_decision_plus_reviewed_quote_lead_and_assumed_delay",
        "source": _PLAN_SOURCE,
    }


def _reviewed_row(row, baseline_row, scenario, recovery):
    """Keep baseline and scenario ownership explicit in a reviewed comparison."""
    chosen = scenario.get("selected_offer_id")
    scenario_timeline = row["timeline"] if chosen else []
    row["baseline_plan_id"] = baseline_row["plan_id"]
    row["baseline_offer_id"] = baseline_row["offer_id"]
    row["baseline_offer_version"] = baseline_row["offer_version"]
    row["baseline_supplier"] = baseline_row["supplier"]
    row["original_expected_arrival"] = baseline_row["expected_arrival"]
    row["delay_days"] = baseline_row["delay_days"]
    row["scenario_plan_id"] = scenario["plan_id"]
    row["scenario_selected_offer_id"] = chosen
    row["scenario_offer_version"] = row["offer_version"] if chosen else None
    row["scenario_supplier"] = row["supplier"] if chosen else None
    row["scenario_expected_arrival"] = row["expected_arrival"] if chosen else None
    row["scenario_delay_days"] = _delay(scenario, chosen or baseline_row["offer_id"])
    row["scenario_status"] = "infeasible" if not chosen else recovery.get("outcome", "selected")
    row["recovery_available"] = False
    row["recovery_reason"] = "scenario_infeasible" if not chosen else "recovery_already_reviewed"
    if not chosen:
        row["status"] = "reviewed_infeasible_scenario"
    row["timeline"] = [
        {**event, "phase": "baseline", "label": "Baseline " + event["label"].lower()}
        for event in baseline_row["timeline"]
    ]
    if chosen:
        row["timeline"].extend({**event, "phase": "scenario", "label": "Scenario " + event["label"].lower()}
                               for event in scenario_timeline)
    return row


def _selected_plan_row(company, offers, plan, materials, orders):
    selected = plan.get("selected_offer_id")
    matching = [offer for offer in offers if isinstance(offer, dict)
                and offer.get("offer_id") == selected]
    return _plan_row(company, matching[0], plan, materials, orders) if len(matching) == 1 else None


def build_dispatch_view(company: dict, offers: list[dict], plan: dict | None,
                        recovery: dict | None) -> dict:
    """Describe fictional expected orders and the current selected plan demand.

    The caller supplies the session-owned company and offers. This function
    checks quote document ownership again before enabling any recovery row.
    """
    company_id = company["company_id"]
    materials = {row["material_id"]: row for row in company.get("materials", [])
                 if isinstance(row, dict) and isinstance(row.get("material_id"), str)}
    orders = [row for row in company.get("orders", []) if isinstance(row, dict)
              and isinstance(row.get("order_id"), str)]
    rows = [_order_row(company_id, materials.get(order.get("material_id")), order)
            for order in orders]
    limitations = ["Fictional expected orders and plans; no live shipment tracking or confirmed purchase orders."]
    valid_recovery = (isinstance(recovery, dict) and isinstance(plan, dict)
                      and isinstance(recovery.get("baseline"), dict)
                      and isinstance(recovery.get("scenario"), dict)
                      and recovery["scenario"] == plan
                      and recovery["scenario"].get("company_id") == company_id
                      and recovery["baseline"].get("company_id") == company_id
                      and recovery["baseline"].get("material") == plan.get("material")
                      and recovery["baseline"].get("quantity_unit") == plan.get("quantity_unit")
                      and recovery["baseline"].get("quantity") == plan.get("quantity")
                      and recovery["baseline"].get("need_by") == plan.get("need_by"))
    baseline_row = (_selected_plan_row(company, offers, recovery["baseline"], materials, orders)
                    if valid_recovery else None)
    if valid_recovery and baseline_row is None:
        valid_recovery = False
    if recovery is not None and not valid_recovery:
        limitations.append("Recovery comparison does not match the current plan; scenario details withheld.")
    active_recovery = recovery if valid_recovery else None
    if isinstance(plan, dict):
        display_plan = recovery["baseline"] if valid_recovery and not plan.get("selected_offer_id") else plan
        row = _selected_plan_row(company, offers, display_plan, materials, orders)
        if row is not None:
            if valid_recovery:
                row = _reviewed_row(row, baseline_row, plan, recovery)
            elif recovery is not None:
                row["recovery_available"] = False
                row["recovery_reason"] = "stale_recovery_context"
            # Foreground the current reviewed purchase; profile demand is not its arrival evidence.
            rows.insert(0, row)
        elif display_plan.get("selected_offer_id"):
            limitations.append("Selected plan shipment unavailable: quote, unit, date, or ownership mismatch.")
    if not any(row["source"] == _PLAN_SOURCE for row in rows):
        limitations.append("No current accepted plan-demand shipment is available for recovery.")
    return {"company_id": company_id, "plan_id": plan.get("plan_id") if isinstance(plan, dict) else None,
            "baseline_plan_id": (active_recovery["baseline"].get("plan_id") if active_recovery else
                                 plan.get("plan_id") if isinstance(plan, dict) else None),
            "synthetic": True, "source": "fictional_company_profile_and_plan",
            "as_of": company.get("as_of"), "shipments": rows, "limitations": limitations}


def resolve_recovery_shipment(view: dict, shipment_id: str, plan_id: str,
                              offer_version: int) -> dict | None:
    """Return an eligible current row only when its exact plan and quote match."""
    if (type(shipment_id) is not str or type(plan_id) is not str
            or type(offer_version) is not int or view.get("plan_id") != plan_id):
        return None
    rows = [row for row in view.get("shipments", []) if isinstance(row, dict)
            and row.get("shipment_id") == shipment_id
            and row.get("company_id") == view.get("company_id")
            and row.get("plan_id") == plan_id
            and row.get("offer_version") == offer_version
            and row.get("recovery_available") is True]
    return deepcopy(rows[0]) if len(rows) == 1 else None
