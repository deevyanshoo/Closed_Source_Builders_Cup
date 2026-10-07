"""Strict HTTP boundary for the public, pure receipt-recovery comparison."""
from __future__ import annotations

import json
from pathlib import Path
import re

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, Response

from procure.production.recovery import compare_receipt_recovery


_SCHEMA = json.loads(Path(__file__).with_name('recovery_http_schema.json').read_text())
_DEFINITIONS = _SCHEMA['components']['schemas']
_LIMITS = _SCHEMA['limits']
router = APIRouter()


class _Invalid(ValueError):
    pass


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _Invalid('duplicate key')
        result[key] = value
    return result


def _nonfinite(_):
    raise _Invalid('nonfinite number')


def _valid(value, schema, depth=0):
    if depth > _LIMITS['max_depth']:
        raise _Invalid('depth')
    if '$ref' in schema:
        prefix = '#/components/schemas/'
        ref = schema['$ref']
        if not ref.startswith(prefix) or ref[len(prefix):] not in _DEFINITIONS:
            raise _Invalid('schema reference')
        return _valid(value, _DEFINITIONS[ref[len(prefix):]], depth + 1)
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict):
            raise _Invalid('object')
        properties = schema.get('properties', {})
        if not set(schema.get('required', ())).issubset(value):
            raise _Invalid('required')
        if schema.get('additionalProperties') is False and value.keys() - properties.keys():
            raise _Invalid('unknown')
        for key, item in value.items():
            if key in properties:
                _valid(item, properties[key], depth + 1)
    elif kind == 'array':
        if not isinstance(value, list):
            raise _Invalid('array')
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            raise _Invalid('items')
        for item in value:
            _valid(item, schema['items'], depth + 1)
        if schema.get('uniqueItems'):
            keys = {json.dumps(item, sort_keys=True, separators=(',', ':')) for item in value}
            if len(keys) != len(value):
                raise _Invalid('duplicate item')
    elif kind == 'string':
        if not isinstance(value, str):
            raise _Invalid('string')
        if (len(value) < schema.get('minLength', 0) or
                len(value) > schema.get('maxLength', len(value)) or
                ('pattern' in schema and re.fullmatch(schema['pattern'], value) is None)):
            raise _Invalid('string bound')
    elif kind == 'boolean':
        if type(value) is not bool:
            raise _Invalid('boolean')
    else:
        raise _Invalid('schema type')
    if 'const' in schema and value != schema['const']:
        raise _Invalid('const')
    if 'enum' in schema and value not in schema['enum']:
        raise _Invalid('enum')


def _error(status, code, message):
    return JSONResponse({'contract_version': '0.2.0',
                         'error': {'code': code, 'message': message}}, status_code=status)


@router.post('/production/receipt-recovery/comparisons')
async def receipt_recovery_comparison(request: Request):
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        return _error(422, 'invalid_request', 'Invalid recovery comparison request')
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _LIMITS['request_bytes']:
            return _error(413, 'request_too_large', 'Recovery comparison request too large')
    try:
        value = json.loads(body, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        _valid(value, _DEFINITIONS['RecoveryComparisonRequest'])
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _error(422, 'invalid_request', 'Invalid recovery comparison request')
    try:
        result = compare_receipt_recovery(value['snapshot'], value['scenario'], value['recovery'])
    except Exception:
        return _error(503, 'service_unavailable', 'Recovery comparison unavailable')
    if result['status'] == 'unsupported':
        payload = {'contract_version': '0.2.0', 'status': 'unsupported',
                   'reason': result['reason'], 'details': []}
        schema_name, status = 'RecoveryComparisonUnsupportedResponse', 422
    else:
        payload = {'contract_version': '0.2.0', 'comparison': result}
        schema_name, status = 'RecoveryComparisonResponse', 200
    try:
        _valid(payload, _DEFINITIONS[schema_name])
        encoded = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(encoded) > _LIMITS['response_bytes']:
            raise _Invalid('response size')
    except (ValueError, TypeError, RecursionError):
        return _error(503, 'service_unavailable', 'Recovery comparison unavailable')
    return Response(encoded, status_code=status, media_type='application/json')
