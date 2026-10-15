"""The live provider cannot turn its own words into research consent."""
import asyncio
import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from procure.demo import live_voice
from procure.demo.sessions import SessionStore


QUERY = 'How would a Hormuz blockage affect petrochemicals today?'


class Socket:
    def __init__(self, session):
        self.frames = asyncio.Queue()
        self.frames.put_nowait(json.dumps({'type': 'start', 'csrf_token': session.csrf_token,
                                            'expected_context_version': 1}))
        self.sent = asyncio.Queue()
        self.history = []

    async def accept(self):
        pass

    async def receive_text(self):
        return await self.frames.get()

    async def send_json(self, item):
        self.history.append(item)
        await self.sent.put(item)

    async def close(self, *, code):
        pass

    async def next(self, kind, direction=None):
        while True:
            item = await asyncio.wait_for(self.sent.get(), 2)
            if item['type'] == kind and (direction is None or item.get('direction') == direction):
                return item


class Provider:
    def __init__(self):
        self.events = asyncio.Queue()
        self.responses = asyncio.Queue()
        self.audio = asyncio.Event()
        self.audio_bytes = 0
        self.ends = 0

    async def receive(self):
        yield await self.events.get()

    async def send_realtime_input(self, **kwargs):
        if kwargs.get('audio'):
            self.audio_bytes += len(kwargs['audio'].data)
            self.audio.set()
        if kwargs.get('audio_stream_end'):
            self.ends += 1

    async def send_tool_response(self, *, function_responses):
        await self.responses.put(function_responses)

    async def call(self, name, args, call_id):
        await self.events.put(NS(tool_call=NS(function_calls=[NS(name=name, args=args, id=call_id)]),
                                 server_content=None))

    async def spoken(self, direction, text, *, complete=False, finished=True):
        await self.events.put(NS(tool_call=None, server_content=NS(
            input_transcription=NS(text=text, finished=finished) if direction == 'input' else None,
            output_transcription=NS(text=text) if direction == 'output' else None,
            interrupted=False, model_turn=(NS(parts=[NS(inline_data=NS(
                data=b'\x01\x00', mime_type='audio/pcm;rate=24000'))])
                if direction == 'output' else None), turn_complete=complete)))

    async def activity(self, kind, offset):
        await self.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type=kind, audio_offset=offset)))


@asynccontextmanager
async def connection(provider):
    yield provider


async def scenario(callback=None, verifier=None):
    now = datetime(2026, 10, 15, tzinfo=timezone.utc)
    store = SessionStore(clock=lambda: now)
    session = store.create()
    socket, provider = Socket(session), Provider()
    calls = []

    async def workspace(name, args, version, key):
        calls.append((name, args, version, key))
        if name == 'prepare_research':
            proposal = {'proposal_id': 'proposal-1', 'kind': 'research',
                        'parameters': {'query': QUERY}, 'context_version': 1,
                        'expires_at': (now + timedelta(minutes=5)).isoformat(),
                        '_session_id': session.session_id, '_company_id': session.company_id,
                        '_marker': 'marker-1'}
            with session.lock:
                session.pending_proposal = proposal
            return turn(session, 'proposal', proposal={k: v for k, v in proposal.items()
                  if not k.startswith('_')})
        if callback:
            return await callback(name, args, version, key)
        return turn(session, 'completed', text='Three sourced findings are available.',
                    workspace={'research': {'status': 'completed', 'answer': 'Three sourced findings are available.'}})

    async def forbidden(*_):
        raise AssertionError('unexpected operator call')

    task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
        operate_workspace=forbidden, workspace_tools=workspace,
        client_factory=lambda: connection(provider), verify_spoken_consent=verifier))
    await socket.next('ready')
    return store, session, socket, provider, calls, task


def turn(session, status, *, proposal=None, text='Review the public query.', workspace=None):
    result = {'turn_id': 'turn-1', 'company_id': session.company_id,
              'context_version': 1, 'status': status, 'assistant_text': text,
              'agent_steps': [], 'evidence_ids': [], 'proposal': proposal, 'read_only': True}
    if workspace is not None:
        result['workspace'] = workspace
    return result


