"""Recovery completion exercises the real app operator, pending proposal and consent."""
import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import anyio
import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo import live_voice
from test_demo_live_voice_rfq import plan_for_copper
from test_demo_operator import Provider as OperatorProvider

REQUEST = 'Prepare a recovery scenario for a 7-day delay of our current copper cathode supplier.'
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)
PCM = base64.b64encode(b'\x01\x00' * 16000).decode()


def event(*, text=None, finished=None, activity=None, call=None, output=None, complete=False):
    return NS(server_content=NS(input_transcription=NS(text=text, finished=finished) if text else None,
        output_transcription=NS(text=output) if output else None, turn_complete=complete,
        model_turn=NS(parts=[NS(inline_data=NS(data=b'\1\0' * 10,
            mime_type='audio/pcm;rate=24000'))]) if output else None),
        tool_call=NS(function_calls=[call] if call else []),
        voice_activity=NS(voice_activity_type=activity[0], audio_offset=activity[1]) if activity else None)


class ScriptProvider:
    def __init__(self, choice='none', *, spoken=REQUEST, late=False, finished=None):
        self.choice, self.spoken, self.late, self.finished = choice, spoken, late, finished
        self.events = asyncio.Queue()
        self.responses, self.client_turns, self.reviews = [], [], []
        self.audio_count = 0
        self.proposal_id = None
        self.consent_mode = 'confirm'
        self.approval_before_review = False
        self.bad_review = False
        self.duplicate_tools = False
        self.delay_original_completion = False
        self.suppress_original_completion = False
        self.original_completed = False
        self.review_requested_before_original_completion = False

    async def receive(self):
        yield await self.events.get()

    async def send_realtime_input(self, **kwargs):
        if not kwargs.get('audio'):
            return
        self.audio_count += 1
        if self.audio_count == 1:
            await self.events.put(event(activity=('ACTIVITY_START', '0.100s')))
            await self.events.put(event(text='Prepare a recovery scenario' if self.late else self.spoken,
                                        finished=self.finished))
            if self.choice != 'none':
                args = {} if self.choice == 'read_workspace' else {
                    'message': 'Plan a supplier fallback for a 7 day delay of Europa'}
                await self.events.put(event(call=NS(name=self.choice, args=args, id='real-call-1')))
                if self.duplicate_tools:
                    await self.events.put(event(call=NS(name='read_workspace', args={}, id='real-call-2')))
                    await self.events.put(event(call=NS(name='operate_workspace',
                        args={'message': REQUEST}, id='real-call-3')))
            await self.events.put(event(activity=('ACTIVITY_END', '0.900s')))
            if self.late:
                async def late_caption():
                    await asyncio.sleep(.4)
                    await self.events.put(event(text=REQUEST, finished=None))
                asyncio.create_task(late_caption())
            if self.suppress_original_completion:
                pass
            elif self.delay_original_completion:
                async def complete_original():
                    await asyncio.sleep(.45)
                    self.original_completed = True
                    await self.events.put(event(output='I am ready. Please provide your request.', complete=True))
                asyncio.create_task(complete_original())
            else:
                self.original_completed = True
                await self.events.put(event(output='I am ready. Please provide your request.', complete=True))
        else:
            base = self.audio_count - 1
            await self.events.put(event(activity=('ACTIVITY_START', f'{base}.100s')))
            await self.events.put(event(text='confirm action', finished=None))
            await self.events.put(event(activity=('ACTIVITY_END', f'{base}.900s')))
            await self.events.put(event(call=NS(name='confirm_workspace_action',
                args={'proposal_id': self.proposal_id}, id=f'confirm-{self.audio_count}')))

    async def say_result(self, result):
        spoken = result.get('required_spoken_response', result.get('assistant_text', ''))
        if (result.get('proposal') or {}).get('kind') == 'recovery':
            self.proposal_id = result['proposal']['proposal_id']
            self.reviews.append(spoken)
            if self.approval_before_review:
                self.approval_before_review = False
                await self.events.put(event(call=NS(name='confirm_workspace_action',
                    args={'proposal_id': self.proposal_id}, id='before-spoken-review')))
            if self.bad_review:
                spoken = 'I am ready. Please provide your request.'
        await self.events.put(event(output=spoken, complete=True))

    async def send_tool_response(self, *, function_responses):
        self.responses.append(function_responses)
        if function_responses.id == 'before-spoken-review':
            return  # The provider's queued review, not this denial, is still speaking.
        await self.say_result(function_responses.response)

    async def send_client_content(self, *, turns, turn_complete):
        assert turn_complete is True
        self.review_requested_before_original_completion |= not self.original_completed
        self.client_turns.append(turns)
        payload = json.loads(turns[0].parts[0].text)
        result = payload['trusted_recovery_result']
        await self.say_result({**result, 'required_spoken_response': payload['required_spoken_response']})


