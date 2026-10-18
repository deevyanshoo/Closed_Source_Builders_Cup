"""Offline native reads retain owned facts and never speak before forecast grounding."""
from copy import deepcopy
import base64
import json
from types import SimpleNamespace as NS

import pytest

from procure.demo import live_voice
from procure.demo.company import list_companies
from test_demo_live_voice_clarification import (
    workspace, ask, tool_result, assert_audio, receive_turn, PCM,
)
from test_demo_live_voice_native_grounded import FORECAST, protected
from test_demo_live_voice_supply_coverage import event
from test_demo_live_voice import Provider, Socket, connection, next_type, setup, start


RISK = ('Hello. What is our current inventory risk? Use the company data already '
        'in our workspace and answer briefly in English.')
COMPANIES = [row['company_id'] for row in list_companies()]


@pytest.mark.parametrize('company_id', COMPANIES)
@pytest.mark.parametrize('tool', ['read_workspace', 'analyze_inventory'])
def test_fresh_inventory_risk_misroute_uses_owned_current_risk_answer(workspace, company_id, tool):
    client, provider, operator = workspace
    info = client.get('/api/demo/session').json()
    value = client.app.state.sessions.get(info['session_id'])
    value.select_company(company_id)
    provider.tool_name, provider.chunks = tool, (RISK,)
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    model = provider.tool_response.response
    assert result['status'] == 'completed' and result['read_only'] is True
    assert result['proposal'] is None and result.get('action_result') is None
    assert any(row['tool'] == 'read_risks' for row in result['agent_steps'])
    assert [row['company_id'] for row in result['workspace']['risks']['companies']] == [company_id]
    assert model['required_spoken_response'] == result['assistant_text']
    assert 'conditional' in model['instruction']
    assert provider.tool_response.name == tool
    assert_audio(frames)
    assert protected(client) == before
    assert operator.calls == [] and provider.executed_messages == []


def large_result():
    material = {'material_id': 'resin', 'name': 'Resin', 'quantity_unit': 't',
        'stock': '31.25', 'consumption_per_day': '2.5', 'monthly_requirement': '75',
        'criticality': 'high'}
    alert = {'alert_id': 'owned-alert', 'company_id': 'owned', 'category': 'inventory',
        'title': 'Conditional inventory exposure', 'impact': 'Only if this supplier option is used.',
        'severity': 'high', 'material_ids': ['resin'], 'provenance': 'synthetic_profile',
        'metrics': {'stock_cover_days': '12.5', 'shortest_lead_days': 15},
        'recovery_actions': [{'label': 'Inspect alternatives', 'requires_review': True}],
        'evidence': [{'kind': 'profile', 'ref_id': 'resin', 'label': 'Resin',
                      'detail': 'Fictional profile stock and supplier lead.', 'observed_at': None}]}
    analytics = {'company_id': 'owned', 'source': 'fictional_company_profile',
        'as_of': '2026-10-14', 'materials': [{**material, 'unit': 't', 'stock_cover_days': '12.5'}]}
    return {'company_id': 'owned', 'context_version': 7, 'status': 'completed',
        'assistant_text': 'Read owned inventory.', 'read_only': True, 'proposal': None,
        'workspace': {'company': {'company_id': 'owned', 'name': 'Owned company',
            'as_of': '2026-10-14', 'synthetic': True, 'materials': [material],
            'orders': [{'order_id': 'order-1', 'material_id': 'resin', 'quantity': '10',
                        'unit': 't', 'need_by': '2026-10-29', 'status': 'pending'}]},
            'risks': {'company_id': 'owned', 'alerts': [alert]},
            'selected_material_id': None, 'buyer_draft': None,
            'news_brief': {'availability': 'available', 'company_id': 'owned',
                'company_name': 'Owned company', 'checked_at': '2026-10-16T12:00:00Z',
                'cache_state': 'fresh', 'collection': {'status': 'complete',
                    'attempted_at': '2026-10-16T11:00:00Z',
                    'news_success_at': '2026-10-16T11:59:00Z'},
                'sources': [{'source_id': 'public-1', 'name': 'Example Public Wire',
                    'state': 'fresh', 'last_checked_at': '2026-10-16T12:00:00Z'}],
                'headlines': [{'item_id': 'headline-1', 'title': 'Resin port delays reported',
                    'url': 'https://example.org/news/resin-delay',
                    'publisher': 'Example Public Wire', 'source_id': 'public-1',
                    'published_at': '2026-10-16T10:00:00Z',
                    'retrieved_at': '2026-10-16T12:00:00Z',
                    'exposure': {'kind': 'direct_material', 'matched_materials': ['resin'],
                        'assessment': 'Conditional relevance; operational impact unverified.'}}],
                'global_watch': {'status': 'fresh', 'success_at': '2026-10-16T12:00:00Z',
                    'sources': [{'source_id': 'watch-1', 'name': 'Example Watch',
                        'state': 'fresh', 'last_checked_at': '2026-10-16T12:00:00Z'}],
                    'events': [{'item_id': 'watch-1', 'title': 'Canal congestion reported',
                        'url': 'https://example.org/watch/canal',
                        'source_id': 'watch-1', 'publisher': 'Example Watch',
                        'published_at': '2026-10-16T09:00:00Z',
                        'exposure': {'kind': 'sector', 'reason': 'Conditional route exposure.'}}]},
                'data_gaps': ['Supplier exposure is unverified.'],
                'caution': 'Public headline is conditional; check the source.',
                'raw_html': 'SYNTHETIC_RAW_HTML_SENTINEL ' * 5000},
            'publicdata': {'raw': 'irrelevant ' * 10000},
            'session': {'csrf_token': 'SYNTHETIC_SECRET_SENTINEL'},
            'documents': [{'text': 'SYNTHETIC_DOCUMENT_SENTINEL'}],
            'history': ['SYNTHETIC_HISTORY_SENTINEL']},
        'analytics': analytics}


