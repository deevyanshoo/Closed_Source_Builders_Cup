"""Hosted ASR regressions through the real public socket and operator boundary."""
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo import live_voice
from test_demo_live_voice import Provider
from test_demo_live_voice_supply_coverage import event, BRIDGE
from test_demo_operator import Provider as OperatorProvider


RFP_CHUNKS = (
    'Prepare an earlier delivery. RFP for the supply gap using our current plan.',
    ' Save the draft without sending it.',
)
CLARIFICATION = 'Should I prepare an earlier-delivery quotation request for the current supply gap?'
PCM = b'\x01\x00\x02\x00'


class ClarificationProvider(Provider):
    def __init__(self, chunks=RFP_CHUNKS, *, reply='grounded', stale=False):
        super().__init__()
        self.chunks, self.reply, self.stale = chunks, reply, stale
        self.turn = 0
        self.model_message = None
        self.tool_name = 'operate_workspace'
        self.executed_messages = []
        self.marker = 'final'
        self.order = 'asr_first'
        self.input_script = None
        self.renew_first = False
        self.script_audio_bytes = 0
        self.received_bytes = 0
        self.original_complete = False
        self.early_start = None

    def caption(self, text):
        frame = event(text=text)
        if self.marker == 'absent':
            frame.server_content.input_transcription = NS(text=text)
        elif self.marker == 'sdk_default':
            from google.genai.types import Transcription
            frame.server_content.input_transcription = Transcription(text=text)
        return frame

    async def send_realtime_input(self, **kwargs):
        if not kwargs.get('audio'):
            return
        self.received_bytes += len(kwargs['audio'].data)
        if self.early_start is not None:
            await self.events.put(event(activity=('ACTIVITY_START', self.early_start)))
            self.early_start = None
        if self.received_bytes < self.script_audio_bytes:
            return
        self.turn += 1
        if self.renew_first and self.turn == 1:
            await self.events.put(NS(go_away=NS(time_left='0s')))
            return
        args = ({'message': self.model_message or ''.join(self.chunks)}
                if self.tool_name == 'operate_workspace' else {})
        call = event(call=NS(name=self.tool_name, args=args, id=f'operate-{self.turn}'))
        if self.input_script is not None:
            for frame in self.input_script:
                if isinstance(frame, float):
                    await asyncio.sleep(frame)
                else:
                    await self.events.put(frame)
            await self.events.put(call)
            if self.original_complete:
                await self.events.put(event(complete=True))
            return
        if not self.stale or self.turn == 1:
            await self.events.put(event(activity=('ACTIVITY_START', '0s')))
            if self.order != 'asr_first':
                await self.events.put(call)
            if self.order == 'after_vad':
                await self.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
            for index, chunk in enumerate(self.chunks):
                frame = self.caption(chunk)
                if self.marker == 'final':
                    frame.server_content.input_transcription.finished = index == len(self.chunks) - 1
                await self.events.put(frame)
            if self.order != 'after_vad':
                await self.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
        # A new tool ID with no fresh ASR must not borrow the preceding turn.
        if self.order == 'asr_first':
            await self.events.put(call)

    async def send_tool_response(self, *, function_responses):
        self.tool_response = function_responses
        response = function_responses.response
        text = response.get('required_spoken_response', response.get('assistant_text', ''))
        if self.reply == 'contradictory':
            text += ' I have sent it to the supplier.'
        if self.reply == 'silent':
            return
        # Separate PCM and caption frames exercise buffering and finalization.
        if self.reply != 'caption_only':
            await self.events.put(NS(tool_call=None, server_content=NS(
                model_turn=NS(parts=[NS(inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]),
                turn_complete=False, interrupted=self.reply == 'interrupted')))
        await self.events.put(NS(tool_call=None, server_content=NS(
            output_transcription=NS(text=text), turn_complete=True)))


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .001)

    @asynccontextmanager
    async def factory():
        yield provider

    provider = ClarificationProvider()
    run_live_voice = live_voice.run_live_voice

    async def observe_execution(*args, operate_workspace, **kwargs):
        async def operate(message, version, key):
            provider.executed_messages.append(message)
            return await operate_workspace(message, version, key)
        return await run_live_voice(*args, operate_workspace=operate, **kwargs)

    monkeypatch.setattr(live_voice, 'run_live_voice', observe_execution)
    operator = OperatorProvider(intent='recovery', evidence=())
    application = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
        operator_provider=operator, live_client_factory=factory)
    with TestClient(application, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        started = client.post('/api/demo/judge/start', headers=headers, json={
            'expected_context_version': 1, 'idempotency_key': 'clarification-start'})
        assert started.status_code == 200, started.text
        yield client, provider, operator


def receive_turn(socket):
    frames = []
    while True:
        frame = socket.receive_json()
        frames.append(frame)
        if frame['type'] == 'error' or (frame['type'] == 'turn_complete'
                and any(item['type'] == 'tool_result' for item in frames)):
            return frames


def ask(client, *, audio_bytes=4):
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        for offset in range(0, audio_bytes, 32000):
            chunk = PCM * (min(32000, audio_bytes - offset) // len(PCM))
            socket.send_json({'type': 'audio', 'data': base64.b64encode(chunk).decode()})
        frames = receive_turn(socket)
        socket.send_json({'type': 'stop'})
    return frames


def protected(client):
    state = client.get('/api/demo/session').json()
    return {key: state.get(key) for key in ('company_id', 'context_version', 'plan', 'recovery',
        'offers', 'rfq', 'drafts', 'simulated_outbox', 'watch_items', 'research')}


def tool_result(frames):
    return next(frame['result'] for frame in frames if frame['type'] == 'tool_result')


def assert_audio(frames):
    audio = [frame for frame in frames if frame['type'] == 'audio']
    assert len(audio) == 1, frames
    assert audio[0]['sample_rate_hertz'] == 24000
    assert base64.b64decode(audio[0]['data']) == PCM
    assert frames[-1]['type'] == 'turn_complete'
    assert not any(frame['type'] == 'error' for frame in frames)
    assert len([frame for frame in frames if frame['type'] == 'transcript'
                and frame.get('direction') == 'output']) == 1


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_exact_hosted_rfp_asr_clarifies_locally_without_mutation(workspace, tool_name):
    client, provider, operator = workspace
    provider.tool_name = tool_name
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    assert result['assistant_text'] == CLARIFICATION
    assert result['status'] == 'needs_clarification' and result['read_only'] is True
    assert result['proposal'] is None and result.get('action_result') is None
    assert provider.executed_messages == [''.join(RFP_CHUNKS)]
    assert operator.calls == []
    assert protected(client) == before
    assert provider.tool_response.response['required_spoken_response'] == CLARIFICATION
    assert_audio(frames)


def test_existing_local_clarification_releases_grounded_pcm_at_finalization(workspace):
    client, provider, operator = workspace
    provider.chunks = ('Prepare a bridge RFQ if the supplier agrees.',)
    before = protected(client)
    frames = ask(client)
    assert tool_result(frames)['status'] == 'needs_clarification'
    assert operator.calls == [] and protected(client) == before
    assert_audio(frames)


@pytest.mark.parametrize('words', [
    'Prepare an earlier delivery request for quotation for the supply gap using our current plan. Save the draft without sending it.',
    BRIDGE,
])
@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_unambiguous_operate_saves_exact_bridge_and_download_with_audio(workspace, words, tool_name):
    client, provider, operator = workspace
    provider.tool_name = tool_name
    provider.chunks = (words,)
    plan = client.get('/api/demo/session').json()['plan']
    frames = ask(client)
    result = tool_result(frames)
    state = client.get('/api/demo/session').json()
    rfq = state['rfq']
    assert result['status'] == 'completed' and result['action_result']['tool'] == 'prepare_rfq'
    assert rfq['bridge'] is True and rfq['status'] == 'draft_unsent'
    assert rfq['quantity_kg'] == '1333.3' == plan['supply_coverage']['bridge_quantity']
    assert rfq['need_by'] == '2026-10-20'
    assert state['plan'] == plan and state['simulated_outbox'] == []
    assert len(state['drafts']) == 1 and state['drafts'][0]['draft_id'] == rfq['draft_id']
    download = client.get(f"/api/demo/rfq/{rfq['draft_id']}/download")
    assert download.status_code == 200 and download.text == rfq['text'] == state['drafts'][0]['body']
    assert operator.calls == []
    assert_audio(frames)


@pytest.mark.parametrize('words', [
    'Prepare an earlier delivery RFP if the supplier agrees.',
    'Do not prepare an earlier delivery RFP for the supply gap.',
    'Prepare an earlier delivery RFP for polymer using our current plan.',
    'Draft a bridge RFP for Northstar Foods.',
    'Prepare an earlier delivery RFP using plan ID stale.',
    'Save a draft request for proposal for the supply gap using our current plan.',
    'Prepare an earlier delivery document for the supply gap. Save the draft without sending it.',
    'Prepare an earlier delivery RFP or RFQ for the supply gap using our current plan.',
])
@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_ambiguous_document_guards_fail_closed_locally(workspace, words, tool_name):
    client, provider, operator = workspace
    provider.tool_name = tool_name
    provider.chunks = (words,)
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification' and result['read_only'] is True
    assert result['proposal'] is None and result.get('action_result') is None
    assert operator.calls == []
    assert protected(client) == before


@pytest.mark.parametrize('reply', ['contradictory', 'interrupted'])
@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_clarification_does_not_release_contradictory_or_interrupted_audio(workspace, reply, tool_name):
    client, provider, operator = workspace
    provider.tool_name = tool_name
    provider.chunks = RFP_CHUNKS
    provider.reply = reply
    before = protected(client)
    frames = ask(client)
    assert tool_result(frames)['assistant_text'] == CLARIFICATION
    assert not any(frame['type'] == 'audio' for frame in frames)
    assert not any('sent it' in frame.get('text', '') for frame in frames)
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('reply', ['caption_only', 'silent'])
def test_clarification_without_pcm_finishes_with_bounded_error(workspace, monkeypatch, reply):
    client, provider, operator = workspace
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', .02)
    provider.reply = reply
    frames = ask(client)
    assert frames[-1]['type'] == 'error' and frames[-1]['code'] == 'voice_review_incomplete'
    assert 'clarification' in frames[-1]['message'].lower()
    assert not any(frame['type'] == 'audio' for frame in frames)
    assert client.get('/api/demo/session').json()['drafts'] == []


@pytest.mark.parametrize('chunks', [RFP_CHUNKS, ('Prepare a bridge RFQ if the supplier agrees.',),
    ('Prepare an earlier delivery RFQ for polymer using our current plan.',),
    ('Draft a bridge RFQ for Northstar Foods.',)])
@pytest.mark.parametrize('model_message', [BRIDGE, 'Read the current plan'])
@pytest.mark.parametrize('marker', ['final', 'sdk_default'])
def test_model_arguments_cannot_replace_ambiguous_or_conditional_asr(workspace, chunks, model_message, marker):
    client, provider, operator = workspace
    provider.chunks, provider.model_message = chunks, model_message
    provider.marker = marker
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification' and result.get('action_result') is None
    assert provider.executed_messages == [''.join(chunks)]
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_model_bridge_arguments_without_any_asr_cannot_create_draft(workspace, tool_name):
    client, provider, operator = workspace
    provider.tool_name = tool_name
    provider.chunks, provider.stale, provider.turn = (BRIDGE,), True, 1
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification' and result.get('action_result') is None
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
@pytest.mark.parametrize('marker', ['final', 'sdk_default'])
def test_replayed_operate_cannot_borrow_completed_asr(workspace, tool_name, marker):
    client, provider, operator = workspace
    provider.tool_name = tool_name
    provider.marker = marker
    provider.chunks, provider.stale = (BRIDGE,), True
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        first = receive_turn(socket)
        assert tool_result(first)['status'] == 'completed'
        before = protected(client)
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        second = receive_turn(socket)
        assert tool_result(second)['status'] == 'needs_clarification'
        assert tool_result(second).get('action_result') is None
        assert protected(client) == before and operator.calls == []
        socket.send_json({'type': 'stop'})


@pytest.mark.parametrize('change_plan', [False, True])
def test_nonbridge_call_never_executes_closed_bridge_asr(workspace, change_plan):
    client, provider, operator = workspace
    provider.chunks, provider.stale = (BRIDGE,), True
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        assert tool_result(receive_turn(socket))['action_result']['tool'] == 'prepare_rfq'
        assert provider.executed_messages == [BRIDGE]
        if change_plan:
            previous_plan = protected(client)['plan']
            operator.intent = operator.action = 'compare'
            compared = client.post('/api/demo/operator', headers={
                'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}, json={
                    'message': 'Compare suppliers for copper.',
                    'expected_context_version': session['context_version'],
                    'idempotency_key': 'typed-new-plan'})
            assert compared.status_code == 200, compared.text
            assert protected(client)['plan']['plan_id'] != previous_plan['plan_id']
        before = protected(client)
        operator.intent, operator.action = 'recovery', 'recovery'
        provider.model_message = 'Read the current plan'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        result = tool_result(receive_turn(socket))
        assert provider.executed_messages == [BRIDGE, 'Read the current plan']
        assert result['read_only'] is True and result.get('action_result') is None
        assert protected(client) == before
        socket.send_json({'type': 'stop'})


def test_generic_ordinary_rfq_stays_on_ordinary_action_path(workspace):
    client, provider, operator = workspace
    provider.tool_name = 'prepare_rfq'
    provider.chunks = ('Prepare an RFQ for the current sourcing plan.',)
    plan = protected(client)['plan']
    frames = ask(client)
    result = tool_result(frames)
    state = client.get('/api/demo/session').json()
    assert result['status'] == 'completed' and result['action_result']['tool'] == 'prepare_rfq'
    assert provider.executed_messages == [] and operator.calls == []
    assert state['rfq'].get('bridge', False) is False
    assert state['rfq']['status'] == 'draft_unsent' and len(state['drafts']) == 1
    assert state['plan'] == plan and state['simulated_outbox'] == []
    assert_audio(frames)


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
@pytest.mark.parametrize('marker', ['absent', 'sdk_default'])
@pytest.mark.parametrize('order', ['asr_first', 'tool_first', 'after_vad'])
@pytest.mark.parametrize('document', ['proposal', 'quotation'])
def test_optional_finished_marker_uses_settled_current_asr(workspace, tool_name, marker, order, document):
    client, provider, operator = workspace
    provider.tool_name, provider.marker, provider.order = tool_name, marker, order
    provider.chunks = (f'Prepare an earlier delivery request for {document} for the supply gap using our current plan.',
                       ' Save the draft without sending it.')
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    if document == 'proposal':
        assert result['status'] == 'needs_clarification' and result['read_only'] is True
        assert result['assistant_text'] == CLARIFICATION
        assert protected(client) == before
    else:
        state = client.get('/api/demo/session').json()
        assert result['status'] == 'completed' and result['action_result']['tool'] == 'prepare_rfq'
        assert state['rfq']['bridge'] is True and state['rfq']['status'] == 'draft_unsent'
        assert state['rfq']['quantity_kg'] == '1333.3' and state['rfq']['need_by'] == '2026-10-20'
        assert state['plan'] == before['plan'] and state['simulated_outbox'] == []
        assert len(state['drafts']) == 1
        assert client.get(f"/api/demo/rfq/{state['rfq']['draft_id']}/download").text == state['rfq']['text']
    assert provider.executed_messages == [''.join(provider.chunks)]
    assert operator.calls == []
    assert_audio(frames)


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
@pytest.mark.parametrize('marker', ['final', 'sdk_default'])
@pytest.mark.parametrize('case', ['late_negation', 'late_correction', 'late_foreign',
    'explicit_incomplete', 'empty_incomplete', 'incomplete_then_omitted',
    'late_after_settlement', 'late_empty_incomplete', 'no_asr', 'no_vad', 'stale_vad', 'closed_turn',
    'caption_deadline'])
def test_optional_marker_adversarial_input_cannot_save(workspace, monkeypatch, tool_name, marker, case):
    client, provider, operator = workspace
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .08)
    provider.tool_name, provider.marker, provider.chunks = tool_name, marker, (BRIDGE,)
    start = event(activity=('ACTIVITY_START', '0s'))
    end = event(activity=('ACTIVITY_END', '0.000125s'))
    empty_incomplete = event()
    empty_incomplete.server_content.input_transcription = NS(text=None, finished=False)
    script = [start, provider.caption(BRIDGE), end]
    if case in {'late_negation', 'late_correction', 'late_foreign'}:
        tail = {'late_negation': ' Do not create the draft.',
                'late_correction': 'Do not ' + BRIDGE,
                'late_foreign': ' For Harvest Foods.'}[case]
        script += [.01, provider.caption(tail)]
    elif case == 'explicit_incomplete':
        script = [start, event(text=BRIDGE, finished=False), end]
    elif case == 'empty_incomplete':
        script += [empty_incomplete]
    elif case == 'incomplete_then_omitted':
        script = [start, event(text='Prepare an earlier delivery', finished=False), end,
                  event(text=BRIDGE, finished=None)]
    elif case == 'late_after_settlement':
        script += [.05, provider.caption(' Do not create the draft.')]
    elif case == 'late_empty_incomplete':
        script += [.05, empty_incomplete]
    elif case == 'no_asr':
        script = [start, end]
    elif case == 'no_vad':
        script = [provider.caption(BRIDGE)]
    elif case == 'stale_vad':
        script = [start, provider.caption(BRIDGE), event(activity=('ACTIVITY_END', '1s'))]
    elif case == 'closed_turn':
        script += [.05, event(complete=True), provider.caption(BRIDGE)]
    elif case == 'caption_deadline':
        # Every delta postpones quiet; the absolute input cap must deny, not
        # authorize the most recent unquiet positive transcript or wait forever.
        script += [.02, provider.caption(' ' + BRIDGE)] * 5
    provider.input_script = script
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    assert result['status'] == 'needs_clarification' and result.get('action_result') is None
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
@pytest.mark.parametrize('mode', ['late_first_caption', 'empty_final_marker'])
def test_optional_marker_waits_for_current_input_before_routing(workspace, monkeypatch, tool_name, mode):
    client, provider, operator = workspace
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .15)
    provider.tool_name, provider.marker, provider.chunks = tool_name, 'sdk_default', (BRIDGE,)
    start = event(activity=('ACTIVITY_START', '0s'))
    end = event(activity=('ACTIVITY_END', '0.000125s'))
    if mode == 'late_first_caption':
        provider.input_script = [start, end, .05, provider.caption(BRIDGE)]
    else:
        final = event()
        final.server_content.input_transcription = NS(text=None, finished=True)
        provider.input_script = [start, event(text=BRIDGE, finished=False), end, .01, final]
    frames = ask(client)
    assert tool_result(frames)['status'] == 'completed'
    assert provider.executed_messages == [BRIDGE] and operator.calls == []
    assert client.get('/api/demo/session').json()['rfq']['quantity_kg'] == '1333.3'
    assert_audio(frames)


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_optional_marker_after_renewal_requires_fresh_vad_and_audio(workspace, tool_name):
    client, provider, operator = workspace
    provider.tool_name, provider.marker, provider.chunks = tool_name, 'sdk_default', (BRIDGE,)
    provider.renew_first = True
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        assert socket.receive_json()['state'] == 'reconnecting'
        assert socket.receive_json()['state'] == 'ready'
        assert protected(client)['drafts'] == []
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        frames = receive_turn(socket)
        assert tool_result(frames)['status'] == 'completed'
        assert provider.executed_messages == [BRIDGE] and operator.calls == []
        assert client.get('/api/demo/session').json()['rfq']['quantity_kg'] == '1333.3'
        assert_audio(frames)
        socket.send_json({'type': 'stop'})


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
@pytest.mark.parametrize('document', ['quotation', 'proposal'])
def test_captured_provider_8_2_second_vad_and_early_turn_complete(workspace, monkeypatch, tool_name, document):
    client, provider, operator = workspace
    # Real bounds and captured order: 0.2s START, 127-character ASR with
    # omitted finished, 8.4s END, tool, turn_complete before 250ms settlement.
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .25)
    provider.tool_name, provider.marker = tool_name, 'sdk_default'
    words = (f'Prepare an earlier delivery request for {document}. For the supply gap using our current plan, '
             'save the draft without sending.')
    provider.chunks = (words,)
    provider.script_audio_bytes = 268800  # 8.4 seconds of actual PCM16 input.
    provider.original_complete = True
    provider.input_script = [event(activity=('ACTIVITY_START', '0.200s')),
        provider.caption(words), event(activity=('ACTIVITY_END', '8.400s'))]
    before = protected(client)
    frames = ask(client, audio_bytes=provider.script_audio_bytes)
    result = tool_result(frames)
    if document == 'quotation':
        assert result['status'] == 'completed' and result['action_result']['tool'] == 'prepare_rfq'
        state = client.get('/api/demo/session').json()
        assert state['rfq']['bridge'] is True and state['rfq']['status'] == 'draft_unsent'
        assert state['rfq']['quantity_kg'] == '1333.3' and state['rfq']['need_by'] == '2026-10-20'
        assert state['plan'] == before['plan'] and state['simulated_outbox'] == []
        assert len(state['drafts']) == 1
        assert client.get(f"/api/demo/rfq/{state['rfq']['draft_id']}/download").text == state['rfq']['text']
    else:
        assert result['assistant_text'] == CLARIFICATION
        assert result['status'] == 'needs_clarification' and result['read_only'] is True
        assert result['proposal'] is None and result.get('action_result') is None
        assert protected(client) == before
    assert provider.executed_messages == [words] and operator.calls == []
    assert provider.received_bytes == 268800
    assert_audio(frames)


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
@pytest.mark.parametrize('case', ['over_command_limit', 'outside_audio', 'missing_start',
    'stale_start', 'interrupted', 'explicit_incomplete', 'empty_incomplete', 'late_negation'])
