"""Regression checks for a continuous, bounded Live conversation."""
import asyncio
import base64
import json
import pytest
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS

from procure.demo import live_voice
from procure.demo.live_continuity import ReplayGuard, call_key
from test_demo_live_voice import Socket, Provider, connection, next_type, setup, start


def test_compression_and_fresh_provider_history_config():
    config = live_voice._config()
    assert config.context_window_compression.trigger_tokens > config.context_window_compression.sliding_window.target_tokens
    assert config.session_resumption is None
    assert config.history_config is None


def test_normal_audio_can_pass_old_cumulative_limit(monkeypatch):
    tick = [10_000.0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: tick[0])
    class QuietProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)
                tick[0] += 1

    async def scenario():
        store, session = setup()
        provider = QuietProvider()
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        chunk = json.dumps({'type': 'audio', 'data': base64.b64encode(b'\0' * 32_000).decode()})
        for _ in range(181):
            await socket.frames.put(chunk)
        for _ in range(200):
            if len(provider.received_audio) == 181 or task.done():
                break
            await asyncio.sleep(.01)
        assert len(provider.received_audio) == 181
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert not any(frame['type'] == 'error' for frame in socket.history)
    asyncio.run(scenario())


def test_go_away_opens_fresh_provider_with_current_company_history_only(monkeypatch):
    class QuietProvider(Provider):
        def __init__(self):
            super().__init__()

        async def send_client_content(self, *, turns, turn_complete):
            raise AssertionError('history must not become client content')

        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [
            {'channel': 'voice', 'speaker': 'input', 'question': 'Completed current-company question.'}]
        session.chat_history['other-company'] = [
            {'channel': 'voice', 'speaker': 'input', 'question': 'SECRET_OTHER_COMPANY'}]
        first, second = QuietProvider(), QuietProvider()
        first.events.put_nowait(NS(session_resumption_update=NS(
            new_handle='private-provider-handle', resumable=True), go_away=NS(time_left='5s')))
        first.events.put_nowait(NS(tool_call=NS(function_calls=[NS(
            name='read_workspace', args={}, id='old-provider-tool')]),
            server_content=NS(output_transcription=NS(text='STALE_ONLY_OLD_PROVIDER'))))
        handles = []
        contexts = []

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            handles.append(handle)
            contexts.append(history_context)
            yield first if len(handles) == 1 else second

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None))
        await next_type(socket, 'ready')
        await next_type(socket, 'connection_status')
        ready = await next_type(socket, 'connection_status')
        assert ready['state'] == 'ready'
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if second.received_audio:
                break
            await asyncio.sleep(.01)
        assert len(second.received_audio) == 1
        assert first.received_audio == []
        assert handles == [None, None]
        assert all('Completed current-company question.' in value for value in contexts)
        assert all('SECRET_OTHER_COMPANY' not in value for value in contexts)
        assert 'STALE_ONLY_OLD_PROVIDER' not in json.dumps(socket.history)
        assert not any(frame['type'] == 'tool_result' for frame in socket.history)
        assert 'private-provider-handle' not in json.dumps(socket.history)
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_rolling_tool_budget_recovers_after_window():
    tick = [0.0]
    budget = live_voice.RollingBudget(12, 180, lambda: tick[0])
    assert all(budget.allow() for _ in range(12))
    assert not budget.allow()
    tick[0] = 180.0
    assert all(budget.allow() for _ in range(12))


def test_script_processor_pcm_cadence_survives_360_seconds_with_bounded_jitter():
    # Browser createScriptProcessor(2048) at 16 kHz sends complete 100 ms
    # (1600 sample) frames. Callbacks cluster at the 180 s window edge.
    tick = [0.0]
    budget = live_voice.RollingBudget(live_voice.MAX_INPUT_BYTES, 180, lambda: tick[0])
    pending = 0
    accepted = 0
    for callback in range(int(365 * 16000 / 2048)):
        scheduled = (callback + 1) * 2048 / 16000
        # A bounded network pause makes a few ordinary frames arrive together.
        tick[0] = max(tick[0], scheduled + (.22 if callback % 701 == 700 else 0))
        pending += 2048
        while pending >= 1600:
            pending -= 1600
            assert budget.allow(3200), (tick[0], accepted, budget.total)
            accepted += 1
    assert accepted > 3600
    assert not budget.allow(live_voice.MAX_INPUT_BYTES)


def test_input_burst_headroom_still_rejects_abusive_high_speed_pcm():
    budget = live_voice.RollingBudget(live_voice.MAX_INPUT_BYTES, 180, lambda: 1.0)
    assert all(budget.allow(32_000) for _ in range(182))
    assert not budget.allow(32_000)


