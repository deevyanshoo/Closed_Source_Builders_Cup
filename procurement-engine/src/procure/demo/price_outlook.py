"""Read-only price and FX outlook for the hosted demo, from config/prediction_results_v1.json.

The numbers come from scripts/build_price_predictions.py (backtest gate) and are never recomputed here. Every
response carries company_id and context_version so the client can drop a reply that belongs to an earlier
company or session context. Statuses and labels are passed through unchanged; nothing is buy or wait advice.
Budget-at-risk reads the current plan from the session without changing it and answers only for that plan id.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Request

from procure.prediction.budget import budget_at_risk

from .company import get_company

RESULTS_FILE = 'prediction_results_v1.json'
STALE_AFTER_DAYS = 45
LABEL = 'Projection with uncertainty, not a recommendation'


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def load_results(path: Path | None = None) -> dict:
    if path is None:
        from procure.settings import get_settings
        path = get_settings().config_dir / RESULTS_FILE
    return _load(str(path))


def _month_end(month: str) -> date:
    year, number = (int(part) for part in month.split('-'))
    return date(year + (number == 12), number % 12 + 1, 1) - timedelta(days=1)


def _series_view(series_id: str, entry: dict, now: date) -> dict:
    age = (now - _month_end(entry['data_date'])).days
    stale = age > STALE_AFTER_DAYS
    return {**deepcopy(entry), 'series_id': series_id, 'age_days': age, 'stale': stale,
            'stale_label': f"Data is older than {STALE_AFTER_DAYS} days: latest month {entry['data_date']}." if stale else None}


def get_price_outlook(company_id: str, material_id: str, *, now: date, context_version: int | None = None,
                      results: dict | None = None) -> dict:
    results = results or load_results()
    base = {'kind': 'price', 'company_id': company_id, 'context_version': context_version, 'material_id': material_id,
            'label': LABEL}
    company = get_company(company_id)
    if company is None:
        return {**base, 'status': 'unknown_company'}
    if material_id not in {m['material_id'] for m in company['materials']}:
        return {**base, 'status': 'unknown_material'}
    mapping = next((row for row in results['material_map']
                    if row['company_id'] == company_id and row['material_id'] == material_id), None)
    if mapping is None or mapping['series_id'] not in results['series']:
        return {**base, 'status': 'no_series',
                'message': 'No public monthly benchmark series is mapped to this material.'}
    return {**base, 'status': 'available', 'match': mapping['match'], 'note': mapping['note'],
            'series': _series_view(mapping['series_id'], results['series'][mapping['series_id']], now)}


def _company_currencies(company: dict) -> list[str]:
    codes = {company['reporting_currency'], *company.get('currencies', []),
             *[s['settlement_currency'] for s in company.get('suppliers', []) if s.get('settlement_currency')],
             *[r['currency'] for r in company.get('routes', []) if r.get('currency')]}
    return sorted(codes - {'USD'})


def get_fx_outlook(company_id: str, *, now: date, context_version: int | None = None,
                   results: dict | None = None) -> dict:
    results = results or load_results()
    base = {'kind': 'fx', 'company_id': company_id, 'context_version': context_version, 'label': LABEL}
    company = get_company(company_id)
    if company is None:
        return {**base, 'status': 'unknown_company', 'currencies': []}
    rows = []
    for code in _company_currencies(company):
        series_id = f'fx:{code}'
        if series_id in results['series']:
            rows.append({'currency': code, 'status': 'available',
                         'series': _series_view(series_id, results['series'][series_id], now)})
        else:
            reason = results['unavailable'].get(series_id) or 'No monthly history in the snapshot.'
            rows.append({'currency': code, 'status': 'no history available', 'reason': reason})
    return {**base, 'status': 'available', 'reporting_currency': company['reporting_currency'], 'currencies': rows}


def current_plan(session) -> dict | None:
    """Read-only view of the session's current comparison: plan id, material, quantity, unit and currency."""
    request, plan = session.plan_request, session.last_plan
    if not request or not plan or not plan.get('plan_id'):
        return None
    legacy = 'quantity' not in request and 'quantity_kg' in request
    return {'plan_id': plan['plan_id'], 'material_id': request.get('material'),
            'quantity': request['quantity_kg'] if legacy else request.get('quantity'),
            'unit': 'kg' if legacy else request.get('quantity_unit'),
            'reporting_currency': request.get('reporting_currency')}


def get_budget_at_risk(company_id: str, material_id: str, *, plan: dict | None, now: date,
                       context_version: int | None = None, expected_plan_id: str | None = None,
                       results: dict | None = None) -> dict:
    results = results or load_results()
    base = {'kind': 'budget', 'company_id': company_id, 'context_version': context_version,
            'material_id': material_id, 'plan_id': None, 'horizons': []}
    if get_company(company_id) is None:
        return {**base, 'status': 'unknown_company', 'reason': 'Unknown company.'}
    if plan is None or plan['material_id'] != material_id:
        return {**base, 'status': 'no_current_plan', 'reason': 'Run a comparison first.'}
    if expected_plan_id is not None and expected_plan_id != plan['plan_id']:
        return {**base, 'status': 'no_current_plan',
                'reason': 'The comparison changed since this was requested; reload the current comparison.'}
    outlook = get_price_outlook(company_id, material_id, now=now, context_version=context_version, results=results)
    currency = plan['reporting_currency'] or get_company(company_id)['reporting_currency']
    fx = results['series'].get(f'fx:{currency}')
    try:
        result = budget_at_risk(outlook_entry=outlook, fx_entry={**fx, 'series_id': f'fx:{currency}'} if fx else None,
                                quantity=plan['quantity'], unit=plan['unit'], reporting_currency=currency)
    except ValueError as error:
        return {**base, 'plan_id': plan['plan_id'], 'status': 'invalid_quantity', 'reason': str(error)}
    return {**result, **{key: value for key, value in base.items() if key != 'horizons'}, 'plan_id': plan['plan_id']}


def outlook_export(price: dict, fx: dict, budget: dict | None = None) -> dict:
    """Bounded JSON export entry; every view must share company and context."""
    scopes = {(view['company_id'], view['context_version']) for view in (price, fx, budget) if view is not None}
    if len(scopes) != 1:
        raise ValueError('price, FX and budget outlook scope differ')
    return {'schema': 'price-outlook-export.v1', 'company_id': price['company_id'],
            'context_version': price['context_version'], 'label': LABEL, 'price': deepcopy(price), 'fx': deepcopy(fx),
            'budget': deepcopy(budget)}


def price_outlook_router(*, session, now) -> APIRouter:
    """GET routes bound to the coordinator's session helper (no shared-file logic lives here)."""
    router = APIRouter()

    def today() -> date:
        value = now()
        return value.date() if isinstance(value, datetime) else value

    @router.get('/api/demo/price-outlook')
    async def price_outlook(request: Request, material_id: str):
        value = session(request)
        return get_price_outlook(value.company_id, material_id, now=today(),
                                 context_version=value.context_version)

    @router.get('/api/demo/fx-outlook')
    async def fx_outlook(request: Request):
        value = session(request)
        return get_fx_outlook(value.company_id, now=today(),
                              context_version=value.context_version)

    @router.get('/api/demo/budget-at-risk')
    async def budget(request: Request, material_id: str, plan_id: str | None = None):
        value = session(request)
        with getattr(value, 'lock', None) or nullcontext():
            company_id, version, plan = value.company_id, value.context_version, deepcopy(current_plan(value))
        return get_budget_at_risk(company_id, material_id, plan=plan, now=today(), context_version=version,
                                  expected_plan_id=plan_id)

    return router