class LateVadProvider(ScriptProvider):
    """Seven seconds of audio, then the observed late ASR/END transport order."""

    async def send_realtime_input(self, **kwargs):
        if not kwargs.get('audio'):
            return
        self.audio_count += 1
        if self.audio_count == 1:
            await self.events.put(event(activity=('ACTIVITY_START', '0.200s')))
        if self.audio_count == 7:
            async def late_events():
                await asyncio.sleep(9.1)  # Initial 8-second clip + 1-second wait has expired.
                self.original_completed = True
                await self.events.put(event(output='I am ready to help.', complete=True))
                await asyncio.sleep(.1)
                await self.events.put(event(text=REQUEST, finished=True))
                await self.events.put(event(activity=('ACTIVITY_END', '6.280s')))
            asyncio.create_task(late_events())

    async def send_client_content(self, *, turns, turn_complete):
        await super().send_client_content(turns=turns, turn_complete=turn_complete)
        await self.events.put(event(text=REQUEST, finished=True))
        await self.events.put(event(activity=('ACTIVITY_END', '6.280s')))


def test_late_exact_asr_before_valid_vad_end_waits_for_canonical_review():
    provider = LateVadProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            for _ in range(7):
                socket.send_json({'type': 'audio', 'data': PCM})
            frames = read_turn(socket)
            proposals = [frame['result']['proposal'] for frame in frames
                         if frame['type'] == 'tool_result' and frame['result'].get('proposal')]
            assert len(proposals) == 1 and proposals[0]['kind'] == 'recovery'
            assert len(provider.client_turns) == len(provider.reviews) == 1
            assert not provider.review_requested_before_original_completion
            assert not any('I am ready' in frame.get('text', '') for frame in frames)
            assert value.pending_proposal['proposal_id'] == proposals[0]['proposal_id']
            assert value.recovery is None
            time.sleep(.1)
            assert value.operator_turns == 1  # Repeated ASR/END cannot create a second proposal.
            socket.send_json({'type': 'stop'})


def test_expired_late_round_renews_then_fresh_request_creates_one_owned_proposal(monkeypatch):
    offset = [0.0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: time.monotonic() + offset[0])

    class AbandonedProvider(ScriptProvider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                self.audio_count += 1

        async def send_tool_response(self, *, function_responses):
            self.responses.append(function_responses)

    old, fresh = AbandonedProvider(), ScriptProvider()
    opens = [0]

    @asynccontextmanager
    async def factory():
        opens[0] += 1
        yield old if opens[0] == 1 else fresh

    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            for _ in range(7):
                socket.send_json({'type': 'audio', 'data': PCM})
            for _ in range(100):
                if old.audio_count == 7:
                    break
                time.sleep(.01)
            assert old.audio_count == 7
            old.events.put_nowait(event(activity=('ACTIVITY_START', '0.200s')))
            old.events.put_nowait(event(call=NS(name='operate_workspace',
                args={'message': REQUEST}, id='abandoned-recovery-call')))
            time.sleep(.03)
            offset[0] += 9.1
            old.events.put_nowait(event())
            time.sleep(.03)
            offset[0] += live_voice.RECOVERY_TRANSPORT_LATENESS_SECONDS + .1
            old.events.put_nowait(event())
            assert socket.receive_json()['state'] == 'reconnecting'
            assert socket.receive_json()['state'] == 'ready'
            assert opens == [2]
            assert [response.id for response in old.responses] == ['abandoned-recovery-call']
            assert old.responses[0].response['action_applied'] is False
            assert value.operator_turns == 0 and value.pending_proposal is None

            old.events.put_nowait(event(text=REQUEST, finished=True))
            old.events.put_nowait(event(activity=('ACTIVITY_END', '6.280s')))
            old.events.put_nowait(event(call=NS(name='operate_workspace',
                args={'message': REQUEST}, id='abandoned-recovery-call')))
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = read_turn(socket)
            proposals = [frame['result']['proposal'] for frame in frames
                         if frame['type'] == 'tool_result' and
                         (frame['result'].get('proposal') or {}).get('kind') == 'recovery']
            assert len(proposals) == 1
            assert value.pending_proposal['proposal_id'] == proposals[0]['proposal_id']
            assert value.operator_turns == 1 and value.recovery is None
            assert opens == [2] and fresh.audio_count == 1
            socket.send_json({'type': 'stop'})


def open_voice(client, session):
    return client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                   headers={'origin': 'https://demo.test'})


