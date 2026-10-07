"""Private local SDK projection mapper; returns only strict recovery-call bytes.

This protocol is server-owned test plumbing, not a public provider wire format.
It neither executes tools nor constructs a provider client.
"""
from __future__ import annotations

import json
import os
import sys


_INPUT_LIMIT = 256 << 10
_OUTPUT_LIMIT = 128 << 10
_DEPTH_LIMIT = 16
_ARRAY_LIMIT = 256
_MAP_LIMIT = 256
_TOTAL_ENTRIES = 4096
_STRING_LIMIT = 128 << 10
_ERROR = b'{"proposal_version":"1","error":"invalid_proposal"}'


def _cli_offline_guard() -> None:
    """Deny network and credential-file reads before SDK import in CLI mode."""
    def deny(*_args, **_kwargs):
        raise ValueError('invalid local proposal')

    def audit(event, args):
        if event.startswith('socket.'):
            deny()
        if event == 'open' and args:
            path = args[0]
            if type(path) in (str, bytes):
                basename = os.path.basename(os.fsdecode(path)).lower()
                if (basename == '.env' or basename.startswith('.env.') or
                        basename == 'application_default_credentials.json' or
                        ('credential' in basename and basename.endswith('.json'))):
                    deny()
    sys.addaudithook(audit)


if __name__ == '__main__':
    _cli_offline_guard()

from google import genai
from google.genai import client as genai_client, types
from procure.agents.gemini_voice_bridge import proposal_call_bytes


def _disable_cli_provider_access() -> None:
    def deny(*_args, **_kwargs):
        raise ValueError('invalid local proposal')
    genai.Client = deny
    genai_client.Client.__init__ = deny
    try:
        import google.auth
    except ImportError:
        pass
    else:
        google.auth.default = deny
        google.auth._default.default = deny
    try:
        import dotenv
    except ImportError:
        pass
    else:
        dotenv.load_dotenv = deny
        dotenv.dotenv_values = deny


if __name__ == '__main__':
    _disable_cli_provider_access()


def _invalid() -> None:
    raise ValueError('invalid local proposal')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _nonfinite(_value):
    _invalid()


def _shape(value, depth: int, total: list[int], budget: list[int]) -> None:
    if depth > _DEPTH_LIMIT:
        _invalid()
    if type(value) is dict:
        if len(value) > _MAP_LIMIT:
            _invalid()
        total[0] += len(value)
        if total[0] > _TOTAL_ENTRIES:
            _invalid()
        for key, item in value.items():
            _string(key, budget)
            _shape(item, depth + 1, total, budget)
    elif type(value) is list:
        if len(value) > _ARRAY_LIMIT:
            _invalid()
        total[0] += len(value)
        if total[0] > _TOTAL_ENTRIES:
            _invalid()
        for item in value:
            _shape(item, depth + 1, total, budget)
    elif type(value) is str:
        _string(value, budget)
    elif value is None or type(value) in (bool, int, float):
        return
    else:
        _invalid()


def _string(value, budget: list[int] | None = None, limit: int = _STRING_LIMIT) -> str:
    if type(value) is not str or len(value) > limit:
        _invalid()
    size = len(value.encode('utf-8'))
    if size > limit:
        _invalid()
    if budget is not None:
        budget[0] += size
        if budget[0] > _INPUT_LIMIT:
            _invalid()
    return value


def _object(value, allowed: frozenset[str]) -> dict:
    if type(value) is not dict or set(value) - allowed:
        _invalid()
    return value


def _ratings(value):
    if value is None:
        return None
    if type(value) is not list or len(value) > 16:
        _invalid()
    result = []
    for item in value:
        item = _object(item, frozenset(('blocked',)))
        blocked = item.get('blocked')
        if blocked is not None and (type(blocked) is not bool or blocked):
            _invalid()
        result.append(types.SafetyRating(blocked=blocked))
    return result