def test_long_command_vad_guards_never_authorize_from_timeout(workspace, monkeypatch, tool_name, case):
    client, provider, operator = workspace
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .1)
    provider.tool_name, provider.marker, provider.chunks = tool_name, 'sdk_default', (BRIDGE,)
    provider.script_audio_bytes = (31 * 32000 if case == 'over_command_limit' else 268800)
    script = [event(activity=('ACTIVITY_START', '0.200s')), provider.caption(BRIDGE),
              event(activity=('ACTIVITY_END', '8.400s'))]
    if case == 'over_command_limit':
        # Start is observed before the retained consent ring has rolled over.
        provider.early_start = '0.200s'
        script = [provider.caption(BRIDGE),
                  event(activity=('ACTIVITY_END', '31s'))]
    elif case == 'outside_audio':
        script[-1] = event(activity=('ACTIVITY_END', '8.500s'))
    elif case == 'missing_start':
        script = script[1:]
    elif case == 'stale_start':
        script = [event(activity=('ACTIVITY_START', '0s')),
                  event(activity=('ACTIVITY_END', '0.200s'))] + script
        script[2] = event(activity=('ACTIVITY_START', '0.100s'))
    elif case == 'interrupted':
        interrupted = event()
        interrupted.server_content.interrupted = True
        script += [interrupted]
    elif case == 'explicit_incomplete':
        script[1] = event(text=BRIDGE, finished=False)
    elif case == 'empty_incomplete':
        incomplete = event()
        incomplete.server_content.input_transcription = NS(text=None, finished=False)
        script += [incomplete]
    elif case == 'late_negation':
        script += [.01, provider.caption(' Do not create the draft.')]
    provider.input_script = script
    before = protected(client)
    result = tool_result(ask(client, audio_bytes=provider.script_audio_bytes))
    assert result['status'] == 'needs_clarification' and result.get('action_result') is None
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool_name', ['operate_workspace', 'prepare_rfq'])
def test_ordinary_command_at_30_second_ceiling_can_settle(workspace, tool_name):
    client, provider, operator = workspace
    provider.tool_name, provider.marker, provider.chunks = tool_name, 'sdk_default', (BRIDGE,)
    provider.script_audio_bytes = 30 * 32000
    provider.early_start = '0s'
    provider.input_script = [provider.caption(BRIDGE), event(activity=('ACTIVITY_END', '30s'))]
    frames = ask(client, audio_bytes=provider.script_audio_bytes)
    assert tool_result(frames)['status'] == 'completed'
    assert provider.executed_messages == [BRIDGE] and operator.calls == []
    assert client.get('/api/demo/session').json()['rfq']['quantity_kg'] == '1333.3'
    assert_audio(frames)