def read_turn(socket):
    frames = []
    for _ in range(80):
        frame = socket.receive_json()
        frames.append(frame)
        assert frame['type'] != 'error', frame
        if frame['type'] == 'turn_complete':
            return frames
    raise AssertionError('No bounded turn completion')


def read_silent_denial(socket, provider):
    """A missing or unfinished ASR has a tool result, but no spoken turn."""
    async def receive_with_deadline(seconds):
        with anyio.move_on_after(seconds) as scope:
            message = await socket._send_rx.receive()
        if scope.cancel_called:
            return None
        socket._raise_on_close(message)
        return json.loads(message['text'])

    frames = []
    deadline = time.monotonic() + 5
    for _ in range(16):
        frame = socket.portal.call(receive_with_deadline, max(.001, deadline - time.monotonic()))
        assert frame is not None, f'No denied tool result before deadline: {frames}'
        frames.append(frame)
        assert frame['type'] != 'error', frame
        if frame['type'] == 'tool_result':
            break
    else:
        pytest.fail(f'No denied tool result within 16 frames: {frames}')

    async def wait_for_callback():
        with anyio.fail_after(2):
            while not provider.responses:
                await asyncio.sleep(.01)

    socket.portal.call(wait_for_callback)
    # Give the provider's queued reply a bounded chance to reach the browser.
    for _ in range(8):
        frame = socket.portal.call(receive_with_deadline, .15)
        if frame is None:
            break
        frames.append(frame)
    return frames


def start_voice(socket, session):
    socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                      'expected_context_version': session['context_version']})
    assert socket.receive_json()['type'] == 'ready'


def test_delayed_original_generic_turn_does_not_consume_canonical_recovery_review():
    provider = ScriptProvider()
    provider.delay_original_completion = True
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = read_turn(socket)
            assert not provider.review_requested_before_original_completion
            assert len(provider.client_turns) == 1
            assert len(provider.reviews) == 1
            assert any(frame['type'] == 'audio' for frame in frames)
            assert not any('I am ready' in frame.get('text', '') for frame in frames), frames
            assert value.pending_proposal['kind'] == 'recovery'
            assert value.recovery is None
            socket.send_json({'type': 'stop'})


def test_missing_original_completion_times_out_without_requesting_review_or_arming_approval():
    provider = ScriptProvider()
    provider.suppress_original_completion = True
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = []
            for _ in range(10):
                frames.append(socket.receive_json())
                if frames[-1]['type'] == 'error':
                    break
            assert frames[-1]['code'] == 'voice_review_incomplete'
            assert not provider.client_turns
            assert not any(frame['type'] in {'audio', 'turn_complete'} for frame in frames)
            assert value.pending_proposal['kind'] == 'recovery'
            assert value.recovery is None
            socket.send_json({'type': 'stop'})


@pytest.mark.parametrize('choice', ['none', 'read_workspace'])
@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize('renewed', [False, True])
def test_real_app_completes_explicit_recovery_without_provider_action_choice(choice, late, renewed):
    provider = ScriptProvider(choice, late=late)
    provider.duplicate_tools = choice == 'read_workspace'
    first = ScriptProvider()
    first.events.put_nowait(NS(go_away=NS(time_left='1s')))
    opens = [0]
    @asynccontextmanager
    async def factory():
        opens[0] += 1
        yield first if renewed and opens[0] == 1 else provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                        'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy((value.last_plan, value.recovery))
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            if renewed:
                assert socket.receive_json()['state'] == 'reconnecting'
                assert socket.receive_json()['state'] == 'ready'
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = read_turn(socket)
            proposals = [frame['result'] for frame in frames if frame['type'] == 'tool_result'
                         and (frame['result'].get('proposal') or {}).get('kind') == 'recovery']
            assert proposals, frames
            assert value.pending_proposal['proposal_id'] == proposals[0]['proposal']['proposal_id']
            assert 'confirm action in English' in proposals[0]['assistant_text']
            assert proposals[0]['proposal']['parameters']['delay_days_by_offer'] == {plan['selected_offer_id']: 7}
            assert provider.reviews and 'Apply recovery for' in provider.reviews[0]
            assert '8000 kilograms' in provider.reviews[0] and 'confirm action' in provider.reviews[0]
            assert any(frame['type'] == 'audio' for frame in frames)
            assert not any('I am ready' in frame.get('text', '') for frame in frames)
            assert value.operator_turns == 1
            assert before == (value.last_plan, value.recovery)
            assert {response.id for response in provider.responses} == (
                {'real-call-1', 'real-call-2', 'real-call-3'} if choice == 'read_workspace' else set())
            assert len({frame['proposal']['proposal_id'] for frame in proposals}) == 1
            socket.send_json({'type': 'stop'})


