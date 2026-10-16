"""Country-wise commodity pricing: dated public benchmarks beside accepted synthetic quotes.

Benchmark values come unmodified from config/commodity_benchmarks_v1.json (World Bank Pink Sheet).
Conversions use the session's ECB reference rates at view time; nothing converted is stored.
Materials without a public benchmark, and currencies without a same-day reference rate, say so.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

from fastapi import Request

from procure.offers import normalize as nz
from procure.settings import get_settings

from .company import get_company
from .sessions import SessionError

SNAPSHOT_FILE = 'commodity_benchmarks_v1.json'
_KG_PER = {'tonne': Decimal('1000'), 'kg': Decimal('1')}


def load_benchmarks(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else get_settings().config_dir / SNAPSHOT_FILE
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _eur_rates(fx: list) -> dict:
    return {row['quote']: row for row in fx if isinstance(row, dict) and row.get('base') == 'EUR'
            and isinstance(row.get('rate'), str) and row.get('quote')}


def usd_rate(fx: list, currency: str) -> dict | None:
    """USD -> currency from same-day EUR reference rates, or None when not derivable."""
    found = _usd_rate(fx, currency)
    return None if found is None else {key: value for key, value in found.items() if key != '_exact'}


def _usd_rate(fx: list, currency: str) -> dict | None:
    rates = _eur_rates(fx)
    usd = rates.get('USD')
    if usd is None:
        return None
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        if currency == 'EUR':
            rate = Decimal(1) / Decimal(usd['rate'])
            formula = f"USD->EUR = 1 / EUR->USD {usd['rate']} (ECB reference rate, {usd['observation_date']})"
        else:
            other = rates.get(currency)
            if other is None or other.get('observation_date') != usd.get('observation_date'):
                return None
            rate = Decimal(other['rate']) / Decimal(usd['rate'])
            formula = (f"USD->{currency} = EUR->{currency} {other['rate']} / EUR->USD {usd['rate']} "
                       f"(ECB reference rates, {usd['observation_date']})")
    return {'rate': nz.fmt(rate), 'observation_date': usd['observation_date'], 'provider': usd.get('provider'),
            'formula': formula, '_exact': rate}


def _currencies(company: dict) -> list[str]:
    others = set(company.get('currencies', [])) | {s['settlement_currency'] for s in company.get('suppliers', [])
                                                   if s.get('settlement_currency')}
    reporting = company['reporting_currency']
    return [reporting] + sorted(others - {reporting})


def _conversion(value: Decimal, source_currency: str, per_unit: str, currency: str, fx: list) -> dict:
    if currency == source_currency:
        return {'currency': currency, 'status': 'same_currency', 'rate': '1', 'amount': nz.fmt(value),
                'per_unit': per_unit, 'observation_date': None, 'provider': None, 'formula': None}
    rate = _usd_rate(fx, currency) if source_currency == 'USD' else None
    if rate is None:
        return {'currency': currency, 'status': 'fx_unavailable', 'rate': None, 'amount': None, 'per_unit': per_unit,
                'observation_date': None, 'provider': None, 'formula': None}
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        amount = value * rate['_exact']
    return {'currency': currency, 'status': 'converted', 'rate': rate['rate'], 'amount': nz.fmt(amount),
            'per_unit': per_unit, 'observation_date': rate['observation_date'], 'provider': rate['provider'],
            'formula': rate['formula']}


def _month_end(month: str) -> date:
    year, number = (int(part) for part in month.split('-'))
    following = date(year + (number == 12), number % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def _benchmark(material: dict, snapshot: dict, fx: list, currencies: list[str], today: date) -> dict:
    entry = snapshot['benchmarks'].get(material['material_id'])
    if entry is None:
        return {'material_id': material['material_id'], 'name': material['name'], 'unit': material['quantity_unit'],
                'status': 'no_public_benchmark', 'message': 'no public benchmark'}
    latest = entry['observations'][-1]
    value = Decimal(latest['value'])
    age = (today - _month_end(latest['month'])).days
    row = {'material_id': material['material_id'], 'name': material['name'], 'unit': material['quantity_unit'],
           'status': 'benchmark', 'series': entry['series'], 'month': latest['month'], 'value': latest['value'],
           'currency': entry['currency'], 'per_unit': entry['per_unit'], 'note': entry['note'],
           'recent': entry['observations'], 'age_days': age, 'stale': age > snapshot['stale_after_days'],
           'conversions': [_conversion(value, entry['currency'], entry['per_unit'], currency, fx) for currency in currencies]}
    company_unit = material['quantity_unit']
    if company_unit in _KG_PER and entry['per_unit'] in _KG_PER and company_unit != entry['per_unit']:
        factor = _KG_PER[entry['per_unit']] / _KG_PER[company_unit]
        row['per_company_unit'] = {'value': nz.fmt(value / factor), 'unit': company_unit,
                                   'formula': f"{latest['value']} {entry['currency']} per {entry['per_unit']} / "
                                              f"{nz.fmt(factor)} {company_unit} per {entry['per_unit']}"}
    return row


QUOTE_NOTE = 'Demo quotes are synthetic and not calibrated to market prices'
COMPARE_LABEL = 'synthetic quote compared with a public benchmark'


def _comparable(offer: dict, units: dict) -> dict | None:
    """The quote's own currency per one material unit, from the reviewed normalized offer."""
    pricing = (((offer.get('normalized') or {}).get('items') or [{}])[0]).get('pricing') or {}
    if (pricing.get('per_kg') or {}).get('amount'):
        return {'amount': pricing['per_kg']['amount'], 'currency': pricing['per_kg']['currency'], 'per_unit': 'kg'}
    material = ((offer.get('items') or [{}])[0].get('material') or {}).get('value')
    per_unit = pricing.get('per_unit') if isinstance(pricing.get('per_unit'), dict) else None
    if per_unit and per_unit.get('amount') and material in units:
        return {'amount': per_unit['amount'], 'currency': per_unit['currency'], 'per_unit': units[material]}
    return None


