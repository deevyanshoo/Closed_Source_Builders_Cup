"""Native live tools and shipment actions use the same current server evidence."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from test_demo_live_voice import Provider
from test_demo_live_voice_rfq import plan_for_copper


class NamedProvider(Provider):
    def __init__(self):
        super().__init__()
        self.next_call = None

    async def send_realtime_input(self, **kwargs):
        if kwargs.get('audio') and self.next_call is not None:
            name, args, call_id = self.next_call
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name=name, args=args, id=call_id)]), server_content=None))


def voice_tool(client, provider, name, args, call_id):
    session = client.get('/api/demo/session').json()
    provider.next_call = (name, args, call_id)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        while socket.receive_json()['type'] != 'ready':
            pass
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        while True:
            event = socket.receive_json()
            assert event['type'] != 'error', event
            if event['type'] == 'tool_result':
                result = event['result']
                break
        while socket.receive_json()['type'] != 'turn_complete':
            pass
        socket.send_json({'type': 'stop'})
    return result, provider.tool_response.response


def test_native_reads_and_preparation_share_current_evidence():
    provider = NamedProvider()

    @asynccontextmanager
    async def factory():
        yield provider

    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        plan = plan_for_copper(client, headers)
        estimate = client.post('/api/demo/carbon/estimate', headers=headers, json={
            'plan_id': plan['plan_id'], 'expected_context_version': 1,
            'legs_by_offer': {row['offer_id']: [{'mode': 'sea_container', 'distance_km': '1000'}]
                              for row in plan['alternatives']}})
        assert estimate.status_code == 200, estimate.text
        saved = client.get('/api/demo/carbon/assessment').json()['assessment']['result']
        carbon, model = voice_tool(client, provider, 'read_carbon', {}, 'carbon-1')
        assert carbon['status'] == 'completed' and carbon['workspace'] == {'carbon': saved}
        assert '31720' in carbon['assistant_text'] and model['workspace'] == {'carbon': saved}
        assert carbon['agent_steps'][0]['provider_call'] == 'none'
        assert session['csrf_token'] not in str(model) and session['session_id'] not in str(model)

        view = client.get('/api/demo/dispatch').json()
        shipments, model = voice_tool(client, provider, 'read_shipments', {}, 'shipments-1')
        assert shipments['status'] == 'completed' and shipments['workspace'] == {'shipments': view}
        assert model['workspace'] == {'shipments': view}
        row = next(row for row in view['shipments'] if row['recovery_available'])

        invalid, _ = voice_tool(client, provider, 'prepare_shipment_recovery',
                                {'shipment_id': 'foreign-shipment', 'delay_days': 20}, 'foreign-1')
        assert invalid['status'] == 'needs_clarification' and invalid['proposal'] is None
        assert client.get('/api/demo/session').json()['recovery'] is None

        prepared, model = voice_tool(client, provider, 'prepare_shipment_recovery',
            {'shipment_id': row['shipment_id'], 'delay_days': 20}, 'prepare-1')
        assert prepared['status'] == 'proposal' and prepared['proposal']['kind'] == 'recovery'
        assert prepared['proposal']['parameters']['shipment_id'] == row['shipment_id']
        assert model['proposal'] == prepared['proposal']
        assert client.get('/api/demo/session').json()['recovery'] is None
        reread, _ = voice_tool(client, provider, 'read_shipments', {}, 'shipments-2')
        assert reread['status'] == 'completed' and reread['proposal'] is None
        assert app.state.sessions.get(session['session_id']).pending_proposal['proposal_id'] == prepared['proposal']['proposal_id']
        confirm = {'proposal_id': prepared['proposal']['proposal_id'], 'reviewed': True,
                   'expected_context_version': 1, 'idempotency_key': 'native-confirm'}
        denied = client.post('/api/demo/operator/confirm', headers=headers,
                             json={**confirm, 'reviewed': False, 'idempotency_key': 'native-denied'})
        assert denied.status_code == 422 and client.get('/api/demo/session').json()['recovery'] is None
        applied = client.post('/api/demo/operator/confirm', headers=headers, json=confirm)
        assert applied.status_code == 200, applied.text
        assert applied.json()['status'] == 'applied'
        repeated = client.post('/api/demo/operator/confirm', headers=headers, json=confirm)
        assert repeated.status_code == 200 and repeated.json()['result'] == applied.json()['result']
        assert client.get('/api/demo/session').json()['simulated_outbox'] == []


def test_linked_direct_recovery_rejects_wrong_offer_and_replays_only_current_result():
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc))
    with TestClient(app, base_url='https://demo.test') as client:
        redirect = client.get('/dispatch', follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers['location'] == '/demo?view=inventory&supply=shipments'
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        plan = plan_for_copper(client, headers)
        row = next(row for row in client.get('/api/demo/dispatch').json()['shipments']
                   if row['recovery_available'])
        other = next(offer for offer in plan['offer_versions'] if offer != row['offer_id'])
        body = {'baseline_plan_id': plan['plan_id'], 'expected_versions': plan['offer_versions'],
                'delay_days_by_offer': {other: 20}, 'shipment_id': row['shipment_id'],
                'expected_context_version': 1, 'idempotency_key': 'wrong-offer'}
        response = client.post('/api/demo/recovery', headers=headers, json=body)
        assert response.status_code == 422 and client.get('/api/demo/session').json()['recovery'] is None
        body['delay_days_by_offer'] = {row['offer_id']: 20}
        body['idempotency_key'] = 'linked-delay'
        applied = client.post('/api/demo/recovery', headers=headers, json=body)
        assert applied.status_code == 200, applied.text
        replay = client.post('/api/demo/recovery', headers=headers, json=body)
        assert replay.status_code == 200 and replay.json()['replayed'] is True
        assert replay.json()['comparison_id'] == applied.json()['comparison_id']
        assert client.get('/api/demo/dispatch').json()['shipments'][-1]['recovery_available'] is False


def test_shipment_proposal_expires_when_selected_quote_changes():
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc))
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        plan = plan_for_copper(client, headers)
        shipment = next(row for row in client.get('/api/demo/dispatch').json()['shipments']
                        if row['recovery_available'])
        prepared = client.post('/api/demo/operator', headers=headers, json={
            'message': f"Prepare recovery for shipment {shipment['shipment_id']} delayed by 20 days",
            'expected_context_version': 1, 'idempotency_key': 'prepare-stale'})
        assert prepared.status_code == 200 and prepared.json()['proposal']['kind'] == 'recovery'
        proposal_id = prepared.json()['proposal']['proposal_id']
        value = app.state.sessions.get(session['session_id'])
        with value.lock:
            value.offers[shipment['offer_id']]['version'] += 1
        current = client.get('/api/demo/dispatch').json()
        assert not any(row['recovery_available'] for row in current['shipments'])
        confirm = client.post('/api/demo/operator/confirm', headers=headers, json={
            'proposal_id': proposal_id, 'reviewed': True,
            'expected_context_version': 1, 'idempotency_key': 'confirm-stale'})
        assert confirm.status_code == 409
        assert client.get('/api/demo/session').json()['recovery'] is None
