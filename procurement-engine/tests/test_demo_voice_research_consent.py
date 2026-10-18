"""The live provider cannot turn its own words into research consent."""
import asyncio
import base64
import json
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

from procure.demo import live_voice
from procure.demo.sessions import SessionStore


def test_research_audit_date_evidence_survives_caption_tail_without_accepting_other_speech():
    audit_dir = Path(__file__).resolve().parents[2] / 'deploy' / 'judge-demo'
    sys.path.insert(0, str(audit_dir))
    try:
        from voice_research_consent_audit import AnswerEvidence, answer_caption_eligible
    finally:
        sys.path.pop(0)

    answer = AnswerEvidence()
    answer.feed('Public lookup checked on 16 Oct', eligible=True)
    answer.feed('ober 2026. Copper supply remains uncertain.', eligible=True)
    for index in range(20):
        answer.feed(f'Sourced finding {index} needs verification.', eligible=True)
    assert answer.has_explicit_date
    assert len(answer.spoken) <= 2000
    assert 'Sourced finding 19' in answer.spoken

    undated = AnswerEvidence()
    for index in range(20):
        undated.feed(f'Sourced finding {index} needs verification.', eligible=True)
    assert not undated.has_explicit_date

    untrusted = AnswerEvidence()
    for kind, direction, streaming, research, completed in (
            ('consent', 'input', True, object(), True),
            ('consent', 'output', True, None, False),  # Cue or pre-result model speech.
            ('request', 'output', True, object(), True),
            ('consent', 'output', False, object(), True)):
        untrusted.feed('16 October 2026', eligible=answer_caption_eligible(
            kind, direction, streaming, research, completed))
    untrusted.feed('Sourced findings need verification.', eligible=answer_caption_eligible(
        'consent', 'output', True, object(), True))
    assert not untrusted.has_explicit_date


def test_research_audit_requires_spoken_checked_day_not_publication_day():
    audit_dir = Path(__file__).resolve().parents[2] / 'deploy' / 'judge-demo'
    sys.path.insert(0, str(audit_dir))
    try:
        from voice_research_consent_audit import (AcceptanceError, AnswerEvidence,
                                                  answer_caption_eligible, final_quality)
    finally:
        sys.path.pop(0)

    expected = date(2026, 10, 16)
    publication = AnswerEvidence()
    publication.bind_lookup_date(expected)
    publication.feed('A source was published on 16 October 2026. Copper supply remains uncertain.', eligible=True)
    assert publication.has_explicit_date
    assert not publication.has_lookup_date

    for utterance in ('Public lookup checked on 15 October 2026.',
                      'Public lookup checked on 16 October 2025.',
                      'Public lookup checked on 2026-10-15.'):
        wrong = AnswerEvidence()
        wrong.bind_lookup_date(expected)
        wrong.feed(utterance, eligible=True)
        assert not wrong.has_lookup_date

    for utterance in ('Public lookup checked on 16 October 2026.',
                      'Public lookup checked on October 16, 2026.',
                      'Public lookup checked on 2026-10-16.',
                      'Public lookup checked on sixteenth October twenty twenty six.'):
        answer = AnswerEvidence()
        answer.bind_lookup_date(expected)
        answer.feed(utterance[:22], eligible=True)
        answer.feed(utterance[22:], eligible=True)
        for index in range(20):
            answer.feed(f'Sourced finding {index} needs verification.', eligible=True)
        assert answer.has_lookup_date, utterance
        assert len(answer._joined_tail) <= 256
        assert len(answer._tight_tail) <= 256

    untrusted = AnswerEvidence()
    untrusted.feed('Public lookup checked on 16 October 2026.', eligible=True)
    assert not untrusted.has_lookup_date  # Metadata has not been validated and bound.
    untrusted.bind_lookup_date(expected)
    for kind, direction, streaming, research, completed in (
            ('consent', 'input', True, object(), True),
            ('consent', 'output', True, None, False),
            ('request', 'output', True, object(), True),
            ('consent', 'output', False, object(), True)):
        untrusted.feed('Public lookup checked on 16 October 2026.', eligible=answer_caption_eligible(
            kind, direction, streaming, research, completed))
    assert not untrusted.has_lookup_date

    receipt = NS(data={'public_result': {'has_explicit_date': True},
                       'turns': [{}, {'spoken_answer_has_explicit_date': True,
                                      'spoken_answer_has_lookup_date': False}]})
    try:
        final_quality(receipt)
    except AcceptanceError as exc:
        assert getattr(exc, 'reason', None) == 'spoken_lookup_date_missing' or \
            'spoken_lookup_date_missing' in str(exc)
    else:
        raise AssertionError('publication date alone passed final audit quality')


