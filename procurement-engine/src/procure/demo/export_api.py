"""Read-only export opportunities for the hosted demo, from the committed UN Comtrade extract.

`get_export_opportunities` is the single function for HTTP and for the coordinator's Operator and voice tools.
It reads config files only and never calls UN Comtrade. Every response carries company_id and context_version so
a client can drop a reply that belongs to an earlier company or context. Nothing is stored in the session.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi import APIRouter, Request

from procure.exports.margin import margin_scenario
from procure.exports.ranking import LABEL, SORTS, rank_markets
from procure.exports.snapshot import FX_FILE, PRODUCTS_FILE, SNAPSHOT_FILE, load

from .company import get_company
from .sessions import SessionError


def _product(company_id: str, config_dir: Path | None) -> dict | None:
    return next((row for row in load(PRODUCTS_FILE, config_dir)['companies'] if row['company_id'] == company_id), None)


def _fx(currency: str, config_dir: Path | None) -> dict | None:
    return load(FX_FILE, config_dir)['rates'].get(currency)


def _margins(company_entry: dict, product: dict, inputs: dict, currency: str, years, config_dir) -> dict:
    ranked = {m['code']: m for m in rank_markets(company_entry, product, years=years)['markets']}
    margins = {}
    for code, entered in inputs.items():
        try:
            code = int(code)
        except (TypeError, ValueError):
            raise ValueError('Market codes must be numbers from this ranking.') from None
        if code not in ranked:
            raise ValueError(f'Market {code} is not in this company ranking.')
        if not isinstance(entered, dict):
            raise ValueError('Margin inputs must be an object of named values.')
        margins[code] = margin_scenario(market=ranked[code], inputs=entered, fx=_fx(currency, config_dir),
                                        reporting_currency=currency)
    return margins


def get_export_opportunities(company_id: str, *, sort: str = 'market_size', inputs: dict | None = None,
                             context_version: int | None = None, config_dir: Path | None = None) -> dict:
    if sort not in SORTS:
        raise ValueError(f'Unknown sort {sort!r}; choose one of {", ".join(SORTS)}.')
    base = {'kind': 'export_opportunities', 'company_id': company_id, 'context_version': context_version,
            'label': LABEL, 'sort': sort, 'markets': []}
    company = get_company(company_id)
    product = _product(company_id, config_dir) if company else None
    snapshot = load(SNAPSHOT_FILE, config_dir)
    entry = snapshot['companies'].get(company_id)
    if company is None or product is None or entry is None:
        return {**base, 'status': 'unknown_company'}
    currency = company['reporting_currency']
    years = tuple(snapshot['years_requested'])
    margins = _margins(entry, product, inputs or {}, currency, years, config_dir)
    ranking = rank_markets(entry, product, sort=sort, margins=margins, years=years)
    terms = snapshot['terms']
    return {**base, **ranking, 'status': 'available', 'home_country': product['home_country'],
            'reporting_currency': currency,
            'source': {'provider': terms['provider'], 'attribution': terms['attribution'], 'use_policy': terms['use_policy'],
                       'terms_summary': terms['summary'], 'endpoint': snapshot['endpoint'], 'years': list(years),
                       'built_on': snapshot['built_on'], 'hs_revision': load(PRODUCTS_FILE, config_dir)['hs_revision']}}


def export_opportunities_export(view: dict) -> dict:
    """Bounded JSON export entry for the existing workspace export."""
    return {'schema': 'export-opportunities-export.v1', 'company_id': view['company_id'],
            'context_version': view['context_version'], 'label': view['label'], 'sort': view['sort'],
            'product': deepcopy(view.get('product')), 'markets': deepcopy(view['markets']),
            'source': deepcopy(view.get('source'))}


def export_opportunities_router(*, session, mutate, body, shape, context) -> APIRouter:
    """Routes bound to the coordinator's session, CSRF, body, shape and context helpers."""
    router = APIRouter()

    def checked(call):
        try:
            return call()
        except ValueError as error:
            raise SessionError('invalid_request', str(error), 422) from None

    @router.get('/api/demo/export-opportunities')
    async def export_opportunities(request: Request, sort: str = 'market_size'):
        value = session(request)
        with value.lock:
            company_id, version = value.company_id, value.context_version
        return checked(lambda: get_export_opportunities(company_id, sort=sort, context_version=version))

    @router.post('/api/demo/export-opportunities/margin')
    async def export_margin(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('market_code', 'inputs'), ('expected_context_version',))
        with value.lock:
            context(value, data)
            company_id, version = value.company_id, value.context_version
        view = checked(lambda: get_export_opportunities(company_id, inputs={data['market_code']: data['inputs']},
                                                        context_version=version))
        market = next(m for m in view['markets'] if m['code'] == int(data['market_code']))
        return {'company_id': company_id, 'context_version': version, 'market_code': market['code'],
                'market_name': market['name'], 'margin': market['margin']}

    return router
