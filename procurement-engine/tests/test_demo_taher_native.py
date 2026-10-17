"""Native live tools and shipment actions use the same current server evidence."""
from copy import deepcopy
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient
import pytest

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
            if name == 'operate_workspace':
                await self.events.put(NS(server_content=None, voice_activity=NS(
                    voice_activity_type='ACTIVITY_START', audio_offset='0.000s')))
                await self.events.put(NS(server_content=NS(input_transcription=NS(
                    text=args['message'], finished=True))))
                await self.events.put(NS(server_content=None, voice_activity=NS(
                    voice_activity_type='ACTIVITY_END', audio_offset='0.000125s')))
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name=name, args=args, id=call_id)]), server_content=None))

    async def send_tool_response(self, *, function_responses):
        self.tool_response = function_responses
        response = function_responses.response
        await self.events.put(NS(server_content=NS(
            output_transcription=NS(text=response.get('required_spoken_response', response.get('assistant_text', ''))),
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\1\0' * 10,
                mime_type='audio/pcm;rate=24000'))]), turn_complete=True)))


def voice_tool(client, provider, name, args, call_id):
    session = client.get('/api/demo/session').json()
    provider.next_call = (name, args, call_id)
    provider.last_frames = []
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        while socket.receive_json()['type'] != 'ready':
            pass
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        while True:
            event = socket.receive_json()
            provider.last_frames.append(event['type'])
            assert event['type'] != 'error', event
            if event['type'] == 'tool_result':
                result = event['result']
                break
        while True:
            event = socket.receive_json()
            provider.last_frames.append(event['type'])
            assert event['type'] != 'error', event
            if event['type'] == 'turn_complete':
                break
        socket.send_json({'type': 'stop'})
    return result, provider.tool_response.response


def test_native_supplier_what_if_uses_pure_preview_and_explicit_action_still_proposes():
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
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy((value.last_plan, value.recovery, value.rfq, value.pending_proposal,
                           value.drafts, value.simulated_outbox))
        question = 'What if our current copper cathode supplier is delayed by 7 days?'
        preview, model = voice_tool(client, provider, 'preview_supplier_delay',
                                    {'message': question}, 'supplier-what-if')
        assert preview['status'] == 'completed' and preview['read_only'] is True, preview['assistant_text']
        assert preview['proposal'] is None and 'session' not in preview and 'action_result' not in preview
        assert preview['workspace']['judge_preview']['baseline_plan_id'] == plan['plan_id']
        assert preview['workspace']['judge_preview']['scenario_plan_id'] is None
        assert model['workspace']['judge_preview']['read_only'] is True
        assert before == (value.last_plan, value.recovery, value.rfq, value.pending_proposal,
                          value.drafts, value.simulated_outbox)
        rejected, _ = voice_tool(client, provider, 'preview_supplier_delay',
            {'message': 'Prepare recovery for the current copper cathode supplier delayed by 7 days.'},
            'not-a-preview')
        assert rejected['status'] == 'needs_clarification' and rejected['proposal'] is None
        assert before == (value.last_plan, value.recovery, value.rfq, value.pending_proposal,
                          value.drafts, value.simulated_outbox)
        shipment = next(row for row in client.get('/api/demo/dispatch').json()['shipments']
                        if row['recovery_available'])
        prepared, _ = voice_tool(client, provider, 'operate_workspace',
            {'message': f"Prepare recovery for shipment {shipment['shipment_id']} delayed by 7 days"},
            'explicit-recovery')
        assert prepared['status'] == 'proposal' and prepared['proposal']['kind'] == 'recovery'
        assert value.recovery is None


def test_native_operate_workspace_exact_spoken_recovery_prepares_without_applying():
    provider = NamedProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                        'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy((value.last_plan, value.recovery, value.rfq, value.drafts))
        result, _ = voice_tool(client, provider, 'operate_workspace', {
            'message': 'Prepare a recovery scenario for a seven day delay of our current copper cathode supplier.'
        }, 'spoken-seven-day-recovery')
        assert result['status'] == 'proposal', result
        assert result['proposal']['kind'] == 'recovery'
        assert result['proposal']['parameters']['delay_days_by_offer'] == {plan['selected_offer_id']: 7}
        assert before == (value.last_plan, value.recovery, value.rfq, value.drafts)


@pytest.mark.parametrize('duration', ['7 to 10 day', 'seven or fourteen day', '0 day'])
def test_native_operate_workspace_ambiguous_duration_does_not_prepare(duration):
    provider = NamedProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy((value.last_plan, value.recovery, value.rfq, value.drafts))
        result, model = voice_tool(client, provider, 'operate_workspace', {
            'message': f'Prepare a recovery scenario for a {duration} delay of our current copper cathode supplier.'
        }, f'ambiguous-recovery-{duration}')
        assert result['status'] == 'needs_clarification', result
        assert result['proposal'] is None
        assert model['required_spoken_response'] == result['assistant_text']
        assert before == (value.last_plan, value.recovery, value.rfq, value.drafts)


