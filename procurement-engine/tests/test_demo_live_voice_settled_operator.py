"""Semantic tools must wait for actual current microphone evidence at the socket."""
import asyncio
import base64
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

from procure.demo import live_voice
from test_demo_live_voice_clarification import workspace, assert_audio, CLARIFICATION
from test_demo_live_voice_native_grounded import CANONICAL, MISSING_DOCUMENT, protected, terra
from test_demo_live_voice_supply_coverage import event, BRIDGE
from test_demo_live_voice_clarification_boundary import output, WORDS, AdvanceEvent
from time import monotonic

OBSERVED = 'Prepare an early deliveryfor the supply gap using our current plan. Save the draft without sending it.'


@contextmanager
def broker(workspace, monkeypatch):
    client, provider, operator = workspace
    terra(client)
    sent, responses = [], []
    run = live_voice.run_live_voice

    async def observe(websocket, **kwargs):
        send = websocket.send_json
        async def record(frame):
            sent.append(frame)
            await send(frame)
        websocket.send_json = record
        return await run(websocket, **kwargs)
    monkeypatch.setattr(live_voice, 'run_live_voice', observe)

    received_bytes = [0]
    async def audio(**kwargs):
        if kwargs.get('audio'):
            received_bytes[0] += len(kwargs['audio'].data)
    async def respond(*, function_responses):
        responses.append(function_responses)
    async def fabricated(**kwargs):
        raise AssertionError('No synthetic user turn')
    provider.send_realtime_input = audio
    provider.send_tool_response = respond
    provider.send_client_content = fabricated
    session = client.get('/api/demo/session').json()
    before = protected(client)

    def emit(*frames):
        async def put():
            for frame in frames:
                await provider.events.put(frame)
            await asyncio.sleep(.02)
        client.portal.call(put)

    def wait_response(count=1):
        async def wait():
            async with asyncio.timeout(2):
                while len(responses) < count:
                    await asyncio.sleep(.001)
        client.portal.call(wait)
        return responses[count-1].response

    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
            headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                         'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        def microphone(total_bytes):
            remaining = total_bytes - received_bytes[0]
            while remaining > 0:
                size = min(32000, remaining)
                socket.send_json({'type': 'audio', 'data': base64.b64encode(b'\x01\x00' * (size // 2)).decode()})
                remaining -= size
            async def delivered():
                async with asyncio.timeout(2):
                    while received_bytes[0] < total_bytes:
                        await asyncio.sleep(.001)
            client.portal.call(delivered)

        def speech(words, generation=0, finished=True):
            if generation:
                socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            emit(event(activity=('ACTIVITY_START', f'{generation * .000125}s')),
                 event(text=words, finished=finished),
                 event(activity=('ACTIVITY_END', f'{(generation + 1) * .000125}s')))
        try:
            yield NS(client=client, provider=provider, operator=operator, sent=sent,
                responses=responses, emit=emit, wait=wait_response, speech=speech,
                socket=socket, before=before, microphone=microphone)
        finally:
            socket.send_json({'type': 'stop'})


def call(name='operate_workspace', ident='early'):
    return event(call=NS(name=name, args={'message': 'Help with this task.'}
                        if name == 'operate_workspace' else {}, id=ident))


@pytest.mark.parametrize('tool', ['operate_workspace', 'prepare_rfq', 'read_workspace'])
@pytest.mark.parametrize('words,answer', [(OBSERVED, CLARIFICATION), (MISSING_DOCUMENT, CLARIFICATION),
                                         (WORDS, CANONICAL)])
def test_early_semantic_call_waits_for_current_asr(workspace, monkeypatch, tool, words, answer):
    with broker(workspace, monkeypatch) as case:
        case.emit(call(tool), output(caption='Select a current baseline offer.', audio=True, complete=True))
        assert case.responses == []
        assert not any(f['type'] in {'tool_result', 'audio', 'turn_complete', 'error'} for f in case.sent)
        case.speech(words)
        result = case.wait()
        if tool == 'read_workspace':
            case.emit(call(ident='bound-operate'))
            result = case.wait(2)
        assert result['assistant_text'] == answer
        case.emit(output(caption=answer, audio=True, complete=True))
        assert_audio([f for f in case.sent if f['type'] != 'ready'])
        assert case.operator.calls == []
        assert case.provider.executed_messages == [words]
        assert protected(case.client) == case.before
        assert not any(f.get('state') == 'awaiting_consent' for f in case.sent)


def test_followup_tool_waits_after_validated_clarification(workspace, monkeypatch):
    with broker(workspace, monkeypatch) as case:
        case.speech(WORDS)
        case.emit(call())
        assert case.wait()['assistant_text'] == CANONICAL
        case.emit(output(caption=CANONICAL, audio=True, complete=True))
        boundary = len(case.sent)
        case.emit(call('prepare_rfq', 'followup'), event(text=BRIDGE))  # late unbound caption
        assert len(case.responses) == 1
        assert not any(f['type'] in {'error', 'tool_result'} for f in case.sent[boundary:])
        case.speech(BRIDGE, generation=1)
        result = case.wait(2)
        assert result.get('action_result') and result['status'] == 'completed'
        assert not any(f['type'] == 'error' for f in case.sent[boundary:])
        assert case.operator.calls == []
        after = protected(case.client)
        assert after['rfq'] != case.before['rfq']
        assert after['pending_proposal']['kind'] == 'rfq'
        assert after['rfq']['status'] == 'draft_unsent' and len(after['drafts']) == 1
        assert {k: v for k, v in after.items() if k not in {'rfq', 'drafts', 'pending_proposal'}} == {
            k: v for k, v in case.before.items() if k not in {'rfq', 'drafts', 'pending_proposal'}}


def test_pre_vad_calls_share_original_bounded_wait(workspace, monkeypatch):
    monkeypatch.setattr(live_voice, 'FRESH_CALL_GRACE_SECONDS', .08)
    with broker(workspace, monkeypatch) as case:
        case.emit(call(), call('prepare_rfq', 'second'), call(ident='early'))
        assert case.responses == []
        result = case.wait(2)
        assert result['read_only'] and result['status'] == 'needs_clarification'
        case.emit(call(ident='third'))
        assert case.wait(3)['status'] == 'needs_clarification'
        assert case.operator.calls == [] and case.provider.executed_messages == []
        assert protected(case.client) == case.before


@pytest.mark.parametrize('words', ['What is the latest copper news?', 'Show local carbon evidence.'])
def test_valid_generic_operator_uses_only_settled_asr(workspace, monkeypatch, words):
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        assert case.responses == []
        case.speech(words)
        case.wait()
        assert case.provider.executed_messages == [words]


def test_explicit_false_cannot_authorize_generic_operator(workspace, monkeypatch):
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .03)
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        case.speech(MISSING_DOCUMENT, finished=False)
        result = case.wait()
        assert result['read_only'] and not result.get('action_result')
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


@pytest.mark.parametrize('words,expected', [(WORDS, 'needs_clarification'), (BRIDGE, 'completed')])
def test_early_call_valid_vad_8_2_second_command_settles(workspace, monkeypatch, words, expected):
    clock = [0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: monotonic() + clock[0])
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        case.microphone(268800)  # actual 8.4 seconds PCM, start .2, end 8.4
        case.emit(AdvanceEvent(clock, output(), seconds=.1),
                  event(activity=('ACTIVITY_START', '0.2s')))
        case.emit(AdvanceEvent(clock, call(ident='early'), seconds=3.1))
        assert case.responses == []  # the pre-VAD deadline must be retired
        case.emit(AdvanceEvent(clock, output(), seconds=5.1),
                  event(text=words, finished=True),
                  event(activity=('ACTIVITY_END', '8.4s')))
        result = case.wait()
        assert result['status'] == expected
        assert case.provider.executed_messages == [words] and case.operator.calls == []
        if expected == 'needs_clarification':
            assert result['assistant_text'] == CANONICAL
            assert result['read_only'] and not result.get('action_result')
            assert protected(case.client) == case.before
        else:
            assert result['action_result']['tool'] == 'prepare_rfq'
            state = case.client.get('/api/demo/session').json()
            assert state['rfq']['bridge'] and state['rfq']['status'] == 'draft_unsent'
            assert len(state['drafts']) == 1 and state['simulated_outbox'] == []


def test_pre_vad_repeats_do_not_extend_no_vad_deadline(workspace, monkeypatch):
    clock = [0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: monotonic() + clock[0])
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        case.emit(AdvanceEvent(clock, call('prepare_rfq', 'second'), seconds=2))
        assert case.responses == []
        case.emit(AdvanceEvent(clock, call(ident='early'), seconds=1.1))
        assert case.wait(2)['status'] == 'needs_clarification'
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


def test_valid_vad_repeated_calls_do_not_extend_command_deadline(workspace, monkeypatch):
    clock = [0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: monotonic() + clock[0])
    with broker(workspace, monkeypatch) as case:
        case.emit(call(), event(activity=('ACTIVITY_START', '0s')))
        case.emit(AdvanceEvent(clock, call(ident='early'), seconds=8))
        assert case.responses == []
        case.emit(AdvanceEvent(clock, output(), seconds=1.1))  # original observation limit
        case.emit(AdvanceEvent(clock, call(ident='early'), seconds=29))
        assert case.responses == []
        case.emit(AdvanceEvent(clock, output(), seconds=1.1))  # original transport limit
        assert case.wait()['status'] == 'unavailable'
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


def test_repeated_vad_does_not_restart_held_command(workspace, monkeypatch):
    clock = [0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: monotonic() + clock[0])
    with broker(workspace, monkeypatch) as case:
        case.emit(call(), event(activity=('ACTIVITY_START', '0s')))
        case.emit(AdvanceEvent(clock, output(), seconds=2),
                  event(activity=('ACTIVITY_START', '0s')))
        assert case.wait()['status'] == 'needs_clarification'
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


@pytest.mark.parametrize('words', [WORDS, BRIDGE])
def test_same_call_id_replays_canonical_result_without_execution(workspace, monkeypatch, words):
    with broker(workspace, monkeypatch) as case:
        case.speech(words)
        case.emit(call())
        original = case.wait()
        case.emit(output(caption=original['assistant_text'], audio=True, complete=True), call())
        assert case.wait(2) == original
        assert len([f for f in case.sent if f['type'] == 'tool_result']) == 2
        assert case.provider.executed_messages == [words] and case.operator.calls == []
        if words == WORDS:
            assert protected(case.client) == case.before
        else:
            assert len(case.client.get('/api/demo/session').json()['drafts']) == 1


def test_pending_semantic_calls_are_capped(workspace, monkeypatch):
    with broker(workspace, monkeypatch) as case:
        case.emit(*(call(ident=f'early-{index}') for index in range(5)))
        assert any(f['type'] == 'error' for f in case.sent)
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


@pytest.mark.parametrize('marker', [None, True])
def test_closed_generation_late_caption_and_replay_cannot_supply_new_request(workspace, monkeypatch, marker):
    monkeypatch.setattr(live_voice, 'FRESH_CALL_GRACE_SECONDS', .08)
    with broker(workspace, monkeypatch) as case:
        case.speech(WORDS, finished=marker)
        case.emit(call())
        assert case.wait()['assistant_text'] == CANONICAL
        case.emit(output(caption=CANONICAL, audio=True, complete=True))
        case.emit(call())  # exact replay: cached local result, no new authority
        case.wait(2)
        case.emit(event(text=BRIDGE), call('prepare_rfq', 'late-caption'))
        assert len(case.responses) == 2
        assert case.wait(3)['status'] == 'needs_clarification'
        assert case.provider.executed_messages == [WORDS] and case.operator.calls == []
        assert protected(case.client) == case.before


def test_interruption_retires_early_call(workspace, monkeypatch):
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        stopped = event()
        stopped.server_content.interrupted = True
        case.emit(stopped)
        assert case.wait()['status'] == 'unavailable'
        case.speech(WORDS)
        case.emit(call())
        assert case.wait(2)['status'] == 'unavailable'
        assert case.provider.executed_messages == [] and case.operator.calls == []


def test_settled_inventory_read_keeps_local_route(workspace, monkeypatch):
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        case.speech('Give an inventory and sourcing overview for all materials.')
        result = case.wait()
        assert result['status'] == 'completed' and result['read_only']
        assert result.get('action_result') is None and result['proposal'] is None
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


def test_context_change_retires_unbound_semantic_call(workspace, monkeypatch):
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        session = case.client.get('/api/demo/session').json()
        value = case.client.app.state.sessions.get(session['session_id'])
        with value.lock:
            value.context_version += 1
        case.emit(event(text=WORDS))
        assert any(f.get('code') == 'stale_context' for f in case.sent)
        assert case.provider.executed_messages == [] and case.operator.calls == []


def test_start_after_pre_vad_deadline_cannot_rescue_held_call(workspace, monkeypatch):
    clock = [0]
    monkeypatch.setattr(live_voice, 'monotonic', lambda: monotonic() + clock[0])
    with broker(workspace, monkeypatch) as case:
        case.emit(call())
        case.emit(AdvanceEvent(clock, event(activity=('ACTIVITY_START', '0s')), seconds=3.1))
        assert case.wait()['status'] == 'needs_clarification'
        case.emit(event(text=BRIDGE, finished=True),
                  event(activity=('ACTIVITY_END', '0.000125s')), call())
        assert case.wait(2)['status'] == 'needs_clarification'
        assert case.provider.executed_messages == [] and case.operator.calls == []
        assert protected(case.client) == case.before


def test_cached_semantic_result_cannot_cross_context(workspace, monkeypatch):
    with broker(workspace, monkeypatch) as case:
        case.speech(WORDS)
        case.emit(call())
        case.wait()
        session = case.client.get('/api/demo/session').json()
        value = case.client.app.state.sessions.get(session['session_id'])
        with value.lock:
            value.select_company('loomworks')
        case.emit(call())
        assert any(f.get('code') == 'stale_context' for f in case.sent)
        assert len(case.responses) == 1
        assert case.provider.executed_messages == [WORDS] and case.operator.calls == []
