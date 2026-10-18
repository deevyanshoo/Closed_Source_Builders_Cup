"""Broker-only market read parity across company workspaces."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo import live_voice
from procure.demo.app import create_app
from procure.demo.company import list_companies
from procure.demo.sessions import SessionError
from test_market_operator_read import NO_PUBLIC_SEARCH


def broker_call(client, monkeypatch, args, *, version=None):
    async def capture(websocket, *, session, workspace_tools, **_):
        await websocket.accept()
        try:
            result = await workspace_tools('_read_market_if_applicable', args,
                                           session.context_version if version is None else version,
                                           'market-broker-test')
            await websocket.send_json({'result': result})
        except SessionError as exc:
            await websocket.send_json({'error': exc.code})
        await websocket.close()
    monkeypatch.setattr(live_voice, 'run_live_voice', capture)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        return socket.receive_json()


@pytest.mark.parametrize('profile', list_companies(), ids=lambda p: p['company_id'])
def test_broker_matches_typed_full_market_preview_for_every_company(profile, monkeypatch):
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)),
                    base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        if profile['company_id'] != session['company_id']:
            selected = client.post('/api/demo/company', headers=headers, json={
                'company_id': profile['company_id'], 'expected_context_version': 1,
                'idempotency_key': 'select-for-market-read'})
            assert selected.status_code == 200, selected.text
        before = client.get('/api/demo/session').json()
        typed_response = client.post('/api/demo/operator', headers=headers, json={
            'message': NO_PUBLIC_SEARCH, 'expected_context_version': before['context_version'],
            'idempotency_key': 'typed-market-read'})
        assert typed_response.status_code == 200, typed_response.text
        typed = typed_response.json()
        native = broker_call(client, monkeypatch, {'message': NO_PUBLIC_SEARCH})['result']
        assert native['status'] == 'completed'
        assert native['company_id'] == profile['company_id']
        assert native['assistant_text'] == typed['assistant_text']
        assert native['workspace']['prediction'] == typed['workspace']['prediction']
        assert native['evidence_ids'] == [f"market:{native['workspace']['prediction']['market_run_hash']}"]
        assert native['agent_steps'][0]['tool'] == 'judge_read'
        assert native['read_only'] is True and native['proposal'] is None
        assert client.get('/api/demo/session').json()['context_version'] == before['context_version']


@pytest.mark.parametrize('args', [
    {}, {'message': ''}, {'message': 123}, {'message': NO_PUBLIC_SEARCH, 'extra': True},
    {'message': NO_PUBLIC_SEARCH.replace('Do not search', 'Search')},
    {'message': 'What does the market forecast say for Harvest Foods and our company?'},
    {'message': 'What does our PET resin market forecast say?'},
    {'message': 'Purchase our copper now based on the market forecast.'},
])
def test_broker_rejects_malformed_public_foreign_and_action_requests(args, monkeypatch):
    with TestClient(create_app(), base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        assert broker_call(client, monkeypatch, args)['error'] in {'invalid_read', 'invalid_request', 'invalid_message'}
        after = client.get('/api/demo/session').json()
        assert after['context_version'] == before['context_version']
        assert after['simulated_outbox'] == before['simulated_outbox']


def test_broker_rejects_stale_context(monkeypatch):
    with TestClient(create_app(), base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        assert broker_call(client, monkeypatch, {'message': NO_PUBLIC_SEARCH}, version=999) == {
            'error': 'stale_context'}