def test_completed_research_projection_does_not_invent_missing_lookup_date():
    result = {'status': 'completed', 'read_only': True,
              'assistant_text': 'A short sourced finding.',
              'workspace': {'research': {'status': 'completed',
                                          'answer': 'A short sourced finding.',
                                          'sources': []}}}
    projected = live_voice.completed_research_speech(result)
    assert projected['tool_succeeded'] is True
    assert projected['read_only'] is True
    assert 'public lookup time is unavailable' in projected['required_spoken_response']
    assert not any(month in projected['required_spoken_response'] for month in
                   ('January', 'February', 'March', 'April', 'May', 'June', 'July',
                    'August', 'September', 'October', 'November', 'December'))
    assert 'assistant_text' not in projected and 'workspace' not in projected


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
        self.offset_base = Decimal(0)
        self.ends = 0
        self.client_content = asyncio.Queue()

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

    async def send_client_content(self, *, turns, turn_complete):
        await self.client_content.put((turns, turn_complete))

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
        # Test offsets are relative to the end of the initial request. Keep the
        # stream cumulative across audio_end; negative malformed offsets stay invalid.
        if isinstance(offset, str) and offset.endswith('s'):
            value = Decimal(offset[:-1])
            if value.is_finite() and value >= 0:
                offset = f'{value + self.offset_base:f}s'
        await self.events.put(NS(tool_call=None, server_content=None,
            voice_activity=NS(voice_activity_type=kind, audio_offset=offset)))


@asynccontextmanager
async def connection(provider):
    yield provider


async def scenario(callback=None, verifier=None, research=None):
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
        client_factory=lambda: connection(provider), verify_spoken_consent=verifier,
        research_tools=research))
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
    # A real current request precedes the model's query readback. No proposal or
    # consent exists during this PCM/VAD/complete-ASR turn.
    await send_pcm(socket, provider, pcm=b'\x01\x00' * 16_000)
    await provider.activity('ACTIVITY_START', '0.100s')
    await provider.spoken('input', QUERY, finished=True)
    await provider.activity('ACTIVITY_END', '0.900s')
    await socket.next('transcript', 'input')
    provider.offset_base = Decimal(provider.audio_bytes) / Decimal(32_000)
    provider.audio.clear()
    await provider.call('prepare_research', {'message': QUERY}, 'prepare-1')
    result = await socket.next('tool_result')
    assert result['result']['proposal']['proposal_id'] == 'proposal-1'
    await asyncio.wait_for(provider.responses.get(), 2)
    return result['result']['proposal']


async def finish(socket, task):
    await socket.frames.put('{"type":"stop"}')
    await asyncio.wait_for(task, 2)


async def send_pcm(socket, provider, size=32_000, pcm=None):
    pcm = b'\0' * size if pcm is None else pcm
    assert len(pcm) == size
    before = provider.audio_bytes
    await socket.frames.put(json.dumps({'type': 'audio',
        'data': base64.b64encode(pcm).decode('ascii')}))
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
        while True:
            provider_result = await asyncio.wait_for(provider.responses.get(), 2)
            if provider_result.id == 'real-consent':
                break
        assert provider_result.response['status'] == 'completed'
        assert provider_result.response['tool_succeeded'] is True
        assert 'public lookup time is unavailable' in provider_result.response['required_spoken_response']
        assert 'assistant_text' not in provider_result.response
        assert 'workspace' not in provider_result.response
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
                 'yes, confirmed', 'it is confirmed', "it's confirmed",
                 'Yes, I confirm. Please start that online research.',
                 'Yes, please start online research.',
                 'Yes, I confirm. Please start the research.'):
        assert live_voice._spoken_confirmation(text), text
    for text in ('yes', 'no', 'do not confirm research', 'I confirmed earlier',
                 'confirmed, but cancel that', 'send the supplier email',
                 'yes, maybe confirmed', "it's confirmed, no", 'that is confirmed',
                 'No, I confirm. Please start that online research.',
                 'Yes, I confirm. Please do not start that online research.',
                 'Yes, I confirm, please start that online research after checking every detail again now.'):
        assert not live_voice._spoken_confirmation(text), text


