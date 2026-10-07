"""Read-only, model-neutral receipt recovery function-call boundary.

The host authenticates and owns a TrustedRecoveryContext and active-turn guard.
Only two arrays come from the model. This module has no transport, provider,
approval, persistence, speech or mutable session registry.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json

from procure.production import http_comparison as wire
from procure.production.recovery import compare_receipt_recovery


TOOL_NAME = 'compare_receipt_recovery'
_CALL_LIMIT = 128 << 10
_CONTEXT_LIMIT = 1 << 20
_OUTPUT_LIMIT = 2 << 20
_MAX_DEPTH = 16
_ARRAY_LIMIT = 256
_TOTAL_ARRAY_ITEMS = 4096


@dataclass(frozen=True, slots=True)
class TrustedRecoveryContext:
    workspace_id: str
    snapshot_id: str
    plan_version: str
    scenario_id: str
    session_id: str
    turn_id: str
    snapshot: Mapping


def function_declaration() -> dict:
    """Return a fresh standalone JSON declaration with no trusted context fields."""
    replacement = deepcopy(wire._DEFINITIONS['ReceiptArrivalReplacement'])
    assignment = deepcopy(wire._DEFINITIONS['ProductionAssignment'])
    return {
        'name': TOOL_NAME,
        'description': 'Compare explicit synthetic receipt arrival changes and a complete supplied production assignment. Read only.',
        'parameters': {
            'type': 'object', 'additionalProperties': False,
            'properties': {
                'arrival_replacements': {'type': 'array', 'items': replacement,
                                         'maxItems': _ARRAY_LIMIT},
                'assignments': {'type': 'array', 'items': assignment,
                                'minItems': 1, 'maxItems': _ARRAY_LIMIT},
            },
            'required': ['arrival_replacements', 'assignments'],
        },
    }


class _Reject(ValueError):
    pass


def _identifier(value):
    return isinstance(value, str) and 0 < len(value) <= 256 and value == value.strip()


def _clone(value, depth, count):
    """Own JSON-shaped values while bounding every array and total work."""
    if depth > _MAX_DEPTH:
        raise _Reject('depth')
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise _Reject('nonstring key')
        return {key: _clone(item, depth + 1, count) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        if len(value) > _ARRAY_LIMIT:
            raise _Reject('array limit')
        count[0] += len(value)
        if count[0] > _TOTAL_ARRAY_ITEMS:
            raise _Reject('array total')
        return [_clone(item, depth + 1, count) for item in value]
    if isinstance(value, (str, bool)):
        return value
    # Every quantity and timestamp in this interface is a string; coercing
    # Decimal, float, integer or null would change the authoritative input.
    raise _Reject('non-json value')


def _encoded(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False,
                      separators=(',', ':')).encode('utf-8')


def _outcome(status, ownership=None, *, reason=None, missing_fields=None, result=None):
    response = {'tool_name': TOOL_NAME, 'read_only': True, 'status': status}
    if ownership is not None:
        response['ownership'] = ownership
    if reason is not None:
        response['reason'] = reason
    if missing_fields is not None:
        response['missing_fields'] = sorted(set(missing_fields))
    if result is not None:
        response['result'] = result
    return response


def _active(guard, context):
    try:
        return callable(guard) and guard(context.session_id, context.turn_id) is True
    except Exception:
        return False


def _missing(arguments):
    missing = []
    for key in ('arrival_replacements', 'assignments'):
        if key not in arguments:
            missing.append('arguments.' + key)
    if arguments.get('assignments') == []:
        missing.append('arguments.assignments')
    for key, fields in (
        ('arrival_replacements', ('receipt_id', 'expected_arrival_at', 'arrival_at')),
        ('assignments', ('order_id', 'period_id')),
    ):
        rows = arguments.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    missing.extend('arguments.' + key + '[].' + field
                                   for field in fields if field not in row)
    return missing


def _unknown_fields(arguments, parameters):
    if set(arguments) - set(parameters['properties']):
        return True
    for key, field in parameters['properties'].items():
        rows = arguments.get(key)
        if isinstance(rows, list):
            allowed = set(field['items']['properties'])
            if any(isinstance(row, dict) and set(row) - allowed for row in rows):
                return True
    return False


def execute_recovery_tool(call_json, context, *, is_active):
    if not isinstance(context, TrustedRecoveryContext) or not all(
            _identifier(getattr(context, field)) for field in
            ('workspace_id', 'snapshot_id', 'plan_version', 'scenario_id',
             'session_id', 'turn_id')):
        return _outcome('rejected', reason='invalid_context')
    ownership = {'session_id': context.session_id, 'turn_id': context.turn_id}
    if not _active(is_active, context):
        return _outcome('suppressed', ownership, reason='inactive_turn')

    try:
        if isinstance(call_json, bytes):
            if len(call_json) > _CALL_LIMIT:
                raise _Reject('call_limit')
            raw = call_json.decode('utf-8', errors='strict')
        elif isinstance(call_json, str):
            raw = call_json
            if len(raw.encode('utf-8')) > _CALL_LIMIT:
                raise _Reject('call_limit')
        else:
            raise _Reject('call_type')
        call = json.loads(raw, object_pairs_hook=wire._pairs,
                          parse_constant=wire._nonfinite)
        count = [0]
        call = _clone(call, 0, count)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return _outcome('rejected', ownership, reason='invalid_call')
    if not isinstance(call, dict) or set(call) != {'name', 'arguments'}:
        return _outcome('rejected', ownership, reason='invalid_call')
    if call['name'] != TOOL_NAME:
        return _outcome('rejected', ownership, reason='unknown_tool')
    arguments = call['arguments']
    if not isinstance(arguments, dict):
        return _outcome('rejected', ownership, reason='invalid_arguments')
    parameters = function_declaration()['parameters']
    if _unknown_fields(arguments, parameters):
        return _outcome('rejected', ownership, reason='invalid_arguments')
    missing = _missing(arguments)
    if missing:
        return _outcome('clarification_required', ownership,
                        reason='missing_critical_fields', missing_fields=missing)
    try:
        wire._valid(arguments, parameters)
    except (ValueError, TypeError, RecursionError):
        return _outcome('rejected', ownership, reason='invalid_arguments')

    try:
        snapshot = _clone(context.snapshot, 0, count)
        if len(_encoded(snapshot)) > _CONTEXT_LIMIT:
            raise _Reject('context limit')
        wire._valid(snapshot, wire._DEFINITIONS['ProductionSnapshot'])
    except (ValueError, TypeError, RecursionError):
        return _outcome('rejected', ownership, reason='invalid_context')
    if (snapshot['workspace_id'] != context.workspace_id or
            snapshot['snapshot_id'] != context.snapshot_id or
            snapshot['plan_version'] != context.plan_version):
        return _outcome('rejected', ownership, reason='context_mismatch')
    orders = {row['order_id'] for row in snapshot['orders']}
    receipts = {row['receipt_id'] for row in snapshot['receipts']}
    assignment_ids = [row['order_id'] for row in arguments['assignments']]
    replacement_ids = [row['receipt_id'] for row in arguments['arrival_replacements']]
    if len(set(assignment_ids)) != len(assignment_ids):
        return _outcome('rejected', ownership, reason='duplicate_assignment')
    if any(order_id not in orders for order_id in assignment_ids):
        return _outcome('rejected', ownership, reason='unknown_order')
    if set(assignment_ids) != orders:
        return _outcome('clarification_required', ownership,
                        reason='incomplete_assignments',
                        missing_fields=['arguments.assignments'])
    if len(set(replacement_ids)) != len(replacement_ids):
        return _outcome('rejected', ownership, reason='duplicate_replacement')
    if any(receipt_id not in receipts for receipt_id in replacement_ids):
        return _outcome('rejected', ownership, reason='unknown_receipt')

    scenario = {'workspace_id': context.workspace_id,
                'expected_plan_version': context.plan_version,
                'source_snapshot_id': context.snapshot_id,
                'scenario_id': context.scenario_id, 'synthetic': True,
                'arrival_replacements': arguments['arrival_replacements']}
    recovery = {'workspace_id': context.workspace_id,
                'expected_plan_version': context.plan_version,
                'source_snapshot_id': context.snapshot_id,
                'scenario_id': context.scenario_id, 'synthetic': True,
                'assignments': arguments['assignments']}
    envelope = {'contract_version': '0.2.0', 'snapshot': snapshot,
                'scenario': scenario, 'recovery': recovery}
    try:
        if len(_encoded(envelope)) > _CONTEXT_LIMIT:
            raise _Reject('envelope limit')
        wire._valid(envelope, wire._DEFINITIONS['RecoveryComparisonRequest'])
    except (ValueError, TypeError, RecursionError):
        return _outcome('rejected', ownership, reason='invalid_envelope')
    if not _active(is_active, context):
        return _outcome('suppressed', ownership, reason='inactive_turn')
    try:
        result = compare_receipt_recovery(snapshot, scenario, recovery)
    except Exception:
        if not _active(is_active, context):
            return _outcome('suppressed', ownership, reason='inactive_turn')
        return _outcome('unavailable', ownership, reason='comparison_unavailable')
    if not _active(is_active, context):
        return _outcome('suppressed', ownership, reason='inactive_turn')
    try:
        if not isinstance(result, dict) or result.get('status') not in {'compared', 'unsupported'}:
            raise _Reject('result status')
        if result['status'] == 'compared':
            wire._valid(result, wire._DEFINITIONS['ReceiptRecoveryComparison'])
        else:
            wire._valid({'contract_version': '0.2.0', **result},
                        wire._DEFINITIONS['RecoveryComparisonUnsupportedResponse'])
        if len(_encoded(result)) > _OUTPUT_LIMIT:
            raise _Reject('output limit')
    except (ValueError, TypeError, RecursionError):
        return _outcome('unavailable', ownership, reason='comparison_unavailable')
    if not _active(is_active, context):
        return _outcome('suppressed', ownership, reason='inactive_turn')
    return _outcome(result['status'], ownership, result=result)
