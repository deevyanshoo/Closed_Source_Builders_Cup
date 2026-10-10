"""Unwired offline text-proposal source for one synthetic, host-owned context.

The future host must authenticate the context and check the user's semantic
intent. This module neither proves ownership nor executes its returned bytes.
``generate_once`` is a local injected fake callback, not a real SDK signature.
"""
from __future__ import annotations

from datetime import datetime
import json

from procure.agents.gemini_voice_bridge import proposal_call_bytes, sdk_function_declaration


_MAX_ROWS = 64
_MAX_ID_BYTES = 128
_MAX_UTTERANCE_BYTES = 4096
_MAX_REQUEST_BYTES = 32 << 10
_MAX_TOOL_BYTES = 8 << 10
_ERROR = 'invalid offline proposal'
_REQUIRED = frozenset(('receipts', 'orders', 'periods'))
_OPTIONAL = frozenset(('snapshot_id', 'plan_version', 'as_of'))


class SourceError(ValueError):
    """Fixed public refusal, without projection, utterance or SDK details."""


def _invalid() -> None:
    raise SourceError(_ERROR)


def _utf8(value: object, maximum: int) -> int:
    if type(value) is not str or not value or len(value) > maximum:
        _invalid()
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        _invalid()
    if size > maximum:
        _invalid()
    return size


def _id(value: object) -> None:
    _utf8(value, _MAX_ID_BYTES)
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _invalid()


def _utc(value: object) -> None:
    if type(value) is not str or len(value) != 20 or not value.endswith('Z'):
        _invalid()
    try:
        if datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%dT%H:%M:%SZ') != value:
            _invalid()
    except ValueError:
        _invalid()


def _json_string_size(value: str) -> int:
    # Exact or conservative compact UTF-8 JSON size; no large intermediate copy.
    size = 2
    for char in value:
        code = ord(char)
        if code < 32:
            size += 6  # \uXXXX bounds shorter standard escapes.
        elif char in ('"', '\\'):
            size += 2
        else:
            size += len(char.encode('utf-8'))
    return size


def _object_size(fields: list[tuple[str, int]]) -> int:
    return 2 + max(0, len(fields) - 1) + sum(_json_string_size(key) + 1 + size for key, size in fields)


def _array_size(sizes: list[int]) -> int:
    return 2 + max(0, len(sizes) - 1) + sum(sizes)


def _rows(value: object, keys: frozenset[str]) -> list[dict]:
    if type(value) is not list or not 1 <= len(value) <= _MAX_ROWS:
        _invalid()
    ids = set()
    for row in value:
        if type(row) is not dict or len(row) != len(keys):
            _invalid()
        if any(type(key) is not str for key in row):
            _invalid()
        if set(row) != keys:
            _invalid()
        key = next(key for key in keys if key.endswith('_id'))
        _id(row[key])
        if row[key] in ids:
            _invalid()
        ids.add(row[key])
        if 'arrival_at' in keys:
            _utc(row['arrival_at'])
    return value


def _projection(value: object, utterance: object, tool_size: int) -> tuple[dict, dict[str, str], set[str], set[str]]:
    _utf8(utterance, _MAX_UTTERANCE_BYTES)
    if not utterance.strip() or type(value) is not dict or not 3 <= len(value) <= 6:
        _invalid()
    if any(type(key) is not str for key in value):
        _invalid()
    present = set(value)
    if present - (_REQUIRED | _OPTIONAL) or not _REQUIRED <= present:
        _invalid()
    receipts = _rows(value['receipts'], frozenset(('receipt_id', 'arrival_at')))
    orders = _rows(value['orders'], frozenset(('order_id',)))
    periods = _rows(value['periods'], frozenset(('period_id',)))
    for key in ('snapshot_id', 'plan_version'):
        if key in value:
            _id(value[key])
    if 'as_of' in value:
        _utc(value['as_of'])

    fields = [
        ('receipts', _array_size([_object_size([('receipt_id', _json_string_size(row['receipt_id'])),
                                               ('arrival_at', _json_string_size(row['arrival_at']))]) for row in receipts])),
        ('orders', _array_size([_object_size([('order_id', _json_string_size(row['order_id']))]) for row in orders])),
        ('periods', _array_size([_object_size([('period_id', _json_string_size(row['period_id']))]) for row in periods])),
    ]
    fields.extend((key, _json_string_size(value[key])) for key in ('snapshot_id', 'plan_version', 'as_of') if key in value)
    request_bytes = _object_size([('utterance', _json_string_size(utterance)),
                                  ('projection', _object_size(fields)),
                                  ('tool', tool_size)])
    if request_bytes > _MAX_REQUEST_BYTES:
        _invalid()

    # Copy only after all shape, item and aggregate budgets pass. Strings are immutable.
    owned = {
        'receipts': [{'receipt_id': row['receipt_id'], 'arrival_at': row['arrival_at']} for row in receipts],
        'orders': [{'order_id': row['order_id']} for row in orders],
        'periods': [{'period_id': row['period_id']} for row in periods],
    }
    owned.update({key: value[key] for key in ('snapshot_id', 'plan_version', 'as_of') if key in value})
    return owned, {row['receipt_id']: row['arrival_at'] for row in receipts}, \
        {row['order_id'] for row in orders}, {row['period_id'] for row in periods}


