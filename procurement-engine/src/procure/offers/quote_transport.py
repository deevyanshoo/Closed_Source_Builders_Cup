"""Pure signed quote ASGI transport shared by normal and dedicated local hosts."""
from __future__ import annotations

import re
from starlette.responses import JSONResponse
from .context import HopRejected, QuoteHopVerifier, strict_json

_OFFER_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


def _offer_route(method: str, path: str):
    if path == '/offers' and method == 'GET':
        return {'status', 'as_of'}, 0, False, 8 << 20
    if path == '/offers/sourcing-candidates' and method == 'GET':
        return {'as_of'}, 0, False, 8 << 20
    if path == '/offers/extractions' and method == 'POST':
        return set(), 12 << 20, False, 24 << 20
    parts = path.split('/')
    if len(parts) == 4 and parts[:3] == ['', 'offers', 'extractions'] and _OFFER_ID.fullmatch(parts[3]) and method == 'GET':
        return set(), 0, False, 24 << 20
    if len(parts) >= 3 and parts[:2] == ['', 'offers'] and parts[2] not in ('extractions', 'sourcing-candidates', 'planning-input') and _OFFER_ID.fullmatch(parts[2]):
        if len(parts) == 3 and method == 'GET':
            return {'include_events', 'as_of'}, 0, False, 8 << 20
        if len(parts) == 4 and parts[3] in ('corrections', 'save') and method == 'POST':
            return set(), 64 << 10, True, 8 << 20
    return None


def _offer_query(raw: bytes, allowed: set[str]) -> bool:
    # Bound work before ASCII decoding and urllib parsing. At most one pair per
    # allowed name can be useful; duplicates are rejected by the parsed check.
    if len(raw) > 4096 or (raw and raw.count(b'&') + 1 > len(allowed)):
        return False
    if not raw:
        return True
    from urllib.parse import parse_qs
    try:
        value = parse_qs(raw.decode('ascii'), keep_blank_values=True, strict_parsing=True)
        if any(k not in allowed or len(v) != 1 or not v[0] for k, v in value.items()):
            return False
        for key, values in value.items():
            if key == 'status' and (len(values[0]) > 40 or not _OFFER_ID.fullmatch(values[0])):
                return False
            if key == 'include_events' and values[0] not in ('true', 'false'):
                return False
            if key == 'as_of':
                import datetime as dt
                if dt.date.fromisoformat(values[0]).isoformat() != values[0]:
                    return False
        return True
    except (UnicodeError, ValueError):
        return False


def _offer_headers_valid(headers) -> bool:
    if type(headers) is not list or len(headers) > 64:
        return False
    singleton = {b'host', b'authorization', b'origin', b'content-type', b'content-length',
                 b'idempotency-key', b'x-quote-context', b'x-quote-signature'}
    seen = set()
    total = 0
    for name, value in headers:
        if type(name) is not bytes or type(value) is not bytes:
            return False
        total += len(name) + len(value)
        if len(value) > 4096 or total > 16384:
            return False
        lowered = name.lower()
        if lowered in singleton:
            if lowered in seen:
                return False
            seen.add(lowered)
    return True

