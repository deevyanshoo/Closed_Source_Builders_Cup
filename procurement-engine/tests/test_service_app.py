"""ASGI service boundary tests; synthetic SDK identity is separate from live IAM."""
import os
from pathlib import Path
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from test_service_auth import signed, AUD, CALLER
from procure import service_app
from procure.offers import quote_transport

@pytest.fixture
def backend():
    calls = []
    async def materials(request):
        calls.append(request.url.path)
        return JSONResponse({'backend': True})
    async def refresh(request):
        calls.append('refresh')
        return JSONResponse({'mutated': True})
    app = Starlette(routes=[Route('/materials', materials), Route('/recommend', materials, methods=['POST']),
                            Route('/ingest/refresh', refresh, methods=['POST'])])
    return app, calls


def staging(monkeypatch, backend, signed):
    _, verifier, _ = signed
    monkeypatch.setenv('PROCURE_DB_PATH', str(Path(os.environ['PROCURE_DATA_DIR']) / 'app.duckdb'))
    monkeypatch.setattr(service_app, 'prepare_cache', lambda config: {'cache_policy': 'bootstrap'})
    return service_app.create_app(mode='private_machine', audience=AUD, callers=(CALLER,),
                                  backend=backend, verifier=verifier)


def test_private_legacy_data_and_mutating_paths_require_identity(monkeypatch, backend, signed):
    app, calls = backend
    with TestClient(staging(monkeypatch, app, signed)) as client:
        for method, path in [('GET','/materials'),('POST','/recommend'),('POST','/ingest/refresh')]:
            response = client.request(method, path)
            assert response.status_code == 401
            assert response.json()['detail']['code'] == 'unauthorized'
        assert client.get('/-/health').json() == {'status': 'ok', 'provider': 'not_verified'}
    assert calls == []


def test_verified_caller_can_read_but_cannot_mutate_cache(monkeypatch, backend, signed):
    app, calls = backend; token, _, _ = signed
    with TestClient(staging(monkeypatch, app, signed)) as client:
        headers = {'Authorization': 'Bearer ' + token()}
        assert client.get('/materials', headers=headers).json() == {'backend': True}
        assert client.post('/recommend', headers=headers).status_code == 200
        blocked = client.post('/ingest/refresh', headers=headers)
        assert blocked.status_code == 403 and blocked.json()['detail']['code'] == 'service_route_unavailable'
    assert calls == ['/materials', '/recommend']

@pytest.mark.parametrize('change', [{'aud':'https://wrong.synthetic.run.app'}, {'sub':'unknown'}, {'email_verified':False}])
def test_wrong_signed_identity_never_reaches_backend(monkeypatch, backend, signed, change):
    app, calls = backend; token, _, _ = signed
    with TestClient(staging(monkeypatch, app, signed)) as client:
        assert client.get('/materials', headers={'Authorization':'Bearer '+token(**change)}).status_code == 401
    assert not calls


def test_client_identity_and_serverless_header_cannot_authorize(monkeypatch, backend, signed):
    app, calls = backend; token, _, _ = signed
    with TestClient(staging(monkeypatch, app, signed)) as client:
        assert client.get('/materials', headers={'X-Serverless-Authorization':'Bearer '+token()}).status_code == 401
        assert client.get('/materials', headers={'Authorization':'Bearer '+token(), 'X-Role':'editor'}).status_code == 403
    assert not calls


def test_local_mode_rejects_remote_clients(monkeypatch, backend):
    app, calls = backend
    monkeypatch.setattr(service_app, 'prepare_cache', lambda config: {})
    local = service_app.create_app(mode='local', backend=app)
    with TestClient(local, client=('192.0.2.1', 10)) as client:
        assert client.get('/materials').status_code == 403
    with TestClient(local, client=('127.0.0.1', 10)) as client:
        assert client.get('/materials').status_code == 200


def test_service_owns_lifespan_and_does_not_enter_legacy_startup(monkeypatch):
    from contextlib import asynccontextmanager
    seen = []
    @asynccontextmanager
    async def legacy(app):
        seen.append('legacy_ingest_or_evaluate')
        yield
    app = Starlette(lifespan=legacy)
    monkeypatch.setattr(service_app, 'prepare_cache', lambda config: seen.append('cache') or {})
    with TestClient(service_app.create_app(mode='local', backend=app), client=('127.0.0.1', 10)) as client:
        assert client.get('/-/health').status_code == 200
    assert seen == ['cache']


