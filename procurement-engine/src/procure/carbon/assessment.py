"""Bounded, versioned carbon_assessment record for the coordinator's session persistence (Task 3).

The record stores the exact inputs and the server result for one company, plan and offer versions. A fresh
process validates it after restore; current_assessment returns it only while company, plan and versions match.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .calculator import CALCULATOR_VERSION, MAX_LEGS, compare_plan, load_registry

SCHEMA = 'carbon-assessment.v1'
MAX_RECORD_BYTES = 65536
_KEYS = {'schema', 'company_id', 'context_version', 'plan_id', 'offer_versions', 'material_id', 'registry_version',
         'calculator_version', 'inputs', 'result', 'created_at'}
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
_DECIMAL = re.compile(r'^(?:0|[1-9]\d{0,29})(?:\.\d{1,30})?$')


def build_assessment(*, company_id: str, context_version: int, plan: dict, comparison: dict, legs_by_offer: dict,
                     mass_by_offer: dict | None, created_at: str) -> dict:
    record = {'schema': SCHEMA, 'company_id': company_id, 'context_version': context_version,
              'plan_id': plan['plan_id'], 'offer_versions': dict(plan.get('offer_versions') or {}),
              'material_id': plan['material'], 'registry_version': comparison['registry_version'],
              'calculator_version': CALCULATOR_VERSION,
              'inputs': {'legs_by_offer': legs_by_offer, 'mass_by_offer': mass_by_offer or {}},
              'result': comparison, 'created_at': created_at}
    return validate_assessment(json.loads(json.dumps(record)))


def _fail(reason: str):
    raise ValueError(f'invalid carbon_assessment: {reason}')


def _required(value, fields, reason):
    if not isinstance(value, dict) or not set(fields) <= value.keys():
        _fail(reason)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value, reason):
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        _fail(reason)
    try:
        number = Decimal(value)
    except InvalidOperation:
        _fail(reason)
    if not number.is_finite() or number >= Decimal('1e30'):
        _fail(reason)
    return number


def _factor(value, reason):
    if value is None:
        return None
    _required(value, ('factor', 'unit', 'system_boundary', 'source_id', 'publisher',
                      'source_url', 'licence', 'factor_year', 'checked_on'), reason)
    if (_number(value['factor'], reason) <= 0 or
            any(not _text(value[key]) for key in ('unit', 'system_boundary', 'source_id',
                                                    'publisher', 'source_url', 'licence', 'checked_on')) or
            type(value['factor_year']) is not int):
        _fail(reason)
    return value['source_id']


def _validate_estimate(estimate, record, quantity_unit, coverage_state):
    _required(estimate, ('label', 'scope', 'quantity', 'unit', 'mass_basis', 'material',
                         'transport', 'total_kg_co2e', 'combined_status', 'combined_reason',
                         'complete', 'factor_versions'), 'result estimate')
    if (not _text(estimate['label']) or not _text(estimate['scope']) or
            estimate['unit'] != quantity_unit or not _text(estimate['combined_reason'])):
        _fail('result estimate')
    _number(estimate['quantity'], 'result quantity')
    basis = estimate['mass_basis']
    if basis is not None:
        _required(basis, ('mass_kg', 'basis', 'source', 'used_for'), 'mass basis')
        if (_number(basis['mass_kg'], 'mass basis') <= 0 or not _text(basis['basis']) or
                basis['source'] != 'entered by you' or basis['used_for'] != 'freight only'):
            _fail('mass basis')

    sources = set()
    used_factors = []
    material = estimate['material']
    _required(material, ('status', 'kg_co2e', 'factor', 'product_match', 'mass_tonnes'), 'material component')
    if material['status'] not in ('estimated', 'unit_mismatch', 'factor_unavailable'):
        _fail('material status')
    source = _factor(material['factor'], 'material factor')
    if source:
        sources.add(source)
        used_factors.append(material['factor'])
    if material['status'] == 'estimated':
        _number(material['kg_co2e'], 'material emissions')
        _number(material['mass_tonnes'], 'material mass')
        if source is None or material['product_match'] not in ('exact', 'proxy') or not _text(material.get('formula')):
            _fail('material estimate')
    else:
        if material['kg_co2e'] is not None or not _text(material.get('reason')):
            _fail('material unavailable')
        if material['status'] == 'unit_mismatch':
            if source is None or material['mass_tonnes'] is not None:
                _fail('material unit mismatch')
        elif source is not None or material['product_match'] is not None:
            _fail('material factor unavailable')
        if material['mass_tonnes'] is not None:
            _number(material['mass_tonnes'], 'material mass')

    transport = estimate['transport']
    _required(transport, ('status', 'kg_co2e', 'legs'), 'transport component')
    if transport['status'] not in ('not_entered', 'estimated', 'incomplete') or not isinstance(transport['legs'], list) \
            or len(transport['legs']) > MAX_LEGS:
        _fail('transport status')
    if transport['status'] == 'not_entered':
        if transport['legs'] or transport['kg_co2e'] is not None:
            _fail('transport not entered')
    elif transport['status'] == 'estimated':
        if not transport['legs']:
            _fail('transport estimate')
        _number(transport['kg_co2e'], 'transport emissions')
    elif not transport['legs'] or transport['kg_co2e'] is not None:
        _fail('transport incomplete')
    for leg in transport['legs']:
        _required(leg, ('mode', 'distance_km', 'status', 'tonne_km', 'kg_co2e', 'factor'), 'transport leg')
        if not _text(leg['mode']) or leg['status'] not in (
                'estimated', 'zero_distance', 'distance_missing', 'factor_unavailable', 'mass_basis_required'):
            _fail('transport leg status')
        source = _factor(leg['factor'], 'transport factor')
        if source:
            sources.add(source)
            used_factors.append(leg['factor'])
        if leg['status'] in ('estimated', 'zero_distance'):
            distance = _number(leg['distance_km'], 'distance')
            _number(leg['tonne_km'], 'tonne km')
            _number(leg['kg_co2e'], 'leg emissions')
            if source is None or not _text(leg.get('formula')) or (
                    leg['status'] == 'zero_distance' and distance != 0):
                _fail('transport leg estimate')
        else:
            if leg['tonne_km'] is not None or leg['kg_co2e'] is not None or not _text(leg.get('reason')):
                _fail('transport leg unavailable')
            if leg['status'] == 'distance_missing':
                if leg['distance_km'] is not None:
                    _fail('distance missing')
            else:
                _number(leg['distance_km'], 'distance')
                if (leg['status'] == 'factor_unavailable' and source is not None) or (
                        leg['status'] == 'mass_basis_required' and source is None):
                    _fail('transport leg factor')
    if transport['status'] == 'estimated' and any(
            leg['status'] not in ('estimated', 'zero_distance') for leg in transport['legs']):
        _fail('transport estimate status')
    if transport['status'] == 'incomplete' and all(
            leg['status'] in ('estimated', 'zero_distance') for leg in transport['legs']):
        _fail('transport incomplete status')

    combined = estimate['combined_status']
    if combined not in ('combined', 'components_shown_separately', 'incomplete') or \
            type(estimate['complete']) is not bool or estimate['complete'] != (combined == 'combined'):
        _fail('combined status')
    if combined == 'combined':
        total = _number(estimate['total_kg_co2e'], 'combined total')
        if coverage_state != 'exact' or material['status'] != 'estimated' or \
                material['product_match'] != 'exact' or \
                material['factor'].get('product_match', 'exact') != 'exact' or \
                material['factor'].get('includes_transport_to_point_of_sale') is not False or \
                transport['status'] != 'estimated' or \
                total != Decimal(material['kg_co2e']) + Decimal(transport['kg_co2e']):
            _fail('combined total')
    elif estimate['total_kg_co2e'] is not None:
        _fail('unavailable total')
    if combined == 'incomplete' and material['status'] == 'estimated' and transport['status'] == 'estimated':
        _fail('incomplete components')
    if combined == 'components_shown_separately' and (
            material['status'] != 'estimated' or transport['status'] != 'estimated'):
        _fail('separate components')

    versions = estimate['factor_versions']
    if not isinstance(versions, dict) or set(versions) != {record['registry_version']} or \
            not isinstance(versions[record['registry_version']], dict):
        _fail('factor versions')
    source_versions = versions[record['registry_version']]
    if not sources <= set(source_versions):
        _fail('factor source versions')
    for source_id, metadata in source_versions.items():
        if not _text(source_id):
            _fail('factor source')
        _required(metadata, ('published_on', 'checked_on', 'factor_year', 'file_sha256'), 'factor source')
        if any(not _text(metadata[key]) for key in ('published_on', 'checked_on', 'file_sha256')) or \
                type(metadata['factor_year']) is not int:
            _fail('factor source')
    for factor in used_factors:
        metadata = source_versions[factor['source_id']]
        if any(factor[key] != metadata[key] for key in ('factor_year', 'checked_on')):
            _fail('factor source metadata')


def validate_assessment(record) -> dict:
    if not isinstance(record, dict) or set(record) != _KEYS:
        _fail('fields')
    if record['schema'] != SCHEMA:
        _fail('schema')
    if len(json.dumps(record, separators=(',', ':'))) > MAX_RECORD_BYTES:
        _fail('record too large')
    for key in ('company_id', 'plan_id', 'material_id'):
        if not isinstance(record[key], str) or not _ID.match(record[key]):
            _fail(key)
    if type(record['context_version']) is not int or record['context_version'] < 1:
        _fail('context_version')
    if not isinstance(record['offer_versions'], dict) or not record['offer_versions'] or not all(
            isinstance(k, str) and _ID.fullmatch(k) and type(v) is int and v >= 1
            for k, v in record['offer_versions'].items()):
        _fail('offer_versions')
    if record['calculator_version'] != CALCULATOR_VERSION or not isinstance(record['registry_version'], str):
        _fail('versions')
    inputs = record['inputs']
    if not isinstance(inputs, dict) or set(inputs) != {'legs_by_offer', 'mass_by_offer'}:
        _fail('inputs')
    legs = inputs['legs_by_offer']
    if not isinstance(legs, dict) or any(not isinstance(v, list) or len(v) > MAX_LEGS for v in legs.values()):
        _fail('legs_by_offer')
    if not isinstance(inputs['mass_by_offer'], dict):
        _fail('mass_by_offer')
    if not set(legs) <= set(record['offer_versions']) or not set(inputs['mass_by_offer']) <= set(record['offer_versions']):
        _fail('input offers')
    result = record['result']
    if not isinstance(result, dict) or any(result.get(key) != record[key] for key in (
            'plan_id', 'company_id', 'context_version', 'material_id',
            'registry_version', 'calculator_version')):
        _fail('result')
    _required(result, ('synthetic_inputs', 'label', 'scope', 'quantity_unit', 'coverage', 'suppliers'), 'comparison')
    if result['synthetic_inputs'] is not True or not _text(result['label']) or not _text(result['scope']) \
            or not _text(result['quantity_unit']):
        _fail('comparison')
    coverage = result['coverage']
    _required(coverage, ('company_id', 'material_id', 'unit', 'state', 'factor_key', 'reason'), 'coverage')
    if (coverage['company_id'] != record['company_id'] or coverage['material_id'] != record['material_id'] or
            coverage['unit'] != result['quantity_unit'] or
            coverage['state'] not in ('exact', 'proxy', 'unit_mismatch', 'unavailable') or
            (coverage['factor_key'] is not None and not _text(coverage['factor_key'])) or
            (coverage['reason'] is not None and not isinstance(coverage['reason'], str)) or
            (coverage['state'] != 'exact' and not _text(coverage['reason']))):
        _fail('coverage')
    suppliers = result.get('suppliers')
    if not isinstance(suppliers, list) or len(suppliers) != len(record['offer_versions']):
        _fail('result suppliers')
    supplier_ids = []
    for supplier in suppliers:
        if not isinstance(supplier, dict) or not isinstance(supplier.get('offer_id'), str):
            _fail('result suppliers')
        estimate = supplier.get('estimate')
        if not isinstance(estimate, dict) or any(estimate.get(key) != record[key] for key in (
                'company_id', 'material_id', 'registry_version')):
            _fail('result estimate')
        if not _text(supplier.get('supplier')) or estimate.get('label') != result['label'] or \
                estimate.get('scope') != result['scope']:
            _fail('result supplier')
        _validate_estimate(estimate, record, result['quantity_unit'], coverage['state'])
        supplier_ids.append(supplier['offer_id'])
    if len(set(supplier_ids)) != len(supplier_ids) or set(supplier_ids) != set(record['offer_versions']):
        _fail('result offers')
    try:
        datetime.fromisoformat(record['created_at'])
    except (TypeError, ValueError):
        _fail('created_at')
    return record


def current_assessment(record, *, company_id: str, plan: dict | None) -> dict | None:
    """The stored record only while it still describes this company, plan and offer versions."""
    if record is None or plan is None:
        return None
    try:
        validate_assessment(record)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None
    if record['company_id'] != company_id or record['plan_id'] != plan.get('plan_id') \
            or record['material_id'] != plan.get('material') \
            or record['offer_versions'] != plan.get('offer_versions'):
        return None
    alternatives = plan.get('alternatives')
    if not isinstance(alternatives, list) or len(alternatives) != len(record['offer_versions']) or any(
            not isinstance(offer, dict) or not isinstance(offer.get('offer_id'), str)
            for offer in alternatives):
        return None
    by_offer = {offer['offer_id']: offer for offer in alternatives}
    if len(by_offer) != len(alternatives) or set(by_offer) != set(record['offer_versions']):
        return None
    if any(supplier.get('supplier') != by_offer[supplier['offer_id']].get('supplier')
           for supplier in record['result']['suppliers']):
        return None
    try:
        expected = compare_plan(load_registry(), plan, record['inputs']['legs_by_offer'],
                                record['inputs']['mass_by_offer'], company_id=company_id)
        expected['context_version'] = record['context_version']
    except (OSError, TypeError, ValueError, KeyError, OverflowError, RecursionError):
        return None
    if record['result'] != expected:
        return None
    return record
