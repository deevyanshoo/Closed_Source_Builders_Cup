"""Regressions for provider-observed native voice result and review quality."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient
import pytest

from procure.demo.app import create_app
from procure.demo import live_voice
from test_demo_live_voice_rfq import plan_for_copper
from test_demo_taher_native import NamedProvider, voice_tool
from test_demo_live_voice import Socket, connection, next_type, setup, start


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def preserve_process_voice_quota():
    with live_voice._START_LOCK:
        starts = live_voice._PROCESS_STARTS
        window = live_voice._PROCESS_WINDOW_STARTED
    yield
    with live_voice._START_LOCK:
        live_voice._PROCESS_STARTS = starts
        live_voice._PROCESS_WINDOW_STARTED = window


def native_app():
    provider = NamedProvider()

    @asynccontextmanager
    async def factory():
        yield provider

    return create_app(clock=lambda: NOW, live_client_factory=factory), provider


def test_carbon_model_gets_only_server_speech_and_truthful_action_flags():
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                       'X-CSRF-Token': session['csrf_token']})
        pending, model = voice_tool(client, provider, 'estimate_carbon',
            {'message': 'Calculate carbon for our current copper purchase'}, 'carbon-question')
        question = pending['assistant_text'].split('For Europa Copper SA, ')[-1]
        assert 'what freight mode and distance in km' in question
        assert model['required_spoken_response'] == pending['assistant_text']
        assert '31720 kg CO2e' in model['required_spoken_response']
        assert model['status'] == 'completed' and model['read_only'] is False
        assert model['tool_succeeded'] is True and model['action_applied'] is True
        assert model['action_result'] == {'tool': 'estimate_carbon'}
        assert set(model) == {'status', 'read_only', 'tool_succeeded', 'action_applied',
                              'action_result', 'required_spoken_response'}
        assert pending['workspace']['carbon']['plan_id'] == plan['plan_id']
        assert pending['session']['carbon_assessment']['result']['plan_id'] == plan['plan_id']

        completed, spoken = voice_tool(client, provider, 'estimate_carbon',
            {'message': 'road 125 km'}, 'carbon-freight')
        assert completed['workspace']['carbon']['plan_id'] == plan['plan_id']
        assert completed['session']['carbon_assessment']['inputs']['legs_by_offer'][
            plan['selected_offer_id']] == [{'mode': 'road_hgv', 'distance_km': '125'}]
        assert 'material 31720 kg CO2e' in spoken['required_spoken_response']
        assert 'freight 103.56 kg CO2e' in spoken['required_spoken_response']
        assert 'Supported total 31823.56 kg CO2e' in spoken['required_spoken_response']
        assert set(spoken) == set(model)


def test_carbon_clarification_and_error_keep_truthful_compact_model_result():
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        clarification, model = voice_tool(client, provider, 'estimate_carbon',
            {'message': 'Assess our carbon footprint'}, 'carbon-clarify')
        assert clarification['status'] == 'needs_clarification'
        assert model['required_spoken_response'] == clarification['assistant_text']
        assert model['read_only'] is clarification['read_only']
        assert model['action_applied'] is False
        assert model['tool_succeeded'] is False
        assert 'workspace' not in model and 'session' not in model
        failure, error_model = voice_tool(client, provider, 'estimate_carbon',
            {'message': 'road 100 km'}, 'carbon-no-plan')
        assert failure['status'] == 'needs_clarification'
        assert error_model['required_spoken_response'] == failure['assistant_text']
        assert error_model['tool_succeeded'] is False
        assert error_model['action_applied'] is False


def test_carbon_provider_failure_cannot_be_spoken_as_saved_calculation():
    async def scenario():
        store, session = setup()
        provider = NamedProvider()
        socket = Socket(start(session))

        async def unavailable(name, args, version, key):
            assert name == 'estimate_carbon'
            raise RuntimeError('calculator offline')

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            workspace_tools=unavailable, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await provider.events.put(NS(tool_call=NS(function_calls=[NS(
            name='estimate_carbon', args={'message': 'Calculate carbon for copper'},
            id='carbon-unavailable')]), server_content=None))
        result = (await next_type(socket, 'tool_result'))['result']
        model = provider.tool_response.response
        assert result['status'] == 'unavailable' and result['read_only'] is True
        assert model['tool_succeeded'] is False and model['action_applied'] is False
        assert model['required_spoken_response'] == result['assistant_text']
        assert 'session' not in model and 'workspace' not in model
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_read_risks_broad_inventory_uses_all_owned_materials_without_risk_scan():
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        changed = client.post('/api/demo/company', headers=headers,
            json={'company_id': 'atlas-petrochem', 'expected_context_version': 1,
                  'idempotency_key': 'select-atlas'})
        assert changed.status_code == 200, changed.text
        session = client.get('/api/demo/session').json()
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy((value.last_plan, value.pending_proposal, value.offers,
                           value.buyer_draft, value.context_version))
        words = 'Analyze our inventory and sourcing across all materials.'
        result, model = voice_tool(client, provider, 'read_risks',
            {'message': words}, 'atlas-broad-inventory')
        assert result['status'] == 'completed' and result['read_only'] is True
        assert len(result['analytics']['materials']) == 12
        assert all('stock' in row and 'stock_cover_days' in row
                   and 'shortest_supplier_lead_days' in row
                   for row in result['analytics']['materials'])
        assert 'Atlas Petrochem' in result['assistant_text']
        assert 'risk and watch signals' not in result['assistant_text']
        assert model['required_spoken_response'] == result['assistant_text']
        assert result['agent_steps'][0]['tool'] == 'inventory_sourcing_read'
        assert before == (value.last_plan, value.pending_proposal, value.offers,
                          value.buyer_draft, value.context_version)
        equivalent, _ = voice_tool(client, provider, 'operate_workspace',
            {'message': words}, 'atlas-broad-operator')
        assert equivalent['agent_steps'][0]['tool'] == 'inventory_sourcing_read'
        assert len(equivalent['analytics']['materials']) == 12
        assert equivalent['assistant_text'] == result['assistant_text']
        assert before == (value.last_plan, value.pending_proposal, value.offers,
                          value.buyer_draft, value.context_version)
        ordinary, _ = voice_tool(client, provider, 'read_risks',
            {'message': 'What risks and alerts affect our company?'}, 'atlas-risk')
        assert ordinary['workspace']['risks']['companies']
        assert ordinary['agent_steps'][0]['tool'] == 'read_risks'
        portfolio, _ = voice_tool(client, provider, 'read_risks',
            {'message': 'What risk alerts affect all ten companies?', 'scope': 'all'}, 'all-risks')
        assert len(portfolio['workspace']['risks']['companies']) == 10
        assert portfolio['agent_steps'][0]['tool'] == 'read_risks'
        for unsafe in ('Analyze inventory and sourcing across all materials for Solara Cables.',
                       'Analyze inventory and sourcing across all materials and prepare recovery.'):
            rejected, _ = voice_tool(client, provider, 'read_risks',
                {'message': unsafe}, 'unsafe-' + str(len(unsafe)))
            assert rejected['status'] == 'needs_clarification'
            assert rejected['read_only'] is True and rejected['proposal'] is None
            assert 'analytics' not in rejected and 'risks' not in rejected.get('workspace', {})
        assert before == (value.last_plan, value.pending_proposal, value.offers,
                          value.buyer_draft, value.context_version)


def test_recovery_review_uses_human_material_and_clean_supplier_without_losing_numbers():
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                       'X-CSRF-Token': session['csrf_token']})
        shipment = next(row for row in client.get('/api/demo/dispatch').json()['shipments']
                        if row['recovery_available'])
        value = app.state.sessions.get(session['session_id'])
        for label in ('Europa Copper SA [SYNTHETIC]', 'Europa Copper SA (synthetic)'):
            chosen = value.offers[plan['selected_offer_id']]
            chosen['fields']['supplier_name']['value'] = label
            result, model = voice_tool(client, provider, 'operate_workspace',
                {'message': f"Prepare recovery for shipment {shipment['shipment_id']} delayed by 7 days"},
                'recovery-' + str(len(label)))
            assert result['status'] == 'proposal'
            speech = model['required_spoken_response']
            assert shipment['shipment_id'] not in speech
            assert '[SYNTHETIC]' not in speech and '(synthetic)' not in speech
            assert 'planned purchase 8000 kilograms of Copper cathode for Solara Cables' in speech
            assert '10000' not in speech
            assert '7 days' in speech and 'USD' in speech
            assert str(plan['total_cost']) in speech
            assert str(result['proposal']['parameters']['preview']['total_cost']) in speech
            assert str(result['proposal']['parameters']['preview']['cost_delta']) in speech
            assert 'unchanged' in speech.lower()
            assert 'Say confirm action to apply.' in speech
            assert value.recovery is None
            gate = live_voice._workspace_action_gate(value, app.state.sessions,
                                                     value.context_version, result)
            assert gate is not None and gate['query'] + ' Say confirm action to apply.' == speech
            gate['spoken'] = 'Say confirm action to apply.'
            gate['prompt_audio'] = True
            assert live_voice._spoken_prompt(gate) is False
            gate['spoken'] = speech
            assert live_voice._spoken_prompt(gate) is True
            old_marker = value.pending_proposal['_marker']
            value.pending_proposal['_marker'] = 'stale-proposal'
            assert live_voice._research_current(value, app.state.sessions,
                                                 value.context_version, gate) is False
            value.pending_proposal['_marker'] = old_marker
            app.state.sessions.clock = lambda: NOW + timedelta(minutes=6)
            assert live_voice._workspace_action_gate(value, app.state.sessions,
                                                       value.context_version, result) is None
            app.state.sessions.clock = lambda: NOW


def test_recovery_review_unselected_outcomes_keep_known_costs_without_none():
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        shipment = next(row for row in client.get('/api/demo/dispatch').json()['shipments']
                        if row['recovery_available'])
        result, _ = voice_tool(client, provider, 'operate_workspace',
            {'message': f"Prepare recovery for shipment {shipment['shipment_id']} delayed by 7 days"},
            'unselected-review-base')
        value = app.state.sessions.get(session['session_id'])
        proposal = value.pending_proposal
        preview = proposal['parameters']['preview']
        preview['selected_offer_id'] = None
        preview['total_cost'] = None
        preview['cost_delta'] = None
        for outcome in ('infeasible', 'stock_covers_need'):
            preview['outcome'] = outcome
            gate = live_voice._workspace_action_gate(value, app.state.sessions,
                                                     value.context_version, result)
            assert gate is not None
            review = gate['query']
            assert f'Preview outcome {outcome}' in review
            assert 'delay ' in review and 'baseline cost ' in review and ' USD' in review
            assert 'None' not in review and 'supplier changes' not in review
            if outcome == 'infeasible':
                assert 'cost unavailable (USD)' in review
            else:
                assert 'no purchase required' in review
            gate['spoken'] = review + ' Say confirm action to apply.'
            gate['prompt_audio'] = True
            assert live_voice._spoken_prompt(gate)

        value.last_plan['required_purchase'] = '3'
        value.last_plan['quantity_unit'] = 'tonne'
        preview['selected_offer_id'] = value.last_plan['selected_offer_id']
        preview['total_cost'] = value.last_plan['total_cost']
        preview['cost_delta'] = '0'
        preview['outcome'] = 'changed'
        gate = live_voice._workspace_action_gate(value, app.state.sessions,
                                                 value.context_version, result)
        assert 'planned purchase 3 tonnes of Copper cathode' in gate['query']
        assert 'supplier changes' not in gate['query']
        value.last_plan['required_purchase'] = '0'
        gate = live_voice._workspace_action_gate(value, app.state.sessions,
                                                 value.context_version, result)
        assert 'no purchase planned for Copper cathode' in gate['query']
        assert 'planned purchase 3' not in gate['query']
        value.last_plan['required_purchase'] = None
        gate = live_voice._workspace_action_gate(value, app.state.sessions,
                                                 value.context_version, result)
        assert 'Apply recovery for Copper cathode for Solara Cables' in gate['query']
        assert 'planned purchase' not in gate['query']


@pytest.mark.parametrize('args', [{}, {'scope': 'all'}, {'message': ''},
                                  {'message': '   '}, {'message': 'risk', 'scope': 'foreign'},
                                  {'message': 'risk', 'extra': 'ignored'},
                                  {'message': 'risk', 'scope': []},
                                  {'message': 'x' * 1201}])
def test_native_read_risks_requires_exact_bounded_message_without_scan(args):
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        value = app.state.sessions.get(session['session_id'])
        result, _ = voice_tool(client, provider, 'read_risks', args,
                               'invalid-risk-' + str(len(args)))
        assert result['status'] == 'needs_clarification'
        assert result['read_only'] is True and result['proposal'] is None
        assert result['agent_steps'] == []
        assert value.risk_report is None


def test_native_read_risks_without_message_never_invokes_workspace_tools():
    async def scenario():
        store, session = setup()
        provider = NamedProvider()
        socket = Socket(start(session))
        calls = []

        async def workspace(name, args, version, key):
            calls.append((name, args))
            raise AssertionError('workspace tools must not run')

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            workspace_tools=workspace, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await provider.events.put(NS(tool_call=NS(function_calls=[NS(
            name='read_risks', args={}, id='missing-risk-message')]),
            server_content=None))
        result = (await next_type(socket, 'tool_result'))['result']
        assert result['status'] == 'needs_clarification'
        assert result['proposal'] is None and result['agent_steps'] == []
        assert calls == []
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
