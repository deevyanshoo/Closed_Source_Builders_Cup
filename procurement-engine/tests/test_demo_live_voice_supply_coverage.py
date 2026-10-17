"""Actual provider tool selection respects the settled microphone request."""
import asyncio
import json
from types import SimpleNamespace as NS
import pytest
from procure.demo import live_voice
from test_demo_live_voice import Socket, Provider, setup, start, connection, next_type

BRIDGE = ('Prepare an earlier delivery RFQ for the supply gap using our current plan. '
          'Save the draft without sending it.')
REGULAR = 'Prepare an RFQ for the current sourcing plan.'


def event(*, text=None, finished=True, activity=None, call=None, complete=False):
    return NS(server_content=NS(input_transcription=NS(text=text, finished=finished) if text else None,
        turn_complete=complete, interrupted=False),
        tool_call=NS(function_calls=[call] if call else []),
        voice_activity=NS(voice_activity_type=activity[0], audio_offset=activity[1]) if activity else None)


def rfq_call(identifier='rfq-call'):
    return NS(name='prepare_rfq', args={}, id=identifier)


@pytest.mark.parametrize('mode', ['asr_first', 'tool_first', 'partial_first', 'late_after_end', 'duplicate'])
def test_provider_generic_rfq_routes_exact_settled_bridge_words(monkeypatch, mode):
    run_case(monkeypatch, BRIDGE, mode=mode, expected='operate_workspace')


def test_provider_regular_rfq_retains_regular_tool(monkeypatch):
    run_case(monkeypatch, REGULAR, expected='prepare_rfq')


@pytest.mark.parametrize('message,mode', [
    (None, 'asr_first'), (BRIDGE, 'incomplete'), (BRIDGE, 'no_vad'),
    (BRIDGE, 'stale'), (BRIDGE, 'interrupted'),
    ('Do not draft a bridge RFQ for earlier delivery.', 'asr_first'),
    ('Prepare a bridge RFQ for earlier delivery, but do not create the draft.', 'asr_first'),
    ('What if we prepare an earlier delivery RFQ?', 'asr_first'),
    ('Explain how to prepare a bridge RFQ for earlier delivery.', 'asr_first'),
    ('Do not prepare an RFQ.', 'asr_first'), ('How do I prepare an RFQ?', 'asr_first'),
])
def test_generic_rfq_never_mutates_without_current_explicit_request(monkeypatch, message, mode):
    run_case(monkeypatch, message, mode=mode, expected=None)


def run_case(monkeypatch, message, *, mode='asr_first', expected):
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .01)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .04)
    async def scenario():
        store, session = setup()
        provider = Provider()
        async def audio(**kwargs):
            pass
        provider.send_realtime_input = audio
        socket = Socket(start(session))
        invoked = []
        async def result(tool, message, version, key):
            invoked.append((tool, message, version, key))
            return dict(turn_id='turn', company_id=session.company_id, context_version=1,
                        status='completed', assistant_text='Saved unsent draft.',
                        proposal=None, read_only=False, agent_steps=[], evidence_ids=[],
                        action_result={'tool': tool}, session={'session_id': session.session_id,
                            'company_id': session.company_id, 'context_version': 1})
        async def operate(message, version, key):
            return await result('operate_workspace', message, version, key)
        async def workspace(name, args, version, key):
            return await result(name, args, version, key)
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace,
            client_factory=connection(provider)))
        try:
            await next_type(socket, 'ready')
            await socket.frames.put(json.dumps({'type': 'audio', 'data': 'AQACAA=='}))
            await asyncio.sleep(.01)
            if mode != 'no_vad':
                await provider.events.put(event(activity=('ACTIVITY_START', '0s')))
            if mode in ('tool_first', 'partial_first', 'late_after_end'):
                if mode == 'partial_first':
                    await provider.events.put(event(text='Prepare an', finished=False))
                await provider.events.put(event(call=rfq_call()))
                if mode == 'late_after_end':
                    await provider.events.put(event(text='Prepare an', finished=False))
                    await provider.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
                await asyncio.sleep(.025)
                assert invoked == [], 'A tool ran before the current ASR settled'
            await provider.events.put(event(text=message, finished=mode != 'incomplete'))
            if mode not in ('no_vad', 'late_after_end'):
                await provider.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
            if mode == 'stale':
                await asyncio.sleep(.06)
                await provider.events.put(event(complete=True))
            if mode == 'interrupted':
                interrupted = event()
                interrupted.server_content.interrupted = True
                await provider.events.put(interrupted)
            if mode not in ('tool_first', 'partial_first', 'late_after_end'):
                await provider.events.put(event(call=rfq_call()))
            try:
                await next_type(socket, 'tool_result')
            except TimeoutError:
                pytest.fail(repr(socket.history))
            if mode == 'duplicate':
                await provider.events.put(event(call=rfq_call()))
                await next_type(socket, 'tool_result')
            if expected is None:
                assert invoked == []
            else:
                assert invoked[0][:3] == (expected, message if expected == 'operate_workspace' else {}, 1)
                assert len({call[3] for call in invoked}) == 1
                assert all(call[0] == expected for call in invoked)
        finally:
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
    asyncio.run(scenario())