def test_exact_hosted_confirmation_requires_readback_and_independent_verifier_once():
    async def check():
        phrase = 'Yes, I confirm. Please start that online research.'
        verified = []
        searches = []
        async def verifier(clip):
            verified.append(len(clip))
            return phrase
        async def research(args, version, key, on_progress):
            searches.append((args, version, key))
            await on_progress('started')
            return turn(session, 'completed', text='Sourced answer ready.')
        _, session, socket, provider, calls, task = await scenario(
            verifier=verifier, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.',
                              complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', phrase, finished=None)
        assert (await socket.next('transcript', 'input'))['text'] == phrase
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        assert (await socket.next('research_progress'))['phase'] == 'started'
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert (await socket.next('research_progress'))['phase'] == 'completed'
        assert len(verified) == 1 and verified[0] >= 8000
        assert len(searches) == 1 and searches[0][0] == {'proposal_id': 'proposal-1'}
        assert [name for name, *_ in calls] == ['prepare_research']
        await finish(socket, task)
    asyncio.run(check())


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
        assert len(verified) == 1 and len(verified[0]) == 19_200
        assert [c[0] for c in calls] == ['prepare_research', 'confirm_research']
        await finish(socket, task)
    asyncio.run(check())


def test_consent_clip_includes_bounded_leading_speech_without_prearm_audio():
    async def check():
        verified = []

        async def verify(clip):
            verified.append(clip)
            return 'confirmed'

        _, _, socket, provider, _, task = await scenario(verifier=verify)
        await prepare(socket, provider)
        await send_pcm(socket, provider, pcm=b'A' * 32_000)  # before readout/arm
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        # Onset is 100 ms after arm; the first syllable may precede VAD start.
        await send_pcm(socket, provider, pcm=b'P' * 3_200 + b'S' * 28_800)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'with-preroll')
        await provider.activity('ACTIVITY_START', '1.100s')
        await provider.activity('ACTIVITY_END', '1.600s')
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert verified == [b'P' * 3_200 + b'S' * 16_000]
        await finish(socket, task)

    asyncio.run(check())


def test_consent_preroll_never_trims_full_vad_or_crosses_prior_boundary():
    assert live_voice._consent_clip_start(40_000, 56_000, 0, 38_000, 0) == 38_000
    assert live_voice._consent_clip_start(40_000, 56_000, 0, 0, 39_000) == 39_000
    assert live_voice._consent_clip_start(40_000, 40_000 + live_voice.MAX_CONSENT_CLIP_BYTES,
                                          0, 0, 0) == 40_000


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
        assert len(verified) == 1 and len(verified[0]) == 37_464
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