def test_private_configuration_fails_closed():
    with pytest.raises(ValueError):
        service_app.create_app(mode='private_machine', audience='', callers=())
    with pytest.raises(ValueError):
        service_app.create_app(mode='mistyped')


def test_real_pinned_cache_startup_and_restart_restore(monkeypatch, backend, signed, tmp_path):
    from test_service_cache import snapshot
    source, digest = snapshot(tmp_path, ('copper',))
    data = tmp_path / 'mutable'
    monkeypatch.setenv('PROCURE_DB_PATH', str(data / 'app.duckdb'))
    token, verifier, _ = signed
    app, calls = backend
    def new_app():
        return service_app.create_app(mode='private_machine', audience=AUD, callers=(CALLER,),
                                      backend=app, verifier=verifier, data_dir=data,
                                      bootstrap_dir=source, bootstrap_sha256=digest, materials=('copper',))
    with TestClient(new_app()) as client:
        assert client.get('/materials', headers={'Authorization': 'Bearer '+token()}).status_code == 200
    original = (source / 'app.duckdb').read_bytes()
    (data / 'app.duckdb').write_bytes(b'ephemeral corruption')
    with TestClient(new_app()) as client:
        assert client.get('/-/health').status_code == 200
    assert (data / 'app.duckdb').read_bytes() == original
    assert (source / 'app.duckdb').read_bytes() == original
    assert calls == ['/materials']


def test_corrupt_cache_fails_startup_without_backend(monkeypatch, backend, signed, tmp_path):
    from test_service_cache import snapshot
    source, digest = snapshot(tmp_path, ('copper',))
    (source / 'app.duckdb').write_bytes(b'corrupt')
    data = tmp_path / 'mutable'
    monkeypatch.setenv('PROCURE_DB_PATH', str(data / 'app.duckdb'))
    _, verifier, _ = signed
    app, calls = backend
    wrapped = service_app.create_app(mode='private_machine', audience=AUD, callers=(CALLER,),
                                    backend=app, verifier=verifier, data_dir=data,
                                    bootstrap_dir=source, bootstrap_sha256=digest, materials=('copper',))
    with pytest.raises(RuntimeError, match='Validated service cache unavailable'):
        with TestClient(wrapped):
            pytest.fail('Corrupt bootstrap must not start')
    assert not calls and not (data / 'app.duckdb').exists()


def test_private_db_override_and_duplicate_authorization_fail(monkeypatch, backend, signed, tmp_path):
    monkeypatch.setenv('PROCURE_DB_PATH', str(tmp_path / 'external.duckdb'))
    with pytest.raises(ValueError, match='inside mutable'):
        service_app.create_app(mode='private_machine', audience=AUD, callers=(CALLER,), data_dir=tmp_path / 'mutable')
    app, calls = backend
    token, _, _ = signed
    with TestClient(staging(monkeypatch, app, signed)) as client:
        assert client.get('/materials', headers=[('Authorization', 'Bearer '+token()),
                                                ('Authorization', 'Bearer '+token())]).status_code == 401
    assert not calls


def test_generated_signed_quote_hop_wraps_owned_response_and_rejects_replay(monkeypatch):
    import base64
    import hashlib
    import hmac
    import json
    from procure.offers.context import QuoteHopVerifier
    from starlette.requests import Request

    seen = []
    async def quote(request: Request):
        context = request.scope['offer_context']
        seen.append((context.principal_id, context.workspace_id, context.role))
        return JSONResponse({'offers': []})
    backend = Starlette(routes=[Route('/offers', quote)])
    monkeypatch.setattr(service_app, 'prepare_cache', lambda config: {})
    key = b'k' * 32
    verifier = QuoteHopVerifier(key)
    local = service_app.create_app(mode='local', backend=backend, quote_verifier=verifier)
    assertion = {'v': 1, 'method': 'GET', 'raw_path': '/offers', 'raw_query': '',
                 'body_sha256': hashlib.sha256(b'').hexdigest(), 'issued_at_unix': int(verifier._clock()),
                 'nonce': 'a' * 32, 'principal_id': 'generated-user', 'workspace_id': 'generated-workspace',
                 'role': 'viewer', 'idempotency_key': None}
    wire = json.dumps(assertion, separators=(',', ':')).encode()
    headers = {'X-Quote-Context': base64.urlsafe_b64encode(wire).rstrip(b'=').decode(),
               'X-Quote-Signature': base64.urlsafe_b64encode(hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b'=').decode()}
    with TestClient(local, client=('127.0.0.1', 10)) as client:
        unsigned = client.get('/offers')
        assert unsigned.status_code == 403
        good = client.get('/offers', headers=headers)
        assert good.status_code == 200
        assert good.json() == {'workspace_id': 'generated-workspace', 'data': {'offers': []}}
        assert good.headers['cache-control'] == 'no-store'
        replay = client.get('/offers', headers=headers)
        assert replay.status_code == 403
        assert client.get('/offers', headers={**headers, 'Authorization': 'Bearer synthetic'}).status_code == 403
    assert seen == [('generated-user', 'generated-workspace', 'viewer')]