def test_retired_tool_id_remains_non_executable_after_result_eviction():
    guard = ReplayGuard()
    guard.remember('call-old')
    for index in range(700):
        guard.remember(f'call-{index}')
    assert guard.seen('call-old')
    assert not guard.seen('brand-new-call')


def test_same_provider_call_id_keeps_idempotency_key_after_uncertain_handoff():
    secret = b'private socket nonce'
    assert call_key(secret, 'call-1') == call_key(secret, 'call-1')
    assert call_key(secret, 'call-1') != call_key(secret, 'call-2')
    assert secret.hex() not in call_key(secret, 'call-1')


def test_passive_history_context_quotes_only_bounded_current_company_completed_history():
    store, session = setup()
    session.chat_history[session.company_id] = [
        {'channel': 'voice', 'speaker': 'input', 'question': 'Old risk question.'},
        {'channel': 'voice', 'speaker': 'output', 'answer': 'Old risk answer.'},
    ]
    session.chat_history['other-company'] = [
        {'channel': 'voice', 'speaker': 'input', 'question': 'SECRET_OTHER_COMPANY'}]
    context = live_voice._history_context(session, session.company_id)
    instruction = live_voice._config(history_context=context).system_instruction
    assert 'Old risk question.' in instruction and 'Old risk answer.' in instruction
    assert 'SECRET_OTHER_COMPANY' not in instruction
    assert 'quoted' in instruction.lower() and 'no consent' in instruction.lower()
    assert 'fresh live audio' in instruction.lower()
    assert len(context) < 7000


def test_passive_history_context_keeps_last_twelve_and_excludes_raw_turn_data():
    store, session = setup()
    session.chat_history[session.company_id] = [
        {'channel': 'voice', 'speaker': 'input',
         'question': f'caption-{index}: ' + 'x' * 700,
         'turn': {'workspace': {'private_token': 'RAW_PRIVATE_CONTEXT'}}}
        for index in range(15)]
    context = live_voice._history_context(session, session.company_id)
    assert len(context) <= 6000
    assert 'caption-0:' not in context
    assert 'caption-14:' in context
    assert 'RAW_PRIVATE_CONTEXT' not in context


def test_repeated_provider_failure_closes_and_releases_slot(monkeypatch):
    async def scenario():
        store, session = setup()
        socket = Socket(start(session))
        starts = session.live_voice_sessions

        @asynccontextmanager
        async def unavailable(company, *, handle=None, history_context=None):
            raise RuntimeError('private-provider-token')
            yield

        monkeypatch.setattr(live_voice, '_vertex_connection', unavailable)
        await asyncio.wait_for(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None), 5)
        errors = [frame for frame in socket.history if frame['type'] == 'error']
        assert len(errors) == 1 and errors[0]['code'] == 'voice_unavailable'
        assert 'private-provider-token' not in json.dumps(socket.history)
        assert socket.closed == 1000
        assert session.live_voice_sessions == starts
    asyncio.run(scenario())


def test_stop_during_provider_reconnect_delay_closes_promptly(monkeypatch):
    async def scenario():
        store, session = setup()
        first = Provider()
        first.events.put_nowait(NS(go_away=NS(time_left='1s')))

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            yield first

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None))
        await next_type(socket, 'ready')
        await next_type(socket, 'connection_status')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, .2)
        assert socket.closed == 1000
    asyncio.run(scenario())


