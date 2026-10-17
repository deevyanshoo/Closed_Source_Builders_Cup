"""Local clarification response boundaries through the real public voice socket."""
import asyncio
from contextlib import contextmanager
from threading import Event
from time import monotonic
from types import SimpleNamespace as NS

import pytest

from procure.demo import live_voice
from test_demo_live_voice_clarification import workspace, PCM, assert_audio
from test_demo_live_voice_native_grounded import protected, CANONICAL
from test_demo_live_voice_supply_coverage import event

# A settled missing-document fragment must clarify, never infer an RFQ.
WORDS = 'For the supply gap using our current plan. Save the draft without sending it.'
DISCONNECT = object()


def output(*, caption=None, audio=False, complete=False):
    return NS(server_content=NS(turn_complete=complete,
        output_transcription=NS(text=caption) if caption is not None else None,
        model_turn=NS(parts=[NS(inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))])
            if audio else None))


class AdvanceEvent:
    """Advance test time at dispatch, after earlier queued calls were held."""
    def __init__(self, clock, frame, seconds=2):
        self.clock, self.frame, self.seconds = clock, frame, seconds
        self.tool_call = getattr(frame, 'tool_call', None)

    @property
    def server_content(self):
        self.clock[0] += self.seconds
        self.seconds = 0
        return self.frame.server_content


@contextmanager
def boundary(workspace, monkeypatch, *, timeout=.3, release='same_event'):
    client, provider, operator = workspace
    clock = [0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: monotonic() + clock[0])
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', 1)
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', timeout)
    received = Event()
    responses = []
    sent = []
    run = live_voice.run_live_voice

    async def observe(websocket, **kwargs):
        send = websocket.send_json
        async def record(frame):
            sent.append(frame)
            await send(frame)
        websocket.send_json = record
        return await run(websocket, **kwargs)
    monkeypatch.setattr(live_voice, 'run_live_voice', observe)

    async def audio_input(**kwargs):
        if kwargs.get('audio'):
            received.set()

    async def response(*, function_responses):
        responses.append(function_responses)

    async def no_fabricated_turn(**kwargs):
        raise AssertionError('Local clarification must not manufacture a user turn')

    async def receive():
        frame = await provider.events.get()
        if frame is DISCONNECT:
            raise RuntimeError('Offline test transport closed')
        yield frame

    provider.send_realtime_input = audio_input
    provider.send_tool_response = response
    provider.send_client_content = no_fabricated_turn
    provider.receive = receive
    before = protected(client)
    session = client.get('/api/demo/session').json()

    def emit(*frames):
        async def put():
            for frame in frames:
                await provider.events.put(frame)
        client.portal.call(put)

    frames = []
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                         'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        assert received.wait(2)
        # First model tool is denied: no current complete RFQ request.
        emit(event(call=NS(name='prepare_rfq', args={}, id='generic-denied')))
        while not any(f['type'] == 'tool_result' for f in frames):
            frames.append(socket.receive_json())
        denied = frames[-1]['result']
        assert denied['status'] == 'needs_clarification' and denied['agent_steps'] == []
        assert denied.get('action_result') is None
        content = output(complete=release not in {'separate', 'no_completion'})
        if release == 'stale_same_frame':
            content = output(caption=CANONICAL, audio=True, complete=True)
            content.server_content.model_turn.parts[0].inline_data.data = b'\x05\x00\x06\x00'
        emit(event(activity=('ACTIVITY_START', '0s')), event(text=WORDS),
             event(activity=('ACTIVITY_END', '0.000125s')),
             event(call=NS(name='operate_workspace', args={'message': WORDS}, id='local-clarification')),
             AdvanceEvent(clock, content))
        while len([f for f in frames if f['type'] == 'tool_result']) < 2:
            frames.append(socket.receive_json())
        local = frames[-1]['result']
        assert local['assistant_text'] == CANONICAL and local['read_only']
        assert local['agent_steps'] and local['proposal'] is None and local.get('action_result') is None
        if release == 'separate':
            emit(output(complete=True))
        try:
            yield NS(client=client, provider=provider, operator=operator, socket=socket,
                emit=emit, frames=frames, responses=responses, clock=clock, before=before,
                armed_at=monotonic(), session=session, sent=sent, review_start=len(frames)-1)
        finally:
            socket.send_json({'type': 'stop'})
    assert protected(client) == before
    assert operator.calls == [] and provider.executed_messages
    assert all(message == WORDS for message in provider.executed_messages)


def terminal(case):
    while True:
        frame = case.socket.receive_json()
        case.frames.append(frame)
        if frame['type'] in {'error', 'turn_complete'}:
            return frame


@pytest.mark.parametrize('release', ['same_event', 'separate', 'stale_same_frame'])
def test_empty_completion_waits_for_authoritative_local_speech(workspace, monkeypatch, release):
    with boundary(workspace, monkeypatch, release=release) as case:
        case.emit(output(), output(caption=CANONICAL), output(audio=True), output(complete=True))
        assert terminal(case)['type'] == 'turn_complete'
        assert_audio(case.frames[case.review_start:])
        assert not any(f.get('state') == 'awaiting_consent' for f in case.frames)
        assert len([f for f in case.frames if f['type'] == 'turn_complete']) == 1


