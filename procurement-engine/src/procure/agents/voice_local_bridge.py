"""One-request fixed local process bridge to the read-only recovery tool.

The host owns context identity and synthetic snapshot. The child does not query
the Go manager; process cancellation and guarded enqueue remain host duties.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unicodedata

from procure.agents.voice_tools import (TOOL_NAME, TrustedRecoveryContext,
                                       execute_recovery_tool)
from procure.production import http_comparison as wire

_CALL_LIMIT = 128 << 10
_HOST_LIMIT = 1 << 20
_REQUEST_LIMIT = 3 << 20
_RESPONSE_LIMIT = 2 << 20
_DEPTH_LIMIT = 16
_ARRAY_LIMIT = 256
_TOTAL_ITEMS = 4096
_HOST_KEYS = {'principal_id', 'workspace_id', 'snapshot_id', 'plan_version',
              'scenario_id', 'session_id', 'turn_id', 'snapshot'}


class _Invalid(ValueError):
    pass


def _id(value):
    return (isinstance(value, str) and 0 < len(value.encode('utf-8')) <= 256
            and value == value.strip()
            and not any(unicodedata.category(char) == 'Cc' for char in value))


def _parse(raw):
    return json.loads(raw, object_pairs_hook=wire._pairs,
                      parse_constant=wire._nonfinite)


def _shape(value, depth, count):
    if depth > _DEPTH_LIMIT:
        raise _Invalid('depth')
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise _Invalid('key')
        for item in value.values():
            _shape(item, depth + 1, count)
    elif isinstance(value, list):
        if len(value) > _ARRAY_LIMIT:
            raise _Invalid('array')
        count[0] += len(value)
        if count[0] > _TOTAL_ITEMS:
            raise _Invalid('total')
        for item in value:
            _shape(item, depth + 1, count)
    elif not isinstance(value, (str, bool)):
        raise _Invalid('scalar')


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def execute_local_request(request_json, *, is_active):
    """Return binding plus the unchanged public tool outcome, or raise ValueError."""
    try:
        if isinstance(request_json, bytes):
            if len(request_json) > _REQUEST_LIMIT:
                raise _Invalid('request size')
            raw = request_json.decode('utf-8', errors='strict')
        elif isinstance(request_json, str):
            raw = request_json
            if len(raw.encode('utf-8')) > _REQUEST_LIMIT:
                raise _Invalid('request size')
        else:
            raise _Invalid('request type')
        request = _parse(raw)
        if (not isinstance(request, dict) or set(request) !=
                {'bridge_version', 'host_context_json', 'call_json'} or
                request['bridge_version'] != '1' or
                not isinstance(request['host_context_json'], str) or
                not isinstance(request['call_json'], str)):
            raise _Invalid('protocol')
        host_raw = request['host_context_json']
        call_raw = request['call_json']
        host_bytes = host_raw.encode('utf-8')
        if len(host_bytes) > _HOST_LIMIT or len(call_raw.encode('utf-8')) > _CALL_LIMIT:
            raise _Invalid('nested size')
        host = _parse(host_raw)
        call = _parse(call_raw)
        count = [0]
        _shape(host, 0, count)
        _shape(call, 0, count)
        if not isinstance(host, dict) or set(host) != _HOST_KEYS:
            raise _Invalid('host keys')
        if not all(_id(host[key]) for key in _HOST_KEYS - {'snapshot'}):
            raise _Invalid('host identity')
        snapshot = host['snapshot']
        if (not isinstance(snapshot, dict) or
                snapshot.get('workspace_id') != host['workspace_id'] or
                snapshot.get('snapshot_id') != host['snapshot_id'] or
                snapshot.get('plan_version') != host['plan_version'] or
                snapshot.get('synthetic') is not True or
                not isinstance(snapshot.get('as_of'), str) or
                not isinstance(call, dict) or set(call) != {'name', 'arguments'} or
                call['name'] != TOOL_NAME or not isinstance(call['arguments'], dict) or
                set(call['arguments']) - {'arrival_replacements', 'assignments'}):
            raise _Invalid('scope')
        context = TrustedRecoveryContext(
            workspace_id=host['workspace_id'], snapshot_id=host['snapshot_id'],
            plan_version=host['plan_version'], scenario_id=host['scenario_id'],
            session_id=host['session_id'], turn_id=host['turn_id'],
            snapshot=snapshot)
        outcome = execute_recovery_tool(call_raw, context, is_active=is_active)
        binding = {key: host[key] for key in
                   ('principal_id', 'workspace_id', 'snapshot_id', 'plan_version',
                    'scenario_id', 'session_id', 'turn_id')}
        binding['context_sha256'] = hashlib.sha256(host_bytes).hexdigest()
        response = {'bridge_version': '1', 'binding': binding, 'outcome': outcome}
        if len(_bytes(response)) > _RESPONSE_LIMIT:
            raise _Invalid('response size')
        return response
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise _Invalid('invalid local request') from None


def main():
    raw = sys.stdin.buffer.read(_REQUEST_LIMIT + 1)
    try:
        response = execute_local_request(raw, is_active=lambda _s, _t: True)
    except Exception:
        sys.stdout.buffer.write(b'{"bridge_version":"1","error":"invalid_request"}')
        return 1
    sys.stdout.buffer.write(_bytes(response))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
