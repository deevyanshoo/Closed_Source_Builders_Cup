"""Pure, offline boundary from one pinned Gemini SDK proposal to tool-call bytes.

The host owns trusted context, turn activity, tool execution and publication.
This module never constructs a provider client or executes a model proposal.
"""
from __future__ import annotations

from copy import deepcopy
from importlib import metadata
import json

from google.genai import types

from procure.agents.voice_tools import TOOL_NAME, function_declaration


PINNED_SDK_VERSION = '2.27.0'
_CALL_LIMIT = 128 << 10
_MAX_DEPTH = 16
_ARRAY_LIMIT = 256
_MAP_LIMIT = 256
_TOTAL_ENTRIES = 4096
_SIGNATURE_LIMIT = 128 << 10
_ROW_FIELDS = {
    'arrival_replacements': frozenset(('receipt_id', 'expected_arrival_at', 'arrival_at')),
    'assignments': frozenset(('order_id', 'period_id')),
}


class ProposalError(ValueError):
    """Fixed, sanitized rejection; no source values or SDK exceptions."""


def _invalid() -> None:
    raise ProposalError('invalid SDK proposal')


def _version() -> None:
    try:
        if metadata.version('google-genai') != PINNED_SDK_VERSION:
            raise ProposalError('unsupported SDK version')
    except metadata.PackageNotFoundError:
        raise ProposalError('unsupported SDK version') from None


def sdk_function_declaration() -> types.FunctionDeclaration:
    """Return a fresh SDK JSON-schema declaration, never a callable tool."""
    _version()
    declaration = function_declaration()
    try:
        return types.FunctionDeclaration(
            name=declaration['name'], description=declaration['description'],
            parameters_json_schema=deepcopy(declaration['parameters']))
    except Exception:
        raise ProposalError('invalid tool declaration') from None


def _storage(value: object, expected: type) -> dict:
    if type(value) is not expected:
        _invalid()
    stored = object.__getattribute__(value, '__dict__')
    fields = expected.model_fields
    if type(stored) is not dict or len(stored) > len(fields):
        _invalid()
    for key in stored:
        if type(key) is not str or key not in fields:
            _invalid()
    extra = object.__getattribute__(value, '__pydantic_extra__')
    if extra is not None and (type(extra) is not dict or len(extra) != 0):
        _invalid()
    return stored


def _field(value: object, expected: type, name: str):
    return _storage(value, expected).get(name)


def _ratings(value: object) -> None:
    if value is None:
        return
    if type(value) is not list or len(value) > 16:
        _invalid()
    for rating in value:
        blocked = _field(rating, types.SafetyRating, 'blocked')
        if blocked is not None and type(blocked) is not bool or blocked is True:
            _invalid()


def _bounded_string(value: object, limit: int = _CALL_LIMIT,
                    budget: list[int] | None = None) -> str:
    if type(value) is not str or len(value) > limit:
        _invalid()
    try:
        size = len(value.encode('utf-8'))
        if size > limit:
            _invalid()
        if budget is not None:
            budget[0] += size
            if budget[0] > _CALL_LIMIT:
                _invalid()
    except UnicodeError:
        _invalid()
    return value


def _clone(value: object, depth: int, total: list[int], seen: set[int],
           budget: list[int]) -> object:
    if depth > _MAX_DEPTH:
        _invalid()
    if type(value) is str:
        return _bounded_string(value, budget=budget)
    if type(value) not in (dict, list):
        _invalid()
    identity = id(value)
    if identity in seen:
        _invalid()
    seen.add(identity)
    try:
        size = len(value)
        if size > (_MAP_LIMIT if type(value) is dict else _ARRAY_LIMIT):
            _invalid()
        total[0] += size
        if total[0] > _TOTAL_ENTRIES:
            _invalid()
        if type(value) is dict:
            result = {}
            for key, item in value.items():
                result[_bounded_string(key, budget=budget)] = _clone(item, depth + 1, total, seen, budget)
            return result
        return [_clone(item, depth + 1, total, seen, budget) for item in value]
    finally:
        seen.remove(identity)


def _arguments(value: object) -> dict:
    if type(value) is not dict:
        _invalid()
    arguments = _clone(value, 0, [0], set(), [0])
    if set(arguments) - set(_ROW_FIELDS):
        _invalid()
    for name, rows in arguments.items():
        if type(rows) is not list:
            _invalid()
        for row in rows:
            if type(row) is not dict or set(row) - _ROW_FIELDS[name]:
                _invalid()
            if any(type(item) is not str for item in row.values()):
                _invalid()
    return arguments


def _single_call(response: object) -> dict:
    candidates = _field(response, types.GenerateContentResponse, 'candidates')
    if type(candidates) is not list or len(candidates) != 1:
        _invalid()
    if _field(response, types.GenerateContentResponse, 'automatic_function_calling_history') is not None:
        _invalid()
    if _field(response, types.GenerateContentResponse, 'parsed') is not None:
        _invalid()
    feedback = _field(response, types.GenerateContentResponse, 'prompt_feedback')
    if feedback is not None:
        if (_field(feedback, types.GenerateContentResponsePromptFeedback, 'block_reason') is not None or
                _field(feedback, types.GenerateContentResponsePromptFeedback, 'block_reason_message') is not None):
            _invalid()
        _ratings(_field(feedback, types.GenerateContentResponsePromptFeedback, 'safety_ratings'))

    candidate = candidates[0]
    if _field(candidate, types.Candidate, 'finish_reason') is not types.FinishReason.STOP:
        _invalid()
    if _field(candidate, types.Candidate, 'continuation_token') is not None:
        _invalid()
    _ratings(_field(candidate, types.Candidate, 'safety_ratings'))
    content = _field(candidate, types.Candidate, 'content')
    role = _field(content, types.Content, 'role')
    if type(role) is not str or role != 'model':
        _invalid()
    parts = _field(content, types.Content, 'parts')
    if type(parts) is not list or len(parts) != 1:
        _invalid()
    part = parts[0]
    part_fields = _storage(part, types.Part)
    if any(part_fields.get(name) is not None for name in types.Part.model_fields
           if name not in ('function_call', 'thought', 'thought_signature')):
        _invalid()
    thought = part_fields.get('thought')
    if thought is not None and (type(thought) is not bool or thought):
        _invalid()
    signature = part_fields.get('thought_signature')
    if signature is not None and (type(signature) is not bytes or len(signature) > _SIGNATURE_LIMIT):
        _invalid()
    call = part_fields.get('function_call')
    name = _field(call, types.FunctionCall, 'name')
    if type(name) is not str or name != TOOL_NAME:
        _invalid()
    call_id = _field(call, types.FunctionCall, 'id')
    if call_id is not None:
        _bounded_string(call_id, 256)
    if _field(call, types.FunctionCall, 'partial_args') is not None:
        _invalid()
    continuation = _field(call, types.FunctionCall, 'will_continue')
    if continuation is not None and (type(continuation) is not bool or continuation):
        _invalid()
    return _arguments(_field(call, types.FunctionCall, 'args'))


def proposal_call_bytes(response: object) -> bytes:
    """Own one completed SDK call as canonical bytes for the existing strict tool."""
    _version()
    try:
        arguments = _single_call(response)
        encoded = json.dumps({'name': TOOL_NAME, 'arguments': arguments}, sort_keys=True,
                             separators=(',', ':'), ensure_ascii=False,
                             allow_nan=False).encode('utf-8')
        if len(encoded) > _CALL_LIMIT:
            _invalid()
        return encoded
    except ProposalError:
        raise
    except Exception:
        raise ProposalError('invalid SDK proposal') from None
