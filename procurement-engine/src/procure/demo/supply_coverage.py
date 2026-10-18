"""Pure, dated stock-and-one-receipt assessment for reviewed demo plans."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

from procure.offers import normalize as nz

from .quote_service import company_for_offer


def _number(value):
    if not isinstance(value, str):
        return None
    return nz._bounded_decimal(value, allow_negative=False)


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _fmt(value):
    return nz.fmt(value) if value is not None else None


def _empty(company, plan):
    return {
        'schema': 1, 'company_id': plan.get('company_id'), 'plan_id': plan.get('plan_id'),
        'material_id': plan.get('material'), 'quantity_unit': plan.get('quantity_unit'),
        'decision_date': plan.get('decision_date'), 'horizon_end': plan.get('need_by'),
        'inventory_as_of': plan.get('decision_date'),
        'consumption_as_of': company.get('as_of') if isinstance(company, dict) else None,
        'stock': None, 'consumption_per_day': None, 'basis': 'constant_daily_consumption',
        'status': 'unknown', 'order_deadline_met': None, 'continuous_supply_covered': None,
        'stock_cover_days': None, 'stock_depletion_date': None, 'selected_arrival_date': None,
        'uncovered_days': None, 'uncovered_quantity': None, 'bridge_quantity': None,
        'bridge_needed_by': None, 'assumptions': [
            'Opening stock is the reviewed buyer amount as of decision_date.',
            'Daily consumption is held constant through need_by; receipt is available at the start of arrival_date.',
            'This is a planning projection, not an observed inventory movement or confirmed bridge supply.'
        ], 'missing_inputs': [], 'evidence_ids': [], 'alternatives': [],
    }


def _project(stock, daily, receipt, *, decision, horizon, arrival):
    """Count unmet demand over [decision, horizon), adding one receipt at arrival start."""
    span = (horizon - decision).days
    before = min(max((arrival - decision).days, 0), span) if arrival else span
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        first_demand = daily * before
        pre_gap = max(first_demand - stock, Decimal(0))
        remaining = max(stock - first_demand, Decimal(0))
        after = span - before
        usable_receipt = receipt if arrival and arrival < horizon else Decimal(0)
        post_gap = max(daily * after - remaining - usable_receipt, Decimal(0))
        gap_quantity = pre_gap + post_gap
        gap_days = gap_quantity / daily if daily else Decimal(0)
        bridge = max(pre_gap, daily * span - stock - usable_receipt, Decimal(0))
        first_gap = None
        if daily and pre_gap > 0:
            first_gap = decision + timedelta(days=int(stock / daily))
        elif daily and post_gap > 0:
            first_gap = (arrival if usable_receipt else decision) + timedelta(
                days=int(((remaining + usable_receipt) if usable_receipt else stock) / daily))
    return gap_quantity, gap_days, bridge, first_gap.isoformat() if first_gap else None


def _assess_supply_coverage(company: dict, plan: dict, *, offers: list[dict] | None = None) -> dict:
    """Assess continuity without changing plan selection or accepted quote state.

    Missing, stale, foreign, or incoherent facts return unknown with named inputs.
    """
    if not isinstance(plan, dict):
        plan = {}
    result = _empty(company, plan)
    missing = result['missing_inputs']
    if not isinstance(company, dict) or company.get('company_id') != plan.get('company_id'):
        missing.append('company_id')
    materials = company.get('materials', []) if isinstance(company, dict) else []
    owned = [row for row in materials if isinstance(row, dict) and row.get('material_id') == plan.get('material')]
    if len(owned) != 1:
        missing.append('material_id')
    material = owned[0] if len(owned) == 1 else {}
    if not plan.get('quantity_unit') or material.get('quantity_unit') != plan.get('quantity_unit'):
        missing.append('quantity_unit')
    stock = _number(plan.get('current_stock'))
    daily = _number(material.get('consumption_per_day'))
    receipt = _number(plan.get('required_purchase'))
    quantity = _number(plan.get('quantity'))
    result['stock'], result['consumption_per_day'] = _fmt(stock), _fmt(daily)
    for value, field in ((quantity if quantity and quantity > 0 else None, 'quantity'),
                         (stock, 'current_stock'), (daily, 'consumption_per_day'),
                         (receipt, 'required_purchase')):
        if value is None:
            missing.append(field)
    if quantity is not None and stock is not None and receipt is not None:
        with localcontext() as context:
            context.prec = nz.ARITHMETIC_PRECISION
            if receipt != max(quantity - stock, Decimal(0)) and 'required_purchase' not in missing:
                missing.append('required_purchase')
    if plan.get('quantity_unit') == 'kg':
        for alias, canonical in (('quantity_kg', quantity), ('current_stock_kg', stock),
                                 ('required_purchase_kg', receipt)):
            if alias in plan and _number(plan[alias]) != canonical:
                missing.append(alias)
    elif any(alias in plan for alias in ('quantity_kg', 'current_stock_kg', 'required_purchase_kg')):
        missing.append('quantity_unit')
    decision, horizon = _date(plan.get('decision_date')), _date(plan.get('need_by'))
    consumption_as_of = _date(material.get('as_of', company.get('as_of') if isinstance(company, dict) else None))
    result['consumption_as_of'] = consumption_as_of.isoformat() if consumption_as_of else None
    if decision is None:
        missing.append('decision_date')
    if horizon is None or (decision and horizon < decision):
        missing.append('need_by')
    elif horizon == date.max:
        missing.append('need_by_exclusive_end')
    if consumption_as_of is None or (decision and consumption_as_of > decision):
        missing.append('consumption_as_of')
    if not isinstance(plan.get('plan_id'), str) or not plan['plan_id']:
        missing.append('plan_id')

    alternatives = plan.get('alternatives')
    if not isinstance(alternatives, list):
        missing.append('alternatives')
        alternatives = []
    selected_id = plan.get('selected_offer_id')
    selected = [row for row in alternatives if isinstance(row, dict) and row.get('offer_id') == selected_id]
    if receipt is not None and receipt > 0 and (not isinstance(selected_id, str) or len(selected) != 1):
        # An all-late/unselected comparison can still project opening-stock loss.
        if selected_id is not None:
            missing.append('selected_offer_id')
    if receipt == 0 and selected_id is not None:
        missing.append('selected_offer_id')
    if selected_id is not None and len(selected) != 1:
        missing.append('selected_offer_id')
    by_id = {}
    if offers is not None:
        if not isinstance(offers, list) or any(not isinstance(o, dict) for o in offers):
            missing.append('offers')
        else:
            by_id = {o.get('offer_id'): o for o in offers}
            versions = plan.get('offer_versions')
            ids = {a.get('offer_id') for a in alternatives if isinstance(a, dict)}
            if (len(by_id) != len(offers) or not isinstance(versions, dict)
                    or set(versions) != set(by_id) or (receipt != 0 and ids != set(by_id))
                    or any(type(versions.get(oid)) is not int or o.get('version') != versions[oid]
                           or o.get('status') != 'saved' for oid, o in by_id.items())):
                missing.append('offer_versions')
            if any(company_for_offer(o) != plan.get('company_id') for o in offers):
                missing.append('offer_ownership')
    if missing:
        return result

    exclusive_end = horizon + timedelta(days=1)

    if daily:
        with localcontext() as context:
            context.prec = nz.ARITHMETIC_PRECISION
            cover = stock / daily
        result['stock_cover_days'] = _fmt(cover)
        try:
            depletion = decision + timedelta(days=int(cover))
            result['stock_depletion_date'] = depletion.isoformat()
        except (OverflowError, ValueError):
            missing.append('stock_depletion_date')
            return result

    for row in alternatives:
        if not isinstance(row, dict):
            missing.append('alternatives')
            return result
        arrival = _date(row.get('arrival_date'))
        item = {'offer_id': row.get('offer_id'), 'arrival_date': row.get('arrival_date'),
                'status': 'unknown', 'order_deadline_met': None, 'continuous_supply_covered': None,
                'uncovered_days': None, 'uncovered_quantity': None, 'bridge_quantity': None,
                'bridge_needed_by': None}
        current_offer = by_id.get(row.get('offer_id')) if offers is not None else None
        material_matches = (current_offer is None or any(
            isinstance(entry, dict) and entry.get('material', {}).get('status') in {'extracted', 'corrected'}
            and entry['material'].get('value') == plan.get('material')
            for entry in current_offer.get('items', [])))
        row_quantity = _number(row.get('quantity'))
        coherent = (row_quantity == receipt and row.get('quantity_unit') == plan.get('quantity_unit')
                    and (plan.get('quantity_unit') != 'kg' or _number(row.get('quantity_kg')) == receipt))
        if 'offer_version' in row and row['offer_version'] != plan.get('offer_versions', {}).get(row.get('offer_id')):
            coherent = False
        if row.get('offer_id') == selected_id and not coherent:
            if row_quantity != receipt:
                missing.append('selected_receipt_quantity')
            if plan.get('quantity_unit') == 'kg' and _number(row.get('quantity_kg')) != receipt:
                missing.append('selected_receipt_quantity_kg')
            if row.get('quantity_unit') != plan.get('quantity_unit'):
                missing.append('selected_receipt_unit')
            if 'offer_version' in row and row['offer_version'] != plan.get('offer_versions', {}).get(selected_id):
                missing.append('selected_receipt_version')
        if arrival is not None and decision <= arrival and material_matches and coherent:
            quantity, days, bridge, first_gap = _project(stock, daily, receipt, decision=decision,
                                                          horizon=exclusive_end, arrival=arrival)
            item.update(status='covered' if quantity == 0 else 'gap',
                        order_deadline_met=arrival < exclusive_end,
                        continuous_supply_covered=quantity == 0,
                        uncovered_days=_fmt(days), uncovered_quantity=_fmt(quantity),
                        bridge_quantity=_fmt(bridge),
                        bridge_needed_by=first_gap if bridge > 0 else None)
        result['alternatives'].append(item)

    if missing:
        return result

    chosen = next((row for row in result['alternatives'] if row['offer_id'] == selected_id), None)
    if selected_id is not None and (chosen is None or chosen['status'] == 'unknown'):
        missing.append('selected_offer_material_or_arrival')
        return result
    if chosen is not None:
        result.update({key: chosen[key] for key in (
            'status', 'order_deadline_met', 'continuous_supply_covered', 'uncovered_days',
            'uncovered_quantity', 'bridge_quantity', 'bridge_needed_by')})
        result['selected_arrival_date'] = chosen['arrival_date']
        offer = by_id.get(selected_id)
        document_id = (offer.get('document') or {}).get('document_id') if offer else None
        if isinstance(document_id, str):
            result['evidence_ids'].append(document_id)
    else:
        quantity, days, bridge, first_gap = _project(stock, daily, Decimal(0), decision=decision,
                                                     horizon=exclusive_end, arrival=None)
        result.update(status='covered' if quantity == 0 else 'gap',
                      order_deadline_met=(receipt == 0), continuous_supply_covered=quantity == 0,
                      uncovered_days=_fmt(days), uncovered_quantity=_fmt(quantity),
                      bridge_quantity=_fmt(bridge),
                      bridge_needed_by=first_gap if bridge > 0 else None)
    return result


def assess_supply_coverage(company: dict, plan: dict, *, offers: list[dict] | None = None) -> dict:
    """Return an unknown assessment for malformed saved state instead of raising."""
    try:
        return _assess_supply_coverage(company, plan, offers=offers)
    except (TypeError, ValueError, OverflowError, InvalidOperation, KeyError, AttributeError):
        safe_plan = plan if isinstance(plan, dict) else {}
        result = _empty(company, safe_plan)
        result['missing_inputs'].append('malformed_plan_or_offer')
        return result


def with_supply_coverage(company: dict, plan: dict | None, *, offers: list[dict] | None = None) -> dict | None:
    if plan is None:
        return None
    enriched = deepcopy(plan)
    enriched['supply_coverage'] = assess_supply_coverage(company, plan, offers=offers)
    return enriched
