"""Deterministic, single-offer feasibility and cost comparison for synthetic quotes."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from decimal import Decimal, localcontext

from procure.offers import lifecycle as lc
from procure.offers import normalize as nz

from .errors import DemoError
from .quote_service import synthetic_rate
from .quote_service import company_for_offer

_DECIMAL = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _number(data: dict, key: str, *, positive: bool = False) -> Decimal:
    value = data.get(key)
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        raise DemoError("invalid_value", f"{key} must be a finite nonnegative decimal string")
    number = nz._bounded_decimal(value, allow_negative=False)
    if number is None or (positive and number <= 0):
        raise DemoError("invalid_value", f"{key} must be a finite {'positive' if positive else 'nonnegative'} decimal string")
    return number


def _date(data: dict, key: str) -> date:
    value = data.get(key)
    try:
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise DemoError("invalid_value", f"{key} must be YYYY-MM-DD") from None


def _field(offer: dict, name: str):
    value = offer.get("fields", {}).get(name, {})
    return lc.usable(value) if value else None


def _decimal_kg(normalized: dict | None) -> Decimal | None:
    if not isinstance(normalized, dict) or normalized.get("status") != "ok":
        return None
    return nz._bounded_decimal(normalized.get("kg"), allow_negative=False)


def _quantity_in_unit(normalized: dict | None, unit: str) -> Decimal | None:
    if not isinstance(normalized, dict) or normalized.get('status') != 'ok':
        return None
    if unit == 'kg':
        return _decimal_kg(normalized)
    if normalized.get('unit') != unit:
        return None
    return nz._bounded_decimal(normalized.get('quantity'), allow_negative=False)


def _alternative(offer: dict, *, material: str, quantity: Decimal, decision: date, need: date, today: date,
                 reporting_currency: str, budget: Decimal, additional: Decimal, delay: int,
                 quantity_unit: str) -> dict:
    reasons: list[str] = []
    offer_id = offer["offer_id"]
    supplier = _field(offer, "supplier_name") or "Unknown supplier"
    item_indexes = [i for i, item in enumerate(offer.get("items", []))
                    if item.get("material", {}).get("status") in {"extracted", "corrected"}
                    and item["material"].get("value") == material]
    item_index = item_indexes[0] if len(item_indexes) == 1 else None
    if not item_indexes:
        reasons.append("material_unavailable")
    elif len(item_indexes) > 1:
        reasons.append("ambiguous_material_item")

    expiry = _field(offer, "valid_until")
    if not isinstance(expiry, str):
        reasons.append("expiry_unknown")
    elif nz.expiry_status(expiry, decision) != "valid" or nz.expiry_status(expiry, today) != "valid":
        reasons.append("offer_expired")

    lead = _field(offer, "lead_time_days")
    arrival = None
    if type(lead) is not int or lead < 0:
        reasons.append("lead_time_unknown")
    else:
        try:
            arrival = decision + timedelta(days=lead + delay)
        except OverflowError:
            raise DemoError("invalid_value", "decision_date plus lead time exceeds the supported date range") from None
        if arrival > need:
            reasons.append("arrives_after_need_by")

    normalized = offer.get("normalized") or {}
    minimum = _quantity_in_unit(normalized.get("min_order"), quantity_unit)
    capacity = _quantity_in_unit(normalized.get("capacity"), quantity_unit)
    if minimum is None:
        reasons.append("minimum_order_unknown")
    elif quantity < minimum:
        reasons.append("below_min_order")
    if capacity is None:
        reasons.append("capacity_unknown")
    else:
        capacity_period = (normalized.get("capacity") or {}).get("original", {}).get("period")
        if capacity_period != "month":
            reasons.append("capacity_period_unsupported")
        if quantity > capacity:
            reasons.append("exceeds_capacity")

    unit_price = None
    total = None
    if item_index is not None:
        normalized_items = normalized.get("items") or []
        normalized_item = normalized_items[item_index] if item_index < len(normalized_items) else {}
        offered = _quantity_in_unit(normalized_item.get("quantity"), quantity_unit)
        if offered is None:
            reasons.append("offered_quantity_unknown")
        elif quantity > offered:
            reasons.append("exceeds_offered_quantity")
        price_field = offer["items"][item_index].get("pricing", {})
        price_value = lc.usable(price_field) if price_field else None
        if not price_value or price_value.get("type") != "fixed":
            reasons.append("fixed_price_unavailable")
        else:
            quote_currency = price_value.get("unit_price", {}).get("currency")
            if _field(offer, "settlement_currency") != quote_currency:
                reasons.append("settlement_price_currency_mismatch")
            if quantity_unit == 'kg':
                price = nz.normalize_pricing(price_value, reporting_currency=reporting_currency,
                                             as_of=decision, rate_lookup=synthetic_rate)
                money = (price.get('reporting') or {}).get('per_kg') or {}
            elif price_value.get('per_unit') == quantity_unit:
                amount = nz._bounded_decimal(price_value['unit_price'].get('amount'), allow_negative=False)
                rate = synthetic_rate(quote_currency, reporting_currency, decision) if quote_currency != reporting_currency else None
                converted = amount if rate is None else amount * rate.rate if amount is not None and rate.rate is not None else None
                price = {'status': 'ok' if converted is not None else 'fx_unavailable'}
                money = {'amount': nz.fmt(converted)} if converted is not None else {}
            else:
                price = {'status': 'unsupported_unit'}
                money = {}
            unit_price = nz._bounded_decimal(money.get("amount"), allow_negative=False)
            if unit_price is None:
                reasons.append("fx_unavailable" if price.get("status") in {'ok', 'fx_unavailable'} else "price_unavailable")
            else:
                with localcontext() as context:
                    context.prec = nz.ARITHMETIC_PRECISION
                    total = quantity * (unit_price + additional)
                if total > budget:
                    reasons.append("over_budget")

    result = {"offer_id": offer_id, "supplier": supplier, "feasible": not reasons,
            "reasons": reasons, "arrival_date": arrival.isoformat() if arrival else None,
            "unit_price_per_unit": nz.fmt(unit_price) if unit_price is not None else None,
            "unit_price": nz.fmt(unit_price) if unit_price is not None else None,
            "quantity_unit": quantity_unit,
            "total_cost": nz.fmt(total) if total is not None else None,
            "quantity": nz.fmt(quantity)}
    if quantity_unit == 'kg':
        result['unit_price_per_kg'] = result['unit_price']
        result['quantity_kg'] = result['quantity']
    return result


def build_plan(offers: list[dict], request: dict, *, today: date) -> dict:
    if not isinstance(request, dict) or not isinstance(offers, list):
        raise DemoError("invalid_request", "Plan request and accepted offers are required", 400)
    ids = request.get("offer_ids")
    versions = request.get("expected_versions")
    if (not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value for value in ids)
            or len(ids) != len(set(ids)) or not isinstance(versions, dict) or set(versions) != set(ids)):
        raise DemoError("invalid_request", "Offer IDs and versions must match exactly", 400)
    by_id = {offer.get("offer_id"): offer for offer in offers if isinstance(offer, dict)}
    if set(by_id) != set(ids) or len(by_id) != len(offers):
        raise DemoError("offer_not_found", "A selected offer is unavailable", 404)
    for offer_id in ids:
        offer = by_id[offer_id]
        expected = versions[offer_id]
        if type(expected) is not int or expected != offer.get("version"):
            raise DemoError("stale_version", "An offer version changed; refresh before planning", 409)
        if offer.get("status") != "saved":
            raise DemoError("offer_not_accepted", "Only accepted offers can enter a plan", 409)

    companies = {company_for_offer(offer) for offer in offers}
    company_id = next(iter(companies)) if len(companies) == 1 else None
    if company_id is None or request.get('company_id', company_id) != company_id:
        raise DemoError('company_mismatch', 'Offers and buyer company must match', 422)

    unit = request.get('quantity_unit', 'kg')
    if not isinstance(unit, str) or not unit or len(unit) > 30:
        raise DemoError('invalid_value', 'quantity_unit is required')
    generic = any(key in request for key in ('quantity', 'current_stock', 'additional_cost_per_unit'))
    if generic and not all(key in request for key in ('quantity', 'current_stock', 'additional_cost_per_unit', 'quantity_unit')):
        raise DemoError('invalid_request', 'Generic quantity, stock, extra cost, and unit are required together', 422)
    if unit != 'kg' and any(key in request for key in ('quantity_kg', 'current_stock_kg', 'additional_cost_per_kg')):
        raise DemoError('unit_mismatch', 'Kilogram aliases cannot describe a non-kilogram material', 422)
    quantity = _number(request, 'quantity' if generic else 'quantity_kg', positive=True)
    stock = _number(request, 'current_stock' if generic else 'current_stock_kg')
    budget = _number(request, "budget")
    additional = _number(request, 'additional_cost_per_unit' if generic else 'additional_cost_per_kg')
    if generic and unit == 'kg':
        for old, current in (('quantity_kg', quantity), ('current_stock_kg', stock),
                             ('additional_cost_per_kg', additional)):
            if old in request and _number(request, old) != current:
                raise DemoError('unit_mismatch', 'Kilogram aliases disagree with generic values', 422)
    decision = _date(request, "decision_date")
    need = _date(request, "need_by")
    if decision > need:
        raise DemoError("invalid_value", "need_by cannot precede decision_date")
    material = request.get("material")
    reporting = request.get("reporting_currency")
    if not isinstance(material, str) or not material.strip() or len(material) > 100:
        raise DemoError("invalid_value", "material is required")
    if generic:
        from .company import list_companies
        matching = [m for p in list_companies() for m in p['materials']
                    if m['material_id'] == material and m['quantity_unit'] == unit]
        if not matching:
            raise DemoError('unit_mismatch', 'Material and quantity unit do not match', 422)
    if not isinstance(reporting, str) or reporting not in nz.ISO_4217_CODES or reporting in nz._NON_SETTLEMENT_CODES:
        raise DemoError("invalid_value", "reporting_currency must be an ISO currency")
    delays = request.get("delay_days_by_offer") or {}
    if (not isinstance(delays, dict) or not set(delays) <= set(ids)
            or any(type(value) is not int or not 0 <= value <= 3650 for value in delays.values())):
        raise DemoError("invalid_value", "Offer delays must be whole nonnegative days")

    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        purchase = max(quantity - stock, Decimal(0))
    alternatives = ([] if purchase == 0 else [
        _alternative(by_id[offer_id], material=material, quantity=purchase, decision=decision, today=today,
                     need=need, reporting_currency=reporting, budget=budget,
                     additional=additional, delay=delays.get(offer_id, 0), quantity_unit=unit) for offer_id in ids])
    feasible = [alternative for alternative in alternatives if alternative["feasible"]]
    selected = min(feasible, key=lambda a: (Decimal(a["total_cost"]), a["offer_id"])) if feasible else None
    if purchase == 0:
        explanation = "Opening stock covers the stated need; no purchase is required."
    elif selected:
        explanation = (f"{selected['supplier']} is the lowest-cost feasible single-offer option under the "
                       "stated synthetic assumptions.")
    else:
        explanation = "No selected single offer meets every stated constraint."
    fx_pairs = {(lc.usable(item.get('pricing', {})) or {}).get('unit_price', {}).get('currency')
                for offer in offers for item in offer.get('items', [])}
    fx_rates = []
    for base in sorted(fx_pairs - {reporting, None}):
        rate = synthetic_rate(base, reporting, decision)
        if rate.rate is not None:
            fx_rates.append({'base': base, 'quote': reporting, 'rate': nz.fmt(rate.rate),
                             'rate_date': rate.rate_date, 'source': rate.source})
    assumptions = {"additional_cost_per_unit": nz.fmt(additional),
                   "additional_cost_currency": reporting, "additional_cost_source": "buyer_synthetic_assumption",
                   "fx_source": "synthetic_scenario", "fx_rates": fx_rates,
                   "delay_days_by_offer": delays, "comparison": "single_offer"}
    if unit == 'kg':
        assumptions['additional_cost_per_kg'] = nz.fmt(additional)
    plan_key = {"offer_ids": ids, "offer_versions": versions, "request": request}
    plan_id = "plan-" + hashlib.sha256(json.dumps(plan_key, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    result = {"plan_id": plan_id, "synthetic": True, "company_id": company_id,
            "decision_date": decision.isoformat(),
            "need_by": need.isoformat(), "material": material, "quantity": nz.fmt(quantity),
            "current_stock": nz.fmt(stock), "required_purchase": nz.fmt(purchase),
            "quantity_unit": unit,
            "reporting_currency": reporting, "assumptions": assumptions,
            "alternatives": alternatives, "selected_offer_id": selected["offer_id"] if selected else None,
            "total_cost": selected["total_cost"] if selected else ("0" if purchase == 0 else None),
            "explanation": explanation, "forecast_action": "ABSTAIN", "customer_savings": None,
            "offer_versions": {offer_id: versions[offer_id] for offer_id in ids}}
    if unit == 'kg':
        result.update(quantity_kg=result['quantity'], current_stock_kg=result['current_stock'],
                      required_purchase_kg=result['required_purchase'])
    return result