def test_provider_turn_complete_during_verifier_preserves_fresh_approval():
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await release.wait()
            return 'confirmed'

        async def research(args, version, key, on_progress):
            searches.append((args, version))
            await on_progress('started')
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, calls, task = await scenario(
            verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        await asyncio.wait_for(verifying.wait(), 2)
        await provider.spoken('output', '', complete=True)
        await socket.next('turn_complete')
        release.set()
        assert (await socket.next('research_progress'))['phase'] == 'started'
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert (await socket.next('research_progress'))['phase'] == 'completed'
        assert searches == [({'proposal_id': 'proposal-1'}, 1)]
        assert [name for name, *_ in calls] == ['prepare_research']
        await finish(socket, task)

    asyncio.run(check())


def test_continuous_silence_and_duplicate_caption_do_not_supersede_clip(caplog):
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await release.wait()
            return 'confirmed'

        async def research(args, version, key, on_progress):
            searches.append((args, version))
            await on_progress('started')
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await scenario(
            verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)  # getUserMedia keeps streaming silence.
        await provider.spoken('input', 'confirmed', finished=None)
        assert (await socket.next('transcript', 'input'))['text'] == 'confirmed'
        await provider.spoken('output', '', complete=True)
        await socket.next('turn_complete')
        release.set()
        assert (await socket.next('research_progress'))['phase'] == 'started'
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert searches == [({'proposal_id': 'proposal-1'}, 1)]
        await finish(socket, task)

    with caplog.at_level('INFO', logger='procure.demo.live_voice'):
        asyncio.run(check())
    lines = [record.message for record in caplog.records
             if 'research_consent_stage stage=verifier_result' in record.message]
    assert len(lines) == 1
    assert 'clip_bytes=19200 clip_ms=600' in lines[0]
    assert 'generation_match=True accepted=True reason=accepted' in lines[0]
    for sensitive in (QUERY, 'confirmed', 'proposal-1', 'session_id', 'audio'):
        assert sensitive not in lines[0]


def test_invalid_vad_supersedes_pending_verifier_before_duplicate_caption():
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await release.wait()
            return 'confirmed'

        async def research(*args):
            searches.append(args)
            raise AssertionError('invalid intervening VAD must retire old clip')

        _, _, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)  # continuous microphone silence
        await provider.activity('ACTIVITY_START', '99.000s')  # beyond received PCM
        await provider.spoken('input', 'confirmed', finished=None)
        assert (await socket.next('transcript', 'input'))['text'] == 'confirmed'
        release.set()
        assert (await socket.next('research_progress'))['phase'] == 'awaiting_consent'
        assert searches == []
        await finish(socket, task)

    asyncio.run(check())


def test_empty_or_punctuation_finalization_keeps_same_verified_clip():
    async def check(fragment, finished):
        verifying = asyncio.Event()
        release = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await release.wait()
            return 'confirmed'

        async def research(args, version, key, on_progress):
            searches.append(args)
            await on_progress('started')
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await scenario(
            verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)  # still streaming silence
        await provider.spoken('input', fragment, finished=finished)
        assert searches == []  # marker alone is never approval
        release.set()
        assert (await socket.next('research_progress'))['phase'] == 'started'
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert searches == [{'proposal_id': 'proposal-1'}]
        await finish(socket, task)

    asyncio.run(check('', True))
    asyncio.run(check('.', None))
    asyncio.run(check(None, True))


def test_late_negative_caption_after_silence_supersedes_pending_verifier():
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await release.wait()
            return 'confirmed'

        async def research(*args):
            searches.append(args)
            raise AssertionError('negative caption must not search')

        _, _, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed, no, do not search', finished=True)
        assert (await socket.next('research_progress'))['phase'] == 'awaiting_consent'
        assert (await socket.next('transcript', 'input'))['text'] == 'confirmed, no, do not search'
        release.set()
        await provider.spoken('output', '', complete=True)
        await socket.next('turn_complete')
        assert searches == []
        await finish(socket, task)

    asyncio.run(check())


def test_new_vad_and_finalized_negation_cannot_reuse_old_verified_clip():
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await release.wait()
            return 'confirmed'

        async def research(*args):
            searches.append(args)
            raise AssertionError('new utterance needs its own verified clip')

        _, _, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'Search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)
        await provider.activity('ACTIVITY_START', '1.100s')
        await provider.spoken('input', 'no, do not search', finished=True)
        assert (await socket.next('research_progress'))['phase'] == 'awaiting_consent'
        assert (await socket.next('transcript', 'input'))['text'] == 'no, do not search'
        release.set()
        await provider.spoken('output', '', complete=True)
        await socket.next('turn_complete')
        assert searches == []
        await finish(socket, task)

    asyncio.run(check())


