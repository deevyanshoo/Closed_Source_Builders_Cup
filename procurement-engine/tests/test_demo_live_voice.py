"""Offline contract checks for the bounded Gemini Live bridge."""
import asyncio
import base64
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
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='operate_workspace', args={'message': 'Watch copper'}, id='call-1')]),
                server_content=NS(input_transcription=NS(text='Watch copper'),
                                  output_transcription=None, interrupted=False,
                                  model_turn=None, turn_complete=False)))

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


def test_invalid_csrf_denies_provider_connection():
    async def scenario():
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
        async def send_realtime_input(self, **kwargs):
            await super().send_realtime_input(**kwargs)
            if kwargs.get('audio'):
                event = self.events.get_nowait()
                self.events.put_nowait(event)
                self.events.put_nowait(event)

    async def scenario():
        store, session = setup()
        socket, provider = Socket(start(session)), DuplicateProvider()
        count = 0

        async def operate(*_):
            nonlocal count
            count += 1
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
        await next_type(socket, 'tool_result')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
        assert count == 1
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


def test_company_and_process_start_limits_after_valid_hello(monkeypatch):
    async def scenario():
        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
        store, session = setup()
        for _ in range(2):
            socket = Socket(start(session))
            task = asyncio.create_task(live_voice.run_live_voice(
                socket, session=session, store=store, operate_workspace=lambda *_: None,
                client_factory=connection(Provider())))
            await next_type(socket, 'ready')
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
        third = Socket(start(session))
        await live_voice.run_live_voice(third, session=session, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=connection(Provider()))
        assert (await next_type(third, 'error'))['code'] == 'voice_limit'
        assert session.live_voice_sessions == 2
        assert session.live_voice_starts_by_company['solara-cables'] == 2

        with session.lock:
            session.select_company('terrapack')
        terra = Socket(start(session, context=2))
        terra_task = asyncio.create_task(live_voice.run_live_voice(
            terra, session=session, store=store, operate_workspace=lambda *_: None,
            client_factory=connection(Provider())))
        assert (await next_type(terra, 'ready'))['context_version'] == 2
        await terra.frames.put('{"type":"stop"}')
        await asyncio.wait_for(terra_task, 2)
        assert session.live_voice_sessions == 3
        assert session.live_voice_starts_by_company == {'solara-cables': 2, 'terrapack': 1}

        with session.lock:
            session.select_company('solara-cables')
        returned = Socket(start(session, context=3))
        await live_voice.run_live_voice(returned, session=session, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=connection(Provider()))
        assert (await next_type(returned, 'error'))['code'] == 'voice_limit'
        assert session.live_voice_sessions == 3

        monkeypatch.setattr(live_voice, '_PROCESS_STARTS', live_voice.MAX_PROCESS_STARTS)
        new_session = store.create()
        fourth = Socket(start(new_session))
        await live_voice.run_live_voice(fourth, session=new_session, store=store,
                                        operate_workspace=lambda *_: None,
                                        client_factory=connection(Provider()))
        assert (await next_type(fourth, 'error'))['code'] == 'voice_limit'
        assert new_session.live_voice_sessions == 0
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
    assert [tool.name for tool in config.tools[0].function_declarations] == ['operate_workspace']