async def prepare(socket, provider):
    await provider.call('prepare_research', {'message': QUERY}, 'prepare-1')
    result = await socket.next('tool_result')
    assert result['result']['proposal']['proposal_id'] == 'proposal-1'
    await asyncio.wait_for(provider.responses.get(), 2)
    return result['result']['proposal']


async def finish(socket, task):
    await socket.frames.put('{"type":"stop"}')
    await asyncio.wait_for(task, 2)


async def send_pcm(socket, provider, size=32_000):
    before = provider.audio_bytes
    await socket.frames.put(json.dumps({'type': 'audio',
        'data': base64.b64encode(b'\0' * size).decode('ascii')}))
    async def wait_sent():
        while provider.audio_bytes < before + size:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_sent(), 2)


async def end_pcm(socket, provider):
    before = provider.ends
    await socket.frames.put('{"type":"audio_end"}')
    async def wait_end():
        while provider.ends == before:
            await asyncio.sleep(0)
    await asyncio.wait_for(wait_end(), 2)


def test_voice_research_requires_fresh_spoken_confirmation_after_query_prompt():
    async def check():
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'too-early')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await provider.spoken('output', '', complete=True)
        await socket.next('turn_complete')
        await provider.spoken('input', 'confirm research')
        await socket.next('transcript', 'input')
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'bot-words')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'old-input')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await provider.spoken('input', 'I confirm. Start the research.')
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'real-consent')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        assert calls[-1][1] == {'proposal_id': 'proposal-1'}
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'real-consent')
        repeated = (await socket.next('tool_result'))['result']
        assert repeated['status'] == 'completed' and repeated['read_only'] is True
        assert 'session' not in repeated and 'action_result' not in repeated
        assert len(calls) == 2
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'replay')
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert len(calls) == 2
        await finish(socket, task)
    asyncio.run(check())


def test_denied_confirmation_keeps_only_current_public_research_proposal():
    async def check():
        _, _, socket, provider, calls, task = await scenario()
        public = await prepare(socket, provider)
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'before-arm')
        denied = (await socket.next('tool_result'))['result']
        assert denied['status'] == 'proposal' and denied['read_only'] is True
        assert denied['proposal'] == public
        assert denied['proposal']['expires_at'] == public['expires_at']
        assert denied['proposal']['context_version'] == public['context_version']
        assert 'session' not in denied and 'action_result' not in denied
        model_response = await asyncio.wait_for(provider.responses.get(), 2)
        assert model_response.response['tool_succeeded'] is False
        assert model_response.response['action_applied'] is False
        assert 'could not verify spoken consent' in model_response.response['required_spoken_response']
        assert [call[0] for call in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_denied_confirmation_replay_drops_expired_or_replaced_proposal():
    async def check(stale_kind):
        store, session, socket, provider, calls, task = await scenario()
        public = await prepare(socket, provider)
        await provider.call('confirm_research', {'proposal_id': public['proposal_id']}, 'denied-once')
        first = (await socket.next('tool_result'))['result']
        assert first['status'] == 'proposal' and first['proposal'] == public
        if stale_kind == 'expired':
            store.clock = lambda: datetime(2026, 10, 15, 0, 6, tzinfo=timezone.utc)
        else:
            with session.lock:
                session.pending_proposal['proposal_id'] = 'replacement'
        await provider.call('confirm_research', {'proposal_id': public['proposal_id']}, 'denied-once')
        replay = (await socket.next('tool_result'))['result']
        assert replay['status'] == 'needs_clarification'
        assert replay['proposal'] is None and replay['read_only'] is True
        assert 'session' not in replay and 'action_result' not in replay
        assert 'prepare a new query' in replay['assistant_text']
        assert [call[0] for call in calls] == ['prepare_research']
        await finish(socket, task)
    for stale_kind in ('expired', 'replaced'):
        asyncio.run(check(stale_kind))


def test_voice_research_rejects_negation_and_replaced_proposal():
    async def check():
        _, session, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'No, do not confirm research.')
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'negated')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        with session.lock:
            session.pending_proposal['proposal_id'] = 'proposal-2'
        await provider.spoken('input', 'confirm research')
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'replaced')
        replaced = (await socket.next('tool_result'))['result']
        assert replaced['status'] == 'needs_clarification' and replaced['proposal'] is None
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_confirmation_phrases_reject_ambiguous_or_negated_speech():
    for text in ('confirmed', 'I confirm', 'yes, start research', 'confirm research',
                 'yes, confirmed', 'it is confirmed', "it's confirmed"):
        assert live_voice._spoken_confirmation(text), text
    for text in ('yes', 'no', 'do not confirm research', 'I confirmed earlier',
                 'confirmed, but cancel that', 'send the supplier email',
                 'yes, maybe confirmed', "it's confirmed, no", 'that is confirmed'):
        assert not live_voice._spoken_confirmation(text), text


