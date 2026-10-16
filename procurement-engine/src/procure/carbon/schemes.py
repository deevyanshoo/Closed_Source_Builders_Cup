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


def _benefit(scheme, entered: dict):
    """Returns (benefit or None, missing input names). Unsupported calculations are refused here, not only in the UI."""
    spec = scheme['benefit']
    if spec['status'] != 'available':
        if entered:
            raise SchemeInputError(f"The benefit calculation is unavailable for {scheme['scheme_id']}: {spec['reason']}")
        return None, []
    names = [item['name'] for item in spec['inputs']]
    if not set(entered) <= set(names):
        raise SchemeInputError(f"Unknown input for {scheme['scheme_id']}.")
    missing = [name for name in names if name not in entered]
    if missing:
        return None, missing
    currency, kind = spec['currency'], spec['kind']
    shown = [{'name': item['name'], 'value': entered[item['name']], 'unit': item['unit'], 'source': 'entered by you'}
             for item in spec['inputs']]
    label = spec['label']
    direction = None
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        if kind == 'hypothetical_gross_certificate_value':
            count, price = _value(entered['issued_certificates'], 'issued_certificates'), _value(entered['price_per_certificate'], 'price_per_certificate')
            amount = count * price
            arithmetic = f'{nz.fmt(count)} certificates x {nz.fmt(price)} {currency}'
        elif kind == 'first_year_simple_interest_difference':
            loan = _value(entered['loan_amount'], 'loan_amount')
            current = _value(entered['current_rate_percent'], 'current_rate_percent')
            offered = _value(entered['offered_rate_percent'], 'offered_rate_percent')
            if loan > Decimal(spec['max_loan']):
                raise SchemeInputError('The loan amount is above the Rs 50 crore limit stated on the scheme page.')
            if current > 100 or offered > 100:
                raise SchemeInputError('Interest rates must be between 0 and 100 percent.')
            amount = loan * (current - offered) / 100
            direction = 'interest_reduction' if amount > 0 else 'extra_cost' if amount < 0 else 'no_difference'
            arithmetic = f'{nz.fmt(loan)} {currency} x ({nz.fmt(current)}% - {nz.fmt(offered)}%) for the first year'
        elif kind == 'gross_cbam_sensitivity':
            quarter = entered['import_quarter']
            if quarter not in spec['quarter_prices']:
                raise SchemeInputError('No CBAM certificate price is published for that import quarter.')
            tonnes, price = _value(entered['embedded_tonnes'], 'embedded_tonnes'), Decimal(spec['quarter_prices'][quarter])
            amount = tonnes * price
            arithmetic = f'{nz.fmt(tonnes)} tCO2e x {nz.fmt(price)} {currency} per certificate ({quarter} published price)'
            label = label.format(quarter=quarter)
            shown[0]['source'] = 'entered by you'
            shown.append({'name': 'certificate_price', 'value': nz.fmt(price), 'unit': f'{currency} per tCO2e',
                          'source': f'European Commission, price of CBAM certificates, {quarter}'})
        else:
            raise ValueError(f'Unknown benefit kind {kind}.')
    benefit = {'kind': kind, 'amount': nz.fmt(amount), 'currency': currency, 'label': label,
               'formula': spec['formula'], 'arithmetic': f'{arithmetic} = {nz.fmt(amount)} {currency}', 'inputs': shown}
    if direction:
        benefit['direction'] = direction
    return benefit, []


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
        benefit, missing = _benefit(scheme, inputs_by_scheme.get(scheme['scheme_id'], {}))
        spec = scheme['benefit']
        matches.append({key: scheme[key] for key in ('scheme_id', 'country', 'official_name', 'publisher',
                                                     'official_url', 'checked_on', 'offers', 'offers_text',
                                                     'who', 'numbers_used')}
                       | {'eligibility': registry['match_wording'], 'matched_on': reasons,
                          'benefit_status': spec['status'], 'benefit_reason': spec.get('reason'),
                          'benefit_label': spec.get('label'), 'benefit_formula': spec.get('formula'),
                          'input_fields': [dict(item) for item in spec.get('inputs', [])],
                          'missing_inputs': missing, 'benefit': benefit})
    for scheme_id in inputs_by_scheme:
        if scheme_id not in {row['scheme_id'] for row in matches}:
            raise SchemeInputError(f'{scheme_id} does not match this company.')
    countries = sorted({location['country'] for location in company.get('locations', [])})
    unverified = {c: registry.get('not_verified', {})[c] for c in countries if c in registry.get('not_verified', {})}
    reason = None
    if not matches:
        reason = (f"No scheme in registry {registry['registry_version']} matches {', '.join(countries)} or this "
                  "company's export routes. Absence here is not evidence that no scheme exists.")
        if unverified:
            reason += ' Official sources for ' + ', '.join(unverified) + ' could not be verified in this pass.'
    return {'company_id': company['company_id'], 'registry_version': registry['registry_version'],
            'checked_on': registry['checked_on'], 'label': registry['label'], 'matches': matches,
            'not_verified_countries': unverified, 'no_match_reason': reason}
