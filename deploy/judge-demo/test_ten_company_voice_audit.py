"""Offline checks for the hosted voice harness's workspace setup."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ten_company_voice_audit as audit
from company_intelligence_news_smoke import PooledClient
from smoke import AcceptanceError


def test_buyer_material_is_selected_before_voice_ready(monkeypatch):
    defaults = {
        'material': 'steel', 'quantity': '120', 'current_stock': '20',
        'quantity_unit': 'tonne', 'budget': '10000',
        'additional_cost_per_unit': '2', 'decision_date': '2026-10-15',
        'need_by': '2026-11-05', 'reporting_currency': 'USD',
    }
    profile = {'company_id': 'forgeline-components', 'quote_coverage': {
        'scenarios': [{'material_id': 'steel', 'buyer_defaults': {
            **defaults, 'offer_ids': [], 'expected_versions': {}}}]}}
    events = []

    class FakeClient:
        def __init__(self):
            self.session = SimpleNamespace(cookies=[SimpleNamespace(name='session', value='fake')])

        def json(self, stage, path, *, body=None, csrf=None):
            events.append((stage, path))
            if path == '/api/demo/session':
                return {'company_id': 'solara-cables', 'context_version': 1,
                        'csrf_token': 'fake-csrf'}
            if path == '/api/demo/company':
                assert body['company_id'] == profile['company_id']
                assert body['expected_context_version'] == 1
                return {'company_id': profile['company_id'], 'context_version': 2,
                        'csrf_token': 'fake-csrf'}
            assert path == '/api/demo/buyer-draft'
            assert csrf == 'fake-csrf'
            assert body['buyer_draft'] == defaults
            assert body['expected_context_version'] == 2
            assert body['idempotency_key']
            return {'company_id': profile['company_id'], 'context_version': 2,
                    'csrf_token': 'fake-csrf', 'selected_material_id': 'steel',
                    'buyer_draft': defaults}

        def close(self):
            events.append(('close', None))

    class FakeSocket:
        async def __aenter__(self):
            assert ('buyer_draft', '/api/demo/buyer-draft') in events
            events.append(('websocket_open', None))
            return self

        async def __aexit__(self, *_):
            return None

        async def send(self, payload):
            message = json.loads(payload)
            if message['type'] == 'start':
                assert message['expected_context_version'] == 2
                events.append(('voice_start', None))

        async def recv(self):
            events.append(('voice_ready', None))
            return json.dumps({'type': 'ready'})

    async def fake_turn(*_):
        events.append(('inventory_turn', None))
        return {'ok': True}

    fake = FakeClient()
    def fake_client(_base, **options):
        assert options == {'max_transport_attempts': 1}
        return fake

    monkeypatch.setattr(audit, 'PooledClient', fake_client)
    monkeypatch.setattr(audit, 'connect', lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(audit, 'turn', fake_turn)
    row = {'company_id': profile['company_id']}
    asyncio.run(audit.company_visit('https://example.test', profile, b'audio', None,
                                    row, lambda: None))
    assert row['ok'] is True
    assert events.index(('company', '/api/demo/company')) < events.index(
        ('buyer_draft', '/api/demo/buyer-draft')) < events.index(('websocket_open', None))
    assert events.index(('voice_ready', None)) < events.index(('inventory_turn', None))


@pytest.mark.parametrize(('max_attempts', 'expected_calls'), [(1, 1), (None, 2)])
def test_transport_failure_attempt_bound_preserves_default(monkeypatch, max_attempts,
                                                           expected_calls):
    options = {} if max_attempts is None else {'max_transport_attempts': max_attempts}
    client = PooledClient('https://example.test', **options)
    calls = []

    def connection_lost(*_args, **_kwargs):
        calls.append(1)
        raise requests.exceptions.ConnectionError('uncertain response')

    monkeypatch.setattr(client.session, 'request', connection_lost)
    try:
        with pytest.raises(AcceptanceError) as failure:
            client.json('buyer_draft', '/api/demo/buyer-draft',
                        body={'buyer_draft': {'material': 'steel'}}, csrf='fake')
        assert failure.value.code == 'transport_error'
        assert len(calls) == client.calls == expected_calls
        assert client.transport_retries == expected_calls - 1
    finally:
        client.close()


@pytest.mark.parametrize('bad_attempts', [0, 3, True, '2'])
def test_transport_attempt_bound_rejects_invalid_values(bad_attempts):
    with pytest.raises(ValueError, match='max_transport_attempts'):
        PooledClient('https://example.test', max_transport_attempts=bad_attempts)
