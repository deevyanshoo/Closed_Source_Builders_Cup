"""Current-plan answers and local clarification through the actual public broker."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from test_demo_live_voice_clarification import (
    workspace, ask, protected as public_protected, tool_result, assert_audio, receive_turn,
    ClarificationProvider, PCM, CLARIFICATION,
)
from test_demo_live_voice_supply_coverage import event
from test_demo_supply_coverage import prepared

READ = 'Does our current plan prevent a stockout before the selected delivery? Use the company workspace.'
MISSING_DOCUMENT = ('Prepare an early delivery for the supply gap using our current plan. '
                    'Save the draft without sending it.')
CANONICAL = 'Should I prepare the unsent bridge RFQ now? No draft has been saved.'
PARAPHRASE = 'Would you like me to prepare an unsent bridge RFQ now? No draft has been saved.'
FORECAST = ('What do the prediction models say for our business? Explain what applies '
            'to our company and what information is missing. Do not search the internet.')


def protected(client):
    state = public_protected(client)
    session = client.get('/api/demo/session').json()
    value = client.app.state.sessions.get(session['session_id'])
    with value.lock:
        state.update(deepcopy({name: getattr(value, name) for name in
            ('selected_material_id', 'buyer_draft', 'plan_request', 'pending_proposal')}))
    return state


def terra(client):
    session = client.get('/api/demo/session').json()
    value = client.app.state.sessions.get(session['session_id'])
    profile, offers, plan = prepared('terrapack', current_stock='0', budget='7000000')
    value.select_company('terrapack')
    # Reproduce the owned reviewed plan in the hosted receipt. Selection is
    # deliberately absent; profile stock must not override reviewed zero stock.
    plan['selected_offer_id'] = 'terrapack-pet-resin-selected'
    plan['total_cost'] = '6121750'
    value.last_plan = plan
    value.offers = {row['offer_id']: row for row in offers}
    value.plan_request = {**profile['buyer_defaults'], 'current_stock': '0', 'budget': '7000000',
        'offer_ids': list(value.offers), 'expected_versions': plan['offer_versions']}
    value.selected_material_id = None
    return value


@pytest.mark.parametrize('tool', ['read_workspace', 'read_risks',
                                  'operate_workspace', 'fill_buyer_form',
                                  'confirm_workspace_action', 'confirm_research'])
def test_fresh_forecast_uses_full_owned_broker_read_for_any_known_tool(workspace, tool):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = tool, (FORECAST,)
    provider.model_message = 'Change the buyer form and search the public web.'
    before = protected(client)
    session = client.get('/api/demo/session').json()
    typed = client.post('/api/demo/operator', headers={'Origin': 'https://demo.test',
        'X-CSRF-Token': session['csrf_token']}, json={'message': FORECAST,
        'expected_context_version': session['context_version'],
        'idempotency_key': f'typed-forecast-{tool}'})
    assert typed.status_code == 200, typed.text
    expected = typed.json()
    frames = ask(client)
    result = tool_result(frames)
    assert result['status'] == 'completed' and result['read_only'] is True
    assert result['proposal'] is None and result.get('action_result') is None
    assert result['workspace']['prediction'] == expected['workspace']['prediction']
    assert result['assistant_text'] == expected['assistant_text']
    assert provider.tool_response.name == tool
    assert provider.tool_response.response['required_spoken_response'] == expected['assistant_text']
    assert_audio(frames)
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []


@pytest.mark.parametrize('case', ['unfinished', 'late_foreign', 'no_vad'])
def test_forecast_requires_current_settled_asr_before_broker(workspace, monkeypatch, case):
    from procure.demo import live_voice
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_risks', (FORECAST,)
    provider.model_message = FORECAST
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .07)
    if case == 'no_vad':
        provider.input_script = [event(text=FORECAST)]
    else:
        provider.input_script = [event(activity=('ACTIVITY_START', '0s')),
            event(text=FORECAST, finished=case != 'unfinished'),
            event(activity=('ACTIVITY_END', '0.000125s'))]
        if case == 'late_foreign':
            provider.input_script += [.01, event(text=' For TerraPack.')]
    before = protected(client)
    frames = ask(client)
    assert not any('prediction' in (f.get('result') or {}).get('workspace', {})
                   for f in frames if f['type'] == 'tool_result')
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []


def test_unfinished_fresh_forecast_denies_with_terminal_without_broker_or_pcm(monkeypatch):
    import asyncio
    from procure.demo import live_voice
    from test_demo_live_voice import Socket, setup, start, connection, next_type
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .01)
    store, session = setup()
    provider = ClarificationProvider((FORECAST,))
    provider.tool_name = 'read_risks'
    provider.input_script = [event(activity=('ACTIVITY_START', '0s')),
        event(text=FORECAST, finished=False), event(activity=('ACTIVITY_END', '0.000125s'))]

    async def scenario():
        socket = Socket(start(session))
        broker_calls = []

        async def workspace_tool(name, args, version, key):
            broker_calls.append(name)
            raise AssertionError('Unfinished ASR cannot reach the broker')

        async def operate(*args):
            raise AssertionError('Unfinished ASR cannot execute an action')

        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace_tool,
            client_factory=connection(provider)))
        try:
            await next_type(socket, 'ready')
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            denied = await next_type(socket, 'tool_result')
            assert denied['result']['status'] in {'needs_clarification', 'unavailable'}
            assert not denied['result'].get('workspace', {}).get('prediction')
            await next_type(socket, 'turn_complete')
            assert broker_calls == []
            assert not any(frame['type'] == 'audio' or frame.get('direction') == 'output'
                for frame in socket.history)
        finally:
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_forecast_empty_tool_completion_waits_for_owned_caption_and_pcm(workspace):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_risks', (FORECAST,)
    before = protected(client)

    async def respond(*, function_responses):
        provider.tool_response = function_responses
        await provider.events.put(event(complete=True))
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]),
            turn_complete=False)))
        await provider.events.put(NS(server_content=NS(
            output_transcription=NS(text=function_responses.response['required_spoken_response']),
            turn_complete=True)))

    provider.send_tool_response = respond
    frames = ask(client)
    assert tool_result(frames)['workspace']['prediction']
    assert_audio(frames)
    assert sum(frame['type'] == 'turn_complete' for frame in frames) == 1
    assert protected(client) == before and operator.calls == []


def test_forecast_repeated_empty_completions_expire_at_original_deadline(monkeypatch):
    import asyncio
    from time import monotonic
    from procure.demo import live_voice
    from test_demo_live_voice import Socket, setup, start, connection, next_type
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', .05)
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .001)
    store, session = setup()

    class EmptyCompletionProvider(ClarificationProvider):
        def __init__(self):
            super().__init__((FORECAST,))
            self.tool_name = 'read_risks'
            self.empty_count = 0
            self.emitter = None

        async def send_tool_response(self, *, function_responses):
            self.tool_response = function_responses

            async def emit_empties():
                for _ in range(5):
                    self.empty_count += 1
                    await self.events.put(event(complete=True))
                    await asyncio.sleep(.03)

            self.emitter = asyncio.create_task(emit_empties())

    async def scenario():
        socket, provider = Socket(start(session)), EmptyCompletionProvider()
        broker_calls = []

        async def workspace_tool(name, args, version, key):
            broker_calls.append(name)
            return {'turn_id': 'owned-forecast', 'company_id': session.company_id,
                'context_version': version, 'status': 'completed',
                'assistant_text': 'Owned forecast answer.', 'agent_steps': [],
                'evidence_ids': [], 'proposal': None, 'read_only': True,
                'workspace': {'prediction': {'status': 'MISSING_OPERATIONS_INPUT'}}}

        async def operate(*args):
            raise AssertionError('Forecast read cannot execute an action')

        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace_tool,
            client_factory=connection(provider)))
        try:
            await next_type(socket, 'ready')
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            assert (await next_type(socket, 'tool_result'))['result']['status'] == 'completed'
            started = monotonic()
            error = await next_type(socket, 'error')
            assert error['code'] == 'voice_review_incomplete'
            assert error['review']['reason'] == 'deadline'
            assert monotonic() - started < .14
            assert provider.empty_count < 5
            assert broker_calls == ['_read_market_if_applicable']
            assert not any(frame['type'] == 'audio' or frame.get('direction') == 'output'
                for frame in socket.history)
        finally:
            if provider.emitter is not None:
                provider.emitter.cancel()
                await asyncio.gather(provider.emitter, return_exceptions=True)
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


@pytest.mark.parametrize('tool', ['operate_workspace', 'read_workspace', 'analyze_inventory',
    'preview_supplier_delay', 'prepare_research', 'confirm_research',
    'confirm_workspace_action', 'read_research', 'read_risks', 'read_news',
    'read_carbon', 'read_routes', 'estimate_carbon', 'read_shipments',
    'fill_buyer_form', 'load_sample_quotes', 'review_sample_quotes',
    'compare_suppliers', 'prepare_rfq', 'prepare_simulated_email',
    'navigate_workspace'])
def test_fresh_forecast_overrides_malformed_args_for_every_known_tool(tool):
    import asyncio
    from procure.demo import live_voice
    from test_demo_live_voice import Provider, Socket, setup, start, connection, next_type
    store, session = setup()

    class WrongArgsProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if not kwargs.get('audio'):
                return
            await self.events.put(event(activity=('ACTIVITY_START', '0s')))
            await self.events.put(event(text=FORECAST))
            await self.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
            await self.events.put(event(call=NS(name=tool, args={'wrong': 'x'}, id='wrong-args')))

        async def send_tool_response(self, *, function_responses):
            self.tool_response = function_responses

    async def scenario():
        provider, socket = WrongArgsProvider(), Socket(start(session))
        calls = []

        async def workspace_tool(name, args, version, key):
            calls.append((name, args))
            return {'turn_id': 'bounded-denial', 'company_id': session.company_id,
                'context_version': version, 'status': 'needs_clarification',
                'assistant_text': 'Current forecast data is incomplete.',
                'agent_steps': [], 'evidence_ids': [], 'proposal': None, 'read_only': True}

        async def operate(*args):
            raise AssertionError('Malformed provider arguments cannot execute an action')

        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace_tool,
            client_factory=connection(provider)))
        try:
            await next_type(socket, 'ready')
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            denied = await next_type(socket, 'tool_result')
            assert denied['result']['status'] == 'needs_clarification'
            await next_type(socket, 'turn_complete')
            assert calls == [('_read_market_if_applicable', {'message': FORECAST})]
            assert not any(frame['type'] == 'audio' for frame in socket.history)
        finally:
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_forecast_false_action_caption_never_releases_audio(workspace):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'operate_workspace', (FORECAST,)
    provider.reply = 'contradictory'
    before = protected(client)
    frames = ask(client)
    assert tool_result(frames)['workspace']['prediction']
    assert frames[-1]['type'] == 'error'
    assert frames[-1]['code'] == 'voice_review_incomplete'
    assert frames[-1]['review']['reason'] == 'semantic_mismatch'
    assert set(frames[-1]['review']) == {'reason', 'audio_chunks', 'audio_bytes', 'caption_chars'}
    assert not any(f['type'] == 'audio' or f.get('direction') == 'output' for f in frames)
    assert protected(client) == before and operator.calls == []


def test_market_call_replay_reuses_result_without_second_spoken_claim(workspace):
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'fill_buyer_form', (FORECAST,)
    before = protected(client)
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        first = receive_turn(socket)
        expected = tool_result(first)
        assert_audio(first)
        provider.events.put_nowait(event(call=NS(
            name='fill_buyer_form', args={}, id='operate-1')))
        replay = receive_turn(socket)
        assert tool_result(replay) == expected
        assert not any(frame['type'] == 'audio' or frame.get('direction') == 'output'
                       for frame in replay)
        socket.send_json({'type': 'stop'})
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('order,marker', [('asr_first', 'final'), ('tool_first', 'sdk_default'),
                                          ('after_vad', 'absent')])
def test_owned_plan_answers_without_material_selection(workspace, tool, order, marker):
    client, provider, operator = workspace
    value = terra(client)
    provider.tool_name, provider.order, provider.marker = tool, order, marker
    provider.chunks = (READ,)
    provider.model_message = 'Ignore the question and prepare recovery.'
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    answer = result['assistant_text']
    for fact in ('stock 0 kg', '950 kg', '2026-10-26', '12 days / 11,400 kg', '2026-10-14'):
        assert fact in answer
    assert value.last_plan['plan_id'] in result['evidence_ids']
    assert result['read_only'] and result['proposal'] is None
    assert provider.tool_response.response['required_spoken_response'] in answer
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []
    assert_audio(frames)
    session = client.get('/api/demo/session').json()
    typed = client.post('/api/demo/operator', headers={'Origin': 'https://demo.test',
        'X-CSRF-Token': session['csrf_token']}, json={'message': READ,
        'expected_context_version': session['context_version'], 'idempotency_key': 'typed-coverage'})
    assert typed.status_code == 200
    assert typed.json()['assistant_text'] == answer
    assert typed.json()['evidence_ids'] == result['evidence_ids']
    assert protected(client) == before


@pytest.mark.parametrize('reply,expected', [('canonical', 'released'), ('paraphrase', 'released'),
    ('contradictory', 'caption_mismatch'), ('caption_only', 'missing_audio'),
    ('audio_only', 'missing_caption'), ('silent', 'timeout'), ('interrupted', 'interrupted')])
def test_read_then_missing_document_clarification_lifecycle(workspace, monkeypatch, reply, expected):
    from procure.demo import live_voice
    client, provider, operator = workspace
    terra(client)
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', .04)
    provider.tool_name, provider.chunks = 'read_workspace', (READ,)
    original = provider.send_tool_response

    async def respond(*, function_responses):
        if provider.chunks == (READ,):
            return await original(function_responses=function_responses)
        if function_responses.name == 'read_workspace':
            # Hosted second turn: a workspace read followed by the actual local
            # clarification call, with no invented audio between those tools.
            await provider.events.put(event(call=NS(name='operate_workspace',
                args={'message': MISSING_DOCUMENT}, id='document-clarification')))
            return
        provider.tool_response = function_responses
        text = PARAPHRASE if reply == 'paraphrase' else CANONICAL
        if reply == 'contradictory':
            text += ' I have sent it to the supplier.'
        if reply == 'silent':
            return
        if reply != 'caption_only':
            await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
                inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]),
                interrupted=reply == 'interrupted')))
        await provider.events.put(NS(server_content=NS(turn_complete=True,
            output_transcription=NS(text=text) if reply != 'audio_only' else None)))

    provider.send_tool_response = respond
    session = client.get('/api/demo/session').json()
    before = protected(client)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                         'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        receive_turn(socket)
        provider.chunks = (MISSING_DOCUMENT,)
        provider.tool_name = 'read_workspace'
        # The second utterance has its own provider-global audio offsets.
        provider.input_script = [event(activity=('ACTIVITY_START', '0.000125s')),
            event(text=MISSING_DOCUMENT), event(activity=('ACTIVITY_END', '0.00025s'))]
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        frames = receive_turn(socket)
        results = [f['result'] for f in frames if f['type'] == 'tool_result']
        assert len(results) == 2
        assert results[-1]['assistant_text'] == CLARIFICATION
        assert results[-1]['read_only'] and results[-1]['proposal'] is None
        if expected == 'released':
            assert_audio(frames)
        else:
            assert not any(f['type'] == 'audio' for f in frames)
            error = next(f for f in frames if f['type'] == 'error')
            assert error['review']['reason'] == expected
            assert set(error['review']) == {'reason', 'audio_chunks', 'audio_bytes', 'caption_chars'}
        assert not any(f.get('state') == 'awaiting_consent' for f in frames)
        socket.send_json({'type': 'stop'})
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('case', ['unknown_active', 'foreign_question', 'foreign_plan',
    'stale_plan_id', 'stale_quote', 'explicit_selection'])
def test_coverage_rejections_never_substitute_known_baseline(workspace, tool, case):
    from procure.demo.recovery import compare
    from datetime import date
    client, provider, operator = workspace
    value = terra(client)
    provider.tool_name, provider.chunks = tool, (READ,)
    if case == 'unknown_active':
        value.recovery = compare(value.last_plan, value.plan_request, list(value.offers.values()),
            {value.last_plan['selected_offer_id']: 1}, today=date(2026, 10, 14))
        value.recovery['scenario']['plan_id'] = 'unknown-active'
        value.recovery['scenario']['current_stock'] = None
        value.recovery['scenario']['current_stock_kg'] = None
    elif case == 'foreign_question':
        provider.chunks = (READ + ' For Solara Cables.',)
    elif case == 'foreign_plan':
        value.last_plan['company_id'] = 'solara-cables'
    elif case == 'stale_plan_id':
        provider.chunks = (READ + ' Use plan ID obsolete-plan.',)
    elif case == 'stale_quote':
        value.offers[value.last_plan['selected_offer_id']]['version'] += 1
    else:
        provider.chunks = ('Will the selected material have a supply gap?',)
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification'
    assert result['read_only'] and result['proposal'] is None
    assert '11,400' not in result['assistant_text']
    if case == 'unknown_active':
        assert 'unknown for the active plan' in result['assistant_text']
        assert 'unknown-active' in result['evidence_ids']
        assert value.last_plan['plan_id'] not in result['evidence_ids']
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('case', ['late_foreign', 'late_false', 'no_vad', 'closed_turn'])
def test_current_asr_guards_cannot_reuse_grounded_coverage(workspace, monkeypatch, tool, case):
    from procure.demo import live_voice
    client, provider, operator = workspace
    terra(client)
    provider.tool_name, provider.chunks = tool, (READ,)
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .08)
    script = [event(activity=('ACTIVITY_START', '0s')), event(text=READ),
              event(activity=('ACTIVITY_END', '0.000125s'))]
    if case == 'late_foreign':
        script += [.01, event(text=' For Solara Cables.')]
    elif case == 'late_false':
        frame = event()
        frame.server_content.input_transcription = NS(text=None, finished=False)
        script += [.04, frame]
    elif case == 'no_vad':
        script = [event(text=READ)]
    else:
        script += [.04, event(complete=True)]
    provider.input_script = script
    before = protected(client)
    frames = ask(client)
    assert not any('11,400' in f.get('text', '') for f in frames)
    assert provider.tool_response.response.get('required_spoken_response', '') != READ
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('words', [
    'Should I prepare the bridge RFQ now? No draft has been saved.',
    'Should I prepare the unsent bridge RFQ now?',
    'Would you like me to send an unsent bridge RFQ now? No draft has been saved.',
    'Would you like me to prepare an unsent bridge RFQ now? A draft has been saved.',
    PARAPHRASE + ' I placed the order.',
])
def test_complete_clarification_grammar_rejects_unsafe_additions(workspace, words):
    client, provider, operator = workspace
    provider.chunks = ('Prepare a bridge RFQ if the supplier agrees.',)
    async def respond(*, function_responses):
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]),
            output_transcription=NS(text=words), turn_complete=True)))
    provider.send_tool_response = respond
    before = protected(client)
    frames = ask(client)
    assert tool_result(frames)['assistant_text'] == CANONICAL
    assert frames[-1]['review']['reason'] == 'caption_mismatch'
    assert not any(f['type'] == 'audio' or f.get('state') == 'awaiting_consent' for f in frames)
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
def test_coverage_renewal_requires_fresh_audio_and_vad(workspace, tool):
    client, provider, operator = workspace
    terra(client)
    provider.tool_name, provider.marker, provider.chunks = tool, 'sdk_default', (READ,)
    provider.renew_first = True
    session = client.get('/api/demo/session').json()
    before = protected(client)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                         'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        assert socket.receive_json()['state'] == 'reconnecting'
        assert socket.receive_json()['state'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        frames = receive_turn(socket)
        assert '12 days / 11,400 kg' in tool_result(frames)['assistant_text']
        assert_audio(frames)
        # Replay after completion: audio arrives but no new VAD/ASR. A new call
        # ID cannot borrow the previous generation's grounded answer.
        provider.stale = True
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        # A renewed transport denies stale calls without publishing a UI result.
        frames = []
        while not frames or frames[-1]['type'] != 'turn_complete':
            frames.append(socket.receive_json())
        assert provider.tool_response.id == 'operate-3'
        assert provider.tool_response.response['status'] == 'unavailable'
        assert provider.tool_response.response['action_applied'] is False
        assert not any(f['type'] == 'tool_result' or '11,400' in f.get('text', '') for f in frames)
        socket.send_json({'type': 'stop'})
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []


@pytest.mark.parametrize('verb', ['Request', 'Create', 'Prepare'])
@pytest.mark.parametrize('qualification', ['explicit', 'negated', 'conditional'])
def test_bridge_imperatives_keep_existing_guarded_route(workspace, verb, qualification):
    client, provider, operator = workspace
    words = f'{verb} a quotation for the supply gap using our current plan.'
    if qualification == 'negated':
        words = 'Do not ' + words[0].lower() + words[1:]
    elif qualification == 'conditional':
        words = words[:-1] + ' if the supplier agrees.'
    provider.tool_name, provider.chunks = 'operate_workspace', (words,)
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    # Actual public socket -> broker -> guarded operator, never the private read.
    assert provider.executed_messages == [words]
    assert operator.calls == []
    if qualification == 'explicit':
        assert result['status'] == 'completed' and result['read_only'] is False
        assert result['action_result']['tool'] == 'prepare_rfq'
        after = protected(client)
        assert after['rfq']['bridge'] is True and after['rfq']['status'] == 'draft_unsent'
        assert len(after['drafts']) == 1 and after['simulated_outbox'] == []
        assert after['plan'] == before['plan']
        assert after['selected_material_id'] == before['selected_material_id']
    else:
        assert result['status'] == 'needs_clarification' and result['read_only'] is True
        assert result['proposal'] is None and result.get('action_result') is None
        assert protected(client) == before
    assert_audio(frames)


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('coverage', ['covered', 'gap', 'unknown'])
def test_required_speech_preserves_complete_coverage_decision(workspace, tool, coverage):
    client, provider, operator = workspace
    value = terra(client)
    if coverage == 'covered':
        profile, offers, value.last_plan = prepared('terrapack')
        value.offers = {row['offer_id']: row for row in offers}
        value.plan_request = {**profile['buyer_defaults'], 'offer_ids': list(value.offers),
            'expected_versions': value.last_plan['offer_versions']}
    elif coverage == 'unknown':
        value.last_plan['current_stock'] = value.last_plan['current_stock_kg'] = None
    provider.tool_name, provider.chunks = tool, (READ,)
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    required = provider.tool_response.response['required_spoken_response']
    if coverage == 'covered':
        assert 'No supply gap is projected; this plan covers continuous supply through 2026-11-05.' in required
    elif coverage == 'gap':
        assert 'A projected supply gap exists.' in required
        assert '12 days / 11,400 kg' in required
        assert 'stock 0 kg' in required and '950 kg' in required
        assert '2026-10-26' in required and '2026-10-14' in required
    else:
        assert 'Supply coverage is unknown for the active plan.' in required
        assert 'Next: provide or review current_stock.' in required
        assert result['status'] == 'needs_clarification'
    assert required == result['assistant_text']
    assert result['read_only'] and result['proposal'] is None
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []
    assert_audio(frames)


# Fake caption reviewer acceptance exercises the release gate only, not real Gemini/language acceptance.
@pytest.mark.parametrize('language,caption', [
    ('Hindi', 'वर्तमान दिनांकित आपूर्ति मार्ग में 2026-10-20 से स्टॉक की कमी रहती है; प्रतिबद्ध होने से पहले फिर से योजना बनाएं। Solara Cables तांबा: बाजार संदर्भ 2026-10-16 का है और उसका प्रभाव केवल संदर्भ है। स्वीकृत कोटेशन लागतें स्थिर रहती हैं। कोई ऑर्डर या मुद्रा विनिमय नहीं हुआ।'),
    ('Portuguese', 'O caminho de fornecimento atual e datado deixa uma falta de estoque a partir de 2026-10-20; refaça o planejamento antes de se comprometer. Solara Cables cobre: a referência de mercado é de 2026-10-16 e sua influência é apenas contexto. Os custos das cotações aceitas permanecem fixos. Nenhum pedido ou câmbio foi realizado.'),
])
def test_market_translated_caption_releases_only_after_fake_review(workspace, monkeypatch, language, caption):
    from procure.demo import live_voice
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_risks', (FORECAST + f' Answer in {language}.',)
    calls = []
    async def verify(utterance, canonical, actual):
        calls.append((utterance, canonical, actual))
        return True
    original_run = live_voice.run_live_voice
    async def inject(*args, **kwargs):
        kwargs['verify_market_caption'] = verify
        return await original_run(*args, **kwargs)
    monkeypatch.setattr(live_voice, 'run_live_voice', inject)
    async def respond(*, function_responses):
        provider.tool_response = function_responses
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]))))
        await provider.events.put(NS(server_content=NS(turn_complete=True,
            output_transcription=NS(text=caption))))
    provider.send_tool_response = respond
    before = protected(client)
    frames = ask(client)
    assert_audio(frames)
    assert calls == [(provider.chunks[0], tool_result(frames)['assistant_text'], caption)]
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('case', ['contradictory', 'missing_qualification', 'additional_number',
    'unavailable', 'wrong_language', 'english_for_hindi', 'timeout'])
def test_market_caption_negative_reviews_never_release_audio(workspace, monkeypatch, case):
    import asyncio
    from procure.demo import live_voice
    client, provider, operator = workspace
    provider.tool_name, provider.chunks = 'read_risks', (FORECAST + (' Answer in Hindi.' if case == 'english_for_hindi' else ''),)
    monkeypatch.setattr(live_voice, 'CAPTION_REVIEW_TIMEOUT', .01)
    called = []
    async def verify(*texts):
        called.append(texts)
        if case == 'timeout':
            await asyncio.sleep(1)
        if case == 'unavailable':
            raise RuntimeError('unavailable')
        return case == 'additional_number'  # Even an approving reviewer cannot waive extra digits.
    original_run = live_voice.run_live_voice
    async def inject(*args, **kwargs):
        kwargs['verify_market_caption'] = verify
        return await original_run(*args, **kwargs)
    monkeypatch.setattr(live_voice, 'run_live_voice', inject)
    async def respond(*, function_responses):
        provider.tool_response = function_responses
        canonical = function_responses.response['required_spoken_response']
        text = {'contradictory': canonical + ' I placed the order.',
            'missing_qualification': canonical.replace('and its influence is context only.', '').replace('Accepted quote costs stay fixed.', ''),
            'additional_number': canonical + ' Savings are 500.',
            'wrong_language': 'Nenhum pedido foi realizado. Referências 2026-10-20 e 2026-10-16.',
            'english_for_hindi': canonical}.get(case, canonical + ' Unverified paraphrase.')
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]))))
        await provider.events.put(NS(server_content=NS(turn_complete=True,
            output_transcription=NS(text=text))))
    provider.send_tool_response = respond
    before = protected(client)
    frames = ask(client)
    assert frames[-1]['code'] == 'voice_review_incomplete'
    assert not any(f['type'] == 'audio' or f.get('direction') == 'output' for f in frames)
    assert bool(called) is (case != 'additional_number')
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('cancel', ['interrupted', 'timeout', 'new_vad', 'review_inflight'])
def test_cancelled_market_review_does_not_taint_next_ordinary_read(workspace, monkeypatch, cancel):
    from procure.demo import live_voice
    client, provider, operator = workspace
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', .04)
    provider.tool_name, provider.chunks = 'read_risks', (FORECAST,)
    provider.reply = 'interrupted' if cancel == 'interrupted' else 'silent'
    before = protected(client)
    session = client.get('/api/demo/session').json()
    original = provider.send_tool_response
    import asyncio
    from threading import Event
    reviewing, cancelled = Event(), Event()
    if cancel == 'review_inflight':
        monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', 2)
        async def verify(*_):
            reviewing.set()
            try:
                await asyncio.sleep(1)
                return True
            except asyncio.CancelledError:
                cancelled.set()
                raise
        original_run = live_voice.run_live_voice
        async def inject(*args, **kwargs):
            kwargs['verify_market_caption'] = verify
            return await original_run(*args, **kwargs)
        monkeypatch.setattr(live_voice, 'run_live_voice', inject)
    async def respond(*, function_responses):
        if provider.chunks == (FORECAST,):
            if cancel == 'review_inflight':
                text = 'Market answer: ' + function_responses.response['required_spoken_response']
                await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
                    inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]))))
                await provider.events.put(NS(server_content=NS(turn_complete=True,
                    output_transcription=NS(text=text))))
                return
            return await original(function_responses=function_responses)
        provider.tool_response = function_responses
        # Ordinary coverage uses the existing grounded containment review.
        text = 'Current workspace: ' + function_responses.response['required_spoken_response']
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]))))
        await provider.events.put(NS(server_content=NS(turn_complete=True,
            output_transcription=NS(text=text))))
    provider.send_tool_response = respond
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
            headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
            'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        first = receive_turn(socket, stop_at_result=cancel in {'new_vad', 'review_inflight'})
        if cancel == 'review_inflight':
            assert reviewing.wait(2)
        if cancel == 'timeout':
            assert first[-1]['code'] == 'voice_review_incomplete'
        provider.tool_name, provider.chunks, provider.reply = 'read_workspace', (READ,), 'grounded'
        provider.input_script = [event(activity=('ACTIVITY_START', '0.000125s')),
            event(text=READ), event(activity=('ACTIVITY_END', '0.00025s'))]
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        assert_audio(receive_turn(socket))
        if cancel == 'review_inflight':
            assert cancelled.wait(2)
        socket.send_json({'type': 'stop'})
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('change', ['faithful', 'additional_claim', 'additional_number', 'continuous'])
def test_market_caption_changed_during_review_rechecks_bounded_current_caption(workspace, monkeypatch, change):
    """A delayed stale approval cannot release PCM or strand its done task."""
    import asyncio
    from procure.demo import live_voice
    client, provider, operator = workspace
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', .25)
    provider.tool_name, provider.chunks = 'read_risks', (FORECAST,)
    calls, initial = [], []
    suffix = {'faithful': ' No business action was taken.',
        'additional_claim': ' I sent the order.', 'additional_number': ' Savings are 999.',
        'continuous': ' No business action was taken.'}[change]
    async def verify(utterance, canonical, caption):
        calls.append(caption)
        if len(calls) == 1 or change == 'continuous':
            # Feed the next completed caption while this exact task is held.
            await provider.events.put(NS(server_content=NS(
                output_transcription=NS(text=suffix if len(calls) == 1 else ' Accepted quote costs remain fixed.'),
                turn_complete=True)))
            await asyncio.sleep(.02)
        return change != 'additional_claim' or len(calls) == 1
    original_run = live_voice.run_live_voice
    async def inject(*args, **kwargs):
        kwargs['verify_market_caption'] = verify
        return await original_run(*args, **kwargs)
    monkeypatch.setattr(live_voice, 'run_live_voice', inject)
    async def respond(*, function_responses):
        provider.tool_response = function_responses
        initial.append('Market answer: ' + function_responses.response['required_spoken_response'])
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]))))
        await provider.events.put(NS(server_content=NS(
            output_transcription=NS(text=initial[0]), turn_complete=True)))
    provider.send_tool_response = respond
    before = protected(client)
    frames = ask(client)
    if change == 'faithful':
        assert_audio(frames)
        assert calls == [initial[0], initial[0] + suffix]
        assert [f['text'] for f in frames if f.get('direction') == 'output'] == [initial[0] + suffix]
    else:
        assert frames[-1]['code'] == 'voice_review_incomplete'
        assert not any(f['type'] == 'audio' or f.get('direction') == 'output' for f in frames)
        assert len(calls) == (1 if change == 'additional_number' else 2)
        if change == 'continuous':
            assert 'unavailable' not in frames[-1]['message']  # Exhaustion rejects before the original deadline.
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []
    assert not any(f.get('state') == 'awaiting_consent' for f in frames)