def test_finalized_consent_starts_one_research_without_model_call_and_drains_events():
    async def check():
        started = asyncio.Event()
        release = asyncio.Event()
        research_calls = []

        async def research(args, version, key, on_progress):
            research_calls.append((args, version, key))
            started.set()
            await release.wait()
            await on_progress('started')
            with session.lock:
                session.research_records['proposal-1'] = {'state': 'completed',
                    'result': {'answer': 'Long competing detail ' * 90}}
            return turn(session, 'completed', text='Long competing detail ' * 90,
                workspace={'research': {'status': 'completed', 'checked_at':
                    '2026-10-15T00:00:00+00:00', 'answer': 'Three sourced findings are available. ' +
                    'Long competing detail ' * 90,
                    'sources': [{'publisher': 'Mining.com.au', 'title': 'Copper report',
                                 'url': 'https://mining.com.au/copper-report'}]}})

        _, session, socket, provider, calls, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        progress = await socket.next('research_progress')
        assert progress == {'type': 'research_progress', 'phase': 'checking_consent',
                            'company_id': session.company_id, 'context_version': 1,
                            'proposal_id': 'proposal-1'}
        assert (await socket.next('transcript', 'input'))['text'] == 'confirm research'
        await asyncio.wait_for(started.wait(), 2)
        await provider.spoken('input', 'confirmed', finished=False)
        assert (await socket.next('transcript', 'input'))['text'] == 'confirmed'
        assert [name for name, *_ in calls] == ['prepare_research']
        assert len(research_calls) == 1
        assert research_calls[0][0] == {'proposal_id': 'proposal-1'}
        assert research_calls[0][1] == 1
        release.set()
        assert (await socket.next('research_progress'))['phase'] == 'started'
        browser_result = (await socket.next('tool_result'))['result']
        assert browser_result['status'] == 'completed'
        assert 'Long competing detail' in browser_result['workspace']['research']['answer']
        assert 'Long competing detail' in session.research_records['proposal-1']['result']['answer']
        assert (await socket.next('research_progress'))['phase'] == 'completed'
        turns, complete = await asyncio.wait_for(provider.client_content.get(), 2)
        assert complete and 'Three sourced findings' in turns[0].parts[0].text
        assert '15 October 2026' in turns[0].parts[0].text
        assert 'Mining.com.au' in turns[0].parts[0].text
        assert 'required_spoken_response' in turns[0].parts[0].text
        provider_payload = json.loads(turns[0].parts[0].text)
        assert provider_payload['trusted_research_result']['status'] == 'completed'
        assert provider_payload['required_spoken_response'].startswith('Public lookup checked on 15 October 2026.')
        assert 'Long competing detail' not in turns[0].parts[0].text
        assert 'assistant_text' not in provider_payload['trusted_research_result']
        assert 'workspace' not in provider_payload['trusted_research_result']
        assert len(research_calls) == 1
        await finish(socket, task)

    asyncio.run(check())


def test_model_confirm_during_server_research_attaches_to_same_result():
    async def check():
        started = asyncio.Event()
        release = asyncio.Event()
        research_calls = []

        async def research(args, version, key, on_progress):
            research_calls.append(args)
            started.set()
            await release.wait()
            await on_progress('started')
            result = turn(session, 'completed', text='Long competing detail ' * 90,
                workspace={'research': {'status': 'completed', 'checked_at':
                    '2026-10-15T00:00:00+00:00', 'answer': 'Current sourced answer. ' +
                    'Long competing detail ' * 90,
                    'sources': [{'title': 'Mining.com.au',
                        'url': 'https://mining.com.au/copper-report'}]}})
            result.update(read_only=False, session={'session_id': session.session_id,
                'company_id': session.company_id, 'context_version': 1},
                action_result={'tool': 'confirm_research', 'summary': 'Public research completed.'})
            return result

        _, session, socket, provider, _, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        await asyncio.wait_for(started.wait(), 2)
        assert (await socket.next('transcript', 'input'))['text'] == 'confirm research'
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'late-real-call')
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'late-real-call')
        await provider.spoken('input', 'not a second approval', finished=False)
        assert (await socket.next('transcript', 'input'))['text'] == 'not a second approval'
        release.set()
        browser_result = (await socket.next('tool_result'))['result']
        assert browser_result['status'] == 'completed'
        assert 'Long competing detail' in browser_result['workspace']['research']['answer']
        response = await asyncio.wait_for(provider.responses.get(), 2)
        assert response.id == 'late-real-call' and response.response['status'] == 'completed'
        assert response.response['read_only'] is False
        guidance = response.response['required_spoken_response']
        assert '15 October 2026' in guidance
        assert 'Current sourced answer.' in guidance
        assert 'Mining.com.au' in guidance
        assert 'verify' in guidance.lower()
        assert 'Long competing detail' not in json.dumps(response.response)
        assert 'assistant_text' not in response.response
        assert 'workspace' not in response.response
        assert response.response['action_result']['tool'] == 'confirm_research'
        assert provider.responses.empty()
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'new-attempt')
        reused = await asyncio.wait_for(provider.responses.get(), 2)
        assert reused.id == 'new-attempt' and reused.response['read_only'] is True
        assert reused.response['action_applied'] is False
        assert reused.response['tool_succeeded'] is True
        assert 'action_result' not in reused.response and 'session' not in reused.response
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'late-real-call')
        replay = await asyncio.wait_for(provider.responses.get(), 2)
        assert replay.id == 'late-real-call' and replay.response['read_only'] is True
        assert replay.response['action_applied'] is False
        assert research_calls == [{'proposal_id': 'proposal-1'}]
        await finish(socket, task)

    asyncio.run(check())