def test_missing_pending_research_never_calls_confirmation():
    async def check():
        store, session, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'confirm research')
        await socket.next('transcript', 'input')
        with session.lock:
            session.pending_proposal = None
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'missing')
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_expired_research_never_calls_confirmation():
    async def check():
        store, session, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'confirm research')
        await socket.next('transcript', 'input')
        store.clock = lambda: datetime(2026, 10, 15, 0, 6, tzinfo=timezone.utc)
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'expired')
        expired = (await socket.next('tool_result'))['result']
        assert expired['status'] == 'needs_clarification' and expired['proposal'] is None
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_fresh_input_transcript_and_confirmation_tool_in_same_provider_event():
    async def check():
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.events.put(NS(
            tool_call=NS(function_calls=[NS(name='confirm_research',
                args={'proposal_id': 'proposal-1'}, id='same-event')]),
            server_content=NS(input_transcription=NS(text='confirm research', finished=True),
                output_transcription=None, interrupted=False, model_turn=None,
                turn_complete=False)))
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check())


def test_streamed_transcript_fragments_can_confirm_only_current_research():
    async def check():
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        pieces = ('I can search for: How would a Horm',
                  'uz blockage affect petrochemicals today?',
                  ' Say confirm ', 'research to start.')
        for piece in pieces[:-1]:
            await provider.spoken('output', piece)
        await provider.spoken('output', pieces[-1], complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'con', finished=False)
        await provider.spoken('input', 'firm', finished=False)
        await provider.spoken('input', ' research')
        for _ in range(3):
            await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'fragments')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check())


def test_later_negation_or_new_turn_revokes_streamed_consent():
    async def check():
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'confirmed', finished=False)
        await provider.spoken('input', ' not')
        for _ in range(2):
            await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'negated-fragments')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await provider.spoken('input', 'confirm research')
        await socket.next('transcript', 'input')
        await provider.spoken('output', 'Please repeat that.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'old-turn')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_unfinished_confirmation_tool_waits_for_final_negation():
    async def check():
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'confirmed', finished=False)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'premature')
        await provider.spoken('input', ' not yet', finished=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        await socket.next('transcript', 'input')
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_buffered_confirmation_runs_once_after_final_fragment_or_empty_final_marker():
    async def check(final_text):
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'con' if final_text else 'confirmed', finished=False)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'buffered')
        await provider.spoken('input', final_text, finished=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        if final_text:
            await socket.next('transcript', 'input')
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check('firm research'))
    asyncio.run(check(''))


def test_finishedless_provider_cannot_authorize_research_at_model_turn_completion():
    async def check(negated):
        _, _, socket, provider, calls, task = await scenario()
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        await asyncio.wait_for(provider.audio.wait(), 2)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'legacy')
        if negated:
            await provider.spoken('input', ' not yet', finished=None)
            await socket.next('transcript', 'input')
        await provider.spoken('output', '', complete=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check(True))
    asyncio.run(check(False))


def test_finishedless_confirmation_uses_only_fresh_completed_audio_clip():
    async def check():
        verified = []

        async def verify(clip):
            verified.append(clip)
            return 'yes, confirmed'

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'verified')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert len(verified) == 1 and len(verified[0]) == 16_000
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check())


