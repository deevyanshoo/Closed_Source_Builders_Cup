"""Offline monitoring contracts: no request content or persistence writes from probes."""
import json
import logging
import threading
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.diagnostics import DependencyReadiness, news_freshness, request_outcome
from procure.demo.session_persistence import FirestoreSessionPersistence, MemorySessionPersistence


def test_request_outcome_is_low_cardinality_and_redacted(caplog):
    logger = logging.getLogger('test.demo.diagnostics')
    with caplog.at_level(logging.INFO, logger=logger.name):
        request_outcome(logger, 'PATCH', '/api/demo/research/private-query?secret=audio', 503, 999999)
    event = json.loads(caplog.records[-1].message)
    assert event == {'event': 'http_request', 'route': 'research', 'method': 'OTHER',
                     'status_class': '5xx', 'outcome': 'error', 'duration_ms': 180000}
    assert 'private-query' not in caplog.text
    assert 'audio' not in caplog.text
    with caplog.at_level(logging.INFO, logger=logger.name):
        request_outcome(logger, 'GET', '/api/demo/session', 429, 1)
    assert json.loads(caplog.records[-1].message)['outcome'] == 'client_error'


def test_news_freshness_label_rejects_untrusted_status(caplog):
    logger = logging.getLogger('test.demo.news')
    with caplog.at_level(logging.INFO, logger=logger.name):
        news_freshness(logger, 'stale')
        news_freshness(logger, 'stale private title')
    events = [json.loads(record.message) for record in caplog.records]
    assert events == [{'event': 'news_freshness', 'state': 'stale'},
                      {'event': 'news_freshness', 'state': 'unknown'}]


def test_request_middleware_redacts_query_cookie_and_exception(caplog):
    app = create_app(clock=lambda: datetime(2026, 10, 15, tzinfo=timezone.utc))

    @app.get('/api/demo/test-error')
    def explode():
        raise RuntimeError('private provider query and session id')

    with caplog.at_level(logging.INFO, logger='procure.demo'):
        with TestClient(app, base_url='https://demo.test') as client:
            client.cookies.set('demo_session', 'private-cookie')
            response = client.get('/api/demo/test-error?query=secret')
    assert response.status_code == 500
    events = [json.loads(record.message) for record in caplog.records
              if record.message.startswith('{') and 'http_request' in record.message]
    assert events[-1]['status_class'] == '5xx'
    assert events[-1]['route'] == 'other'
    assert not any(s in json.dumps(events) for s in ('secret', 'private', 'cookie', 'session id'))


def test_readiness_is_read_only_and_separate_from_liveness(monkeypatch):
    import procure.demo.diagnostics as diagnostics
    now = [100.0]
    monkeypatch.setattr(diagnostics, 'monotonic', lambda: now[0])
    backend = MemorySessionPersistence()
    app = create_app(session_backend=backend)
    with TestClient(app, base_url='https://demo.test') as client:
        assert client.get('/-/health').status_code == 200
        assert client.get('/-/ready').json() == {'ready': True, 'persistence': 'firestore'}
        assert backend._documents == {}
        backend.unavailable = True
        assert client.get('/-/ready').status_code == 200  # Bounded cache before expiry.
        now[0] += 6
        assert client.get('/-/ready').status_code == 503
        assert client.get('/-/health').status_code == 200


def test_readiness_has_cross_region_budget_and_short_cache():
    readiness = DependencyReadiness(MemorySessionPersistence())
    try:
        assert 1 <= readiness.timeout_seconds <= 5
        assert 1 <= readiness.ttl_seconds <= 10
    finally:
        readiness.close()


def test_ephemeral_mode_is_not_durable_release_ready():
    with TestClient(create_app(), base_url='https://demo.test') as client:
        assert client.get('/-/health').status_code == 200
        assert client.get('/-/ready').status_code == 503


def test_readiness_timeout_reuses_one_inflight_probe():
    gate = threading.Event()

    class SlowBackend:
        def __init__(self):
            self.probes = 0
            self.saves = 0

        def probe_readiness(self):
            self.probes += 1
            gate.wait(1)
            return True

        def save(self, *args):
            self.saves += 1

    backend = SlowBackend()
    readiness = DependencyReadiness(backend, timeout_seconds=0.01, ttl_seconds=0)
    try:
        assert readiness.check() is False
        assert readiness.check() is False
        assert backend.probes == 1
        assert backend.saves == 0
    finally:
        gate.set()


def test_firestore_missing_database_fails_ready_without_writes():
    class Response:
        status_code = 404
        def json(self):
            return {'error': {'code': 404, 'status': 'NOT_FOUND',
                              'message': 'The database does not exist.'}}

    class Client:
        def __init__(self):
            self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response()

    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    readiness = DependencyReadiness(backend)
    try:
        assert readiness.check() is False
        assert len(backend._client.calls) == 1
        assert backend._client.calls[0][1].endswith('/documents:batchGet')
    finally:
        readiness.close()


def test_unchanged_session_read_does_not_write_again():
    class CountingBackend(MemorySessionPersistence):
        saves = 0

        def save(self, *args):
            self.saves += 1
            return super().save(*args)

    backend = CountingBackend()
    with TestClient(create_app(session_backend=backend), base_url='https://demo.test') as client:
        first = client.get('/api/demo/session')
        session_id = first.json()['session_id']
        first_saves = backend.saves
        assert client.get('/api/demo/session').status_code == 200
        assert backend.saves == first_saves


def test_request_budget_remains_shared_without_rewriting_session(monkeypatch):
    import procure.demo.app as demo_app
    monkeypatch.setattr(demo_app, 'MAX_REQUESTS', 2)
    backend = MemorySessionPersistence()
    with (TestClient(create_app(session_backend=backend), base_url='https://demo.test') as first,
          TestClient(create_app(session_backend=backend), base_url='https://demo.test') as second):
        created = first.get('/api/demo/session').json()
        session_id = created['session_id']
        second.cookies.set('demo_session', session_id)
        payload = next(iter(backend._documents.values()))[0]
        assert first.get('/api/demo/session').status_code == 200
        assert second.get('/api/demo/session').status_code == 200
        assert first.get('/api/demo/session').status_code == 429
        assert next(iter(backend._documents.values()))[0] == payload
