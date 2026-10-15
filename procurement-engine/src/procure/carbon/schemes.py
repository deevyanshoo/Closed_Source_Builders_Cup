"""Carbon-linked schemes a company might use, with an explicit approximate saving.

Matching uses only the company's stated countries, sector and export routes. Wording is always
"possibly eligible, check conditions"; a saving is only calculated from inputs the buyer states
or a dated official default, and every input is returned with its source.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, localcontext
from pathlib import Path

from procure.offers import normalize as nz
from procure.settings import get_settings

REGISTRY_FILE = 'carbon_schemes_v1.json'
_DECIMAL = re.compile(r'^(?:0|[1-9]\d{0,14})(?:\.\d{1,6})?$')


class SchemeInputError(ValueError):
    """Buyer input that does not fit a scheme's stated formula."""


def load_schemes(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else get_settings().config_dir / REGISTRY_FILE
    registry = json.loads(Path(path).read_text(encoding='utf-8'))
    for scheme in registry['schemes']:
        if not str(scheme.get('official_url', '')).startswith('https://') or not scheme.get('checked_on'):
            raise ValueError(f"Scheme {scheme.get('scheme_id')} lacks an official URL or check date.")
    return registry


def _country(place: str) -> str:
    return place.rsplit(',', 1)[-1].strip()


def _matched_on(registry, scheme, company) -> list[str]:
    rule = scheme['match']
    countries = {location['country'] for location in company.get('locations', [])}
    if 'company_country' in rule:
        if rule['company_country'] not in countries:
            return []
        reasons = [f"company location in {rule['company_country']}"]
        keywords = rule.get('sector_keywords')
        if keywords:
            sector = company.get('sector', '').lower()
            hits = [word for word in keywords if word in sector]
            if not hits:
                return []
            reasons.append(f"sector \"{company['sector']}\" names {', '.join(hits)}")
        return reasons
    if rule.get('export_to_eu'):
        eu = set(registry['eu_member_states'])
        return [f"export route {route['origin']} to {route['destination']} carries {material}"
                for route in company.get('routes', [])
                if route.get('direction') == 'export' and _country(route['destination']) in eu
                for material in route.get('materials', []) if material in rule['material_ids']]
    return []


def _value(raw, name):
    if not isinstance(raw, str) or not _DECIMAL.match(raw):
        raise SchemeInputError(f'{name} must be a non-negative decimal written as text.')
    return Decimal(raw)


def _saving(scheme, entered: dict) -> dict:
    spec = scheme['saving']
    known = {item['name']: item for item in spec['inputs']}
    if not set(entered) <= set(known):
        raise SchemeInputError(f"Unknown input for {scheme['scheme_id']}.")
    shown, values, missing = [], {}, []
    for item in spec['inputs']:
        name = item['name']
        if name in entered:
            raw, source = entered[name], 'entered by you'
        elif 'default' in item:
            raw, source = item['default'], item['default_source']
        else:
            missing.append(name)
            continue
        if name == 'enterprise_category':
            if raw not in spec['rates']:
                raise SchemeInputError('enterprise_category must be micro, small or medium.')
            values[name] = raw
        else:
            values[name] = _value(raw, name)
        shown.append({'name': name, 'value': raw if name == 'enterprise_category' else nz.fmt(values[name]),
                      'unit': item['unit'], 'source': source})
    base = {'currency': spec['currency'], 'wording': 'approximate, based on these assumptions',
            'formula': spec['text'], 'inputs': shown}
    if missing:
        return {'status': 'missing_inputs', 'amount': None, 'missing': missing, **base}
    currency = spec['currency']
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        if spec['formula'] in ('credit_value', 'avoided_certificates'):
            tonnes, price = values['tonnes_reduced'], values['price_per_tonne']
            amount = tonnes * price
            arithmetic = f"{nz.fmt(tonnes)} tCO2e x {nz.fmt(price)} {currency} per tCO2e"
        elif spec['formula'] == 'subsidy_share':
            category, cost = values['enterprise_category'], values['certification_cost']
            rate = Decimal(spec['rates'][category])
            amount = cost * rate
            arithmetic = f"{nz.fmt(cost)} {currency} x {nz.fmt(rate * 100)}% ({category})"
        elif spec['formula'] == 'rate_difference':
            loan, current, green = values['loan_amount'], values['current_rate_percent'], values['green_rate_percent']
            amount = loan * (current - green) / 100
            arithmetic = f"{nz.fmt(loan)} {currency} x ({nz.fmt(current)}% - {nz.fmt(green)}%)"
        else:
            raise ValueError(f"Unknown saving formula {spec['formula']}.")
    suffix = ' per year' if spec['formula'] == 'rate_difference' else ''
    return {'status': 'calculated', 'amount': nz.fmt(amount), **base,
            'arithmetic': f'{arithmetic} = {nz.fmt(amount)} {currency}{suffix}'}


def find_schemes(registry: dict, company: dict, inputs_by_scheme: dict) -> dict:
    ids = {scheme['scheme_id'] for scheme in registry['schemes']}
    if not isinstance(inputs_by_scheme, dict) or not set(inputs_by_scheme) <= ids or \
            not all(isinstance(value, dict) for value in inputs_by_scheme.values()):
        raise SchemeInputError('Inputs may only name schemes in this registry.')
    matches = []
    for scheme in registry['schemes']:
        reasons = _matched_on(registry, scheme, company)
        if not reasons:
            continue
        matches.append({key: scheme[key] for key in ('scheme_id', 'country', 'official_name', 'publisher',
                                                     'official_url', 'checked_on', 'offers', 'offers_text',
                                                     'who', 'numbers_used')}
                       | {'eligibility': registry['match_wording'], 'matched_on': reasons,
                          'input_fields': [dict(item) for item in scheme['saving']['inputs']],
                          'saving': _saving(scheme, inputs_by_scheme.get(scheme['scheme_id'], {}))})
    for scheme_id in inputs_by_scheme:
        if scheme_id not in {row['scheme_id'] for row in matches}:
            raise SchemeInputError(f'{scheme_id} does not match this company.')
    countries = sorted({location['country'] for location in company.get('locations', [])})
    return {'company_id': company['company_id'], 'registry_version': registry['registry_version'],
            'checked_on': registry['checked_on'], 'label': registry['label'], 'matches': matches,
            'no_match_reason': None if matches else (
                f"No scheme in registry {registry['registry_version']} matches {', '.join(countries)} or this "
                "company's export routes. Absence here is not evidence that no scheme exists.")}
