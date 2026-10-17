"""Hosted ASR regressions through the real public socket and operator boundary."""
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

    async def send_realtime_input(self, **kwargs):
        if not kwargs.get('audio'):
            return
        self.turn += 1
        if not self.stale or self.turn == 1:
            await self.events.put(event(activity=('ACTIVITY_START', '0s')))
            for index, chunk in enumerate(self.chunks):
                await self.events.put(event(text=chunk, finished=index == len(self.chunks) - 1))
            await self.events.put(event(activity=('ACTIVITY_END', '0.000125s')))
        # A new tool ID with no fresh ASR must not borrow the preceding turn.
        await self.events.put(event(call=NS(name='operate_workspace',
            args={'message': self.model_message or ''.join(self.chunks)}, id=f'operate-{self.turn}')))

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
        if frame['type'] in {'turn_complete', 'error'}:
            return frames


def ask(client):
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
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


def test_exact_hosted_rfp_asr_clarifies_locally_without_mutation(workspace):
    client, provider, operator = workspace
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    assert result['assistant_text'] == CLARIFICATION
    assert result['status'] == 'needs_clarification' and result['read_only'] is True
    assert result['proposal'] is None and result.get('action_result') is None
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
def test_unambiguous_operate_saves_exact_bridge_and_download_with_audio(workspace, words):
    client, provider, operator = workspace
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
def test_ambiguous_document_guards_fail_closed_locally(workspace, words):
    client, provider, operator = workspace
    provider.chunks = (words,)
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification' and result['read_only'] is True
    assert result['proposal'] is None and result.get('action_result') is None
    assert operator.calls == []
    assert protected(client) == before


@pytest.mark.parametrize('reply', ['contradictory', 'interrupted'])
def test_clarification_does_not_release_contradictory_or_interrupted_audio(workspace, reply):
    client, provider, operator = workspace
    provider.chunks = ('Prepare a bridge RFQ if the supplier agrees.',)
    provider.reply = reply
    frames = ask(client)
    assert not any(frame['type'] == 'audio' for frame in frames)
    assert not any('sent it' in frame.get('text', '') for frame in frames)


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


@pytest.mark.parametrize('chunks', [RFP_CHUNKS, ('Prepare a bridge RFQ if the supplier agrees.',)])
def test_model_arguments_cannot_replace_ambiguous_or_conditional_asr(workspace, chunks):
    client, provider, operator = workspace
    provider.chunks, provider.model_message = chunks, BRIDGE
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification' and result.get('action_result') is None
    assert protected(client) == before and operator.calls == []


def test_model_bridge_arguments_without_any_asr_cannot_create_draft(workspace):
    client, provider, operator = workspace
    provider.chunks, provider.stale, provider.turn = (BRIDGE,), True, 1
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification' and result.get('action_result') is None
    assert protected(client) == before and operator.calls == []


def test_replayed_operate_cannot_borrow_completed_asr(workspace):
    client, provider, operator = workspace
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
