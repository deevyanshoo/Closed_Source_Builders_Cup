"""Offline contract checks for the bounded Gemini Live bridge."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from procure.demo import live_voice
from procure.demo.sessions import SessionStore


class Socket:
    def __init__(self, *frames):
        self.frames = asyncio.Queue()
        for frame in frames:
            self.frames.put_nowait(frame)
        self.sent = asyncio.Queue()
        self.history = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        return await self.frames.get()

    async def send_json(self, value):
        self.history.append(value)
        await self.sent.put(value)

    async def close(self, *, code):
        self.closed = code


class Provider:
    def __init__(self):
        self.events = asyncio.Queue()
        self.received_audio = []
        self.tool_response = None

    async def send_realtime_input(self, **kwargs):
        if audio := kwargs.get('audio'):
            self.received_audio.append(audio)
            await self.events.put(NS(tool_call=None, server_content=None,
                voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
            await self.events.put(NS(tool_call=None, server_content=NS(
                input_transcription=NS(text='Watch copper', finished=True))))
            await self.events.put(NS(tool_call=None, server_content=None,
                voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.000125s')))
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='operate_workspace', args={'message': 'Watch copper'}, id='call-1')]),
                server_content=None))

    async def send_tool_response(self, *, function_responses):
        self.tool_response = function_responses
        await self.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=None, output_transcription=NS(text='Review this watch proposal.'),
            interrupted=False, model_turn=NS(parts=[NS(inline_data=NS(
                data=b'\x01\x00\x02\x00', mime_type='audio/pcm;rate=24000'))]),
            turn_complete=True)))

    async def receive(self):
        event = await self.events.get()
        yield event


def start(session, context=1, token=None):
    import json
    return json.dumps({'type': 'start', 'csrf_token': token or session.csrf_token,
                       'expected_context_version': context})


def setup():
    store = SessionStore(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc))
    return store, store.create()


def connection(provider):
    @asynccontextmanager
    async def factory():
        yield provider
    return factory


async def next_type(socket, kind):
    while True:
        event = await asyncio.wait_for(socket.sent.get(), 2)
        if event['type'] == kind:
            return event


def test_live_audio_tool_result_and_transcriptions():
    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), Provider()
        result = {'turn_id': 'turn-1', 'company_id': session.company_id,
                  'context_version': 1, 'status': 'proposal',
                  'assistant_text': 'A copper watch is ready for review.',
                  'agent_steps': [], 'evidence_ids': [],
                  'proposal': {'proposal_id': 'review-1'}, 'read_only': True}
        calls = []

        async def operate(message, version, key):
            calls.append((message, version, key))
            return result

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=operate,
            client_factory=connection(provider)))
        ready = await next_type(socket, 'ready')
        assert ready['output_mime_type'] == 'audio/pcm;rate=24000'
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        assert (await next_type(socket, 'tool_result'))['result'] == result
        assert (await next_type(socket, 'audio'))['data'] == base64.b64encode(b'\x01\x00\x02\x00').decode()
        await next_type(socket, 'turn_complete')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert calls[0][:2] == ('Watch copper', 1)
        assert calls[0][2].startswith('live-')
        assert provider.received_audio[0].mime_type == 'audio/pcm;rate=16000'
        assert provider.tool_response.response == result
        assert {'type': 'transcript', 'direction': 'input', 'text': 'Watch copper'} in socket.history
        assert {'type': 'transcript', 'direction': 'output',
                'text': 'Review this watch proposal.'} in socket.history
        assert any(event['type'] == 'audio' and event['sample_rate_hertz'] == 24000
                   for event in socket.history)
        assert any(event['type'] == 'metrics' and event['input_audio_bytes'] == 4
                   and event['output_audio_bytes'] == 4 for event in socket.history)
        assert socket.closed == 1000
    asyncio.run(scenario())


def test_live_research_preparation_delegates_without_search_or_voice_approval():
    class ResearchProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name='prepare_research',
                    args={'message': 'How would a Hormuz blockage affect petrochemicals today?'},
                    id='research-call')]), server_content=None))

    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), ResearchProvider()
        calls = []
        result = {'turn_id': 'research-turn', 'company_id': session.company_id,
                  'context_version': 1, 'status': 'proposal',
                  'assistant_text': 'Review the exact public query before searching.',
                  'agent_steps': [], 'evidence_ids': [],
                  'proposal': {'proposal_id': 'research-proposal', 'kind': 'research'},
                  'read_only': True}

        async def workspace_tool(name, args, version, key):
            calls.append((name, args, version, key))
            return result

        async def forbidden_operate(*_):
            raise AssertionError('operator model should not handle research preparation')

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=forbidden_operate,
            workspace_tools=workspace_tool, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        assert (await next_type(socket, 'tool_result'))['result'] == result
        assert calls[0][0] == 'prepare_research'
        assert calls[0][1] == {'message': 'How would a Hormuz blockage affect petrochemicals today?'}
        assert provider.tool_response.response['status'] == 'proposal'
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_invalid_csrf_denies_provider_connection(monkeypatch):
    async def scenario():
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, session = setup()
        socket = Socket(start(session, token='incorrect'))
        opened = False

        @asynccontextmanager
        async def factory():
            nonlocal opened
            opened = True
            yield Provider()

        await live_voice.run_live_voice(socket, session=session, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=factory)
        assert (await next_type(socket, 'error'))['code'] == 'voice_invalid'
        assert not opened and socket.closed == 1000
        assert session.live_voice_sessions == 0
        assert session.live_voice_starts_by_company == {}
        assert live_voice._PROCESS_STARTS == 0
    asyncio.run(scenario())


def test_stale_hello_does_not_count_a_start(monkeypatch):
    async def scenario():
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, session = setup()
        socket = Socket(start(session, context=2))
        await live_voice.run_live_voice(socket, session=session, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=connection(Provider()))
        assert (await next_type(socket, 'error'))['code'] == 'stale_context'
        assert session.live_voice_sessions == 0
        assert session.live_voice_starts_by_company == {}
        assert live_voice._PROCESS_STARTS == 0
    asyncio.run(scenario())


def test_workspace_switch_closes_existing_live_session():
    async def scenario():
        store, session = setup()
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        await next_type(socket, 'ready')
        with session.lock:
            session.select_company('terrapack')
        assert (await next_type(socket, 'error'))['code'] == 'stale_context'
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_invalid_audio_frame_closes_without_forwarding_bytes():
    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), Provider()
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"%%%"}')
        assert (await next_type(socket, 'error'))['code'] == 'voice_invalid'
        await asyncio.wait_for(task, 2)
        assert provider.received_audio == []
    asyncio.run(scenario())


def test_expired_session_closes_open_socket():
    async def scenario():
        store, session = setup()
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        await next_type(socket, 'ready')
        store.invalidate(session)
        assert (await next_type(socket, 'error'))['code'] == 'stale_context'
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_repeated_provider_call_id_reuses_verified_operator_turn():
    class DuplicateProvider(Provider):
        def __init__(self):
            super().__init__()
            self.responses = []

        async def send_tool_response(self, *, function_responses):
            self.responses.append(function_responses.response)
            await super().send_tool_response(function_responses=function_responses)

        async def send_realtime_input(self, **kwargs):
            await super().send_realtime_input(**kwargs)
            if kwargs.get('audio'):
                self.events.put_nowait(NS(tool_call=NS(function_calls=[NS(
                    name='operate_workspace', args={'message': 'Watch copper'}, id='call-1')]),
                    server_content=None))

    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), DuplicateProvider()
        keys = []

        async def operate(message, context_version, key):
            keys.append(key)
            return {'turn_id': 'one', 'company_id': session.company_id,
                    'context_version': 1, 'status': 'unavailable',
                    'assistant_text': 'No verified result.', 'agent_steps': [],
                    'evidence_ids': [], 'proposal': None, 'read_only': True}

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=operate,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await next_type(socket, 'tool_result')
        # Pending retries coalesce; a retry after completion replays the cache.
        await provider.events.put(NS(tool_call=NS(function_calls=[NS(
            name='operate_workspace', args={'message': 'Watch copper'}, id='call-1')]),
            server_content=None))
        await next_type(socket, 'tool_result')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert len(keys) == 1
        assert len(provider.responses) == 2
        assert provider.responses[0] == provider.responses[1]
        assert provider.responses[0]["turn_id"] == "one"
    asyncio.run(scenario())


def test_context_switch_during_tool_call_discards_stale_result():
    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), Provider()
        entered, release = asyncio.Event(), asyncio.Event()

        async def operate(*_):
            entered.set()
            await release.wait()
            return {'turn_id': 'stale', 'company_id': session.company_id,
                    'context_version': 1, 'status': 'unavailable',
                    'assistant_text': 'Stale response.', 'agent_steps': [],
                    'evidence_ids': [], 'proposal': None, 'read_only': True}

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store, operate_workspace=operate,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(entered.wait(), 2)
        with session.lock:
            session.select_company('terrapack')
        release.set()
        assert (await next_type(socket, 'error'))['code'] == 'stale_context'
        await asyncio.wait_for(task, 2)
        assert not any(event['type'] == 'tool_result' for event in socket.history)
    asyncio.run(scenario())


def test_repeated_company_starts_preserve_workspace_and_admit_persisted_count(monkeypatch):
    async def scenario():
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, session = setup()
        session.live_voice_sessions = 6
        session.live_voice_starts_by_company['solara-cables'] = 6
        session.watch_items.append({'material_id': 'copper'})
        for _ in range(7):
            socket = Socket(start(session))
            task = asyncio.create_task(live_voice.run_live_voice(
                socket, session=session, store=store, operate_workspace=lambda *_: None,
                client_factory=connection(Provider())))
            await next_type(socket, 'ready')
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
        assert session.live_voice_sessions == 13
        assert session.live_voice_starts_by_company['solara-cables'] == 13
        assert session.watch_items == [{'material_id': 'copper'}]

        with session.lock:
            session.select_company('terrapack')
        terra = Socket(start(session, context=2))
        terra_task = asyncio.create_task(live_voice.run_live_voice(
            terra, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        assert (await next_type(terra, 'ready'))['context_version'] == 2
        await terra.frames.put('{"type":"stop"}')
        await asyncio.wait_for(terra_task, 2)
        assert session.live_voice_sessions == 14
        assert session.live_voice_starts_by_company == {'solara-cables': 13, 'terrapack': 1}

        with session.lock:
            session.select_company('solara-cables')
        returned = Socket(start(session, context=3))
        returned_task = asyncio.create_task(live_voice.run_live_voice(
            returned, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        assert (await next_type(returned, 'ready'))['context_version'] == 3
        await returned.frames.put('{"type":"stop"}')
        await asyncio.wait_for(returned_task, 2)
        assert session.live_voice_sessions == 15
        assert session.live_voice_starts_by_company == {'solara-cables': 14, 'terrapack': 1}
        assert session.watch_items == [{'material_id': 'copper'}]
    asyncio.run(scenario())


def test_provider_startup_failure_does_not_exhaust_retry_budget(monkeypatch):
    async def scenario():
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, session = setup()

        @asynccontextmanager
        async def unavailable():
            raise RuntimeError('private provider detail')
            yield

        for _ in range(7):
            socket = Socket(start(session))
            await live_voice.run_live_voice(socket, session=session, store=store,
                                            operate_workspace=lambda *_: None,
                                            client_factory=unavailable)
            assert (await next_type(socket, 'error'))['code'] == 'voice_unavailable'
        assert session.live_voice_starts_by_company.get(session.company_id, 0) == 0
        assert session.live_voice_sessions == 0
        assert live_voice._PROCESS_STARTS == 0
        retry = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            retry, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        assert (await next_type(retry, 'ready'))['context_version'] == 1
        await retry.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_process_start_budget_resets_after_one_hour(monkeypatch):
    async def scenario():
        tick = [1000.0]
        monkeypatch.setattr(live_voice, 'monotonic', lambda: tick[0])
        monkeypatch.setattr(live_voice, '_PROCESS_WINDOW_STARTED', 1000.0)
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, session = setup()
        for _ in range(24):
            socket = Socket(start(session))
            task = asyncio.create_task(live_voice.run_live_voice(
                socket, session=session, store=store, operate_workspace=lambda *_: None,
                client_factory=connection(Provider())))
            await next_type(socket, 'ready')
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
        blocked = Socket(start(session))
        await live_voice.run_live_voice(blocked, session=session, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=connection(Provider()))
        error = await next_type(blocked, 'error')
        assert error['code'] == 'voice_limit'
        assert 'temporary' in error['message'].lower()
        assert 'shared' in error['message'].lower()
        assert 'service' in error['message'].lower()
        assert 'refresh' not in error['message'].lower()
        assert 'company' not in error['message'].lower()
        assert 'demo' not in error['message'].lower()
        assert session.live_voice_sessions == 24
        tick[0] += 3601
        retry = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(
            retry, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        await next_type(retry, 'ready')
        await retry.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert session.live_voice_sessions == 25
    asyncio.run(scenario())


def test_concurrent_slot_is_released_after_disconnect(monkeypatch):
    async def scenario():
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, one = setup()
        two, three = store.create(), store.create()

        def launch(session):
            socket = Socket(start(session))
            task = asyncio.create_task(live_voice.run_live_voice(
                socket, session=session, store=store, operate_workspace=lambda *_: None,
                client_factory=connection(Provider())))
            return socket, task

        first, first_task = launch(one)
        second, second_task = launch(two)
        await next_type(first, 'ready')
        await next_type(second, 'ready')
        blocked = Socket(start(three))
        await live_voice.run_live_voice(blocked, session=three, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=connection(Provider()))
        assert (await next_type(blocked, 'error'))['code'] == 'voice_busy'
        await first.frames.put('{"type":"stop"}')
        await asyncio.wait_for(first_task, 2)
        retry, retry_task = launch(three)
        await next_type(retry, 'ready')
        await retry.frames.put('{"type":"stop"}')
        await second.frames.put('{"type":"stop"}')
        await asyncio.wait_for(retry_task, 2)
        await asyncio.wait_for(second_task, 2)
        assert all(task.done() for task in (first_task, second_task, retry_task))
    asyncio.run(scenario())


def test_malformed_and_oversize_pcm_rejected():
    for data in ('%%%', base64.b64encode(b'\x00').decode(),
                 base64.b64encode(b'\x00' * (live_voice.MAX_INPUT_CHUNK + 2)).decode()):
        try:
            live_voice._audio({'type': 'audio', 'data': data})
        except live_voice._Invalid:
            pass
        else:
            raise AssertionError('invalid PCM accepted')


def test_sdk_config_contains_one_bounded_workspace_tool_and_transcriptions():
    config = live_voice._config()
    assert config.response_modalities == ['AUDIO']
    assert config.input_audio_transcription is not None
    assert config.output_audio_transcription is not None
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == 'Charon'
    assert len(config.tools) == 1
    assert len(config.tools[0].function_declarations) == 21
    assert config.tools[0].function_declarations[0].name == 'operate_workspace'
    assert {'estimate_carbon', 'read_routes', 'confirm_workspace_action'} <= {
        declaration.name for declaration in config.tools[0].function_declarations}


def test_current_material_reference_reads_server_selection_before_clarifying():
    from procure.demo.company import get_company

    config = live_voice._config(get_company('atlas-petrochem'))
    instruction = config.system_instruction.lower()
    read_workspace = next(declaration for declaration in config.tools[0].function_declarations
                          if declaration.name == 'read_workspace')

    assert 'selected material' in instruction
    assert 'read_workspace without material_id' in instruction
    assert 'selected_material_id' in instruction
    assert 'buyer_draft' in instruction
    assert 'before asking for clarification' in instruction
    assert 'never use the first catalogue material' in instruction
    assert 'current selected material' in read_workspace.description.lower()


def test_oversized_workspace_keeps_only_bounded_current_selection_for_voice():
    class ReadProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                await self.events.put(NS(tool_call=None, server_content=None,
                    voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
                await self.events.put(NS(tool_call=None, server_content=NS(
                    input_transcription=NS(text='Read the current workspace.', finished=True))))
                await self.events.put(NS(tool_call=None, server_content=None,
                    voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.000125s')))
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name='read_workspace', args={}, id='read-call')]), server_content=None))

    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), ReadProvider()
        buyer = {'material': 'toluene', 'quantity': '100', 'current_stock': '20',
                 'quantity_unit': 'tonne', 'budget': '10000',
                 'additional_cost_per_unit': '0', 'decision_date': '2026-10-14',
                 'need_by': '2026-11-14', 'reporting_currency': 'AED'}
        result = {'turn_id': 'read-turn', 'company_id': session.company_id,
                  'context_version': 1, 'status': 'completed',
                  'assistant_text': 'Current workspace facts are ready.',
                  'agent_steps': [], 'evidence_ids': [], 'proposal': None,
                  'read_only': True, 'session': {'csrf_token': 'private-session-token'},
                  'workspace': {'company': {'secret': 'private-company-data',
                                           'large_profile': 'x' * 80_000},
                                'selected_material_id': 'toluene',
                                'buyer_draft': {**buyer, 'secret': 'private-buyer-data'},
                                'news_brief': {'availability': 'unavailable'}},
                  'analytics': {'large': 'y' * 80_000}}

        async def workspace_tool(name, args, version, key):
            assert (name, args, version) == ('read_workspace', {}, 1)
            return result

        task = asyncio.create_task(live_voice.run_live_voice(
            socket, session=session, store=store,
            operate_workspace=lambda *_: None, workspace_tools=workspace_tool,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        assert (await next_type(socket, 'tool_result'))['result'] == result
        await next_type(socket, 'turn_complete')
        response = provider.tool_response.response
        assert response['workspace'] == {'selected_material_id': 'toluene',
                                         'buyer_draft': buyer,
                                         'news_brief': {'availability': 'unavailable'}}
        assert 'analytics' not in response
        assert len(json.dumps(response, ensure_ascii=True)) <= 64_000
        assert 'private-session-token' not in json.dumps(response)
        assert 'private-company-data' not in json.dumps(response)
        assert 'private-buyer-data' not in json.dumps(response)
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())
