"""Deterministic text for a synthetic, unsent RFQ bound to an active plan."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_CEILING

from procure.offers import lifecycle as lc

from .errors import DemoError
from .quote_service import company_for_offer


def _field(offer: dict, name: str):
    field = offer.get("fields", {}).get(name)
    return lc.usable(field) if field else None


def _term(value) -> str:
    return str(value) if value not in (None, "") else "Unknown in the reviewed document"


def _price_text(field: dict) -> str:
    value = field.get("value")
    if field.get("status") != "extracted" or field.get("source") not in {"rules", "gemini"}:
        status = field.get("status")
        if status not in {"missing", "unverified", "conflict"}:
            status = "unavailable"
        return f"unavailable ({status} document field)"
    if not isinstance(value, dict) or value.get("type") != "fixed":
        return "unavailable (unsupported original document price)"
    money = value.get("unit_price")
    if not isinstance(money, dict) or not isinstance(money.get("amount"), str):
        return "unavailable (unsupported original document price)"
    return f"{money['amount']} {money.get('currency', 'Unknown')} per {value.get('per_unit', 'unknown unit')}"


def build_bridge_draft(plan: dict, offer: dict, *, coverage: dict, created_at: datetime) -> dict:
    """Request fresh bridge terms from the selected supplier without borrowing quote terms."""
    selected = plan.get('selected_offer_id')
    if (offer.get('offer_id') != selected or company_for_offer(offer) != plan.get('company_id')
            or offer.get('version') != (plan.get('offer_versions') or {}).get(selected)):
        raise DemoError('stale_plan', 'The selected reviewed quote no longer matches this plan.', 409)
    if (coverage.get('status') != 'gap' or coverage.get('plan_id') != plan.get('plan_id')
            or coverage.get('company_id') != plan.get('company_id')
            or coverage.get('material_id') != plan.get('material')
            or coverage.get('quantity_unit') != plan.get('quantity_unit')
            or coverage.get('missing_inputs')):
        raise DemoError('draft_unavailable', 'A current positive supply gap is required for a bridge RFQ.', 409)
    try:
        arithmetic = Decimal(coverage['bridge_quantity'])
    except (TypeError, ValueError, KeyError):
        raise DemoError('draft_unavailable', 'Bridge quantity is unavailable.', 409) from None
    due = coverage.get('bridge_needed_by')
    if not arithmetic.is_finite() or arithmetic <= 0 or not isinstance(due, str) or not due:
        raise DemoError('draft_unavailable', 'A positive bridge quantity and date are required.', 409)
    unit = plan['quantity_unit']
    discrete = unit not in {'kg', 'g', 'tonne', 't', 'lb', 'litre', 'liter', 'l', 'm', 'metre', 'meter'}
    quantity = arithmetic.to_integral_value(rounding=ROUND_CEILING) if discrete else arithmetic
    amount = format(quantity, 'f')
    supplier = _term(_field(offer, 'supplier_name'))
    if supplier.startswith('Unknown'):
        raise DemoError('draft_unavailable', 'Selected supplier name is unavailable.', 409)
    body = '\n'.join([
        'SYNTHETIC TEST DOCUMENT — UNSENT BRIDGE REQUEST FOR QUOTATION DRAFT',
        'No order commitment. This text has not been sent to a supplier.',
        f'Supplier: {supplier}',
        f'Buyer company: {plan["company_id"]}',
        f'Material: {plan["material"]}',
        f'Requested bridge quantity: {amount} {unit}',
        f'Calculated uncovered quantity: {coverage["uncovered_quantity"]} {unit}',
        f'Calculated bridge quantity: {coverage["bridge_quantity"]} {unit}',
        f'Earlier delivery needed by: {due}',
        f'Current selected arrival: {coverage.get("selected_arrival_date") or "unavailable"}',
        f'Planning basis: stock {coverage["stock"]} {unit} as of {coverage["inventory_as_of"]}; '
        f'daily consumption {coverage["consumption_per_day"]} {unit} as of {coverage["consumption_as_of"]}.',
        'Please confirm fresh price and capacity, delivery date, payment and transport terms for this separate bridge request.',
        'The accepted quote does not confirm this earlier quantity, date, price or capacity.',
        'The existing accepted plan quantity and selected quote remain unchanged.',
        '',
    ])
    result = {'plan_id': plan['plan_id'], 'offer_versions': dict(plan['offer_versions']),
        'synthetic': True, 'status': 'draft_unsent', 'bridge': True, 'supplier': supplier,
        'company_id': plan['company_id'], 'material': plan['material'], 'quantity': amount,
        'quantity_unit': unit, 'need_by': due, 'arithmetic_bridge_quantity': coverage['bridge_quantity'],
        'reporting_currency': plan['reporting_currency'], 'total_cost': None,
        'created_at': created_at.isoformat(), 'text': body}
    if unit == 'kg':
        result['quantity_kg'] = amount
    return result


def build_draft(plan: dict, offer: dict, *, all_offers: list[dict], created_at: datetime) -> dict:
    selected = offer["offer_id"]
    if selected != plan.get('selected_offer_id') or company_for_offer(offer) != plan.get('company_id'):
        raise DemoError('stale_plan', 'The reviewed selected offer no longer matches this plan.', 409)
    if offer.get('version') != (plan.get('offer_versions') or {}).get(selected):
        raise DemoError('stale_version', 'The selected offer version changed.', 409)
    items = [item for item in offer.get("items", []) if lc.usable(item.get("material", {})) == plan["material"]]
    if len(items) != 1:
        raise DemoError("draft_unavailable", "The selected offer has no unique material item.", 409)
    item = items[0]
    pricing = lc.usable(item.get("pricing", {}))
    if not isinstance(pricing, dict) or pricing.get("type") != "fixed":
        raise DemoError("draft_unavailable", "The selected offer has no fixed quoted price.", 409)
    money = pricing.get("unit_price") or {}
    if not isinstance(money, dict) or not isinstance(money.get("amount"), str):
        raise DemoError("draft_unavailable", "The selected offer has no quoted amount.", 409)
    price_field = item["pricing"]
    original_price = price_field.get("original") if price_field.get("status") == "corrected" else price_field
    if not isinstance(original_price, dict):
        original_price = {"status": "unavailable", "value": None, "source": "unknown", "evidence": []}
    incoterm = _field(offer, "incoterm")
    if isinstance(incoterm, dict):
        incoterm_text = " ".join(str(incoterm.get(key)) for key in ("code", "place") if incoterm.get(key))
    else:
        incoterm_text = _term(incoterm)
    corrections = []
    locators = []
    for field in [*offer.get("fields", {}).values(), *(f for row in offer.get("items", []) for f in row.values())]:
        if field.get("status") == "corrected":
            corrections.append(f"{field['name']}: {field.get('value')} (reviewer entered; no document evidence)")
        for evidence in field.get("evidence") or []:
            locator = evidence.get("locator")
            if isinstance(locator, str) and locator and locator not in locators:
                locators.append(locator)
        original = field.get("original")
        if isinstance(original, dict):
            for evidence in original.get("evidence") or []:
                locator = evidence.get("locator")
                if isinstance(locator, str) and locator and locator not in locators:
                    locators.append(locator)
        for evidence in field.get('original_candidate_evidence') or []:
            locator = evidence.get('locator')
            if isinstance(locator, str) and locator and locator not in locators:
                locators.append(locator)
    assumptions = plan.get("assumptions") or {}
    delay = (assumptions.get("delay_days_by_offer") or {}).get(selected, 0)
    names = {other["offer_id"]: _term(_field(other, "supplier_name")) for other in all_offers}
    comparison_delays = "; ".join(
        f"{names[offer_id]}: {days} days" for offer_id, days in
        (assumptions.get("delay_days_by_offer") or {}).items() if offer_id in names
    ) or "None"
    fx_rates = assumptions.get("fx_rates") or []
    fx_text = ", ".join(f"{rate.get('base')} {rate.get('quote')} {rate.get('rate')} on {rate.get('rate_date')}"
                        for rate in fx_rates if isinstance(rate, dict)) or "none needed or unavailable"
    supplier = _term(_field(offer, "supplier_name"))
    text = "\n".join([
        "SYNTHETIC TEST DOCUMENT — UNSENT REQUEST FOR QUOTATION DRAFT",
        "No order commitment. This text has not been sent to a supplier.",
        f"Supplier: {supplier}",
        f"Buyer company: {plan.get('company_id', 'Unknown fictional buyer')}",
        f"Requested product: {_term(lc.usable(item.get('description', {})))}",
        f"Material: {plan['material']}",
        f"Requested quantity: {plan['required_purchase']} {plan['quantity_unit']}",
        f"Need by: {plan['need_by']}",
        f"Original quoted price: {_price_text(original_price)}",
        f"Current reviewed price: {money['amount']} {money.get('currency', 'Unknown')} per {pricing.get('per_unit', 'unknown unit')}",
        f"Settlement currency: {_term(_field(offer, 'settlement_currency'))}",
        f"Incoterm: {_term(incoterm_text)}",
        f"Payment terms: {_term(_field(offer, 'payment_terms'))}",
        "Reviewer corrections: " + ("; ".join(corrections) if corrections else "None"),
        f"Original document: {offer['document']['filename']} ({offer['document']['document_id']})",
        "Original document locators: " + (", ".join(locators) if locators else "None recorded"),
        f"Hypothetical delay for selected offer: {delay} days; this is not a confirmed shipment event.",
        f"Hypothetical comparison delays across accepted offers: {comparison_delays}.",
        f"Separate buyer extra-cost assumption: {assumptions.get('additional_cost_per_unit', assumptions.get('additional_cost_per_kg', 'unknown'))} "
        f"{assumptions.get('additional_cost_currency', 'unknown')} per {plan['quantity_unit']} "
        f"({assumptions.get('additional_cost_source', 'unknown source')}).",
        f"Separate synthetic FX assumptions: {fx_text} ({assumptions.get('fx_source', 'unknown source')}).",
        f"Scenario comparison total: {plan['total_cost']} {plan['reporting_currency']}; "
        "this is not a supplier commercial quote.",
        "Please respond with a fresh formal quotation. No purchase or supplier communication has occurred.",
        "",
    ])
    result = {"plan_id": plan["plan_id"], "offer_versions": dict(plan["offer_versions"]),
            "synthetic": True, "status": "draft_unsent", "supplier": supplier,
            "company_id": plan.get('company_id'), "material": plan["material"], "quantity": plan["required_purchase"],
            "quantity_unit": plan['quantity_unit'],
            "need_by": plan["need_by"], "reporting_currency": plan["reporting_currency"],
            "total_cost": plan["total_cost"], "created_at": created_at.isoformat(), "text": text}
    if plan['quantity_unit'] == 'kg':
        result['quantity_kg'] = result['quantity']
    return result