def test_compaction_keeps_all_owned_inventory_and_conditional_risks_without_private_payloads():
    result = large_result()
    original = deepcopy(result)
    assert len(json.dumps(result, ensure_ascii=True)) > 64000
    project = getattr(live_voice, '_compact_model_result', None)
    assert callable(project), 'An independently testable bounded projection is required'
    compact = project('read_workspace', result)
    encoded = json.dumps(compact, ensure_ascii=True)
    assert len(encoded) <= 64000
    assert compact['company_id'] == 'owned' and compact['context_version'] == 7
    assert compact['workspace']['company'] == original['workspace']['company']
    assert compact['workspace']['risks'] == original['workspace']['risks']
    assert compact['analytics'] == original['analytics']
    brief = compact['workspace']['news_brief']
    assert brief['headlines'] == original['workspace']['news_brief']['headlines']
    assert brief['sources'] == original['workspace']['news_brief']['sources']
    assert brief['global_watch'] == original['workspace']['news_brief']['global_watch']
    assert brief['checked_at'] == original['workspace']['news_brief']['checked_at']
    assert brief['caution'] == original['workspace']['news_brief']['caution']
    assert 'SYNTHETIC_RAW_HTML_SENTINEL' not in encoded
    assert all(sentinel not in encoded for sentinel in (
        'SYNTHETIC_SECRET_SENTINEL', 'SYNTHETIC_DOCUMENT_SENTINEL', 'SYNTHETIC_HISTORY_SENTINEL'))
    assert result == original


def test_compaction_does_not_replace_oversized_owned_facts_with_empty_facts():
    result = large_result()
    result['workspace']['company']['materials'][0]['name'] = 'owned fact ' * 10000
    project = getattr(live_voice, '_compact_model_result', None)
    assert callable(project), 'An independently testable bounded projection is required'
    with pytest.raises(live_voice._Invalid):
        project('read_workspace', result)


def test_compaction_fails_if_essential_news_title_cannot_fit():
    result = large_result()
    result['workspace']['news_brief']['headlines'][0]['title'] = 'Current sourced headline ' * 5000
    with pytest.raises(live_voice._Invalid):
        live_voice._compact_model_result('read_workspace', result)