def _versus(comparable: dict | None, benchmark: dict | None, fx: list) -> dict:
    if benchmark is None:
        return {'status': 'no_public_benchmark', 'difference_percent': None, 'label': COMPARE_LABEL}
    if comparable is None or comparable['per_unit'] not in _KG_PER or benchmark['per_unit'] not in _KG_PER:
        return {'status': 'unit_not_comparable', 'difference_percent': None, 'label': COMPARE_LABEL}
    latest = benchmark['observations'][-1]
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        base = Decimal(latest['value']) * _KG_PER[comparable['per_unit']] / _KG_PER[benchmark['per_unit']]
        note = ''
        if comparable['currency'] != benchmark['currency']:
            rate = _usd_rate(fx, comparable['currency']) if benchmark['currency'] == 'USD' else None
            if rate is None:
                return {'status': 'fx_unavailable', 'difference_percent': None, 'label': COMPARE_LABEL,
                        'reason': f"No same-day ECB reference rate for {benchmark['currency']} to {comparable['currency']} is loaded."}
            base = base * rate['_exact']
            note = f"; benchmark converted with {rate['formula']}"
        quote = Decimal(comparable['amount'])
        percent = ((quote - base) / base * 100).quantize(Decimal('0.1'), rounding='ROUND_HALF_UP')
    return {'status': 'compared', 'difference_percent': format(percent, 'f'), 'benchmark_per_unit': nz.fmt(base),
            'currency': comparable['currency'], 'per_unit': comparable['per_unit'], 'benchmark_month': latest['month'],
            'series': benchmark['series'], 'label': COMPARE_LABEL,
            'formula': f"({nz.fmt(quote)} - {nz.fmt(base)}) / {nz.fmt(base)} x 100, rounded to 0.1{note}"}


def _quote(offer: dict, snapshot: dict, fx: list, units: dict) -> dict | None:
    if offer.get('status') != 'saved' or not offer.get('items'):
        return None
    item = offer['items'][0]
    pricing = (item.get('pricing') or {}).get('value') or {}
    unit_price = pricing.get('unit_price') or {}
    if not unit_price.get('amount'):
        return None
    material = (item.get('material') or {}).get('value')
    comparable = _comparable(offer, units)
    return {'offer_id': offer['offer_id'], 'supplier': (offer['fields'].get('supplier_name') or {}).get('value'),
            'origin_country_code': (offer.get('normalized') or {}).get('origin_country_code'),
            'material_id': material, 'unit_price': unit_price['amount'],
            'currency': unit_price.get('currency'), 'per_unit': pricing.get('per_unit'), 'as_of': offer.get('as_of'),
            'synthetic': offer.get('synthetic') is True, 'document': (offer.get('document') or {}).get('filename'),
            'comparable': comparable,
            'vs_benchmark': _versus(comparable, snapshot['benchmarks'].get(material), fx)}


def build_country_pricing(company: dict, offers: list, fx: list, snapshot: dict, *, today: date) -> dict:
    currencies = _currencies(company)
    units = {material['material_id']: material['quantity_unit'] for material in company.get('materials', [])}
    limitations = ['Benchmarks are monthly averages in nominal US dollars from the World Bank Pink Sheet, '
                   'not quotes for your grade, origin or delivery terms.',
                   'Quotes are synthetic demo documents and are shown separately from public benchmarks.']
    if 'USD' not in _eur_rates(fx):
        limitations.append('No dated reference FX rate is loaded for this session, so conversions show as unavailable.')
    return {'company_id': company['company_id'], 'reporting_currency': company['reporting_currency'],
            'currencies': currencies, 'source': snapshot['source'], 'snapshot_version': snapshot['snapshot_version'],
            'benchmarks_synthetic': snapshot['synthetic'], 'synthetic_quotes': True,
            'benchmarks': [_benchmark(material, snapshot, fx, currencies, today) for material in company.get('materials', [])],
            'quote_note': QUOTE_NOTE,
            'quotes': [row for row in (_quote(offer, snapshot, fx, units) for offer in offers) if row is not None],
            'fx_rates_used': sorted({(row['observation_date'], row['provider']) for row in _eur_rates(fx).values()}),
            'limitations': limitations}


def register_country_pricing_routes(app, *, session, now):
    loaded = {}

    @app.get('/api/demo/country-pricing')
    async def country_pricing(request: Request):
        value = session(request)
        if 'snapshot' not in loaded:
            try:
                loaded['snapshot'] = load_benchmarks()
            except (OSError, ValueError):
                raise SessionError('pricing_unavailable', 'Commodity benchmarks are unavailable in this deployment.', 503) from None
        with value.lock:
            fx = list((value.news or {}).get('fx') or [])
            return build_country_pricing(get_company(value.company_id), list(value.offers.values()), fx,
                                         loaded['snapshot'], today=now().date())