def test_finishedless_verifier_negation_and_invalid_offsets_never_search():
    async def check(start, end, response):
        verified = []

        async def verify(clip):
            verified.append(clip)
            return response

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'verify-denied')
        await provider.activity('ACTIVITY_START', start)
        await provider.activity('ACTIVITY_END', end)
        await provider.spoken('output', '', complete=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        assert len(verified) == (1 if start == '0.100s' and end == '0.600s' else 0)
        await finish(socket, task)
    asyncio.run(check('0.100s', '0.600s', 'confirmed not yet'))
    asyncio.run(check('0.100s', '2.000s', 'confirmed'))
    asyncio.run(check('0.900s', '0.600s', 'confirmed'))
    asyncio.run(check('-0.100s', '0.600s', 'confirmed'))


def test_finished_transcription_does_not_spend_independent_verifier_call():
    async def check():
        async def verify(_):
            raise AssertionError('finished transcription must not call verifier')

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'finished')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check())


def test_finishedless_clip_before_prompt_or_reset_offset_after_audio_end_cannot_authorize():
    async def check(before_prompt):
        verified = []

        async def verify(clip):
            verified.append(clip)
            return 'confirmed'

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await send_pcm(socket, provider)
        if not before_prompt:
            await end_pcm(socket, provider)
            await provider.activity('ACTIVITY_START', '0.100s')
            await provider.activity('ACTIVITY_END', '0.600s')
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        if not before_prompt:
            await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'old-clip')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert not verified and [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check(True))
    asyncio.run(check(False))


def test_finishedless_audio_offsets_continue_after_audio_end():
    async def check():
        verified = []

        async def verify(clip):
            verified.append(clip)
            return 'confirmed'

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await send_pcm(socket, provider)
        await send_pcm(socket, provider, 8_984)
        await end_pcm(socket, provider)
        # Google may deliver the first clip's VAD after audio_end.
        await provider.activity('ACTIVITY_START', '0.360s')
        await provider.activity('ACTIVITY_END', '1.280750s')
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await send_pcm(socket, provider, 8_984)
        await end_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'after-end')
        await provider.spoken('output', '', complete=True)
        await socket.next('turn_complete')
        # Model turn_complete is not transcript finality; late VAD still works.
        await provider.activity('ACTIVITY_START', '1.640750s')
        await provider.activity('ACTIVITY_END', '2.561500s')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert len(verified) == 1 and len(verified[0]) == 29_464
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check())


def test_voice_activity_offset_requires_exact_pcm_sample_boundaries():
    assert live_voice._audio_offset_bytes('1.280750s') == 40_984
    for value in ('-1s', 'NaNs', '1.000015625s', '1.2', 'inf s', None):
        assert live_voice._audio_offset_bytes(value) is None


def test_explicit_unfinished_transcript_cannot_use_finishedless_fallback():
    async def check():
        attempts = []

        async def verify(_):
            attempts.append(True)
            return 'confirmed'

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=False)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'unfinished-vad')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        await provider.spoken('input', ' not yet', finished=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        assert attempts == []
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_late_activity_grace_expires_with_one_clarification_response(monkeypatch):
    monkeypatch.setattr(live_voice, 'CONSENT_ACTIVITY_GRACE_SECONDS', 0.05)

    async def check():
        async def verify(_):
            raise AssertionError('no complete clip')

        _, _, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'no-vad')
        await provider.spoken('output', '', complete=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        response = await asyncio.wait_for(provider.responses.get(), 2)
        assert response.id == 'no-vad' and response.response['status'] == 'proposal'
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


def test_proposal_replaced_during_independent_verification_never_searches():
    async def check():
        started = asyncio.Event()
        release = asyncio.Event()

        async def verify(_):
            started.set()
            await release.wait()
            return 'confirmed'

        _, session, socket, provider, calls, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'replaced-during-await')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        await asyncio.wait_for(started.wait(), 2)
        with session.lock:
            session.pending_proposal['proposal_id'] = 'proposal-replaced'
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'needs_clarification'
        assert [c[0] for c in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())