@pytest.mark.parametrize('company_id', COMPANIES)
def test_large_workspace_reaches_provider_with_every_owned_material_and_risk(workspace, monkeypatch, caplog, company_id):
    from procure.demo import app as demo_app
    client, provider, operator = workspace
    info = client.get('/api/demo/session').json()
    value = client.app.state.sessions.get(info['session_id'])
    value.select_company(company_id)
    provider.tool_name, provider.chunks = 'read_workspace', ('Read our current company inventory facts.',)
    mock_brief = deepcopy(large_result()['workspace']['news_brief'])
    mock_brief['company_id'] = company_id
    mock_brief['company_name'] = value.company_id
    mock_brief['raw_html'] = 'PRIVATE_LOG_SENTINEL ' * 5000
    monkeypatch.setattr(demo_app, 'build_news_brief', lambda *a, **kw: mock_brief)
    before = protected(client)
    frames = ask(client)
    result, model = tool_result(frames), provider.tool_response.response
    assert len(json.dumps(result, ensure_ascii=True)) > 64000
    assert len(json.dumps(model, ensure_ascii=True)) <= 64000
    assert model['company_id'] == company_id and model['context_version'] == result['context_version']
    assert model['workspace']['company']['materials'] == [
        {k: v for k, v in row.items() if k != 'keywords'}
        for row in result['workspace']['company']['materials']]
    assert model['analytics']['materials'] == result['analytics']['materials']
    assert len(model['workspace']['risks']['alerts']) == len(result['workspace']['risks']['alerts'])
    for compact, original in zip(model['workspace']['risks']['alerts'], result['workspace']['risks']['alerts']):
        for key in ('impact', 'metrics', 'recovery_actions', 'provenance', 'material_ids'):
            assert compact[key] == original[key]
    brief = model['workspace']['news_brief']
    assert brief['headlines'] == mock_brief['headlines']
    assert brief['sources'] == mock_brief['sources']
    assert brief['global_watch'] == mock_brief['global_watch']
    assert brief['checked_at'] == mock_brief['checked_at']
    assert model['workspace']['company']['orders'] == [
        {k: v for k, v in row.items() if k in {'order_id', 'material_id', 'quantity', 'unit', 'need_by', 'status'}}
        for row in result['workspace']['company']['orders']]
    for compact, original in zip(model['workspace']['risks']['alerts'], result['workspace']['risks']['alerts']):
        assert compact['evidence'] == original['evidence']
    assert 'PRIVATE_LOG_SENTINEL' not in caplog.text
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('question', [
    'Search the public web for our current inventory risk.',
    'Prepare a draft about our current inventory risk.',
    'What is the current inventory risk for TerraPack?',
])
def test_risk_override_excludes_public_action_and_foreign_company_intent(workspace, question):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_workspace', (question,)
    before = protected(client)
    result = tool_result(ask(client))
    assert not any(row.get('tool') == 'read_risks' for row in result['agent_steps'])
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('case', ['no_vad', 'unfinished', 'foreign_correction'])
def test_risk_override_needs_current_settled_owned_asr(workspace, monkeypatch, case):
    monkeypatch.setattr(live_voice, 'FRESH_CALL_GRACE_SECONDS', .03)
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_workspace', (RISK,)
    provider.model_message = RISK
    provider.input_script = ([event(text=RISK)] if case == 'no_vad' else [
        event(activity=('ACTIVITY_START', '0s')), event(text=RISK, finished=case != 'unfinished'),
        event(activity=('ACTIVITY_END', '0.000125s'))])
    if case == 'foreign_correction':
        provider.input_script += [event(text=' For TerraPack.', finished=True)]
    before = protected(client)
    result = tool_result(ask(client, stop_at_result=True))
    assert not any(row.get('tool') == 'read_risks' for row in result['agent_steps'])
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('before_asr', [False, True])
def test_forecast_discards_pretool_caption_and_audio_without_closing_fresh_input(workspace, before_asr):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_workspace', (FORECAST,)
    preface = event(text=None, complete=True)
    preface.server_content.output_transcription = NS(text='STALE_INVENTORY_PREFACE')
    preface.server_content.model_turn = NS(parts=[NS(inline_data=NS(
        data=PCM * 2, mime_type='audio/pcm;rate=24000'))])
    start, end = event(activity=('ACTIVITY_START', '0s')), event(activity=('ACTIVITY_END', '0.000125s'))
    caption = event(text=FORECAST, finished=True)
    provider.input_script = ([start, preface, caption, end] if before_asr else
                             [start, caption, end, .01, preface])
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    assert result['status'] == 'completed' and result['workspace']['prediction']
    assert provider.tool_response.response['required_spoken_response'] == result['assistant_text']
    assert not any(f.get('direction') == 'output' and 'STALE_INVENTORY_PREFACE' in f.get('text', '') for f in frames)
    # The fake provider's owned answer has one PCM chunk; the preface has two.
    assert sum(len(base64.b64decode(f['data'])) for f in frames if f['type'] == 'audio') == len(PCM)
    assert_audio(frames)
    assert protected(client) == before
    assert operator.calls == [] and provider.executed_messages == []


