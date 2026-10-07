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