def _offer_body_shape(path: str, value) -> bool:
    if type(value) is not dict:
        return False
    forbidden = {'actor', 'role', 'principal_id', 'workspace_id', 'idempotency_key'}
    # Match the gateway's 32-level JSON ceiling without Python recursion.
    # The body byte cap and this node cap bound traversal independently.
    pending = [(value, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if depth > 32 or visited > 100_000:
            return False
        if type(node) is dict:
            if forbidden.intersection(node):
                return False
            pending.extend((child, depth + 1) for child in node.values())
        elif type(node) is list:
            pending.extend((child, depth + 1) for child in node)
    if path == '/offers/extractions':
        return (set(value) in ({'filename', 'content_base64', 'mode', 'synthetic'},
                               {'filename', 'content_base64', 'mode', 'synthetic', 'reporting_currency'})
                and type(value.get('filename')) is str and type(value.get('content_base64')) is str
                and value.get('mode') == 'rules' and value.get('synthetic') is True)
    if path.endswith('/save'):
        return set(value) == {'expected_version'} and type(value['expected_version']) is int
    if path.endswith('/corrections'):
        return (set(value) in ({'field', 'value', 'expected_version'},
                               {'field', 'value', 'expected_version', 'evidence'},
                               {'field', 'value', 'expected_version', 'reason'},
                               {'field', 'value', 'expected_version', 'evidence', 'reason'})
                and type(value.get('field')) is str and type(value.get('expected_version')) is int)
    return False


async def serve_signed_quote(scope, receive, send, *, backend, verifier, ready):
    async def error(status, code):
        await JSONResponse({'detail': {'code': code}}, status_code=status)(scope, receive, send)

    if not ready or backend is None or type(verifier) is not QuoteHopVerifier:
        await error(503, 'quote_unavailable')
        return
    path, method = scope['path'], scope['method']
    route = _offer_route(method, path)
    if route is None or scope.get('raw_path') != path.encode('utf-8'):
        await error(403, 'service_route_unavailable')
        return
    queries, body_limit, idempotent, response_limit = route
    headers = scope.get('headers', [])
    if not _offer_headers_valid(headers) or not _offer_query(scope.get('query_string', b''), queries):
        await error(422, 'invalid_request')
        return
    content_types = [v for k, v in headers if k.lower() == b'content-type']
    lengths = [v for k, v in headers if k.lower() == b'content-length']
    if (len(content_types) != (1 if body_limit else 0)
            or (body_limit and content_types[0] != b'application/json') or len(lengths) > 1):
        await error(422, 'invalid_request')
        return
    if lengths:
        if not lengths[0].isdigit() or int(lengths[0]) > body_limit:
            await error(413, 'request_too_large')
            return
    body = bytearray()
    while True:
        event = await receive()
        if event['type'] != 'http.request':
            await error(422, 'invalid_request')
            return
        body.extend(event.get('body', b''))
        if len(body) > body_limit:
            await error(413, 'request_too_large')
            return
        if not event.get('more_body', False):
            break
    if lengths and int(lengths[0]) != len(body):
        await error(422, 'invalid_request')
        return
    try:
        context = verifier.verify(scope, bytes(body), idempotent=idempotent)
    except (HopRejected, UnicodeError, KeyError, TypeError):
        await error(403, 'quote_context_invalid')
        return
    if body_limit:
        try:
            parsed = strict_json(bytes(body))
        except HopRejected:
            await error(422, 'invalid_request')
            return
        if not _offer_body_shape(path, parsed):
            await error(422, 'invalid_request')
            return
    if body_limit and context.role != 'editor':
        await error(403, 'forbidden')
        return
    scope = {**scope, 'offer_context': context}
    delivered = False
    async def replay():
        nonlocal delivered
        if delivered:
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        delivered = True
        return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
    status = 500
    payload = bytearray()
    async def capture(message):
        nonlocal status
        if message['type'] == 'http.response.start':
            status = message['status']
        elif message['type'] == 'http.response.body':
            payload.extend(message.get('body', b''))
            if len(payload) > response_limit:
                raise HopRejected('quote_response_too_large')
    try:
        await backend(scope, replay, capture)
        result = strict_json(bytes(payload))
        if type(result) is not dict:
            raise HopRejected('quote_response_invalid')
    except Exception:
        await error(502, 'quote_response_invalid')
        return
    safe_status = status if status in (200, 201, 400, 404, 409, 422) else 502
    if safe_status == 502:
        await error(502, 'quote_response_invalid')
        return
    await JSONResponse({'workspace_id': context.workspace_id, 'data': result},
                       status_code=safe_status, headers={'Cache-Control': 'no-store'})(scope, replay, send)

