"""Risk and public-signal reads must not be claimed by broad stock analytics."""

import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from procure.demo import live_voice
from procure.demo.company import get_company
from procure.demo.judge_resolver import _broad_inventory_read as judge_broad_read
from procure.demo.operator import _broad_inventory_scope
from test_demo_live_voice import Socket, connection, next_type, setup, start
from test_demo_taher_native import NamedProvider, voice_tool
from test_demo_voice_actual_quality import native_app


def atlas_snapshot():
    company = get_company('atlas-petrochem')
    return {'company_id': company['company_id'], 'company': company,
            'selected_material_id': None, 'buyer_draft': None}


@pytest.mark.parametrize('verb', ['Analyze', 'Summarize'])
@pytest.mark.parametrize('signal', ['risks', 'alerts', 'headlines', 'news'])
def test_signal_question_is_not_a_pure_inventory_read(verb, signal):
    message = f'{verb} our inventory and sourcing {signal} across all materials.'
    state = atlas_snapshot()
    assert live_voice._broad_inventory_read(message) is False
    assert _broad_inventory_scope(state, message) is None
    assert judge_broad_read(state, message) is None


@pytest.mark.parametrize('message', [
    'Analyze our inventory and sourcing risks across all materials.',
    'Summarize our inventory and sourcing alerts across all materials.',
    'Summarize our inventory and sourcing headlines across all materials.',
])
def test_native_read_risks_preserves_owned_risk_projection(message):
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        changed = client.post('/api/demo/company',
            headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
            json={'company_id': 'atlas-petrochem', 'expected_context_version': 1,
                  'idempotency_key': 'select-atlas'})
        assert changed.status_code == 200, changed.text
        result, _ = voice_tool(client, provider, 'read_risks',
            {'message': message}, 'risk-intent-' + str(len(message)))
        assert result['status'] == 'completed'
        assert result['read_only'] is True and result['proposal'] is None
        assert result['agent_steps'][0]['tool'] == 'read_risks'
        assert result['workspace']['risks']['companies'][0]['company_id'] == 'atlas-petrochem'


@pytest.mark.parametrize('signal', ['risks', 'alerts', 'headlines'])
def test_actual_text_and_native_operator_do_not_substitute_generic_stock(signal):
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        message = f'Analyze our inventory and sourcing {signal} across all materials.'
        typed = client.post('/api/demo/operator',
            headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
            json={'message': message, 'expected_context_version': session['context_version'],
                  'idempotency_key': 'actual-text-' + signal})
        assert typed.status_code == 200, typed.text
        native, _ = voice_tool(client, provider, 'operate_workspace',
                               {'message': message}, 'actual-native-' + signal)
        for result in (typed.json(), native):
            assert result['read_only'] is True and result['proposal'] is None
            assert 'stock:' not in result['assistant_text'].lower()
            assert 'analytics' not in result
        assert 'analytics' not in result


@pytest.mark.parametrize('message', [
    'Analyze our inventory and sourcing risks across all materials.',
    'Summarize our inventory and sourcing headlines across all materials.',
])
def test_native_operate_workspace_keeps_signal_request_on_operator_path(message):
    async def scenario():
        store, session = setup()
        audio_received = asyncio.Event()

        class RiskProvider(NamedProvider):
            async def send_realtime_input(self, **kwargs):
                if audio := kwargs.get('audio'):
                    self.received_audio.append(audio)
                    audio_received.set()

        socket, provider = Socket(start(session)), RiskProvider()
        calls = []

        async def operate(words, version, key):
            calls.append(('operate_workspace', words))
            return {'turn_id': 'risk-turn', 'company_id': session.company_id,
                    'context_version': version, 'status': 'unavailable',
                    'assistant_text': 'No verified result.', 'agent_steps': [],
                    'evidence_ids': [], 'proposal': None, 'read_only': True}

        async def workspace(name, args, version, key):
            calls.append((name, args))
            raise AssertionError('Signal question was intercepted as inventory.')

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=operate,
            workspace_tools=workspace, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(audio_received.wait(), 2)
        await provider.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
        await provider.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=NS(text=message, finished=True))))
        await provider.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.000125s')))
        await provider.events.put(NS(tool_call=NS(function_calls=[NS(
            # The tool argument cannot supply or replace the current user request.
            name='operate_workspace', args={'message': 'Show current stock.'}, id='risk-intent')]),
            server_content=None))
        result = (await next_type(socket, 'tool_result'))['result']
        assert result['status'] == 'unavailable'
        assert calls == [('operate_workspace', message)]
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_plain_atlas_inventory_read_still_covers_all_twelve_materials():
    app, provider = native_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        changed = client.post('/api/demo/company',
            headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
            json={'company_id': 'atlas-petrochem', 'expected_context_version': 1,
                  'idempotency_key': 'select-atlas'})
        assert changed.status_code == 200, changed.text
        result, _ = voice_tool(client, provider, 'read_risks',
            {'message': 'Analyze our inventory and sourcing across all materials.'},
            'plain-atlas-inventory')
        assert result['agent_steps'][0]['tool'] == 'inventory_sourcing_read'
        assert len(result['analytics']['materials']) == 12
        assert all('stock_cover_days' in row for row in result['analytics']['materials'])