@pytest.mark.parametrize('gate_kind', ['research', 'action'])
def test_long_command_window_does_not_expand_consent_clip(gate_kind):
    from test_demo_voice_research_consent import (scenario as research_scenario,
        prepare, send_pcm, finish, QUERY)
    from test_demo_voice_workspace_actions import scenario as action_scenario

    async def check():
        verified = []
        async def verify(clip):
            verified.append(clip)
            return 'confirm ' + gate_kind

        if gate_kind == 'research':
            _, _, socket, provider, calls, task = await research_scenario(verifier=verify)
            await prepare(socket, provider)
            review = f'Search for: {QUERY} Say confirm research to start.'
            name, proposal_id = 'confirm_research', 'proposal-1'
        else:
            _, _, socket, provider, calls, task, review = await action_scenario(verify)
            name, proposal_id = 'confirm_workspace_action', 'watch-1'
        try:
            await provider.spoken('output', review, complete=True)
            await socket.next('turn_complete')
            for _ in range(8):
                await send_pcm(socket, provider)
            await send_pcm(socket, provider, size=12800)
            await provider.activity('ACTIVITY_START', '0.200s')
            await provider.spoken('input', 'confirm ' + gate_kind, finished=None)
            await socket.next('transcript', 'input')
            await provider.activity('ACTIVITY_END', '8.400s')
            await provider.call(name, {'proposal_id': proposal_id}, 'overlong-consent')
            result = (await socket.next('tool_result'))['result']
            assert result['read_only'] is True and result.get('action_result') is None
            assert len(calls) == 1 and verified == []
        finally:
            await finish(socket, task)
    asyncio.run(check())
