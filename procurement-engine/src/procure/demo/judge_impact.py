"""Read-only, decimal impact display for one owned baseline and recovery comparison."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation


def _decimal(value):
    if not isinstance(value, str):
        return None
    try:
        amount = Decimal(value)
        return amount if amount.is_finite() else None
    except InvalidOperation:
        return None


def _plain(value):
    return format(value, 'f')


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _metric(value=None, *, unit=None, currency=None, as_of=None, reason=None, label=None):
    return {'value': value, 'unit': unit, 'currency': currency, 'as_of': as_of,
            'status': 'available' if value is not None else 'unavailable',
            'reason': reason, 'label': label}


def _paired_requests(snapshot, recovery, baseline, versions):
    original = recovery.get('baseline_request')
    scenario = recovery.get('scenario_request')
    current = snapshot.get('plan_request')
    if (not isinstance(original, dict) or not original or
        not isinstance(scenario, dict) or not scenario or
        not isinstance(current, dict) or current != original):
        return False
    without_delay = lambda value: {key: item for key, item in value.items()
                                   if key != 'delay_days_by_offer'}
    delays = scenario.get('delay_days_by_offer') or {}
    if (without_delay(original) != without_delay(scenario) or
        not isinstance(delays, dict) or not set(delays) <= set(versions) or
        any(type(day) is not int or not 0 <= day <= 3650 for day in delays.values())):
        return False
    return (original.get('company_id', snapshot.get('company_id')) == snapshot.get('company_id')
            and original.get('material') == baseline.get('material')
            and original.get('quantity_unit', 'kg') == baseline.get('quantity_unit')
            and original.get('reporting_currency') == baseline.get('reporting_currency')
            and original.get('decision_date') == baseline.get('decision_date')
            and original.get('need_by') == baseline.get('need_by')
            and original.get('expected_versions') == versions
            and isinstance(original.get('offer_ids'), list)
            and len(original['offer_ids']) == len(versions)
            and set(original['offer_ids']) == set(versions))


def build_judge_impact(snapshot: dict, recovery: dict, affected_order_ids: list[str]) -> dict:
    """Show exposure and additional scenario cost, never a realized loss."""
    baseline = recovery.get('baseline') or {}
    scenario = recovery.get('scenario') or {}
    company_id = snapshot.get('company_id')
    current = snapshot.get('plan') or snapshot.get('baseline_plan') or {}
    versions = baseline.get('offer_versions') or {}
    selected = baseline.get('selected_offer_id')
    candidate = scenario.get('selected_offer_id')
    request_linked = _paired_requests(snapshot, recovery, baseline, versions)
    selected_rows = [row for row in scenario.get('alternatives', [])
                     if isinstance(row, dict) and row.get('offer_id') == candidate]
    selected_valid = (candidate is None or
                      (len(selected_rows) == 1 and selected_rows[0].get('feasible') is True
                       and selected_rows[0].get('quantity_unit') == baseline.get('quantity_unit')))
    compatible = (baseline.get('company_id') == company_id == snapshot.get('company', {}).get('company_id')
                  and isinstance(baseline.get('plan_id'), str)
                  and baseline.get('plan_id') == current.get('plan_id')
                  and versions == current.get('offer_versions') and selected in versions
                  and scenario.get('company_id') == company_id
                  and scenario.get('material') == baseline.get('material')
                  and scenario.get('quantity_unit') == baseline.get('quantity_unit')
                  and scenario.get('reporting_currency') == baseline.get('reporting_currency')
                  and scenario.get('offer_versions') == versions
                  and scenario.get('decision_date') == baseline.get('decision_date')
                  and scenario.get('need_by') == baseline.get('need_by')
                  and candidate in (None, *versions)
                  and selected_valid
                  and _date(baseline.get('decision_date')) is not None)
    currency = baseline.get('reporting_currency') if compatible else None
    unit = baseline.get('quantity_unit') if compatible else None
    as_of = baseline.get('decision_date') if compatible else None
    result = {'schema': 1, 'company_id': company_id,
              'context_version': snapshot.get('context_version'),
              'baseline_plan_id': baseline.get('plan_id') if compatible else None,
              'scenario_plan_id': scenario.get('plan_id') if compatible else None,
              'scenario_feasible': bool(compatible and candidate
                                        and _decimal(scenario.get('total_cost')) is not None),
              'affected_order_ids': [], 'evidence_refs': []}
    delays = (recovery.get('scenario_request') or {}).get('delay_days_by_offer') or {}
    days = delays.get(selected)
    result['gap_days'] = _metric(days if compatible and type(days) is int and 0 <= days <= 3650 else None,
                                 unit='day', as_of=as_of,
                                 reason=None if compatible and type(days) is int else 'No validated selected-offer delay.',
                                 label='Assumed planning delay')
    alternatives = [a for a in baseline.get('alternatives', []) if a.get('offer_id') == selected]
    price = _decimal(alternatives[0].get('unit_price_per_unit')) if len(alternatives) == 1 else None
    if (len(alternatives) != 1 or alternatives[0].get('feasible') is False or
        alternatives[0].get('quantity_unit') != unit or
        alternatives[0].get('price_currency', currency) != currency or
        (_date(alternatives[0].get('price_as_of')) is not None and
         _date(alternatives[0]['price_as_of']) > _date(as_of)) or
        ('price_as_of' in alternatives[0] and _date(alternatives[0].get('price_as_of')) is None)):
        price = None
    owned_orders = {o.get('order_id'): o for o in snapshot.get('company', {}).get('orders', [])
                    if isinstance(o, dict) and o.get('order_id')}
    ids = list(dict.fromkeys(affected_order_ids)) if isinstance(affected_order_ids, list) else []
    shipments = (snapshot.get('shipments') or {}).get('shipments', [])
    values = []
    for order_id in ids:
        order = owned_orders.get(order_id)
        linked = [row for row in shipments if isinstance(row, dict)
                  and row.get('order_id') == order_id
                  and row.get('company_id') == company_id
                  and row.get('plan_id') == baseline.get('plan_id')
                  and row.get('material_id') == baseline.get('material')
                  and row.get('offer_id') == selected
                  and row.get('offer_version') == versions.get(selected)
                  and row.get('quantity_unit') == unit]
        if (not compatible or not order or order.get('material_id') != baseline.get('material')
                or order.get('unit') != unit or len(linked) != 1):
            values = []
            break
        qty = _decimal(order.get('quantity'))
        linked_demand = _decimal(linked[0].get('order_demand_quantity')) if 'order_demand_quantity' in linked[0] else qty
        if (qty is None or qty < 0 or linked_demand != qty or
            (_date(order.get('as_of')) is not None and _date(order['as_of']) > _date(as_of))):
            values = []
            break
        values.append(qty)
    if compatible and price is not None and values and len(values) == len(ids) and currency:
        result['planned_spend_exposed'] = _metric(_plain(sum(values, Decimal('0')) * price),
                                                   unit=unit, currency=currency, as_of=as_of,
                                                   label='Planned spend exposed, not realized loss')
        result['affected_order_ids'] = ids
        result['evidence_refs'] = [baseline['plan_id'], selected] + ids
    else:
        result['planned_spend_exposed'] = _metric(unit=unit, currency=currency, as_of=as_of,
                                                   reason='Owned orders, compatible unit/currency, or selected reviewed price unavailable.',
                                                   label='Planned spend exposed, not realized loss')
    delta = _decimal(recovery.get('cost_delta'))
    baseline_total = _decimal(baseline.get('total_cost'))
    scenario_total = _decimal(scenario.get('total_cost'))
    if (not result['scenario_feasible'] or not request_linked or
        baseline_total is None or scenario_total is None or delta is None or
        scenario.get('reporting_currency') != currency or
        delta != scenario_total - baseline_total):
        delta = None
    result['additional_recovery_cost'] = _metric(_plain(delta) if delta is not None else None,
                                                  currency=currency, as_of=as_of,
                                                  reason=None if delta is not None else 'No feasible compatible paired cost.',
                                                  label='Additional recovery cost')
    carbon = snapshot.get('carbon_assessment') or {}
    carbon_result = carbon.get('result') or {}
    estimate = next((r.get('estimate') or {} for r in carbon_result.get('suppliers', [])
                     if r.get('offer_id') == selected), {})
    carbon_value = _decimal(estimate.get('total_kg_co2e'))
    if (not compatible or carbon.get('company_id') != company_id or
        carbon.get('context_version') != snapshot.get('context_version') or
        carbon.get('plan_id') != baseline.get('plan_id') or
        carbon.get('offer_versions') != baseline.get('offer_versions') or
        estimate.get('combined_status') != 'combined'):
        carbon_value = None
    result['carbon'] = _metric(_plain(carbon_value) if carbon_value is not None else None,
                               unit='kg CO2e', as_of=as_of,
                               reason=None if carbon_value is not None else 'No compatible authoritative whole-scenario assessment.',
                               label='Baseline carbon estimate')
    return result