def test_inventory_then_forecast_keeps_generation_and_suppresses_old_output(workspace):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_workspace', (RISK,)
    before = protected(client)
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': base64.b64encode(PCM).decode()})
        inventory_frames = receive_turn(socket)
        inventory = tool_result(inventory_frames)
        assert inventory['workspace']['risks']
        assert_audio(inventory_frames)
        stale = event(complete=True)
        stale.server_content.output_transcription = NS(text=inventory['assistant_text'])
        stale.server_content.model_turn = NS(parts=[NS(inline_data=NS(
            data=PCM * 2, mime_type='audio/pcm;rate=24000'))])
        provider.input_script = [event(activity=('ACTIVITY_START', '0.000125s')), stale,
            event(text=FORECAST, finished=True), event(activity=('ACTIVITY_END', '0.00025s'))]
        socket.send_json({'type': 'audio', 'data': base64.b64encode(PCM).decode()})
        forecast_frames = receive_turn(socket)
        forecast = tool_result(forecast_frames)
        assert forecast['workspace']['prediction']
        assert len([f for f in forecast_frames if f['type'] == 'tool_result']) == 1
        assert_audio(forecast_frames)
        assert inventory['assistant_text'] not in ''.join(f.get('text', '') for f in forecast_frames
            if f.get('direction') == 'output')
        assert sum(len(base64.b64decode(f['data'])) for f in forecast_frames if f['type'] == 'audio') == len(PCM)
        assert provider.turn == 2
        assert provider.tool_response.id == 'operate-2'
        assert provider.tool_response.response['required_spoken_response'] == forecast['assistant_text']
        socket.send_json({'type': 'stop'})
    assert protected(client) == before
    assert operator.calls == [] and provider.executed_messages == []