def test_fresh_renewal_answers_new_inventory_utterance_once(monkeypatch):
    class ManualProvider(Provider):
        def __init__(self):
            super().__init__()
            self.responses = []

        async def send_client_content(self, *, turns, turn_complete):
            raise AssertionError('prior risk history must not be sent as a user turn')

        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

        async def send_tool_response(self, *, function_responses):
            self.responses.append(function_responses)
            await self.events.put(NS(tool_call=None, server_content=NS(
                output_transcription=NS(text='Fresh inventory answer.'), turn_complete=True)))

    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [
            {'channel': 'voice', 'speaker': 'input', 'question': 'Prior completed risk question.'},
            {'channel': 'voice', 'speaker': 'output', 'answer': 'Prior risk answer.'}]
        first, second = ManualProvider(), ManualProvider()
        handles = []
        contexts = []
        calls = []

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            handles.append(handle)
            contexts.append(history_context)
            yield first if len(handles) == 1 else second

        async def workspace_tool(name, args, version, key):
            calls.append((name, key))
            return {'turn_id': 'inventory-1', 'company_id': session.company_id,
                    'context_version': version, 'status': 'completed',
                    'assistant_text': 'Current inventory and sourcing read.',
                    'agent_steps': [], 'evidence_ids': [], 'proposal': None,
                    'read_only': True}

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            workspace_tools=workspace_tool))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if first.received_audio:
                break
            await asyncio.sleep(.01)
        assert len(first.received_audio) == 1
        await first.events.put(NS(session_resumption_update=NS(
            new_handle='server-secret', resumable=True), go_away=NS(time_left='1s')))
        await first.events.put(NS(tool_call=NS(function_calls=[NS(
            name='read_risks', args={'message': 'Old risk question'}, id='old-risk')]),
            server_content=NS(output_transcription=NS(text='OLD_RISK_ANSWER'))))
        await next_type(socket, 'connection_status')
        ready = await next_type(socket, 'connection_status')
        assert ready['state'] == 'ready'
        assert handles == [None, None]
        assert all('Prior completed risk question.' in value and 'Prior risk answer.' in value
                   for value in contexts)
        await socket.frames.put(json.dumps({'type': 'audio', 'data': base64.b64encode(
            b'\x01\x00\x02\x00\x03\x00\x04\x00').decode()}))
        for _ in range(100):
            if second.received_audio:
                break
            await asyncio.sleep(.01)
        assert len(second.received_audio) == 1
        await second.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0.000125s')))
        await second.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.00025s')))
        await second.events.put(NS(tool_call=NS(function_calls=[NS(
            name='read_workspace', args={}, id='new-inventory')]), server_content=None))
        await asyncio.sleep(.03)
        assert calls == []
        await second.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=NS(text='What is the current inventory and sourcing?', finished=True))))
        assert (await next_type(socket, 'tool_result'))['result']['assistant_text'] == 'Current inventory and sourcing read.'
        assert (await next_type(socket, 'transcript'))['text'] == 'What is the current inventory and sourcing?'
        assert (await next_type(socket, 'transcript'))['text'] == 'Fresh inventory answer.'
        assert len(calls) == 1 and calls[0][0] == 'read_workspace'
        assert len([response for response in second.responses if response.id == 'new-inventory']) == 1
        assert 'OLD_RISK_ANSWER' not in json.dumps(socket.history)
        assert not any(response.id == 'old-risk' for response in first.responses)
        assert 'server-secret' not in json.dumps(socket.history)
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_verified_old_consent_cannot_execute_after_provider_handoff(monkeypatch):
    class FirstProvider(Provider):
        async def send_tool_response(self, *, function_responses):
            self.tool_response = function_responses

    async def scenario():
        store, session = setup()
        first, second = FirstProvider(), Provider()
        second.events.put_nowait(NS(tool_call=NS(function_calls=[NS(
            name='confirm_research', args={'proposal_id': 'old-proposal'}, id='old-approval')]),
            server_content=None))
        calls = []

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            yield first if first.tool_response is None else second

        async def operate(*_):
            return {'turn_id': 'turn-1', 'company_id': session.company_id,
                    'context_version': 1, 'status': 'proposal',
                    'assistant_text': 'Review research.', 'agent_steps': [], 'evidence_ids': [],
                    'proposal': {'kind': 'research', 'proposal_id': 'old-proposal'},
                    'read_only': True}

        async def workspace_tool(*args):
            calls.append(args)
            raise AssertionError('old approval executed')

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        monkeypatch.setattr(live_voice, '_research_gate', lambda *_: {
            'proposal_id': 'old-proposal', 'gate_kind': 'research', 'query': 'Old query',
            'public_proposal': {'kind': 'research', 'proposal_id': 'old-proposal'},
            'armed': True, 'finalized': True, 'confirmed': True,
            'verifier_denied': False, 'consumed': False})
        monkeypatch.setattr(live_voice, '_research_current', lambda *_: True)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=operate,
            workspace_tools=workspace_tool))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await next_type(socket, 'tool_result')
        first.events.put_nowait(NS(go_away=NS(time_left='1s')))
        await next_type(socket, 'connection_status')
        await next_type(socket, 'connection_status')
        for _ in range(100):
            if second.tool_response is not None:
                break
            await asyncio.sleep(.01)
        assert second.tool_response.response['status'] == 'unavailable'
        assert calls == []
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_tool_saturation_is_temporary_and_thirteenth_later_executes(monkeypatch):
    class ManyToolProvider(Provider):
        def __init__(self):
            super().__init__()
            self.count = 0

        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                self.count += 1
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name='read_workspace', args={}, id=f'call-{self.count}')]),
                    server_content=None))

        async def send_tool_response(self, *, function_responses):
            self.tool_response = function_responses

    async def scenario():
        tick = [1_000.0]
        monkeypatch.setattr(live_voice, 'monotonic', lambda: tick[0])
        store, session = setup()
        provider = ManyToolProvider()
        socket = Socket(start(session))
        calls = []

        async def workspace_tool(name, args, version, key):
            calls.append(name)
            return {'turn_id': f'turn-{len(calls)}', 'company_id': session.company_id,
                    'context_version': version, 'status': 'completed',
                    'assistant_text': 'Current workspace read.', 'agent_steps': [],
                    'evidence_ids': [], 'proposal': None, 'read_only': True}

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            workspace_tools=workspace_tool, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        for _ in range(12):
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            assert (await next_type(socket, 'tool_result'))['result']['status'] == 'completed'
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if provider.tool_response.id == 'call-13':
                break
            await asyncio.sleep(.01)
        assert provider.tool_response.response['status'] == 'unavailable'
        tick[0] += 181
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        assert (await next_type(socket, 'tool_result'))['result']['status'] == 'completed'
        assert calls == ['read_workspace'] * 13
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert not any(frame['type'] == 'error' for frame in socket.history)
    asyncio.run(scenario())


