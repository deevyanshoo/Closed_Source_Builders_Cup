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


def _material(registry, material_id, mass):
    factor = registry['materials'].get(material_id)
    if factor is None:
        return {'status': 'factor_unavailable', 'kg_co2e': None, 'factor': None,
                'mass_tonnes': None if mass is None else _fmt(mass),
                'reason': f'No published factor is registered for {material_id}.'}
    view = _factor_view(registry, factor)
    if mass is None:
        return {'status': 'unit_mismatch', 'kg_co2e': None, 'factor': view, 'mass_tonnes': None,
                'reason': 'The factor is per tonne of material, but the quantity is not stated as a mass unit (kg or tonne).'}
    return {'status': 'estimated', 'kg_co2e': _fmt(mass * Decimal(factor['factor'])), 'factor': view,
            'mass_tonnes': _fmt(mass), 'formula': f"{_fmt(mass)} t x {factor['factor']} kg CO2e/t"}


def _leg(registry, leg, mass):
    if not isinstance(leg, dict) or set(leg) != {'mode', 'distance_km'} or not isinstance(leg['mode'], str):
        raise CarbonInputError('Each transport leg needs exactly a mode and a distance_km.')
    distance = _decimal_text(leg['distance_km'], 'distance_km')
    base = {'mode': leg['mode'], 'distance_km': _fmt(distance)}
    factor = registry['transport_modes'].get(leg['mode'])
    if factor is None:
        return {**base, 'status': 'factor_unavailable', 'tonne_km': None, 'kg_co2e': None, 'factor': None,
                'reason': f"No published factor is registered for transport mode {leg['mode']}."}
    if mass is None:
        return {**base, 'status': 'unit_mismatch', 'tonne_km': None, 'kg_co2e': None,
                'factor': _factor_view(registry, factor),
                'reason': 'Tonne-km needs the shipped mass, but the quantity is not stated in kg or tonnes.'}
    tonne_km = mass * distance
    return {**base, 'status': 'estimated', 'tonne_km': _fmt(tonne_km),
            'kg_co2e': _fmt(tonne_km * Decimal(factor['factor'])), 'factor': _factor_view(registry, factor),
            'formula': f"{_fmt(mass)} t x {_fmt(distance)} km x {factor['factor']} kg CO2e/tonne.km"}


def estimate(*, registry: dict, material_id: str, quantity: str, unit: str, legs: list) -> dict:
    quantity_value = _decimal_text(quantity, 'quantity')
    if not isinstance(legs, list) or len(legs) > MAX_LEGS:
        raise CarbonInputError(f'Provide at most {MAX_LEGS} transport legs.')
    with localcontext() as context:
        context.prec = nz.ARITHMETIC_PRECISION
        per_tonne = _TONNES_PER_UNIT.get(unit)
        mass = None if per_tonne is None else quantity_value * per_tonne
        material = _material(registry, material_id, mass)
        rows = [_leg(registry, leg, mass) for leg in legs]
        if not rows:
            transport = {'status': 'not_entered', 'kg_co2e': None, 'legs': []}
        elif all(row['status'] == 'estimated' for row in rows):
            transport = {'status': 'estimated', 'kg_co2e': _fmt(sum(Decimal(r['kg_co2e']) for r in rows)), 'legs': rows}
        else:
            transport = {'status': 'incomplete', 'kg_co2e': None, 'legs': rows}
        parts = [Decimal(part['kg_co2e']) for part in (material, transport) if part['kg_co2e'] is not None]
        total = _fmt(sum(parts)) if parts and material['kg_co2e'] is not None else None
    used = {registry['materials'][material_id]['source']} if material_id in registry['materials'] else set()
    used |= {registry['transport_modes'][r['mode']]['source'] for r in rows if r['mode'] in registry['transport_modes']}
    return {'label': registry['label'], 'scope': registry['scope'], 'registry_version': registry['registry_version'],
            'material_id': material_id, 'quantity': _fmt(quantity_value), 'unit': unit,
            'material': material, 'transport': transport, 'total_kg_co2e': total,
            'complete': material['status'] == 'estimated' and transport['status'] == 'estimated',
            'factor_versions': {registry['registry_version']: {
                source_id: {key: registry['sources'][source_id][key]
                            for key in ('published_on', 'checked_on', 'factor_year', 'file_sha256')}
                for source_id in sorted(used)}}}


def compare_plan(registry: dict, plan: dict, legs_by_offer: dict) -> dict:
    """One estimate per compared supplier, using the plan's own material, unit and quantity."""
    offers = [alternative['offer_id'] for alternative in plan['alternatives']]
    if not isinstance(legs_by_offer, dict) or not set(legs_by_offer) <= set(offers):
        raise CarbonInputError('Transport legs may only name suppliers in this comparison.')
    unit = plan.get('quantity_unit') or ('kg' if 'quantity_kg' in plan else None)
    suppliers = []
    for alternative in plan['alternatives']:
        quantity = alternative.get('quantity') or alternative.get('quantity_kg')
        suppliers.append({'offer_id': alternative['offer_id'], 'supplier': alternative['supplier'],
                          'estimate': estimate(registry=registry, material_id=plan['material'], quantity=quantity,
                                               unit=unit, legs=legs_by_offer.get(alternative['offer_id'], []))})
    return {'plan_id': plan['plan_id'], 'synthetic_inputs': True, 'registry_version': registry['registry_version'],
            'label': registry['label'], 'scope': registry['scope'], 'material_id': plan['material'],
            'quantity_unit': unit, 'suppliers': suppliers}
