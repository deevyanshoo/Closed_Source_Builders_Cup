"""Native risk read speech must keep the server's owned conditional answer intact."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from procure.demo import live_voice
from test_demo_live_voice import Provider, Socket, connection, next_type, setup, start
from test_demo_risk_answer_quality import risk_app, select
from test_demo_taher_native import voice_tool


QUESTION = 'Analyze our inventory and sourcing risks across all materials.'


def owned_risk_result(session):
    answer = ('Solara Cables: Copper cathode has a potentially late supplier option. '
              'Potential lateness applies only if this supplier option is used; '
              'no actual shipment delay is confirmed. This listed option would be '
              '11 days late for the stated need date. Next for review: Review faster alternatives.')
    return {'turn_id': 'owned-risk-turn', 'company_id': session.company_id,
        'context_version': session.context_version, 'status': 'completed',
        'assistant_text': answer,
        'agent_steps': [{'agent': 'risk_monitor', 'status': 'completed',
                         'tool': 'read_risks', 'model': 'none', 'provider_call': 'none'}],
        'evidence_ids': ['order-1'], 'proposal': None, 'read_only': True,
        'workspace': {'risks': {'checked_at': '2026-10-14T00:00:00+00:00',
            'companies': [{'company_id': session.company_id,
                'company_name': 'Solara Cables',
                'counts': {'high': 0, 'medium': 1, 'low': 0},
                'alerts': [{'category': 'supplier_delay', 'severity': 'medium',
                    'title': 'Copper cathode has a potentially late supplier option',
                    'impact': 'Potential lateness applies only if this supplier option is used.',
                    'material_ids': ['copper'], 'provenance': 'synthetic_delay_scenario',
                    'metrics': {'lateness_days': 11},
                    'evidence': [{'kind': 'order', 'ref_id': 'order-1',
                                  'label': 'Copper order', 'observed_at': None}],
                    'recovery_actions': [{'label': 'Review faster alternatives',
                                          'requires_review': True}]}]}],
            'limitations': ['Review before changing a plan.']}}}


async def native_result(name, mutate=lambda result, session: None):
    store, session = setup()
    class RiskProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

    socket, provider = Socket(start(session)), RiskProvider()
    result = owned_risk_result(session)
    mutate(result, session)
    original = deepcopy(result)
    calls = []

    async def operate(message, version, key):
        calls.append(('operate_workspace', message, version))
        return result

    async def workspace(tool, args, version, key):
        calls.append((tool, args, version))
        return result

    task = asyncio.create_task(live_voice.run_live_voice(
        socket, session=session, store=store, operate_workspace=operate,
        workspace_tools=workspace, client_factory=connection(provider)))
    await next_type(socket, 'ready')
    await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
    async def wait_for_mic():
        while not provider.received_audio:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_for_mic(), 2)
    await provider.events.put(NS(tool_call=None, server_content=None,
        voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
    await provider.events.put(NS(tool_call=None, server_content=NS(
        input_transcription=NS(text=QUESTION, finished=True))))
    await provider.events.put(NS(tool_call=None, server_content=None,
        voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.000125s')))
    arguments = {'message': QUESTION} if name in {'read_risks', 'operate_workspace'} else {}
    await provider.events.put(NS(tool_call=NS(function_calls=[NS(
        name=name, args=arguments, id='owned-risk-call')]),
        server_content=None))
    delivered = (await next_type(socket, 'tool_result'))['result']
    await next_type(socket, 'turn_complete')
    model = provider.tool_response.response
    await socket.frames.put('{"type":"stop"}')
    await asyncio.wait_for(task, 2)
    assert delivered == original
    assert result == original
    assert calls == [((name, arguments, session.context_version)
                      if name != 'operate_workspace' else
                      ('operate_workspace', QUESTION, session.context_version))]
    return original, model


@pytest.mark.parametrize('name', ['read_risks', 'operate_workspace'])
def test_completed_owned_risk_projection_requires_full_server_spoken_answer(name):
    result, model = asyncio.run(native_result(name))
    assert model['required_spoken_response'] == result['assistant_text']
    assert '11 days late' in model['required_spoken_response']
    assert 'Review faster alternatives' in model['required_spoken_response']
    assert 'no actual shipment delay is confirmed' in model['required_spoken_response']
    assert 'required_spoken_response' in model['instruction']
    assert 'conditional' in model['instruction']
    assert 'review' in model['instruction']
    assert model['workspace'] == result['workspace']
    assert model['evidence_ids'] == result['evidence_ids']
    assert model['read_only'] is True and model['proposal'] is None


@pytest.mark.parametrize('change', [
    lambda result, session: result.update(status='unavailable'),
    lambda result, session: result.update(company_id='atlas-petrochem'),
    lambda result, session: result.update(context_version=session.context_version + 1),
    lambda result, session: result['workspace']['risks']['companies'][0].update(company_id='atlas-petrochem'),
    lambda result, session: result['workspace']['risks']['companies'].append({'company_id': 'atlas-petrochem'}),
    lambda result, session: result.update(proposal={'proposal_id': 'pending'}),
    lambda result, session: result.update(read_only=False),
    lambda result, session: result.update(action_result={'tool': 'apply_recovery'}),
    lambda result, session: result.update(agent_steps=[]),
    lambda result, session: result['agent_steps'][0].update(provider_call='real'),
    lambda result, session: result['agent_steps'][0].update(model='gemini'),
    lambda result, session: result['agent_steps'].append({
        'tool': 'explain_risk', 'status': 'completed', 'model': 'gemini',
        'provider_call': 'real'}),
    lambda result, session: result['agent_steps'].append('malformed step'),
    lambda result, session: result['agent_steps'].append({
        'tool': 'explain_risk', 'status': 'pending', 'model': 'none',
        'provider_call': 'none'}),
    lambda result, session: result.update(workspace={'risks': {'companies': 'invalid'}}),
    lambda result, session: result.update(assistant_text='x' * 2401),
])
def test_noncurrent_or_nonread_risk_result_is_not_authoritative(change):
    _, session = setup()
    result = owned_risk_result(session)
    change(result, session)
    assert live_voice._owned_risk_speech(result, session) is False


def test_unrelated_workspace_read_does_not_gain_risk_spoken_contract():
    _, model = asyncio.run(native_result('read_workspace'))
    assert 'required_spoken_response' not in model


def test_extra_completed_deterministic_step_keeps_owned_risk_contract():
    _, session = setup()
    result = owned_risk_result(session)
    result['agent_steps'].append({'tool': 'source_lookup', 'status': 'completed',
                                  'model': 'none', 'provider_call': 'none'})
    assert live_voice._owned_risk_speech(result, session) is True


def test_existing_analytics_speech_remains_distinct_from_risk_contract():
    def analytics(result, session):
        result.pop('workspace')
        result['analytics'] = {'materials': [{'material_id': 'copper'}]}
        result['agent_steps'][0]['tool'] = 'inventory_sourcing_read'

    result, model = asyncio.run(native_result('read_risks', analytics))
    assert model['required_spoken_response'] == result['assistant_text']
    assert 'instruction' not in model
    assert model['analytics'] == result['analytics']


def test_large_owned_projection_keeps_spoken_brief_after_existing_compaction():
    def large(result, session):
        result['workspace']['risks']['extra'] = 'x' * 70_000

    result, model = asyncio.run(native_result('read_risks', large))
    assert model['required_spoken_response'] == result['assistant_text']
    assert 'workspace' not in model


@pytest.mark.parametrize('name', ['read_risks', 'operate_workspace'])
def test_actual_app_owned_risk_result_has_native_spoken_contract(name):
    app, operator_model, voice, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        result, model = voice_tool(client, voice, name,
                                   {'message': QUESTION}, 'actual-risk-speech-' + name)
        assert result['status'] == 'completed' and result['read_only'] is True
        assert result['workspace']['risks']['companies'][0]['company_id'] == 'atlas-petrochem'
        assert model['required_spoken_response'] == result['assistant_text']
        assert '11 days late' in model['required_spoken_response']
        assert 'Review faster alternatives' in model['required_spoken_response']
        assert model['workspace'] == result['workspace']
        assert operator_model.calls == 0 and news.calls == 0