def test_late_model_call_after_proposal_expiry_cannot_reuse_result():
    async def check():
        searches = []

        async def research(args, version, key, on_progress):
            searches.append(args)
            await on_progress('started')
            return turn(session, 'completed', text='Earlier sourced answer.')

        store, session, socket, provider, _, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        await asyncio.wait_for(provider.client_content.get(), 2)
        store.clock = lambda: datetime(2026, 10, 15, 0, 6, tzinfo=timezone.utc)
        await provider.call('confirm_research', {'proposal_id': 'proposal-1'}, 'expired-late-call')
        response = await asyncio.wait_for(provider.responses.get(), 2)
        assert response.id == 'expired-late-call'
        assert response.response['status'] == 'needs_clarification'
        assert response.response['read_only'] is True
        assert response.response['action_applied'] is False
        assert searches == [{'proposal_id': 'proposal-1'}]
        await finish(socket, task)

    asyncio.run(check())


def test_model_interruption_during_claimed_research_keeps_terminal_result():
    async def check():
        started = asyncio.Event()
        release = asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            started.set()
            await release.wait()
            return turn(session, 'completed', text='The sourced result is ready.')

        _, session, socket, provider, _, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        await asyncio.wait_for(started.wait(), 2)
        await provider.events.put(NS(tool_call=None, server_content=NS(
            interrupted=True, input_transcription=None, output_transcription=None,
            model_turn=None, turn_complete=False)))
        assert (await socket.next('interrupted'))['type'] == 'interrupted'
        release.set()
        assert (await socket.next('tool_result'))['result']['assistant_text'] == 'The sourced result is ready.'
        assert (await socket.next('research_progress'))['phase'] == 'completed'
        await finish(socket, task)

    asyncio.run(check())


def test_delayed_independent_verifier_keeps_voice_responsive_and_starts_one_search():
    async def check():
        verification_started = asyncio.Event()
        release = asyncio.Event()
        research_calls = []

        async def verify(_):
            verification_started.set()
            await release.wait()
            return 'confirmed'

        async def research(args, version, key, on_progress):
            research_calls.append(args)
            await on_progress('started')
            return turn(session, 'completed', text='Verified public result.')

        _, session, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        await asyncio.wait_for(verification_started.wait(), 2)
        await provider.spoken('output', 'I am checking that confirmation.')
        assert (await socket.next('transcript', 'output'))['text'] == 'I am checking that confirmation.'
        assert research_calls == []
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        assert research_calls == [{'proposal_id': 'proposal-1'}]
        await finish(socket, task)

    asyncio.run(check())


def test_negated_finalized_utterance_never_calls_research_callback():
    async def check():
        research_calls = []

        async def research(*args):
            research_calls.append(args)
            raise AssertionError('negation must not search')

        _, _, socket, provider, _, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research not yet', finished=True)
        assert (await socket.next('research_progress'))['phase'] == 'awaiting_consent'
        assert research_calls == []
        await finish(socket, task)

    asyncio.run(check())


