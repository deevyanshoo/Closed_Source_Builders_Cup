"""The hosted Live route shares the typed operator's security and review path."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from threading import Event

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from procure.demo.app import create_app
from test_demo_live_voice import Provider as FakeLiveProvider
from test_demo_operator import Provider as OperatorProvider


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
URL = 'wss://demo.test/api/demo/voice/live'
ORIGIN = {'origin': 'https://demo.test'}


def live_factory(provider, opened):
    @asynccontextmanager
    async def factory():
        opened.append(True)
        yield provider
    return factory


def app(*, operator=None, opened=None):
    opened = opened if opened is not None else []
    result = create_app(clock=lambda: NOW,
                        operator_provider=operator or OperatorProvider(evidence=()),
                        live_client_factory=live_factory(FakeLiveProvider(), opened))
    return result, opened


def start(session, *, token=None, version=1):
    return {'type': 'start', 'csrf_token': token or session['csrf_token'],
            'expected_context_version': version}


def next_event(socket, kind):
    while True:
        result = socket.receive_json()
        if result['type'] == kind:
            return result


@pytest.mark.parametrize('headers,with_cookie', [
    ({}, True),
    ({'origin': 'https://foreign.test'}, True),
])
def test_live_route_rejects_origin_or_cookie_before_provider(headers, with_cookie):
    application, opened = app()
    with TestClient(application, base_url='https://demo.test') as client:
        if with_cookie:
            client.get('/api/demo/session')
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(URL, headers=headers):
                pass
        assert denied.value.code == 1008
    assert opened == []


def test_invalid_first_frame_csrf_never_opens_provider():
    application, opened = app()
    with TestClient(application, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        with client.websocket_connect(URL, headers=ORIGIN) as socket:
            socket.send_json(start(session, token='incorrect'))
            assert next_event(socket, 'error')['code'] == 'voice_invalid'
            with pytest.raises(WebSocketDisconnect):
                socket.receive_json()
    assert opened == []


def test_spoken_watch_uses_three_verified_roles_and_ui_confirmation_once():
    operator = OperatorProvider(evidence=())
    application, opened = app(operator=operator)
    with TestClient(application, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        auth = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        with client.websocket_connect(URL, headers=ORIGIN) as socket:
            socket.send_json(start(session))
            assert next_event(socket, 'ready')['context_version'] == 1
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            turn = next_event(socket, 'tool_result')['result']
            assert set(turn) == {'turn_id', 'company_id', 'context_version', 'status',
                                 'assistant_text', 'agent_steps', 'evidence_ids',
                                 'proposal', 'read_only'}
            assert turn['read_only'] is True and turn['status'] == 'proposal'
            assert turn['proposal']['kind'] == 'watch'
            assert {role for role, _ in operator.calls} == {'coordinator', 'evidence', 'procurement'}
            assert len(operator.calls) == 3
            assert client.get('/api/demo/operator').json()['watch_items'] == []

            confirm = {'proposal_id': turn['proposal']['proposal_id'], 'reviewed': True,
                       'expected_context_version': 1, 'idempotency_key': 'reviewed-live-watch'}
            applied = client.post('/api/demo/operator/confirm', headers=auth, json=confirm)
            replay = client.post('/api/demo/operator/confirm', headers=auth, json=confirm)
            assert applied.status_code == replay.status_code == 200
            assert applied.json()['status'] == replay.json()['status'] == 'applied'
            assert len(client.get('/api/demo/operator').json()['watch_items']) == 1
            socket.send_json({'type': 'stop'})
    assert opened == [True]


@pytest.mark.parametrize('change', ['switch', 'expire'])
def test_context_change_while_operator_waits_drops_tool_result(change):
    entered, release = Event(), Event()

    class BlockedOperator(OperatorProvider):
        def __call__(self, role, prompt):
            if role == 'coordinator':
                entered.set()
                assert release.wait(3)
            return super().__call__(role, prompt)

    operator = BlockedOperator(evidence=())
    application, _ = app(operator=operator)
    try:
        with TestClient(application, base_url='https://demo.test') as client:
            session = client.get('/api/demo/session').json()
            auth = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
            with client.websocket_connect(URL, headers=ORIGIN) as socket:
                socket.send_json(start(session))
                next_event(socket, 'ready')
                socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
                assert entered.wait(3)
                if change == 'switch':
                    switched = client.post('/api/demo/company', headers=auth, json={
                        'company_id': 'terrapack', 'expected_context_version': 1,
                        'idempotency_key': 'switch-during-live'})
                    assert switched.status_code == 200, switched.text
                else:
                    value = application.state.sessions.get(session['session_id'])
                    application.state.sessions.invalidate(value)
                release.set()
                seen = []
                while True:
                    event = socket.receive_json()
                    seen.append(event['type'])
                    if event['type'] == 'error':
                        assert event['code'] == 'stale_context'
                        break
                assert 'tool_result' not in seen
    finally:
        release.set()