def _arguments(value):
    value = _object(value, frozenset(('arrival_replacements', 'assignments')))
    fields = {
        'arrival_replacements': frozenset(('receipt_id', 'expected_arrival_at', 'arrival_at')),
        'assignments': frozenset(('order_id', 'period_id')),
    }
    for name, rows in value.items():
        if type(rows) is not list:
            _invalid()
        for row in rows:
            row = _object(row, fields[name])
            for item in row.values():
                _string(item)
    return value


def _response(value):
    value = _object(value, frozenset(('candidates', 'prompt_feedback',
                                     'automatic_function_calling_history', 'parsed')))
    if value.get('automatic_function_calling_history') is not None or value.get('parsed') is not None:
        _invalid()
    candidates = value.get('candidates')
    if type(candidates) is not list or len(candidates) != 1:
        _invalid()
    candidate = _object(candidates[0], frozenset(('finish_reason', 'content',
                                                'continuation_token', 'safety_ratings')))
    if candidate.get('continuation_token') is not None:
        _invalid()
    reason = candidate.get('finish_reason')
    if type(reason) is not str or reason != 'STOP':
        _invalid()
    content = _object(candidate.get('content'), frozenset(('role', 'parts')))
    if type(content.get('role')) is not str or content['role'] != 'model':
        _invalid()
    parts = content.get('parts')
    if type(parts) is not list or len(parts) != 1:
        _invalid()
    part = _object(parts[0], frozenset(('function_call', 'text', 'thought')))
    if 'text' in part and part['text'] is not None:
        _invalid()
    thought = part.get('thought')
    if thought is not None and (type(thought) is not bool or thought):
        _invalid()
    call = _object(part.get('function_call'), frozenset(('name', 'args', 'id',
                                                      'partial_args', 'will_continue')))
    if type(call.get('name')) is not str or call['name'] != 'compare_receipt_recovery':
        _invalid()
    if call.get('partial_args') is not None:
        _invalid()
    will_continue = call.get('will_continue')
    if will_continue is not None and (type(will_continue) is not bool or will_continue):
        _invalid()
    call_id = call.get('id')
    if call_id is not None:
        _string(call_id, limit=256)
    args = _arguments(call.get('args'))
    feedback = value.get('prompt_feedback')
    sdk_feedback = None
    if feedback is not None:
        feedback = _object(feedback, frozenset(('block_reason', 'block_reason_message',
                                              'safety_ratings')))
        if feedback.get('block_reason') is not None or feedback.get('block_reason_message') is not None:
            _invalid()
        sdk_feedback = types.GenerateContentResponsePromptFeedback(
            safety_ratings=_ratings(feedback.get('safety_ratings')))
    sdk_call = types.FunctionCall(name=call['name'], args=args, id=call_id,
                                  will_continue=will_continue)
    sdk_part = types.Part(function_call=sdk_call, thought=thought)
    sdk_content = types.Content(role='model', parts=[sdk_part])
    sdk_candidate = types.Candidate(finish_reason=types.FinishReason.STOP,
                                    content=sdk_content,
                                    safety_ratings=_ratings(candidate.get('safety_ratings')))
    return types.GenerateContentResponse(candidates=[sdk_candidate], prompt_feedback=sdk_feedback)


def map_local_proposal(request_json: bytes) -> bytes:
    """Map one bounded private JSON projection through the existing pinned bridge."""
    try:
        if type(request_json) is not bytes or len(request_json) > _INPUT_LIMIT:
            _invalid()
        request = json.loads(request_json.decode('utf-8'),
                             object_pairs_hook=_pairs, parse_constant=_nonfinite)
        _shape(request, 0, [0], [0])
        request = _object(request, frozenset(('proposal_version', 'sdk_response')))
        if set(request) != {'proposal_version', 'sdk_response'} or type(request['proposal_version']) is not str or request['proposal_version'] != '1':
            _invalid()
        result = proposal_call_bytes(_response(request['sdk_response']))
        if len(result) > _OUTPUT_LIMIT:
            _invalid()
        return result
    except Exception:
        raise ValueError('invalid local proposal') from None


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(_INPUT_LIMIT + 1)
        result = map_local_proposal(raw)
    except Exception:
        sys.stdout.buffer.write(_ERROR)
        return 1
    sys.stdout.buffer.write(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