def test_unfinished_forecast_correction_quarantines_pretool_speech(monkeypatch):
    import asyncio
    monkeypatch.setattr(live_voice, 'FRESH_CALL_GRACE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .01)
    store, session = setup()

    class CorrectionProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if not kwargs.get('audio'):
                return
            await self.events.put(event(activity=('ACTIVITY_START', '0s')))
            await self.events.put(event(text=FORECAST, finished=True))
            await self.events.put(event(text=' More about our forecast', finished=False))
            await self.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
            stale = event(complete=True)
            stale.server_content.output_transcription = NS(text='UNOWNED_FORECAST_PREFACE')
            stale.server_content.model_turn = NS(parts=[NS(inline_data=NS(
                data=PCM * 2, mime_type='audio/pcm;rate=24000'))])
            await self.events.put(stale)
            await self.events.put(event(call=NS(name='read_workspace', args={}, id='unfinished-forecast')))

        async def send_tool_response(self, *, function_responses):
            self.tool_response = function_responses
            await self.events.put(event(text=None, complete=True))

    async def scenario():
        socket, provider = Socket(start(session)), CorrectionProvider()
        calls = []

        async def workspace(tool, args, version, key):
            calls.append(tool)
            raise AssertionError('Incomplete ASR cannot reach a workspace tool')

        async def operate(*args):
            raise AssertionError('Incomplete ASR cannot operate')

        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        result = await next_type(socket, 'tool_result')
        assert result['result']['status'] in {'needs_clarification', 'unavailable'}
        await next_type(socket, 'turn_complete')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert not any(frame.get('direction') == 'output' and
            'UNOWNED_FORECAST_PREFACE' in frame.get('text', '') for frame in socket.history)
        assert not any(frame['type'] == 'audio' for frame in socket.history)
        assert calls == []

    asyncio.run(scenario())


def test_rejected_forecast_does_not_mute_next_fresh_no_tool_greeting(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    monkeypatch.setattr(live_voice, 'FRESH_CALL_GRACE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .01)
    store, session = setup()

    class GreetingAfterRejectionProvider(Provider):
        def __init__(self):
            super().__init__()
            self.turn = 0

        async def send_realtime_input(self, **kwargs):
            if not kwargs.get('audio'):
                return
            self.turn += 1
            if self.turn == 1:
                await self.events.put(event(activity=('ACTIVITY_START', '0s')))
                await self.events.put(event(text=FORECAST, finished=True))
                await self.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
                stale = event(complete=True)
                stale.server_content.output_transcription = NS(text='UNOWNED_FORECAST_PREFACE')
                stale.server_content.model_turn = NS(parts=[NS(inline_data=NS(
                    data=PCM * 2, mime_type='audio/pcm;rate=24000'))])
                await self.events.put(stale)
                await self.events.put(event(call=NS(name='read_workspace', args={}, id='incomplete-forecast')))
            else:
                await self.events.put(event(activity=('ACTIVITY_START', '0.000125s')))
                await self.events.put(event(text='hello', finished=True))
                await self.events.put(event(activity=('ACTIVITY_END', '0.00025s')))
                greeting = event(complete=True)
                greeting.server_content.output_transcription = NS(text='Hello!')
                greeting.server_content.model_turn = NS(parts=[NS(inline_data=NS(
                    data=PCM, mime_type='audio/pcm;rate=24000'))])
                await self.events.put(greeting)

        async def send_tool_response(self, *, function_responses):
            self.tool_response = function_responses

    async def scenario():
        socket, provider = Socket(start(session)), GreetingAfterRejectionProvider()
        calls = []
        openings = []

        @asynccontextmanager
        async def factory():
            openings.append(True)
            yield provider

        async def workspace(tool, args, version, key):
            calls.append(tool)
            assert tool == '_read_market_if_applicable'
            return {'turn_id': 'incomplete-forecast', 'company_id': session.company_id,
                'context_version': version, 'status': 'unavailable',
                'assistant_text': 'The current forecast is incomplete. Please ask again.',
                'agent_steps': [], 'evidence_ids': [], 'proposal': None, 'read_only': True}

        async def operate(*args):
            raise AssertionError('Incomplete forecast cannot operate')

        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace,
            client_factory=factory))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        denied = await next_type(socket, 'tool_result')
        assert denied['result']['status'] == 'unavailable'
        assert denied['result']['assistant_text'] == 'The current forecast is incomplete. Please ask again.'
        await next_type(socket, 'turn_complete')
        first_turn = len(socket.history)
        assert not any(frame['type'] == 'audio' for frame in socket.history)
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await next_type(socket, 'turn_complete')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        later = socket.history[first_turn:]
        assert [frame['text'] for frame in later if frame['type'] == 'transcript'
                and frame.get('direction') == 'output'] == ['Hello!']
        assert [base64.b64decode(frame['data']) for frame in later
                if frame['type'] == 'audio'] == [PCM]
        assert sum(frame['type'] == 'turn_complete' for frame in later) == 1
        assert not any(frame['type'] == 'connection_status' for frame in socket.history)
        assert not any('UNOWNED_FORECAST_PREFACE' in frame.get('text', '')
                       for frame in socket.history)
        assert calls == ['_read_market_if_applicable'] and provider.turn == 2
        assert len(openings) == 1

    asyncio.run(scenario())
