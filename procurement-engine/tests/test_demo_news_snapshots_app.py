"""The existing hosted news boundary selects durable storage without weakening scope."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from procure.demo.app import create_app

NOW = datetime(2026, 10, 15, 2, tzinfo=timezone.utc)


def install_adapter(monkeypatch):
    calls = []

    class Store:
        def __init__(self, bucket):
            calls.append(('bucket', bucket))

    class Service:
        def __init__(self, *, live, store, clock):
            calls.append(('adapter', clock()))

        def get(self, company, force=False):
            calls.append(('get', company['company_id'], force))
            return {'checked_at': NOW.isoformat(), 'cache_state': 'cached',
                    'sources': [], 'items': [], 'fx': [], 'limitations': [],
                    'collection': {'mode': 'manual' if force else 'scheduled',
                                   'attempted_at': NOW.isoformat(),
                                   'news_success_at': NOW.isoformat(),
                                   'fx_success_at': None, 'status': 'fresh'}}

    monkeypatch.setattr('procure.demo.news_snapshots.GcsSnapshotStore', Store)
    monkeypatch.setattr('procure.demo.news_snapshots.SnapshotNewsService', Service)
    return calls


def test_configured_bucket_selects_snapshot_adapter_and_exposes_freshness(monkeypatch):
    monkeypatch.setenv('OFFLINE', '0')
    monkeypatch.setenv('NEWS_SNAPSHOT_BUCKET', 'demo-news-private')
    calls = install_adapter(monkeypatch)
    with TestClient(create_app(clock=lambda: NOW), base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        response = client.get('/api/demo/news')
        assert response.status_code == 200
        assert response.json()['collection']['mode'] == 'scheduled'
        assert response.json()['company_id'] == 'solara-cables'
        assert response.json()['context_version'] == 1
    assert calls[0] == ('bucket', 'demo-news-private')
    assert ('get', 'solara-cables', False) in calls


def test_manual_refresh_reaches_live_force_path_without_claiming_scheduled_refresh(monkeypatch):
    monkeypatch.setenv('OFFLINE', '0')
    monkeypatch.setenv('NEWS_SNAPSHOT_BUCKET', 'demo-news-private')
    calls = install_adapter(monkeypatch)
    with TestClient(create_app(clock=lambda: NOW), base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        response = client.post('/api/demo/news/refresh',
            headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
            json={'expected_context_version': 1, 'idempotency_key': 'manual-news'})
        assert response.status_code == 200
        assert response.json()['collection']['mode'] == 'manual'
    assert ('get', 'solara-cables', True) in calls


def test_explicit_injected_news_service_remains_authoritative(monkeypatch):
    monkeypatch.setenv('NEWS_SNAPSHOT_BUCKET', 'demo-news-private')
    calls = install_adapter(monkeypatch)

    class Injected:
        def get(self, company, force=False):
            return {'checked_at': NOW.isoformat(), 'cache_state': 'offline',
                    'sources': [], 'items': [], 'fx': [], 'limitations': []}

    with TestClient(create_app(clock=lambda: NOW, news_service=Injected()),
                    base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        assert client.get('/api/demo/news').json()['cache_state'] == 'offline'
    assert calls == []


def test_no_bucket_keeps_existing_offline_live_path(monkeypatch):
    monkeypatch.delenv('NEWS_SNAPSHOT_BUCKET', raising=False)
    monkeypatch.setenv('OFFLINE', '1')
    calls = install_adapter(monkeypatch)
    with TestClient(create_app(clock=lambda: NOW), base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        assert client.get('/api/demo/news').json()['cache_state'] == 'offline'
    assert calls == []


def test_offline_mode_blocks_storage_even_when_bucket_is_configured(monkeypatch):
    monkeypatch.setenv('NEWS_SNAPSHOT_BUCKET', 'demo-news-private')
    monkeypatch.setenv('OFFLINE', '1')
    calls = install_adapter(monkeypatch)
    with TestClient(create_app(clock=lambda: NOW), base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        assert client.get('/api/demo/news').json()['cache_state'] == 'offline'
        risks = client.get('/api/demo/risks').json()
        assert risks['news_refresh_seconds'] == 900
        assert all('hourly' not in note for note in risks['limitations'])
    assert calls == []
