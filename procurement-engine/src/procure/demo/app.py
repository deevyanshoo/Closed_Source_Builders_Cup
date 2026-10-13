"""Public hosted demo boundary: synthetic inputs, scoped state, bounded provider work."""
from __future__ import annotations

import base64
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from .sessions import SessionError, SessionStore

COOKIE = 'demo_session'
MAX_BODY = 750_000
MAX_UPLOAD = 500_000
MAX_REQUESTS = 240
MAX_OFFERS = 24
MAX_IDEMPOTENCY = 120
MAX_SESSION_CALLS = 6
MAX_PROCESS_CALLS = 60
MAX_CACHE = 64


def failure(code, message, status=422):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


class BodyLimit:
    """Limit streamed bodies as well as declared content lengths before JSON parsing."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/demo'):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            limit = MAX_BODY if scope['path'] == '/api/demo/extract' else 16_384
            if len(body) > limit:
                return await failure('body_too_large', 'Request is too large.', 413)(scope, receive, send)
            if not message.get('more_body', False):
                break
        sent = False
        async def bounded_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, bounded_receive, send)


def create_app(*, web_dir: Path | None = None, secure_cookies=True, clock=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = SessionStore(clock=clock)
    app.state.sessions = store
    cache: OrderedDict = OrderedDict()
    cache_lock = threading.RLock()
    workers = threading.BoundedSemaphore(2)
    process_calls = 0
    pending_cache = set()
    app.add_middleware(BodyLimit)

    @app.exception_handler(SessionError)
    async def session_error(request, exc):
        return failure(exc.code, exc.message, exc.status_code)

    @app.middleware('http')
    async def public_errors(request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            # Only domain errors contain reviewed public messages. Never reflect provider exceptions.
            from .errors import DemoError
            if isinstance(exc, DemoError):
                response = failure(exc.code, exc.message, exc.status_code)
            else:
                response = failure('internal_error', 'Demo could not complete this request.', 500)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def session(request):
        value = store.get(request.cookies.get(COOKIE))
        with value.lock:
            value.requests += 1
            if value.requests > MAX_REQUESTS:
                raise SessionError('request_limit', 'Session request limit reached. Start a new session later.', 429)
        return value

    def origin(value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return None
            return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
        except ValueError:
            return None

    def mutate(request):
        value = session(request)
        source = request.headers.get('origin') or request.headers.get('referer')
        expected = origin(str(request.url))
        if not secure_cookies and (expected is None or expected[1] not in ('localhost', '127.0.0.1', '::1')):
            raise SessionError('insecure_host', 'Local test mode requires localhost.', 403)
        if source is None or origin(source) != expected:
            raise SessionError('invalid_origin', 'A same-origin browser request is required.', 403)
        token = request.headers.get('x-csrf-token', '')
        if not secrets.compare_digest(token, value.csrf_token):
            raise SessionError('invalid_csrf', 'Refresh the demo session and try again.', 403)
        return value

    async def body(request):
        def finite_float(raw):
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError()
            return value

        try:
            if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                raise ValueError()
            result = json.loads(await request.body(), parse_float=finite_float, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise SessionError('invalid_json', 'Provide a valid JSON object with finite values.', 422)

    def shape(data, required, optional=()):
        if not set(required) <= data.keys() or not data.keys() <= set(required) | set(optional):
            raise SessionError('invalid_request', 'Request fields do not match this operation.', 422)

    def active(value):
        if not value.active or value.expires_at <= store.now():
            raise SessionError('session_expired', 'Session changed or expired. Refresh the demo.', 401)

    def start(value, data, action):
        active(value)
        key = data.get('idempotency_key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
            raise SessionError('invalid_idempotency', 'A short alphanumeric retry key is required.', 422)
        digest = hashlib.sha256(json.dumps([action, data], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        previous = value.idempotency.get(key)
        if previous:
            if previous['digest'] != digest:
                raise SessionError('idempotency_conflict', 'Retry key was already used for another request.', 409)
            if previous['result'] is None:
                if previous['pending']:
                    raise SessionError('request_in_progress', 'This request is still processing.', 409)
                previous['pending'] = True
                return key, None
            result = deepcopy(previous['result'])
            result['replayed'] = True
            return key, result
        if len(value.idempotency) >= MAX_IDEMPOTENCY:
            raise SessionError('operation_limit', 'Session operation limit reached.', 429)
        value.idempotency[key] = {'digest': digest, 'result': None, 'pending': True}
        return key, None

    def failed(value, key):
        # Keep failed retry keys bound to their original action and payload.
        if key in value.idempotency:
            value.idempotency[key]['pending'] = False

    def complete(value, key, result):
        active(value)
        value.idempotency[key]['result'] = deepcopy(result)
        return result

    def owned(value, offer_id):
        result = value.offers.get(offer_id)
        if result is None:
            raise SessionError('offer_not_found', 'Offer not found in this session.', 404)
        return result

    def version(offer, expected):
        if type(expected) is not int or expected < 1:
            raise SessionError('invalid_version', 'An integer offer version is required.', 422)
        if offer['version'] != expected:
            raise SessionError('stale_version', 'Offer changed. Refresh it before continuing.', 409)

    def provider():
        # Availability means configured, not successful-call evidence. Extraction reports actual outcome.
        from procure.settings import assumptions
        return {'model': assumptions()['news']['gemini_model'],
                'available': os.getenv('OFFLINE', '').lower() not in ('1', 'true')}

    def view(value):
        with value.lock:
            return {'session_id': value.session_id, 'csrf_token': value.csrf_token,
                    'expires_at': value.expires_at.isoformat(), 'synthetic': True,
                    'persistence': 'ephemeral', 'offers': deepcopy(list(value.offers.values())), 'provider': provider()}

    def session_response(value):
        response = JSONResponse(view(value))
        response.set_cookie(COOKIE, value.session_id, max_age=store.ttl_seconds,
                            httponly=True, secure=secure_cookies, samesite='lax', path='/api/demo')
        return response

    @app.get('/-/health')
    async def health():
        return {'ready': True, 'revision': os.getenv('K_REVISION', 'local'), 'persistence': 'ephemeral'}

    @app.get('/api/demo/session')
    async def get_session(request: Request):
        try:
            value = session(request)
        except SessionError as exc:
            if exc.status_code != 401:
                raise
            value = store.create()
        return session_response(value)

    @app.get('/api/demo/fixtures')
    async def fixtures():
        from .quote_service import list_fixtures
        return {'fixtures': list_fixtures(), 'synthetic': True}

    @app.get('/api/demo/fixtures/{fixture_id}')
    async def download(fixture_id: str):
        from .quote_service import fixture_bytes
        filename, data = fixture_bytes(fixture_id)
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', filename)
        return Response(data, media_type='application/pdf' if filename.endswith('.pdf') else 'text/csv',
                        headers={'Content-Disposition': f'attachment; filename="{safe_name}"'})

    @app.get('/api/demo/offers')
    async def offers(request: Request):
        value = session(request)
        with value.lock:
            return {'offers': deepcopy(list(value.offers.values()))}

    @app.post('/api/demo/extract')
    async def extract(request: Request):
        nonlocal process_calls
        value = mutate(request)
        data = await body(request)
        from .quote_service import fixture_bytes, match_fixture, extract_fixture, new_offer
        if 'fixture_id' in data:
            shape(data, ('fixture_id', 'mode', 'idempotency_key'))
            fixture_id = data['fixture_id']
            if not isinstance(fixture_id, str) or len(fixture_id) > 100:
                raise SessionError('invalid_fixture', 'Select a synthetic demo fixture.', 422)
            _, raw = fixture_bytes(fixture_id)
        else:
            shape(data, ('filename', 'document_base64', 'mode', 'idempotency_key'))
            filename, encoded = data['filename'], data['document_base64']
            if not isinstance(filename, str) or len(filename) > 150 or '/' in filename or '\\' in filename:
                raise SessionError('invalid_upload', 'Upload an unchanged synthetic demo fixture.', 422)
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise SessionError('invalid_upload', 'Upload an unchanged synthetic demo fixture.', 422)
            if len(raw) > MAX_UPLOAD:
                raise SessionError('upload_too_large', 'Synthetic document is too large.', 413)
            fixture_id = match_fixture(filename, raw)
        if data['mode'] not in ('rules', 'gemini'):
            raise SessionError('invalid_mode', 'Choose rules or gemini extraction.', 422)
        with value.lock:
            key, replay = start(value, data, 'extract')
            if replay:
                return replay
            if len(value.offers) >= MAX_OFFERS:
                failed(value, key)
                raise SessionError('offer_limit', 'Session offer limit reached.', 429)
        today = store.now().date()
        cache_key = (hashlib.sha256(raw).hexdigest(), data['mode'], today.isoformat())
        acquired = False
        marked = False
        try:
            with cache_lock:
                payload = deepcopy(cache.get(cache_key))
                cached = payload is not None
                if not cached:
                    if cache_key in pending_cache:
                        raise SessionError('extraction_busy', 'This document is being extracted. Try again shortly.', 409)
                    if not workers.acquire(blocking=False):
                        raise SessionError('provider_busy', 'Extraction is busy. Try again shortly.', 429)
                    acquired = True
                    if data['mode'] == 'gemini':
                        if not provider()['available'] or value.live_calls >= MAX_SESSION_CALLS or process_calls >= MAX_PROCESS_CALLS:
                            raise SessionError('provider_limit', 'Live extraction is unavailable or its demo limit was reached.', 429)
                        value.live_calls += 1
                        process_calls += 1
                    pending_cache.add(cache_key)
                    marked = True
            if payload is None:
                payload = await run_in_threadpool(extract_fixture, fixture_id, mode=data['mode'], as_of=today)
                if data['mode'] == 'gemini' and (payload.get('extractor') != 'gemini' or payload.get('extractor_detail', {}).get('call') != 'real'):
                    raise SessionError('provider_unavailable', 'Gemini did not return a verified live extraction. Try again or select rules.', 503)
                with cache_lock:
                    cache[cache_key] = deepcopy(payload)
                    while len(cache) > MAX_CACHE:
                        cache.popitem(last=False)
            with value.lock:
                active(value)
                if len(value.offers) >= MAX_OFFERS:
                    raise SessionError('offer_limit', 'Session offer limit reached.', 429)
                offer = new_offer(deepcopy(payload), offer_id=secrets.token_urlsafe(18), today=today)
                offer['served_from_cache'] = cached
                value.offers[offer['offer_id']] = offer
                result = {'offer': deepcopy(offer), 'provider': deepcopy(offer.get('extractor_detail', {})),
                          'replayed': False, 'served_from_cache': cached}
                return complete(value, key, result)
        except Exception:
            with value.lock:
                failed(value, key)
            raise
        finally:
            with cache_lock:
                if marked:
                    pending_cache.discard(cache_key)
                if acquired:
                    workers.release()

    async def change(request, offer_id, operation):
        value = mutate(request)
        data = await body(request)
        shape(data, ('expected_version', 'idempotency_key', 'field', 'value') if operation == 'correct' else ('expected_version', 'idempotency_key'))
        with value.lock:
            original = owned(value, offer_id)
            key, replay = start(value, data, f'{operation}:{offer_id}')
            if replay:
                return replay
            try:
                version(original, data['expected_version'])
                from .quote_service import correct_offer, save_offer
                if operation == 'correct':
                    if not isinstance(data['field'], str) or len(data['field']) > 100 or len(json.dumps(data['value'])) > 4096:
                        raise SessionError('invalid_correction', 'Provide a supported field and a short typed value.', 422)
                    result = correct_offer(deepcopy(original), field=data['field'], value=data['value'], today=store.now().date())
                else:
                    result = save_offer(deepcopy(original), today=store.now().date())
                value.offers[offer_id] = result
                value.last_plan = None
                return complete(value, key, {'offer': deepcopy(result), 'replayed': False})
            except Exception:
                failed(value, key)
                raise

    @app.post('/api/demo/offers/{offer_id}/correct')
    async def correct(request: Request, offer_id: str):
        return await change(request, offer_id, 'correct')

    @app.post('/api/demo/offers/{offer_id}/save')
    async def save(request: Request, offer_id: str):
        return await change(request, offer_id, 'save')

    @app.post('/api/demo/plan')
    async def plan(request: Request):
        value = mutate(request)
        data = await body(request)
        from .planner import build_plan
        shape(data, ('offer_ids', 'expected_versions', 'material', 'quantity_kg', 'current_stock_kg',
                     'need_by', 'decision_date', 'reporting_currency', 'budget', 'additional_cost_per_kg'),
              ('delay_days_by_offer',))
        ids, versions = data['offer_ids'], data['expected_versions']
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_OFFERS or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not isinstance(versions, dict) or set(versions) != set(ids):
            raise SessionError('invalid_offers', 'Provide unique offer IDs and their exact versions.', 422)
        with value.lock:
            active(value)
            selected = [owned(value, i) for i in ids]
            for offer in selected:
                version(offer, versions[offer['offer_id']])
            result = build_plan(deepcopy(selected), data, today=store.now().date())
            result['plan_id'] = secrets.token_urlsafe(18)
            value.last_plan = deepcopy(result)
            return result

    @app.get('/api/demo/export')
    async def export(request: Request):
        value = session(request)
        with value.lock:
            return JSONResponse({'synthetic': True, 'persistence': 'ephemeral',
                                 'exported_at': store.now().isoformat(),
                                 'offers': deepcopy(list(value.offers.values())), 'plan': deepcopy(value.last_plan)},
                                headers={'Content-Disposition': 'attachment; filename="synthetic-demo-report.json"'})

    @app.post('/api/demo/reset')
    async def reset(request: Request):
        value = mutate(request)
        store.invalidate(value)
        return session_response(store.create())

    if web_dir is not None:
        web_dir = Path(web_dir).resolve()
        if (web_dir / 'assets').is_dir():
            app.mount('/assets', StaticFiles(directory=web_dir / 'assets'), name='assets')
        @app.get('/demo')
        @app.get('/demo/{path:path}')
        async def spa(path: str = ''):
            index = web_dir / 'index.html'
            if not index.is_file():
                return failure('assets_unavailable', 'Demo assets are not available.', 503)
            return FileResponse(index)
        @app.get('/')
        async def demo_root():
            return RedirectResponse('/demo', status_code=307)
    return app


app = create_app(web_dir=Path(os.environ['DEMO_WEB_DIR']) if os.getenv('DEMO_WEB_DIR') else None)
