"""Restricted service entrypoint; legacy API startup remains unchanged.

This wrapper owns startup: validate/restore a cache, then dispatch only numerical
reads and recommendation calculations. It never invokes the legacy API lifespan.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import ipaddress
import os
from pathlib import Path
import re

import anyio
from starlette.responses import JSONResponse
import yaml

from .service_auth import Caller, EMAIL, TokenRejected, authorize, load_callers, validate_audience
from .offers.context import HopRejected, QuoteHopVerifier, strict_json

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


@dataclass(frozen=True)
class Config:
    mode: str
    audience: str
    callers: tuple[Caller, ...]
    data_dir: Path
    db_path: Path
    bootstrap_dir: Path | None
    bootstrap_sha256: str
    materials: tuple[str, ...]


def prepare_cache(config: Config) -> dict:
    from .service_cache import CacheUnavailable, restore_snapshot, validate_database
    if config.mode == 'private_machine':
        if config.bootstrap_dir is None or not config.bootstrap_sha256:
            raise CacheUnavailable('Pinned bootstrap snapshot is required')
        return restore_snapshot(config.bootstrap_dir, config.data_dir,
                                config.bootstrap_sha256, config.materials)
    validate_database(config.db_path, config.materials)
    return {'db_path': str(config.db_path), 'cache_policy': 'existing_validated'}


class ServiceApp:
    def __init__(self, config: Config, backend=None, verifier=None, quote_verifier=None):
        self.config = config
        self.backend = backend
        self.verifier = verifier
        self.quote_verifier = quote_verifier
        self.ready = False
        self.owns_core = backend is None

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            while True:
                event = await receive()
                if event['type'] == 'lifespan.startup':
                    try:
                        await anyio.to_thread.run_sync(prepare_cache, self.config)
                        if self.owns_core:
                            # Dedicated service process only; no legacy startup or ingestion.
                            os.environ['PROCURE_DATA_DIR'] = str(self.config.data_dir)
                            os.environ['PROCURE_DB_PATH'] = str(self.config.db_path)
                            from . import db, settings
                            db.close()
                            settings.reset_caches()
                            from .api import app
                            self.backend = app
                        self.ready = True
                    except Exception:
                        await send({'type': 'lifespan.startup.failed',
                                    'message': 'Validated service cache unavailable'})
                        raise RuntimeError('Validated service cache unavailable') from None
                    await send({'type': 'lifespan.startup.complete'})
                elif event['type'] == 'lifespan.shutdown':
                    self.ready = False
                    if self.owns_core:
                        from . import db
                        db.close()
                    await send({'type': 'lifespan.shutdown.complete'})
                    return
            return
        if scope['type'] != 'http':
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            return

        async def error(status, code):
            await JSONResponse({'detail': {'code': code}}, status_code=status)(scope, receive, send)

        method, path = scope['method'], scope['path']
        if method == 'GET' and path == '/-/health':
            await JSONResponse({'status': 'ok', 'provider': 'not_verified'})(scope, receive, send)
            return
        headers = scope.get('headers', [])
        if path == '/offers' or path.startswith('/offers/'):
            if self.config.mode != 'local':
                await error(403, 'service_route_unavailable')
                return
            try:
                local = ipaddress.ip_address(scope.get('client', ('', 0))[0]).is_loopback
            except (ValueError, TypeError):
                local = False
            if not local:
                await error(403, 'local_only')
                return
            await self._quote(scope, receive, send)
            return
        if self.config.mode == 'private_machine':
            auth = [value for name, value in headers if name.lower() == b'authorization']
            if len(auth) != 1 or not auth[0].startswith(b'Bearer '):
                await error(401, 'unauthorized')
                return
            try:
                token = auth[0][7:].decode('ascii')
                await anyio.to_thread.run_sync(lambda: authorize(
                    token, self.config.audience, self.config.callers, verifier=self.verifier))
            except (TokenRejected, UnicodeError):
                await error(401, 'unauthorized')
                return
            if any(name.lower() in {b'origin', b'x-role', b'x-actor', b'x-principal',
                                   b'x-principal-id', b'x-workspace', b'x-workspace-id'}
                   for name, _ in headers):
                await error(403, 'client_identity_forbidden')
                return
        else:
            try:
                local = ipaddress.ip_address(scope.get('client', ('', 0))[0]).is_loopback
            except (ValueError, TypeError):
                local = False
            if not local:
                await error(403, 'local_only')
                return
        allowed = ((method == 'GET' and path in {'/health', '/materials', '/metrics', '/overview'})
                   or (method == 'GET' and any(path.startswith(prefix) and
                       '/' not in path[len(prefix):] and bool(path[len(prefix):])
                       for prefix in ('/prices/', '/forecast/', '/news/', '/backtest/')))
                   or (method == 'POST' and path in {'/recommend',
                       '/production/receipt-recovery/comparisons'}))
        if not allowed:
            await error(403, 'service_route_unavailable')
            return
        if not self.ready or self.backend is None:
            await error(503, 'cache_unavailable')
            return
        await self.backend(scope, receive, send)

    async def _quote(self, scope, receive, send):
        async def error(status, code):
            await JSONResponse({'detail': {'code': code}}, status_code=status)(scope, receive, send)

        if not self.ready or self.backend is None or type(self.quote_verifier) is not QuoteHopVerifier:
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
            context = self.quote_verifier.verify(scope, bytes(body), idempotent=idempotent)
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
            await self.backend(scope, replay, capture)
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


def create_app(*, mode=None, audience=None, callers=None, backend=None, verifier=None, quote_verifier=None,
               data_dir=None, bootstrap_dir=None, bootstrap_sha256=None, materials=None):
    mode = mode if mode is not None else os.getenv('PROCURE_SERVICE_MODE', 'local')
    if mode not in {'local', 'private_machine'}:
        raise ValueError('Invalid service mode')
    audience = audience if audience is not None else os.getenv('PROCURE_SERVICE_AUDIENCE', '')
    if mode == 'private_machine':
        audience = validate_audience(audience)
        if callers is None:
            path = os.getenv('PROCURE_SERVICE_CALLERS_FILE', '')
            if not path:
                raise ValueError('Restricted service caller configuration is required')
            callers = load_callers(Path(path))
        if (not isinstance(callers, tuple) or not callers
                or any(not isinstance(c, Caller) or not c.subject or not EMAIL.fullmatch(c.email)
                       for c in callers)):
            raise ValueError('Restricted service caller configuration is required')
    else:
        callers = ()
    project = Path(__file__).resolve().parents[2]
    explicit_data = data_dir if data_dir is not None else os.getenv('PROCURE_DATA_DIR')
    if mode == 'private_machine' and not explicit_data:
        raise ValueError('Explicit mutable cache directory is required')
    data = Path(explicit_data or project / 'data').absolute()
    db = Path(os.getenv('PROCURE_DB_PATH', str(data / 'app.duckdb'))).absolute()
    if mode == 'private_machine' and db != data / 'app.duckdb':
        raise ValueError('Private database must be inside mutable cache directory')
    bootstrap = bootstrap_dir if bootstrap_dir is not None else os.getenv('PROCURE_BOOTSTRAP_DIR')
    digest = bootstrap_sha256 if bootstrap_sha256 is not None else os.getenv('PROCURE_BOOTSTRAP_SHA256', '')
    if materials is None:
        config_dir = Path(os.getenv('PROCURE_CONFIG_DIR', project / 'config'))
        config = yaml.safe_load((config_dir / 'materials.yaml').read_text())
        materials = tuple(config['materials'])
    return ServiceApp(Config(mode, audience, callers, data, db,
                            Path(bootstrap).absolute() if bootstrap else None,
                            digest, materials), backend, verifier, quote_verifier)


app = create_app()


def main():
    import uvicorn
    value = os.getenv('PORT', '8018' if app.config.mode == 'local' else '8080')
    if not value.isdecimal() or not 1 <= int(value) <= 65535:
        raise ValueError('PORT must be a valid TCP port')
    uvicorn.run(app, host='127.0.0.1' if app.config.mode == 'local' else '0.0.0.0',
                port=int(value), workers=1, proxy_headers=False)


if __name__ == '__main__':
    main()