def test_stop_during_provider_handshake_closes_promptly(monkeypatch):
    async def scenario():
        store, session = setup()

        @asynccontextmanager
        async def slow_factory(company, *, handle=None, history_context=None):
            await asyncio.sleep(1)
            yield Provider()

        monkeypatch.setattr(live_voice, '_vertex_connection', slow_factory)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None))
        await asyncio.sleep(.02)
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, .2)
        assert socket.closed == 1000
        assert session.live_voice_sessions == 0
    asyncio.run(scenario())


@pytest.mark.parametrize('call_before_end', [True, False])
def test_new_call_id_cannot_repeat_action_until_fresh_completed_speech(monkeypatch, call_before_end):
    class ManualProvider(Provider):
        def __init__(self):
            super().__init__()
            self.responses = []

        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

        async def send_tool_response(self, *, function_responses):
            self.responses.append(function_responses)

    async def scenario():
        store, session = setup()
        first, second = ManualProvider(), ManualProvider()
        opens = [0]
        applied = []
        reads = []

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            opens[0] += 1
            yield first if opens[0] == 1 else second

        async def workspace_tool(name, args, version, key):
            if name == 'fill_buyer_form':
                applied.append(key)
                return {'turn_id': f'action-{len(applied)}', 'company_id': session.company_id,
                        'context_version': version, 'status': 'completed',
                        'assistant_text': 'Buyer form updated.', 'agent_steps': [],
                        'evidence_ids': [], 'proposal': None, 'read_only': False,
                        'action_result': {'tool': 'fill_buyer_form'},
                        'session': {'session_id': session.session_id,
                                    'company_id': session.company_id, 'context_version': version}}
            reads.append(key)
            return {'turn_id': 'read-1', 'company_id': session.company_id,
                    'context_version': version, 'status': 'completed',
                    'assistant_text': 'Current workspace.', 'agent_steps': [],
                    'evidence_ids': [], 'proposal': None, 'read_only': True}

        def call(call_id, name='fill_buyer_form'):
            args = ({'material_id': 'copper', 'values': {}} if name == 'fill_buyer_form' else {})
            return NS(tool_call=NS(function_calls=[NS(name=name, args=args, id=call_id)]),
                      server_content=None)

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            workspace_tools=workspace_tool))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await first.events.put(call('initial-action'))
        assert (await next_type(socket, 'tool_result'))['result']['status'] == 'completed'
        assert len(applied) == 1
        await first.events.put(NS(go_away=NS(time_left='1s')))
        await next_type(socket, 'connection_status')
        await next_type(socket, 'connection_status')
        await second.events.put(call('different-id-same-action'))
        for _ in range(100):
            if second.responses:
                break
            await asyncio.sleep(.01)
        assert second.responses[-1].response['status'] == 'unavailable'
        assert len(applied) == 1
        await socket.frames.put('{"type":"audio","data":"AAAAAA=="}')
        await second.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=NS(text='Update the buyer form', finished=True))))
        await second.events.put(call('silence-action'))
        for _ in range(100):
            if len(second.responses) >= 2:
                break
            await asyncio.sleep(.01)
        assert second.responses[-1].response['status'] == 'unavailable'
        assert len(applied) == 1
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if len(second.received_audio) >= 2:
                break
            await asyncio.sleep(.01)
        await second.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0.000125s')))
        if not call_before_end:
            await second.events.put(NS(tool_call=None, server_content=None,
                voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.00025s')))
        await second.events.put(call('early-fresh-read', 'read_workspace'))
        await asyncio.sleep(.03)
        assert not any(response.id == 'early-fresh-read' for response in second.responses)
        await second.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=NS(text='What is current stock?', finished=True)),
            voice_activity=(NS(voice_activity_type='ACTIVITY_END', audio_offset='0.00025s')
                            if call_before_end else None)))
        assert (await next_type(socket, 'tool_result'))['result']['status'] == 'completed'
        assert len([response for response in second.responses if response.id == 'early-fresh-read']) == 1
        assert len(reads) == 1
        assert len(applied) == 1
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize('ending', ['timeout', 'interruption', 'handoff'])
def test_held_fresh_call_never_runs_without_finished_utterance(monkeypatch, ending):
    class ManualProvider(Provider):
        def __init__(self):
            super().__init__()
            self.responses = []

        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

        async def send_tool_response(self, *, function_responses):
            self.responses.append(function_responses)

    async def scenario():
        store, session = setup()
        first, second, third = ManualProvider(), ManualProvider(), ManualProvider()
        first.events.put_nowait(NS(go_away=NS(time_left='1s')))
        opens = [0]
        effects = []

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            opens[0] += 1
            yield (first, second, third)[min(opens[0] - 1, 2)]

        async def workspace_tool(name, args, version, key):
            effects.append(key)
            return {'turn_id': 'read-1', 'company_id': session.company_id,
                    'context_version': version, 'status': 'completed',
                    'assistant_text': 'Current workspace.', 'agent_steps': [],
                    'evidence_ids': [], 'proposal': None, 'read_only': True}

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        monkeypatch.setattr(live_voice, 'FRESH_CALL_GRACE_SECONDS', .12, raising=False)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            workspace_tools=workspace_tool))
        await next_type(socket, 'ready')
        await next_type(socket, 'connection_status')
        await next_type(socket, 'connection_status')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if second.received_audio:
                break
            await asyncio.sleep(.01)
        await second.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0.000125s')))
        await second.events.put(NS(tool_call=NS(function_calls=[NS(
            name='read_workspace', args={}, id='held-read')]), server_content=None))
        await asyncio.sleep(.02)
        assert second.responses == []
        if ending == 'handoff':
            await second.events.put(NS(go_away=NS(time_left='1s')))
            await next_type(socket, 'connection_status')
            await next_type(socket, 'connection_status')
            await third.events.put(NS(tool_call=None, server_content=NS(
                input_transcription=NS(text='Read workspace', finished=True))))
            await asyncio.sleep(.02)
            assert effects == []
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
            return
        if ending == 'interruption':
            await second.events.put(NS(tool_call=None, server_content=NS(interrupted=True)))
        for _ in range(100):
            if second.responses:
                break
            await asyncio.sleep(.01)
        assert second.responses and second.responses[0].response['status'] == 'unavailable'
        await second.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=NS(text='Read workspace', finished=True)),
            voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.00025s')))
        await asyncio.sleep(.02)
        assert effects == []
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_provider_setup_timeout_releases_slot_without_browser_close(monkeypatch):
    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [
            {'channel': 'voice', 'speaker': 'input', 'question': 'Previous completed question.'}]

        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            await asyncio.Event().wait()
            yield Provider()

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        monkeypatch.setattr(live_voice, 'PROVIDER_SETUP_TIMEOUT_SECONDS', .05, raising=False)
        socket = Socket(start(session))
        await asyncio.wait_for(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None), .3)
        assert [frame['code'] for frame in socket.history if frame['type'] == 'error'] == ['voice_unavailable']
        assert session.live_voice_sessions == 0
        assert socket.closed == 1000
        assert live_voice._SLOTS.acquire(blocking=False)
        assert live_voice._SLOTS.acquire(blocking=False)
        live_voice._SLOTS.release()
        live_voice._SLOTS.release()
    asyncio.run(scenario())


def test_cue_and_provider_audio_share_rolling_output_budget(monkeypatch, tmp_path):
    from test_demo_voice_cues import claimed_research, cue_file
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', .01)
    monkeypatch.setattr(live_voice, 'CUE_CHUNK_BYTES', 800)
    monkeypatch.setattr(live_voice, 'MAX_OUTPUT_BYTES', 2402)

    async def scenario():
        entered = asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            entered.set()
            await asyncio.Event().wait()

        _, session, socket, provider, _, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        await socket.next('audio')
        await socket.next('turn_complete')
        assert sum(len(base64.b64decode(frame['data'])) for frame in socket.history
                   if frame['type'] == 'audio') == 2402
        await provider.spoken('output', 'Provider audio')
        assert (await socket.next('error'))['code'] == 'voice_invalid'
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())