def test_generic_provider_readback_is_never_forwarded_or_marked_complete():
    provider = ScriptProvider()
    provider.bad_review = True
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = []
            for _ in range(80):
                frame = socket.receive_json()
                frames.append(frame)
                if frame['type'] == 'error':
                    break
            assert frames[-1]['code'] == 'voice_review_incomplete'
            assert not any(frame['type'] in {'turn_complete', 'audio'} for frame in frames)
            assert not any('I am ready' in frame.get('text', '') for frame in frames)
            assert value.pending_proposal['kind'] == 'recovery' and value.recovery is None
            socket.send_json({'type': 'stop'})


@pytest.mark.parametrize('stale', [False, True])
def test_routed_real_recovery_review_requires_fresh_verified_consent_and_applies_once(stale):
    provider = ScriptProvider('read_workspace')
    provider.approval_before_review = True
    now = [NOW]
    verified = []
    async def verify(audio):
        verified.append(audio)
        return 'confirm action'
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: now[0], live_client_factory=factory, voice_consent_verifier=verify)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = read_turn(socket)
            denied = [frame['result'] for frame in frames if frame['type'] == 'tool_result'
                      and frame['result']['status'] == 'needs_clarification']
            # The early confirm is a model event without fresh user approval.
            assert denied and value.recovery is None
            if stale:
                now[0] += timedelta(minutes=6)
            socket.send_json({'type': 'audio', 'data': PCM})
            applied = [frame['result'] for frame in read_turn(socket) if frame['type'] == 'tool_result']
            assert applied
            if stale:
                assert applied[-1]['status'] == 'needs_clarification'
                assert value.recovery is None
            else:
                assert applied[-1]['action_result']['tool'] == 'recovery'
                assert verified and value.recovery is not None
                accepted = deepcopy(value.recovery)
                socket.send_json({'type': 'audio', 'data': PCM})
                replay = [frame['result'] for frame in read_turn(socket) if frame['type'] == 'tool_result']
                assert replay[-1]['status'] == 'needs_clarification'
                assert accepted == value.recovery
            socket.send_json({'type': 'stop'})


@pytest.mark.parametrize('spoken,finished', [
    (None, None),
    ('Do not prepare any recovery scenario.', True),
    ('Read this quote: "Prepare a recovery scenario for a 7-day delay."', True),
    ('Prepare a recovery scenario', False),
])
def test_actual_operator_cannot_retain_recovery_from_paraphrased_tool_args(spoken, finished):
    provider = ScriptProvider('operate_workspace', spoken=spoken, finished=finished)
    model = OperatorProvider(intent='recovery', delay_days=7)
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory, operator_provider=model)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                        'X-CSRF-Token': session['csrf_token']})
        model.offer_id = plan['selected_offer_id']
        value = app.state.sessions.get(session['session_id'])
        with open_voice(client, session) as socket:
            start_voice(socket, session)
            socket.send_json({'type': 'audio', 'data': PCM})
            frames = (read_silent_denial(socket, provider) if spoken is None or finished is False
                      else read_turn(socket))
            if spoken is None or finished is False:
                assert len(provider.responses) == 1
                assert provider.responses[0].id == 'real-call-1'
                denied = provider.responses[0].response
                assert denied['status'] == 'needs_clarification'
                assert denied.get('proposal') is None
                assert denied.get('action_applied') is not True
                assert not any(frame['type'] in {'audio', 'turn_complete'} or
                               frame.get('direction') == 'output' for frame in frames)
                assert not model.calls
                assert value.operator_turns == 0
            assert value.pending_proposal is None
            assert not any((frame.get('result', {}).get('proposal') or {}).get('kind') == 'recovery'
                           for frame in frames)
            assert not any(frame.get('result', {}).get('action_applied')
                           for frame in frames if frame['type'] == 'tool_result')
            assert value.recovery is None
            assert all((row.get('result', {}).get('proposal') or {}).get('kind') != 'recovery'
                       for row in value.idempotency.values())
            assert all((row.get('turn', {}).get('proposal') or {}).get('kind') != 'recovery'
                       for row in value.chat_history.get(value.company_id, []))
            socket.send_json({'type': 'stop'})
