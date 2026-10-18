"""A completed fresh recovery request survives a factual Live tool choice."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from time import monotonic as real_monotonic
from types import SimpleNamespace as NS

import pytest

from procure.demo import live_voice
from test_demo_live_voice import Provider, Socket, connection, next_type, setup, start


REQUEST = 'Prepare a recovery scenario for a 7-day delay of our current copper cathode supplier.'


class ManualProvider(Provider):
    def __init__(self):
        super().__init__()
        self.responses = []

    async def send_realtime_input(self, **kwargs):
        if audio := kwargs.get('audio'):
            self.received_audio.append(audio)

    async def send_tool_response(self, *, function_responses):
        self.responses.append(function_responses)


def event(*, text=None, finished=None, activity=None, calls=(), turn_complete=False):
    content = NS(turn_complete=turn_complete)
    if text is not None:
        content.input_transcription = NS(text=text, finished=finished)
    return NS(server_content=content, tool_call=NS(function_calls=list(calls)),
              voice_activity=(NS(voice_activity_type=activity[0], audio_offset=activity[1])
                              if activity else None))


@pytest.mark.parametrize('renewed', [False, True])
@pytest.mark.parametrize('finished', [True, None])
@pytest.mark.parametrize('first_tool', ['read_workspace', 'operate_workspace'])
def test_completed_speech_rejects_recovery_callback_without_valid_owned_binding(monkeypatch, renewed, finished, first_tool):
    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [{'channel': 'voice', 'speaker': 'input',
            'question': REQUEST}]
        first, second = ManualProvider(), ManualProvider()
        providers = [first, second]
        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            yield providers.pop(0)
        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        socket = Socket(start(session))
        operated = []
        reads = []
        async def operate(message, version, key):
            operated.append((message, version, key))
            return {'turn_id': 'recovery-turn', 'company_id': session.company_id,
                'context_version': version, 'status': 'proposal',
                'assistant_text': 'Review the 7-day copper recovery comparison. No plan has changed.',
                'agent_steps': [], 'evidence_ids': [],
                'proposal': {'proposal_id': 'recovery-review', 'kind': 'recovery'},
                'read_only': True}
        async def workspace(name, args, version, key):
            reads.append(name)
            raise AssertionError('The explicit recovery request must reach the operator')
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace))
        await next_type(socket, 'ready')
        if renewed:
            await first.events.put(NS(go_away=NS(time_left='1s')))
            await next_type(socket, 'connection_status')
            await next_type(socket, 'connection_status')
        provider = second if renewed else first
        audio = base64.b64encode(b'\x01\x00' * 16000).decode()
        await socket.frames.put(json.dumps({'type': 'audio', 'data': audio}))
        for _ in range(100):
            if provider.received_audio:
                break
            await asyncio.sleep(.01)
        assert provider.received_audio
        await provider.events.put(event(activity=('ACTIVITY_START', '0.100s')))
        await provider.events.put(event(text=REQUEST, finished=finished))
        await provider.events.put(event(activity=('ACTIVITY_END', '0.900s')))
        await provider.events.put(event(calls=[NS(name=first_tool,
            args={} if first_tool == 'read_workspace' else {'message': REQUEST}, id='first')]))
        result = (await next_type(socket, 'tool_result'))['result']
        assert result['status'] == 'needs_clarification'
        assert result['proposal'] is None
        assert session.pending_proposal is None
        assert operated[0][:2] == (REQUEST, 1)
        assert reads == []
        await provider.events.put(event(calls=[NS(name='read_workspace', args={}, id='read-2'),
            NS(name='operate_workspace', args={'message': REQUEST}, id='operate-3')]))
        for _ in range(2):
            assert (await next_type(socket, 'tool_result'))['result']['proposal'] is None
        assert len(operated) == 1
        assert len({response.id for response in provider.responses}) == 3
        assert all(response.name in {'read_workspace', 'operate_workspace'} for response in provider.responses)
        assert 'fresh explicit recovery request' in provider.responses[0].response['required_spoken_response']
        assert session.recovery is None
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize('spoken,finished,end,expected', [
    (REQUEST, False, True, False),
    (REQUEST, None, False, False),
    ('Do not prepare a recovery scenario for a 7-day delay of copper.', True, True, False),
    ('Read this quote: "Prepare a recovery scenario for a 7-day delay of copper."', True, True, False),
    ('What if our copper supplier is delayed by 7 days?', True, True, False),
    ('Prepare a recovery scenario for a 7-day delay of copper as an example only.', True, True, False),
    ('Prepare a recovery scenario for a 7 to 10 day delay of copper.', True, True, True),
    ('Prepare a recovery scenario for a 7-day delay of copper and aluminium.', True, True, True),
])
def test_recovery_read_requires_completed_explicit_current_speech(spoken, finished, end, expected):
    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [{'channel': 'voice', 'speaker': 'input',
            'question': REQUEST}]
        provider = ManualProvider()
        socket = Socket(start(session))
        operated = []
        async def operate(message, version, key):
            operated.append(message)
            return {'turn_id': 'review', 'company_id': session.company_id,
                'context_version': version, 'status': 'needs_clarification',
                'assistant_text': 'Choose one exact current material and duration.',
                'agent_steps': [], 'evidence_ids': [], 'proposal': None, 'read_only': True}
        async def workspace(name, args, version, key):
            return {'turn_id': 'read', 'company_id': session.company_id,
                'context_version': version, 'status': 'completed',
                'assistant_text': 'Current workspace facts.', 'agent_steps': [],
                'evidence_ids': [], 'proposal': None, 'read_only': True}
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if provider.received_audio:
                break
            await asyncio.sleep(.01)
        await provider.events.put(event(activity=('ACTIVITY_START', '0.000s')))
        await provider.events.put(event(text=spoken, finished=finished))
        if end:
            await provider.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
        await provider.events.put(event(calls=[NS(name='read_workspace', args={}, id='read-1')]))
        if not end:
            await asyncio.sleep(.05)
            assert operated == []
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
            return
        result = (await next_type(socket, 'tool_result'))['result']
        assert bool(operated) is expected
        assert operated == ([spoken] if expected else [])
        assert result['status'] == ('needs_clarification' if expected or spoken == REQUEST and finished is False
                                    else 'completed')
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize('fresh_audio,vad_end,finished', [
    (False, False, None), (True, False, None), (True, True, False),
])
def test_provider_cannot_operate_recovery_from_history_or_partial_asr(fresh_audio, vad_end, finished):
    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [{'channel': 'voice', 'speaker': 'input',
            'question': REQUEST}]
        provider = ManualProvider()
        socket = Socket(start(session))
        operated = []
        async def operate(message, version, key):
            operated.append(message)
            raise AssertionError('Incomplete speech cannot reach the operator')
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate,
            client_factory=connection(provider)))
        await next_type(socket, 'ready')
        if fresh_audio:
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            for _ in range(100):
                if provider.received_audio:
                    break
                await asyncio.sleep(.01)
            await provider.events.put(event(activity=('ACTIVITY_START', '0.000s')))
            await provider.events.put(event(text=REQUEST, finished=finished))
            if vad_end:
                await provider.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
        await provider.events.put(event(calls=[NS(name='operate_workspace',
            args={'message': REQUEST}, id='unverified-operation')]))
        for _ in range(400 if not fresh_audio else 200 if vad_end else 5):
            if provider.responses:
                break
            await asyncio.sleep(.01)
        assert operated == []
        if not fresh_audio or vad_end:
            assert provider.responses and provider.responses[0].response['status'] == 'needs_clarification'
        else:
            assert provider.responses == []  # Held until VAD completion or the bounded deadline.
        assert not any(frame.get('result', {}).get('proposal') for frame in socket.history)
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', [
    'expired', 'interrupted', 'browser_interrupt', 'new_activity',
    'old_generation', 'incomplete', 'contradictory', 'missing_end', 'invalid_end',
])
def test_late_transport_never_accepts_unfinished_or_superseded_request(monkeypatch, failure):
    async def scenario():
        offset = [0.0]
        monkeypatch.setattr(live_voice, 'monotonic', lambda: real_monotonic() + offset[0])
        store, session = setup()
        provider = ManualProvider()
        socket = Socket(start(session))
        operated = []

        async def operate(message, version, key):
            operated.append(message)
            raise AssertionError('Unverified late speech cannot reach the operator')

        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        for _ in range(7):
            await socket.frames.put(json.dumps({'type': 'audio',
                'data': base64.b64encode(b'\x01\x00' * 16000).decode()}))
        for _ in range(100):
            if len(provider.received_audio) == 7:
                break
            await asyncio.sleep(.01)
        assert len(provider.received_audio) == 7
        await provider.events.put(event(activity=('ACTIVITY_START', '0.200s')))
        await asyncio.sleep(.02)
        offset[0] += 9.1
        await provider.events.put(event())  # Wake the initial physical-clip deadline.
        await asyncio.sleep(.02)

        if failure == 'expired':
            offset[0] += live_voice.RECOVERY_TRANSPORT_LATENESS_SECONDS + .1
            await provider.events.put(event())
            await asyncio.sleep(.02)
        elif failure == 'interrupted':
            interrupted = event()
            interrupted.server_content.interrupted = True
            await provider.events.put(interrupted)
        elif failure == 'browser_interrupt':
            await socket.frames.put('{"type":"interrupt"}')
            await next_type(socket, 'interrupted')
            await asyncio.sleep(.02)
        elif failure == 'new_activity':
            await provider.events.put(event(activity=('ACTIVITY_START', '0.300s')))
        elif failure == 'old_generation':
            await provider.events.put(event(activity=('ACTIVITY_END', '6.280s')))
            await provider.events.put(event(activity=('ACTIVITY_START', '6.400s')))

        spoken = ('Do not prepare a recovery scenario for a 7-day delay of copper.'
                  if failure == 'contradictory' else REQUEST)
        await provider.events.put(event(text=spoken, finished=failure != 'incomplete'))
        if failure != 'missing_end':
            end = ('7.280s' if failure == 'invalid_end' else
                   '6.700s' if failure == 'old_generation' else '6.280s')
            await provider.events.put(event(activity=('ACTIVITY_END', end)))
        if failure in {'incomplete', 'missing_end'}:
            await asyncio.sleep(.02)
            offset[0] += live_voice.RECOVERY_TRANSPORT_LATENESS_SECONDS + .1
            await provider.events.put(event())
        await asyncio.sleep(.08)
        assert operated == []
        assert session.pending_proposal is None
        assert not any((frame.get('result', {}).get('proposal') or {}).get('kind') == 'recovery'
                       for frame in socket.history)
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


@pytest.mark.parametrize('abandonment', ['expired', 'interrupted'])
def test_abandoned_late_round_renews_before_fresh_recovery_request(monkeypatch, abandonment):
    async def scenario():
        offset = [0.0]
        monkeypatch.setattr(live_voice, 'monotonic', lambda: real_monotonic() + offset[0])
        store, session = setup()
        old, fresh = ManualProvider(), ManualProvider()
        opened = []

        @asynccontextmanager
        async def factory():
            opened.append(len(opened) + 1)
            yield old if len(opened) == 1 else fresh

        operated = []

        async def operate(message, version, key):
            operated.append((message, version, key))
            return {'turn_id': 'fresh-recovery', 'company_id': session.company_id,
                    'context_version': version, 'status': 'proposal',
                    'assistant_text': 'Review the current recovery comparison.',
                    'agent_steps': [], 'evidence_ids': [],
                    'proposal': {'proposal_id': 'owned-fresh-recovery', 'kind': 'recovery'},
                    'read_only': True}

        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, client_factory=factory))
        try:
            await next_type(socket, 'ready')
            audio = json.dumps({'type': 'audio', 'data':
                base64.b64encode(b'\x01\x00' * 16000).decode()})
            for _ in range(7):
                await socket.frames.put(audio)
            for _ in range(100):
                if len(old.received_audio) == 7:
                    break
                await asyncio.sleep(.01)
            assert len(old.received_audio) == 7
            await old.events.put(event(activity=('ACTIVITY_START', '0.200s')))
            await old.events.put(event(calls=[NS(name='operate_workspace',
                args={'message': REQUEST}, id='abandoned-call')]))
            await asyncio.sleep(.02)
            offset[0] += 9.1
            await old.events.put(event())
            await asyncio.sleep(.02)
            if abandonment == 'expired':
                offset[0] += live_voice.RECOVERY_TRANSPORT_LATENESS_SECONDS + .1
                await old.events.put(event())
            else:
                interrupted = event()
                interrupted.server_content.interrupted = True
                await old.events.put(interrupted)
            await next_type(socket, 'connection_status')
            assert (await next_type(socket, 'connection_status'))['state'] == 'ready'
            assert [response.id for response in old.responses] == ['abandoned-call']
            assert old.responses[0].response['action_applied'] is False
            assert old.responses[0].response['tool_succeeded'] is False
            await old.events.put(event(text=REQUEST, finished=True))
            await old.events.put(event(activity=('ACTIVITY_END', '6.280s')))
            await old.events.put(event(calls=[NS(name='operate_workspace',
                args={'message': REQUEST}, id='abandoned-call')]))
            await asyncio.sleep(.02)
            assert operated == []

            await socket.frames.put(audio)
            for _ in range(100):
                if fresh.received_audio:
                    break
                await asyncio.sleep(.01)
            assert fresh.received_audio
            await fresh.events.put(event(activity=('ACTIVITY_START', '0.100s')))
            await fresh.events.put(event(text=REQUEST, finished=True))
            await fresh.events.put(event(activity=('ACTIVITY_END', '0.900s')))
            await fresh.events.put(event(calls=[NS(name='operate_workspace',
                args={'message': REQUEST}, id='fresh-call')]))
            result = (await next_type(socket, 'tool_result'))['result']
            assert result['proposal'] is None  # No trusted plan exists in this isolated guard fixture.
            assert len(operated) == 1 and operated[0][0] == REQUEST
            assert opened == [1, 2]
        finally:
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_recovery_policy_allows_requested_language_without_relaxing_consent():
    instruction = live_voice._config().system_instruction.lower()
    assert 'hindi' in instruction and 'portuguese' in instruction
    assert 'english' in instruction
    assert 'identifiers' in instruction and 'quantities' in instruction
    assert 'fresh' in instruction and 'confirm action' in instruction