def test_native_carbon_calculation_uses_same_saved_result_as_text():
    provider = NamedProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                       'X-CSRF-Token': session['csrf_token']})
        result, model = voice_tool(client, provider, 'estimate_carbon',
            {'message': 'Calculate carbon for our current copper purchase'}, 'carbon-native')
        assert result['status'] == 'completed'
        assert result['action_result']['tool'] == 'estimate_carbon'
        assert result['session']['carbon_assessment']['result']['plan_id'] == plan['plan_id']
        assert model['required_spoken_response'] == result['assistant_text']
        assert 'session' not in model
        assert provider.last_frames.index('tool_result') < provider.last_frames.index('audio')
        assert app.state.sessions.get(session['session_id']).pending_carbon is not None


def test_native_routes_read_uses_owned_profile_without_distance():
    provider = NamedProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        result, model = voice_tool(client, provider, 'read_routes',
            {'message': 'What are our current routes?'}, 'routes-native')
        assert result['read_only'] is True
        assert result['workspace']['routes']
        assert 'distance' in result['assistant_text']
        assert 'routes' in model['workspace']


def test_native_carbon_missing_material_asks_with_real_live_audio():
    provider = NamedProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        result, model = voice_tool(client, provider, 'estimate_carbon',
            {'message': 'Assess our carbon footprint'}, 'carbon-select')
        assert result['status'] == 'needs_clarification'
        assert result['session']['company_id'] == 'solara-cables'
        assert 'material' in model['required_spoken_response'].lower()
        assert provider.last_frames.index('tool_result') < provider.last_frames.index('audio')


def test_native_navigation_accepts_operations_views():
    provider = NamedProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        for view in ('overview', 'inventory', 'routes', 'shipments'):
            result, _ = voice_tool(client, provider, 'navigate_workspace',
                                   {'view': view}, f'nav-{view}')
            assert result['action_result']['view'] == view
            assert result['session']['company_id'] == 'solara-cables'


def test_native_supplier_preview_uses_original_baseline_after_accepted_recovery():
    from procure.demo.recovery import compare
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
        value = app.state.sessions.get(session['session_id'])
        accepted = compare(value.last_plan, value.plan_request, list(value.offers.values()),
                           {plan['selected_offer_id']: 1}, today=datetime(2026, 10, 14).date())
        accepted['scenario']['plan_id'] = 'accepted-scenario'
        value.recovery = accepted
        before = deepcopy(value.recovery)
        result, _ = voice_tool(client, provider, 'preview_supplier_delay',
            {'message': 'What if our current copper cathode supplier is delayed by 7 days?'},
            'baseline-preview')
        assert result['status'] == 'completed' and result['proposal'] is None, result
        assert result['workspace']['judge_preview']['baseline_plan_id'] == plan['plan_id']
        assert result['workspace']['judge_preview']['scenario_plan_id'] is None
        assert value.recovery == before


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
        assert '31720' in carbon['assistant_text']
        selected_carbon = next(row for row in saved['suppliers']
                               if row['offer_id'] == plan['selected_offer_id'])
        assert model['workspace']['carbon']['suppliers'] == [selected_carbon]
        assert model['workspace']['carbon']['selected_offer_id'] == plan['selected_offer_id']
        assert model['selected_purchase_brief'] == carbon['assistant_text']
        assert 'required_spoken_response' not in model
        assert carbon['agent_steps'][0]['provider_call'] == 'none'
        assert session['csrf_token'] not in str(model) and session['session_id'] not in str(model)

        view = client.get('/api/demo/dispatch').json()
        shipments, model = voice_tool(client, provider, 'read_shipments', {}, 'shipments-1')
        assert shipments['status'] == 'completed' and shipments['workspace'] == {'shipments': view}
        linked_rows = [row for row in view['shipments']
                       if row.get('plan_id') == plan['plan_id']]
        assert model['workspace']['shipments']['shipments'] == linked_rows
        assert model['current_purchase_brief'] == shipments['assistant_text']
        assert 'required_spoken_response' not in model
        row = next(row for row in view['shipments'] if row['recovery_available'])

        invalid, _ = voice_tool(client, provider, 'operate_workspace',
            {'message': 'Prepare recovery for shipment foreign-shipment delayed by 20 days'},
            'foreign-1')
        assert invalid['status'] in {'needs_clarification', 'unavailable'} and invalid['proposal'] is None
        assert client.get('/api/demo/session').json()['recovery'] is None

        prepared, model = voice_tool(client, provider, 'operate_workspace',
            {'message': f"Prepare recovery for shipment {row['shipment_id']} delayed by 20 days"},
            'prepare-1')
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