def test_replaced_proposal_drops_late_research_completion():
    async def check():
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()

        async def research(args, version, key, on_progress):
            started.set()
            await release.wait()
            completed.set()
            return turn(session, 'completed', text='Stale answer must not appear.')

        _, session, socket, provider, _, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        await asyncio.wait_for(started.wait(), 2)
        with session.lock:
            session.pending_proposal = None
        # A later prepare replaces the in-memory voice gate while the search runs.
        await provider.call('prepare_research', {'message': QUERY}, 'prepare-2')
        assert (await socket.next('tool_result'))['result']['status'] == 'proposal'
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        await provider.spoken('output', 'Current proposal remains available.')
        await socket.next('transcript', 'output')
        assert all('Stale answer' not in json.dumps(frame) for frame in socket.history)
        await finish(socket, task)

    asyncio.run(check())


def test_nested_unavailable_research_emits_unavailable_progress():
    async def check():
        async def research(args, version, key, on_progress):
            await on_progress('started')
            return turn(session, 'completed', text='Public research could not verify a sourced answer.',
                        workspace={'research': {'status': 'unavailable', 'answer': ''}})

        _, session, socket, provider, _, task = await scenario(research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        assert (await socket.next('tool_result'))['result']['workspace']['research']['status'] == 'unavailable'
        assert (await socket.next('research_progress'))['phase'] == 'unavailable'
        turns, complete = await asyncio.wait_for(provider.client_content.get(), 2)
        payload = json.loads(turns[0].parts[0].text)
        assert complete and payload['trusted_research_result']['workspace']['research']['status'] == 'unavailable'
        assert payload['required_spoken_response'] == 'Public research could not verify a sourced answer.'
        assert 'Public lookup checked on' not in turns[0].parts[0].text
        await finish(socket, task)

    asyncio.run(check())


def test_late_verifier_cannot_approve_a_new_unconfirmed_utterance():
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        cancelled = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            try:
                await release.wait()
                return 'confirmed'
            finally:
                cancelled.set()

        async def research(*args):
            searches.append(args)
            raise AssertionError('old clip must not approve new utterance')

        _, _, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)
        await provider.spoken('input', 'maybe later', finished=True)
        assert (await socket.next('transcript', 'input'))['text'] == 'maybe later'
        await asyncio.wait_for(cancelled.wait(), 2)
        await provider.spoken('output', 'Confirmation still needed.')
        await socket.next('transcript', 'output')
        assert searches == []
        await finish(socket, task)

    asyncio.run(check())


def test_only_one_independent_verifier_runs_while_prior_clip_is_pending():
    async def check():
        verifying = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def verify(clip):
            calls.append(clip)
            verifying.set()
            await release.wait()
            return None

        async def research(*args):
            raise AssertionError('no verified consent')

        _, _, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)
        await provider.spoken('input', 'maybe later', finished=None)
        await provider.activity('ACTIVITY_START', '1.100s')
        await provider.activity('ACTIVITY_END', '1.600s')
        await provider.spoken('output', 'Still listening.')
        await socket.next('transcript', 'output')
        await asyncio.sleep(0.02)
        assert len(calls) == 1
        release.set()
        assert (await socket.next('research_progress'))['phase'] == 'awaiting_consent'
        await finish(socket, task)

    asyncio.run(check())


def test_new_finalized_confirmation_supersedes_pending_old_verifier():
    async def check():
        verifying = asyncio.Event()
        blocker = asyncio.Event()
        searches = []

        async def verify(_):
            verifying.set()
            await blocker.wait()
            return None

        async def research(args, version, key, on_progress):
            searches.append(args)
            await on_progress('started')
            return turn(session, 'completed', text='New approval result.')

        _, session, socket, provider, _, task = await scenario(verifier=verify, research=research)
        await prepare(socket, provider)
        await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
        await socket.next('turn_complete')
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirmed', finished=None)
        await socket.next('transcript', 'input')
        await provider.activity('ACTIVITY_START', '0.100s')
        await provider.activity('ACTIVITY_END', '0.600s')
        await asyncio.wait_for(verifying.wait(), 2)
        await send_pcm(socket, provider)
        await provider.spoken('input', 'confirm research', finished=True)
        assert (await socket.next('tool_result'))['result']['assistant_text'] == 'New approval result.'
        assert searches == [{'proposal_id': 'proposal-1'}]
        await finish(socket, task)

    asyncio.run(check())
