"""Estimate kg CO2e for a purchase and its entered transport legs.

material: kg CO2e = mass (tonnes) x factor (kg CO2e per tonne)
transport: kg CO2e = mass (tonnes) x distance (km) x factor (kg CO2e per tonne.km)

Every factor comes from config/carbon_factors_v1.json with its source. A material or mode
without a registered factor is "factor_unavailable"; quantities not stated as a mass are
"unit_mismatch". Nothing is inferred: distances are only what the buyer enters.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, localcontext
from pathlib import Path

from procure.offers import normalize as nz
from procure.settings import get_settings

REGISTRY_FILE = 'carbon_factors_v1.json'
MAX_LEGS = 5
CALCULATOR_VERSION = 'carbon-calculator-v2'
_DECIMAL = re.compile(r'^(?:0|[1-9]\d{0,8})(?:\.\d{1,6})?$')
_TONNES_PER_UNIT = {'kg': Decimal('0.001'), 'tonne': Decimal('1')}


class CarbonInputError(ValueError):
    """Buyer input that cannot be estimated as given."""


def load_registry(path: Path | None = None) -> dict:
    path = Path(path) if path is not None else get_settings().config_dir / REGISTRY_FILE
    registry = json.loads(Path(path).read_text(encoding='utf-8'))
    for group, unit in (('transport_modes', 'kg CO2e per tonne.km'), ('materials', 'kg CO2e per tonne')):
        for key, factor in registry[group].items():
            source = registry['sources'].get(factor.get('source'))
            if (source is None or not source.get('page_url') or not source.get('licence')
                    or type(source.get('factor_year')) is not int or factor.get('unit') != unit
                    or not factor.get('system_boundary')
                    or not _DECIMAL.match(str(factor.get('factor', ''))) or Decimal(factor['factor']) <= 0):
                raise ValueError(f'Carbon factor {group}.{key} lacks a cited source, unit or valid value.')
    return registry


def _fmt(value: Decimal) -> str:
    return nz.fmt(value)


def _decimal_text(value, what: str) -> Decimal:
    if not isinstance(value, str) or not _DECIMAL.match(value):
        raise CarbonInputError(f'{what} must be a non-negative decimal written as text.')
    return Decimal(value)


def _factor_view(registry: dict, factor: dict) -> dict:
    source = registry['sources'][factor['source']]
    return {**{k: v for k, v in factor.items() if k != 'source'}, 'source_id': factor['source'],
            'publisher': source['publisher'], 'source_url': source['page_url'], 'file_url': source.get('file_url'),
            'licence': source['licence'], 'factor_year': source['factor_year'], 'checked_on': source['checked_on']}


COMBINE_REASONS = {
    'incomplete': 'A component is missing or unavailable, so there is no whole-scenario total.',
    'overlap': 'The material factor already includes transport to the point of sale, so adding entered freight '
               'could double count. Components shown separately.',
    'proxy': 'The material factor is a proxy for this product, so a combined figure would overstate precision. '
             'Components shown separately.',
    'combined': 'Exact product factor ending at the production gate, plus entered freight from the supplier.',
}
_BASIS_MAX = 200


def coverage_state(registry: dict, company_id: str, material_id: str, unit: str | None = None) -> dict:
    """The reviewed state of one company material line: exact, proxy, unit_mismatch or unavailable."""
    for entry in registry.get('catalogue', []):
        if entry['company_id'] == company_id and entry['material_id'] == material_id:
            return dict(entry)
    return {'company_id': company_id, 'material_id': material_id, 'unit': unit, 'state': 'unavailable',
            'factor_key': None, 'reason': 'Not in the reviewed carbon catalogue; no factor applied.'}


def _factor_for(registry, material_id, company_id):
    if company_id is not None:
        entry = coverage_state(registry, company_id, material_id)
        if entry['factor_key'] is None:
            return None, None, entry['reason']
        factor = registry['materials'][entry['factor_key']]
        match = entry['state'] if entry['state'] in ('exact', 'proxy') else factor.get('product_match', 'proxy')
        return factor, match, entry['reason']
    factor = registry['materials'].get(material_id)
    return factor, (factor or {}).get('product_match', 'proxy'), None


def _material(registry, material_id, mass, company_id):
    factor, match, reason = _factor_for(registry, material_id, company_id)
    if factor is None:
        return {'status': 'factor_unavailable', 'kg_co2e': None, 'factor': None, 'product_match': None,
                'mass_tonnes': None if mass is None else _fmt(mass),
                'reason': reason or f'No published factor is registered for {material_id}.'}
    view = _factor_view(registry, factor)
    if mass is None:
        return {'status': 'unit_mismatch', 'kg_co2e': None, 'factor': view, 'product_match': match, 'mass_tonnes': None,
                'reason': 'The factor is per tonne of material, but the quantity is not stated as a mass unit (kg or '
                          'tonne). A shipping mass is used for freight only and never for material production.'}
    return {'status': 'estimated', 'kg_co2e': _fmt(mass * Decimal(factor['factor'])), 'factor': view,
            'product_match': match, 'mass_tonnes': _fmt(mass),
            'formula': f"{_fmt(mass)} t x {factor['factor']} kg CO2e/t"}


def _leg(registry, leg, mass):
    if not isinstance(leg, dict) or 'mode' not in leg or not set(leg) <= {'mode', 'distance_km'} \
            or not isinstance(leg['mode'], str):
        raise CarbonInputError('Each transport leg needs a mode and, when known, a distance_km.')
    factor = registry['transport_modes'].get(leg['mode'])
    if 'distance_km' not in leg:
        return {'mode': leg['mode'], 'distance_km': None, 'status': 'distance_missing', 'tonne_km': None,
                'kg_co2e': None, 'factor': None if factor is None else _factor_view(registry, factor),
                'reason': 'No distance entered for this leg; it is not treated as zero.'}
    distance = _decimal_text(leg['distance_km'], 'distance_km')
    base = {'mode': leg['mode'], 'distance_km': _fmt(distance)}
    if factor is None:
        return {**base, 'status': 'factor_unavailable', 'tonne_km': None, 'kg_co2e': None, 'factor': None,
                'reason': f"No published factor is registered for transport mode {leg['mode']}."}
    if mass is None:
        return {**base, 'status': 'mass_basis_required', 'tonne_km': None, 'kg_co2e': None,
                'factor': _factor_view(registry, factor),
                'reason': 'Tonne-km needs the shipped mass. Enter a shipping mass and its basis; it is never inferred.'}
    tonne_km = mass * distance
    return {**base, 'status': 'zero_distance' if distance == 0 else 'estimated', 'tonne_km': _fmt(tonne_km),
            'kg_co2e': _fmt(tonne_km * Decimal(factor['factor'])), 'factor': _factor_view(registry, factor),
            'formula': f"{_fmt(mass)} t x {_fmt(distance)} km x {factor['factor']} kg CO2e/tonne.km"}


def _mass_basis(unit, mass_override_kg, mass_basis):
    if mass_override_kg is None and mass_basis is None:
        return None
    if unit in _TONNES_PER_UNIT:
        raise CarbonInputError('A shipping mass is only needed when the quantity is not stated in kg or tonnes.')
    mass = _decimal_text(mass_override_kg, 'mass_kg')
    if mass <= 0:
        raise CarbonInputError('mass_kg must be above zero.')
    if not isinstance(mass_basis, str) or not mass_basis.strip() or len(mass_basis) > _BASIS_MAX:
        raise CarbonInputError('Say where the shipping mass comes from (basis, up to 200 characters).')
    return {'mass_kg': _fmt(mass), 'basis': mass_basis.strip(), 'source': 'entered by you', 'used_for': 'freight only'}


def estimate(*, registry: dict, material_id: str, quantity: str, unit: str, legs: list,
             company_id: str | None = None, mass_override_kg: str | None = None, mass_basis: str | None = None) -> dict:
    quantity_value = _decimal_text(quantity, 'quantity')
    if not isinstance(legs, list) or len(legs) > MAX_LEGS:
        raise CarbonInputError(f'Provide at most {MAX_LEGS} transport legs.')
    basis = _mass_basis(unit, mass_override_kg, mass_basis)
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        per_tonne = _TONNES_PER_UNIT.get(unit)
        mass = None if per_tonne is None else quantity_value * per_tonne
        freight_mass = mass if basis is None else Decimal(basis['mass_kg']) / 1000
        material = _material(registry, material_id, mass, company_id)
        rows = [_leg(registry, leg, freight_mass) for leg in legs]
        if not rows:
            transport = {'status': 'not_entered', 'kg_co2e': None, 'legs': []}
        elif all(row['status'] in ('estimated', 'zero_distance') for row in rows):
            transport = {'status': 'estimated', 'kg_co2e': _fmt(sum(Decimal(r['kg_co2e']) for r in rows)), 'legs': rows}
        else:
            transport = {'status': 'incomplete', 'kg_co2e': None, 'legs': rows}
        factor, _, _ = _factor_for(registry, material_id, company_id)
        if material['status'] != 'estimated' or transport['status'] != 'estimated':
            combined, total = 'incomplete', None
            reason = COMBINE_REASONS['incomplete']
        elif factor.get('includes_transport_to_point_of_sale', True):
            combined, total, reason = 'components_shown_separately', None, COMBINE_REASONS['overlap']
        elif material['product_match'] != 'exact':
            combined, total, reason = 'components_shown_separately', None, COMBINE_REASONS['proxy']
        else:
            combined, reason = 'combined', COMBINE_REASONS['combined']
            total = _fmt(Decimal(material['kg_co2e']) + Decimal(transport['kg_co2e']))
    used = {factor['source']} if factor else set()
    used |= {registry['transport_modes'][r['mode']]['source'] for r in rows if r['mode'] in registry['transport_modes']}
    return {'label': registry['label'], 'scope': registry['scope'], 'registry_version': registry['registry_version'],
            'material_id': material_id, 'company_id': company_id, 'quantity': _fmt(quantity_value), 'unit': unit,
            'mass_basis': basis, 'material': material, 'transport': transport, 'total_kg_co2e': total,
            'combined_status': combined, 'combined_reason': reason, 'complete': combined == 'combined',
            'factor_versions': {registry['registry_version']: {
                source_id: {key: registry['sources'][source_id][key]
                            for key in ('published_on', 'checked_on', 'factor_year', 'file_sha256')}
                for source_id in sorted(used)}}}


def compare_plan(registry: dict, plan: dict, legs_by_offer: dict, mass_by_offer: dict | None = None,
                 company_id: str | None = None) -> dict:
    """One estimate per compared supplier, using the plan's own material, unit and quantity."""
    offers = [alternative['offer_id'] for alternative in plan['alternatives']]
    mass_by_offer = mass_by_offer or {}
    if not isinstance(legs_by_offer, dict) or not set(legs_by_offer) <= set(offers):
        raise CarbonInputError('Transport legs may only name suppliers in this comparison.')
    if not isinstance(mass_by_offer, dict) or not set(mass_by_offer) <= set(offers) or any(
            not isinstance(v, dict) or set(v) != {'mass_kg', 'basis'} for v in mass_by_offer.values()):
        raise CarbonInputError('A shipping mass needs mass_kg and basis and may only name suppliers in this comparison.')
    unit = plan.get('quantity_unit') or ('kg' if 'quantity_kg' in plan else None)
    suppliers = []
    for alternative in plan['alternatives']:
        quantity = alternative.get('quantity') or alternative.get('quantity_kg')
        mass = mass_by_offer.get(alternative['offer_id'], {})
        suppliers.append({'offer_id': alternative['offer_id'], 'supplier': alternative['supplier'],
                          'estimate': estimate(registry=registry, material_id=plan['material'], quantity=quantity,
                                               unit=unit, legs=legs_by_offer.get(alternative['offer_id'], []),
                                               company_id=company_id, mass_override_kg=mass.get('mass_kg'),
                                               mass_basis=mass.get('basis'))})
    return {'plan_id': plan['plan_id'], 'company_id': company_id, 'synthetic_inputs': True,
            'registry_version': registry['registry_version'], 'calculator_version': CALCULATOR_VERSION,
            'label': registry['label'], 'scope': registry['scope'], 'material_id': plan['material'],
            'quantity_unit': unit, 'coverage': None if company_id is None else coverage_state(registry, company_id, plan['material'], unit),
            'suppliers': suppliers}
