"""Conservative read-only service trace over owned, dated operations facts."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json

from .market_prediction import influence_for_material


def _decimal(raw):
    if not isinstance(raw, str) or len(raw) > 40:
        raise ValueError('decimal string required')
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not value.is_finite() or value < 0:
        raise ValueError('nonnegative finite decimal required')
    # Bound both scientific notation and long fractional inputs before any
    # context-limited arithmetic or fixed-point rendering. A 90-day trace
    # with twelve receipts remains comfortably within Decimal's precision.
    if len(value.as_tuple().digits) > 18 or value.adjusted() > 17 or value.as_tuple().exponent < -12:
        raise ValueError('decimal magnitude, precision or scale out of bounds')
    return value


def _fmt(value):
    return format(value.normalize(), 'f')


def simulate_daily_service(*, stock: str, daily_demand: str, safety: str,
                           start: str, end: str, unit: str, receipts: list[dict]) -> dict:
    """Date-only firm receipts become usable at the next local day start.

    This is a conservative daily proof. It never validates a same-day
    commitment or unknown intra-day demand, calendar, or quality release.
    """
    opening, demand, floor = _decimal(stock), _decimal(daily_demand), _decimal(safety)
    if not isinstance(receipts, list) or len(receipts) > 12:
        raise ValueError('at most twelve receipts required')
    parsed_receipts = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError('invalid receipt')
        quantity = _decimal(receipt['quantity'])
        parsed_receipts.append((receipt, quantity))
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 90 or not isinstance(unit, str) or not unit:
        raise ValueError('invalid service window')
    credited = {}
    for receipt, quantity in parsed_receipts:
        if receipt.get('firm') is not True:
            continue
        arrival = date.fromisoformat(receipt['arrival_date']) + timedelta(days=1)
        credited[arrival] = credited.get(arrival, Decimal(0)) + quantity
    balance, intervals, open_gap, largest_gap, trace = (
        opening, [], None, Decimal(0), [])
    for index in range((last - first).days + 1):
        day = first + timedelta(days=index)
        balance += credited.get(day, Decimal(0))
        balance -= demand
        deficit = max(floor - balance, Decimal(0))
        if deficit:
            if open_gap is None:
                open_gap = {'start': day.isoformat(), 'end': day.isoformat()}
                intervals.append(open_gap)
            else:
                open_gap['end'] = day.isoformat()
        else:
            open_gap = None
        largest_gap = max(largest_gap, deficit)
        trace.append({'date': day.isoformat(), 'firm_receipts_usable': _fmt(credited.get(day, Decimal(0))),
                      'demand': _fmt(demand), 'closing_stock': _fmt(balance),
                      'safety_gap': _fmt(deficit), 'unit': unit})
    return {'status': 'REPLAN_REQUIRED' if intervals else 'COVERED_DAILY_DATE_ONLY',
            'first_gap_date': intervals[0]['start'] if intervals else None,
            'gap_quantity': _fmt(largest_gap),
            'uncovered_interval': intervals[0] if intervals else None,
            'uncovered_intervals': intervals,
            'unit': unit, 'trace': trace,
            'assumption': 'Date-only firm receipts usable at next local day start; exact same-day continuity unverified.'}


def preview_from_snapshot(snapshot: dict, material_id: str, market: dict,
                          overrides: dict | None = None) -> dict:
    """Use the existing covered plan and accepted quote versions, without mutation."""
    company = snapshot['company']
    material = next((row for row in company['materials'] if row['material_id'] == material_id), None)
    if material is None:
        raise ValueError('material outside company')
    influence = influence_for_material(company, material_id, market)
    plan = snapshot.get('plan')
    plan = plan if isinstance(plan, dict) and plan.get('material') == material_id else None
    coverage = plan.get('supply_coverage') if plan else None
    missing = []
    if plan is None:
        missing.append('reviewed_quote_comparison')
    if not isinstance(coverage, dict) or coverage.get('status') == 'unknown':
        missing.extend((coverage or {}).get('missing_inputs') or ['dated_supply_coverage'])
    # These facts are not present in today's fictional catalogue. The preview
    # cannot call a purchase feasible even if the older planner selected it.
    missing.extend(['safety_floor', 'receiving_calendar', 'quality_release_time',
                    'cash_and_payment_deadline', 'moq_and_capacity_evidence'])
    service = None
    hypothetical = overrides is not None
    if hypothetical:
        required = {'stock', 'daily_demand', 'safety', 'start', 'end', 'receipts'}
        if (not isinstance(overrides, dict) or set(overrides) != required or
                not isinstance(overrides['receipts'], list) or len(overrides['receipts']) > 12 or
                any(not isinstance(row, dict) or set(row) != {'arrival_date', 'quantity', 'firm'}
                    or type(row['firm']) is not bool for row in overrides['receipts'])):
            raise ValueError('Invalid hypothetical operations fields')
        service = simulate_daily_service(stock=overrides['stock'],
            daily_demand=overrides['daily_demand'], safety=overrides['safety'],
            start=overrides['start'], end=overrides['end'],
            unit=material['quantity_unit'], receipts=overrides['receipts'])
        missing.append('hypothetical_receipts_not_owned_or_verified')
    if not hypothetical and plan and coverage and coverage.get('status') != 'unknown':
        try:
            # An accepted quote is a possible purchase, not a placed order or
            # firm quality-released receipt. No firm receipts are recorded in
            # this workspace today, so none can close a stock gap.
            receipts = []
            if _decimal(plan['required_purchase']) > 0:
                missing.append('firm_usable_receipt')
            service = simulate_daily_service(stock=plan['current_stock'],
                daily_demand=material['consumption_per_day'], safety='0',
                start=plan['decision_date'], end=plan['need_by'],
                unit=material['quantity_unit'], receipts=receipts)
        except (KeyError, ValueError, TypeError, StopIteration):
            missing.append('dated_firm_receipt')
    replan = bool(service and service['status'] == 'REPLAN_REQUIRED')
    status = 'REPLAN_REQUIRED' if replan else 'MISSING_OPERATIONS_INPUT'
    marker = {'company': company['company_id'], 'material': material_id,
              'context_version': snapshot['context_version'],
              'plan_id': plan.get('plan_id') if plan else None,
              'offer_versions': plan.get('offer_versions') if plan else {},
              'market_run_hash': market['run_hash']}
    if hypothetical:
        marker['overrides'] = overrides
    digest = hashlib.sha256(json.dumps(marker, sort_keys=True).encode()).hexdigest()
    return {'contract_version': 'prediction.v1', 'decision_id': digest[:24],
            'company_id': company['company_id'], 'context_version': snapshot['context_version'],
            'material_id': material_id, 'quantity_unit': material['quantity_unit'],
            'status': status, 'recommendation': None, 'execution_state': 'NOT_EXECUTED_HUMAN_REVIEW_REQUIRED',
            'market_influence': influence, 'market_run_hash': market['run_hash'],
            'missing_inputs': sorted(set(missing)),
            'operations_baseline': {'plan_id': plan.get('plan_id'),
                                    'selected_offer_id': plan.get('selected_offer_id'),
                                    'total_cost': plan.get('total_cost'),
                                    'reporting_currency': plan.get('reporting_currency'),
                                    'supply_coverage': coverage} if plan else None,
            'service_trace': service, 'reason_codes':
                ['DATE_ONLY_STOCK_GAP'] if replan else ['OPERATIONS_FACTS_NOT_VERIFIED'],
            'evidence': {'company_as_of': company.get('as_of'),
                         'market_as_of_utc': market['as_of_utc'],
                         'publication_mode': market['publication_mode'],
                         'source': ('USER_HYPOTHETICAL date-only trace; receipts and operating conditions are unverified; no purchase executed'
                                    if hypothetical else
                                    'existing owned plan plus conservative date-only trace; no purchase executed')}}
