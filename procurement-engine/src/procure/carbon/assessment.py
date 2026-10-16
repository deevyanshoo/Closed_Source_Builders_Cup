"""Bounded, versioned carbon_assessment record for the coordinator's session persistence (Task 3).

The record stores the exact inputs and the server result for one company, plan and offer versions. A fresh
process validates it after restore; current_assessment returns it only while company, plan and versions match.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .calculator import CALCULATOR_VERSION, MAX_LEGS

SCHEMA = 'carbon-assessment.v1'
MAX_RECORD_BYTES = 65536
_KEYS = {'schema', 'company_id', 'context_version', 'plan_id', 'offer_versions', 'material_id', 'registry_version',
         'calculator_version', 'inputs', 'result', 'created_at'}
_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')


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
    if not isinstance(record['offer_versions'], dict) or not all(
            isinstance(k, str) and type(v) is int for k, v in record['offer_versions'].items()):
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
    result = record['result']
    if not isinstance(result, dict) or result.get('plan_id') != record['plan_id'] \
            or result.get('company_id') != record['company_id']:
        _fail('result')
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
    except ValueError:
        return None
    if record['company_id'] != company_id or record['plan_id'] != plan.get('plan_id') \
            or record['offer_versions'] != dict(plan.get('offer_versions') or {}):
        return None
    return record