def test_signed_local_service_to_real_offer_routes_synthetic_saved_path(monkeypatch):
    import base64
    import hashlib
    import hmac
    import json
    from procure.api import app as legacy_api
    from procure.offers.context import QuoteHopVerifier

    key = b'q' * 32
    verifier = QuoteHopVerifier(key)
    monkeypatch.setattr(service_app, 'prepare_cache', lambda config: {})
    svc = service_app.create_app(mode='local', backend=legacy_api, quote_verifier=verifier)
    nonce = iter(range(1, 20))

    def request(client, method, path, *, body=None, operation_key=None, role='editor', workspace='signed-workspace'):
        raw = json.dumps(body, separators=(',', ':')).encode() if body is not None else b''
        assertion = {'v': 1, 'method': method, 'raw_path': path, 'raw_query': '',
                     'body_sha256': hashlib.sha256(raw).hexdigest(), 'issued_at_unix': int(verifier._clock()),
                     'nonce': f'{next(nonce):032x}', 'principal_id': 'signed-reviewer',
                     'workspace_id': workspace, 'role': role, 'idempotency_key': operation_key}
        wire = json.dumps(assertion, separators=(',', ':')).encode()
        headers = {'X-Quote-Context': base64.urlsafe_b64encode(wire).rstrip(b'=').decode(),
                   'X-Quote-Signature': base64.urlsafe_b64encode(hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b'=').decode()}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if operation_key is not None:
            headers['Idempotency-Key'] = operation_key
        return client.request(method, path, content=raw, headers=headers)

    fixture = Path(__file__).parent / 'fixtures' / 'offers'
    manifest = __import__('json').loads((fixture / 'manifest.json').read_text())
    quote = next(q for q in manifest['quotes'] if q['id'] == 'Q01')
    path = fixture / quote['file']
    with TestClient(svc, client=('127.0.0.1', 10)) as client:
        uploaded = request(client, 'POST', '/offers/extractions', body={
            'filename': path.name, 'content_base64': base64.b64encode(path.read_bytes()).decode(),
            'mode': 'rules', 'synthetic': True})
        assert uploaded.status_code == 201
        assert uploaded.json()['workspace_id'] == 'signed-workspace'
        offer = uploaded.json()['data']['offer']
        oid = offer['offer_id']
        viewer = request(client, 'GET', f'/offers/{oid}', role='viewer')
        assert viewer.status_code == 200 and viewer.json()['data']['offer_id'] == oid
        assert request(client, 'GET', f'/offers/{oid}', workspace='other-workspace').status_code == 404
        denied = request(client, 'POST', f'/offers/{oid}/save', body={'expected_version': 1},
                         operation_key='viewer-denied-0001', role='viewer')
        assert denied.status_code == 403
        corrected = request(client, 'POST', f'/offers/{oid}/corrections', body={
            'field': 'settlement_currency', 'value': 'USD', 'expected_version': offer['version']},
            operation_key='signed-correct-0001')
        assert corrected.status_code == 200
        saved = request(client, 'POST', f'/offers/{oid}/save', body={
            'expected_version': corrected.json()['data']['version']}, operation_key='signed-save-0001')
        assert saved.status_code == 200 and saved.json()['data']['status'] == 'saved'
        sourcing = request(client, 'GET', '/offers/sourcing-candidates')
        assert sourcing.status_code == 200 and sourcing.json()['workspace_id'] == 'signed-workspace'
        assert oid in json.dumps(sourcing.json()['data'])


def test_quote_query_ceiling_precedes_parser_and_parameter_count(monkeypatch):
    import urllib.parse
    def forbidden(*args, **kwargs):
        raise AssertionError('oversize query reached parser')
    monkeypatch.setattr(urllib.parse, 'parse_qs', forbidden)
    assert quote_transport._offer_query(b'as_of=' + b'1' * 5000, {'as_of'}) is False
    assert quote_transport._offer_query(b'as_of=1&as_of=2&as_of=3', {'as_of'}) is False