@pytest.mark.parametrize('kind', ['contradiction', 'caption_only', 'audio_only'])
def test_nonempty_incomplete_or_contradictory_completion_still_fails(workspace, monkeypatch, kind):
    with boundary(workspace, monkeypatch) as case:
        case.emit(output(caption=None if kind == 'audio_only' else CANONICAL +
            (' I sent it.' if kind == 'contradiction' else ''), audio=kind != 'caption_only', complete=True))
        error = terminal(case)
        assert error['type'] == 'error'
        assert error['review']['reason'] == {'contradiction': 'caption_mismatch',
            'caption_only': 'missing_audio', 'audio_only': 'missing_caption'}[kind]
        assert not any(f['type'] == 'audio' or f.get('state') == 'awaiting_consent' for f in case.frames)


@pytest.mark.parametrize('repeated', ['empty', 'calls', 'both'])
def test_repeated_empty_completions_and_calls_keep_original_deadline(workspace, monkeypatch, repeated):
    with boundary(workspace, monkeypatch, timeout=.2, release='no_completion') as case:
        # All subsequent tool calls and completions belong to the same review.
        # Advance beyond the original deadline, but not a restarted deadline.
        for index in range(3):
            frame = (output(complete=True) if repeated == 'empty' else
                event(call=NS(name='operate_workspace', args={'message': WORDS}, id='local-clarification')))
            case.emit(AdvanceEvent(case.clock, frame, seconds=.05))
            if repeated == 'both':
                case.emit(output(complete=True))
        case.emit(AdvanceEvent(case.clock, output(complete=True), seconds=.06))
        error = terminal(case)
        assert error['type'] == 'error' and error['review']['reason'] == 'timeout'
        assert error['review']['audio_bytes'] == error['review']['caption_chars'] == 0
        assert monotonic() - case.armed_at < .15, 'A repeated call extended the original review deadline'
        assert not any(f['type'] in {'audio', 'turn_complete'} for f in case.frames)


@pytest.mark.parametrize('retirement', ['interruption', 'new_generation'])
def test_retired_clarification_cannot_release_late_output(workspace, monkeypatch, retirement):
    with boundary(workspace, monkeypatch) as case:
        case.emit(output(caption=CANONICAL))
        if retirement == 'interruption':
            stopped = event()
            stopped.server_content.interrupted = True
        else:
            case.socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            stopped = event(activity=('ACTIVITY_START', '0.000125s'))
        case.emit(stopped, output(caption=CANONICAL, audio=True, complete=True))
        assert terminal(case)['review']['reason'] == 'interrupted'
        # A denied consent call is a processing barrier, never action authority.
        case.emit(event(call=NS(name='confirm_workspace_action',
            args={'proposal_id': 'absent'}, id='retired-output-barrier')))
        async def processed():
            async with asyncio.timeout(2):
                while not any(r.id == 'retired-output-barrier' for r in case.responses):
                    await asyncio.sleep(.001)
        case.client.portal.call(processed)
        assert not any(f['type'] == 'audio' for f in case.sent)


@pytest.mark.parametrize('cause', ['go_away', 'transport_failure'])
def test_renewal_retires_partial_clarification_and_discards_old_output(workspace, monkeypatch, cause):
    with boundary(workspace, monkeypatch) as case:
        case.emit(output(caption=CANONICAL), NS(go_away=NS(time_left='0s')) if cause == 'go_away' else DISCONNECT)
        while True:
            frame = case.socket.receive_json()
            case.frames.append(frame)
            if frame.get('state') == 'ready':
                break
        case.emit(output(caption=CANONICAL, audio=True, complete=True),
            event(call=NS(name='operate_workspace', args={'message': WORDS}, id='renewed-history')))
        async def processed():
            async with asyncio.timeout(2):
                while not any(r.id == 'renewed-history' for r in case.responses):
                    await asyncio.sleep(.001)
        case.client.portal.call(processed)
        assert not any(f['type'] in {'audio', 'turn_complete'} for f in case.sent)
        assert case.responses[-1].response['status'] == 'unavailable'


def test_context_change_retires_review_before_late_output(workspace, monkeypatch):
    with boundary(workspace, monkeypatch) as case:
        value = case.client.app.state.sessions.get(case.session['session_id'])
        case.emit(output(caption=CANONICAL))
        with value.lock:
            value.context_version += 1
        case.before['context_version'] += 1
        case.emit(output(audio=True, complete=True))
        error = terminal(case)
        assert error['type'] == 'error' and error['code'] == 'stale_context'
        assert not any(f['type'] == 'audio' for f in case.sent)


def test_active_action_consent_empty_completion_keeps_existing_behavior():
    from test_demo_voice_workspace_actions import scenario
    from test_demo_voice_research_consent import finish, send_pcm
    async def check():
        async def verify(_):
            return 'confirm action'
        _, session, socket, provider, calls, task, review = await scenario(verify)
        try:
            await provider.spoken('output', review, complete=True)
            await socket.next('turn_complete')
            await provider.events.put(output(complete=True))
            # Active consent does not enter local clarification deferral.
            await socket.next('turn_complete')
            assert [name for name, _ in calls] == ['operate_workspace']
            await send_pcm(socket, provider)
            await provider.spoken('input', 'confirm action', finished=None)
            await socket.next('transcript', 'input')
            await provider.activity('ACTIVITY_START', '0.100s')
            await provider.activity('ACTIVITY_END', '0.600s')
            await provider.call('confirm_workspace_action', {'proposal_id': 'watch-1'}, 'apply-after-empty')
            assert (await socket.next('tool_result'))['result']['status'] == 'completed'
            assert [name for name, _ in calls] == ['operate_workspace', 'confirm_workspace_action']
        finally:
            await finish(socket, task)
    asyncio.run(check())