def _call(encoded: bytes, arrivals: dict[str, str], orders: set[str], periods: set[str]) -> None:
    call = json.loads(encoded)
    if type(call) is not dict or set(call) != {'name', 'arguments'} or call['name'] != 'compare_receipt_recovery':
        _invalid()
    args = call['arguments']
    if type(args) is not dict or set(args) != {'arrival_replacements', 'assignments'}:
        _invalid()
    replacements, assignments = args['arrival_replacements'], args['assignments']
    if type(replacements) is not list or not 1 <= len(replacements) <= _MAX_ROWS or \
            type(assignments) is not list or len(assignments) != len(orders):
        _invalid()
    seen_receipts = set()
    for row in replacements:
        if type(row) is not dict or set(row) != {'receipt_id', 'expected_arrival_at', 'arrival_at'}:
            _invalid()
        receipt_id = row['receipt_id']
        if type(receipt_id) is not str or receipt_id not in arrivals or receipt_id in seen_receipts or \
                row['expected_arrival_at'] != arrivals[receipt_id]:
            _invalid()
        _utc(row['arrival_at'])
        if row['arrival_at'] == arrivals[receipt_id]:
            _invalid()
        seen_receipts.add(receipt_id)
    seen_orders = set()
    for row in assignments:
        if type(row) is not dict or set(row) != {'order_id', 'period_id'}:
            _invalid()
        order_id, period_id = row['order_id'], row['period_id']
        if type(order_id) is not str or type(period_id) is not str or order_id not in orders or \
                order_id in seen_orders or period_id not in periods:
            _invalid()
        seen_orders.add(order_id)
    if seen_orders != orders:
        _invalid()


def propose_once(projection: object, utterance: object, generate_once: object, *, is_cancelled: object) -> bytes:
    """Return untrusted canonical tool bytes after one injected fake response.

    No callback request is a real SDK transport contract. A future host must
    recheck context ownership/revision, semantic intent and typed tool authority.
    A blocking callback cannot be interrupted by this synchronous seam.
    """
    try:
        if not callable(generate_once) or not callable(is_cancelled) or is_cancelled() is not False:
            _invalid()
        declaration = sdk_function_declaration()  # Pinned SDK/version check before fake invocation.
        tool_json = declaration.model_dump_json(by_alias=True, exclude_none=True)
        tool_size = _utf8(tool_json, _MAX_TOOL_BYTES)
        owned, arrivals, orders, periods = _projection(projection, utterance, tool_size)
        if is_cancelled() is not False:
            _invalid()
        response = generate_once({'utterance': utterance, 'projection': owned, 'tool': declaration})
        if is_cancelled() is not False:
            _invalid()
        encoded = proposal_call_bytes(response)
        _call(encoded, arrivals, orders, periods)
        if is_cancelled() is not False:
            _invalid()
        return encoded
    except Exception:
        raise SourceError(_ERROR) from None


def propose_configured_once(
    projection: object,
    utterance: object,
    *,
    model: object,
    generate_content: object,
    is_cancelled: object,
) -> bytes:
    """Build one bounded SDK-shaped fake request and return unverified tool bytes.

    ``generate_content`` is an injected callback, never an SDK client. The
    caller still owns context, semantic confirmation, and typed execution.
    A synchronous, noncooperative callback cannot be interrupted here.
    """
    try:
        if (type(model) is not str or not model or len(model) > 128 or
                not all(char.isascii() and (char.isalnum() or char in '-._/')
                        for char in model)):
            _invalid()
        _utf8(model, 128)
        if not callable(generate_content) or not callable(is_cancelled) or is_cancelled() is not False:
            _invalid()

        # This creates only pinned SDK data models; no Client or transport is
        # constructed. The declaration's serialized bytes count in the entire
        # callback-request budget before any projection copy.
        from google.genai import types

        declaration = sdk_function_declaration()
        tool_json = declaration.model_dump_json(by_alias=True, exclude_none=True)
        tool_size = _utf8(tool_json, _MAX_TOOL_BYTES)
        owned, arrivals, orders, periods = _projection(projection, utterance, tool_size)
        contents = json.dumps({'utterance': utterance, 'projection': owned},
                              sort_keys=True, separators=(',', ':'),
                              ensure_ascii=False, allow_nan=False)
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=[declaration])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=['compare_receipt_recovery'])),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            candidate_count=1,
            max_output_tokens=512,
            http_options=types.HttpOptions(
                timeout=3500, retry_options=types.HttpRetryOptions(attempts=1)),
        )
        if is_cancelled() is not False:
            _invalid()
        response = generate_content(model=model, contents=contents, config=config)
        if is_cancelled() is not False:
            _invalid()
        encoded = proposal_call_bytes(response)
        _call(encoded, arrivals, orders, periods)
        if is_cancelled() is not False:
            _invalid()
        return encoded
    except Exception:
        raise SourceError(_ERROR) from None