def test_quote_headers_rejected_before_verifier_or_backend():
    import base64
    import hashlib
    import hmac
    import json
    from procure.offers.context import QuoteHopVerifier
    seen = []
    async def quote(request):
        seen.append(request.url.path)
        return JSONResponse({'offers': []})
    key = b'h' * 32
    verifier = QuoteHopVerifier(key)
    svc = service_app.create_app(mode='local', backend=Starlette(routes=[Route('/offers', quote)]),
                                 quote_verifier=verifier)
    svc.ready = True  # No lifespan entry.
    assertion = {'v': 1, 'method': 'GET', 'raw_path': '/offers', 'raw_query': '',
                 'body_sha256': hashlib.sha256(b'').hexdigest(), 'issued_at_unix': int(verifier._clock()),
                 'nonce': 'b' * 32, 'principal_id': 'p', 'workspace_id': 'w',
                 'role': 'viewer', 'idempotency_key': None}
    wire = json.dumps(assertion, separators=(',', ':')).encode()
    signed = [('X-Quote-Context', base64.urlsafe_b64encode(wire).rstrip(b'=').decode()),
              ('X-Quote-Signature', base64.urlsafe_b64encode(hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b'=').decode())]
    client = TestClient(svc, client=('127.0.0.1', 1234))
    for extras in ([('X-Junk', 'a' * 5000)],
                   [(f'X-Many-{i}', 'v') for i in range(70)],
                   [('X-Junk-1', 'a' * 3900), ('X-Junk-2', 'a' * 3900),
                    ('X-Junk-3', 'a' * 3900), ('X-Junk-4', 'a' * 3900), ('X-Junk-5', 'a' * 3900)],
                   [signed[0]]):
        response = client.get('/offers', headers=signed + extras)
        assert response.status_code == 422
    assert seen == []
    accepted = client.get('/offers', headers=signed + [('X-Junk', 'a' * 4096)])
    assert accepted.status_code == 200 and seen == ['/offers']


def test_quote_body_shape_rejects_overdepth_iteratively():
    import json
    near = {'field': 'x', 'expected_version': 1, 'value': 0}
    for _ in range(31):
        near['value'] = [near['value']]
    assert quote_transport._offer_body_shape('/offers/o/corrections', near) is True
    too_deep = {'field': 'x', 'expected_version': 1, 'value': 0}
    for _ in range(32):
        too_deep['value'] = [too_deep['value']]
    assert quote_transport._offer_body_shape('/offers/o/corrections', too_deep) is False
    raw = '{"field":"x","expected_version":1,"value":' + '[' * 650 + '0' + ']' * 650 + '}'
    assert quote_transport._offer_body_shape('/offers/o/corrections', json.loads(raw)) is False
    too_many = {'field': 'x', 'expected_version': 1, 'value': [None] * 100_001}
    assert quote_transport._offer_body_shape('/offers/o/corrections', too_many) is False


def test_signed_overdepth_correction_returns_controlled_422_without_backend():
    import base64
    import hashlib
    import hmac
    import json
    from procure.offers.context import QuoteHopVerifier
    seen = []
    async def correction(request):
        seen.append('backend')
        return JSONResponse({'offer_id': 'unexpected'})
    key = b'd' * 32
    verifier = QuoteHopVerifier(key)
    svc = service_app.create_app(mode='local', backend=Starlette(routes=[Route('/offers/o/corrections', correction,
                                                                             methods=['POST'])]),
                                 quote_verifier=verifier)
    svc.ready = True
    path = '/offers/o/corrections'
    body = ('{"field":"x","expected_version":1,"value":' + '[' * 650 + '0' + ']' * 650 + '}').encode()
    assertion = {'v': 1, 'method': 'POST', 'raw_path': path, 'raw_query': '',
                 'body_sha256': hashlib.sha256(body).hexdigest(), 'issued_at_unix': int(verifier._clock()),
                 'nonce': 'd' * 32, 'principal_id': 'p', 'workspace_id': 'w',
                 'role': 'editor', 'idempotency_key': 'depth-key-0001'}
    wire = json.dumps(assertion, separators=(',', ':')).encode()
    headers = {'X-Quote-Context': base64.urlsafe_b64encode(wire).rstrip(b'=').decode(),
               'X-Quote-Signature': base64.urlsafe_b64encode(hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b'=').decode(),
               'Idempotency-Key': 'depth-key-0001', 'Content-Type': 'application/json'}
    client = TestClient(svc, client=('127.0.0.1', 1234))
    response = client.post(path, content=body, headers=headers)
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'invalid_request'
    assert seen == []