def test_provider_config_distinguishes_bridge_and_ordinary_rfq():
    config = live_voice._config()
    instruction = config.system_instruction.lower()
    assert 'stockout' in instruction and 'continuous-supply' in instruction
    assert 'earlier-delivery' in instruction and 'bridge rfq' in instruction
    assert "user's exact" in instruction
    tool = next(d for d in config.tools[0].function_declarations if d.name == 'prepare_rfq')
    assert 'ordinary' in tool.description.lower()
    assert 'operate_workspace' in tool.description


def test_rfq_handoff_rejects_history_and_preserves_replay_idempotency(monkeypatch):
    from contextlib import asynccontextmanager
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .01)
    class Manual(Provider):
        def __init__(self):
            super().__init__()
            self.responses = asyncio.Queue()
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                self.received_audio.append(kwargs['audio'])
        async def send_tool_response(self, *, function_responses):
            await self.responses.put(function_responses)
    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [
            {'channel': 'voice', 'speaker': 'input', 'question': BRIDGE}]
        first, second = Manual(), Manual()
        providers = iter((first, second))
        @asynccontextmanager
        async def factory(company, *, handle=None, history_context=None):
            yield next(providers)
        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        invoked = []
        async def operate(message, version, key):
            invoked.append((message, key))
            return dict(turn_id='turn', company_id=session.company_id, context_version=1,
                status='completed', assistant_text='Current bridge request.', proposal=None,
                read_only=True, agent_steps=[], evidence_ids=[])
        async def workspace(*args):
            raise AssertionError('A bridge or replay reached the ordinary RFQ tool')
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session,
            store=store, operate_workspace=operate, workspace_tools=workspace))
        async def audio(provider):
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            async with asyncio.timeout(2):
                while not provider.received_audio:
                    await asyncio.sleep(.001)
        try:
            await next_type(socket, 'ready')
            await audio(first)
            await first.events.put(event(activity=('ACTIVITY_START', '0s')))
            await first.events.put(event(text=BRIDGE))
            await first.events.put(event(activity=('ACTIVITY_END', '0.000125s'), call=rfq_call('old')))
            await next_type(socket, 'tool_result')
            assert len(invoked) == 1
            await first.events.put(NS(go_away=NS(time_left='0s')))
            await next_type(socket, 'connection_status')
            assert (await next_type(socket, 'connection_status'))['state'] == 'ready'
            await second.events.put(event(text=BRIDGE, call=rfq_call('history-replay')))
            denied = await asyncio.wait_for(second.responses.get(), 2)
            assert denied.response['action_applied'] is False and len(invoked) == 1
            await audio(second)
            await second.events.put(event(activity=('ACTIVITY_START', '0s')))
            await second.events.put(event(call=rfq_call('fresh')))
            await asyncio.sleep(.02)
            assert len(invoked) == 1
            await second.events.put(event(text=BRIDGE))
            await second.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
            await next_type(socket, 'tool_result')
            assert len(invoked) == 2 and invoked[1][0] == BRIDGE
            assert invoked[0][1] != invoked[1][1]
            await second.events.put(event(call=rfq_call('old')))
            assert (await asyncio.wait_for(second.responses.get(), 2)).id == 'fresh'
            replay = await asyncio.wait_for(second.responses.get(), 2)
            assert replay.id == 'old'
            assert {key for _, key in invoked} == {invoked[0][1], invoked[1][1]}
            assert invoked[-1][1] == invoked[0][1]
        finally:
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
    asyncio.run(scenario())

@pytest.mark.parametrize('message', [
    'Prepare a bridge RFQ only if we approve the extra cost.',
    'Prepare a bridge RFQ unless we reject the extra cost.',
    'Prepare a bridge RFQ until we approve the extra cost.',
    'Prepare a bridge RFQ once we approve the extra cost.',
    'Prepare a bridge RFQ after approval.',
])
def test_conditional_settled_generic_rfq_has_no_regular_fallback(monkeypatch, message):
    run_case(monkeypatch, message, expected=None)
