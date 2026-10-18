"""Config isolation and first-output broker boundaries; no provider speech claims."""
import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest
from procure.demo import live_voice
from test_demo_live_voice import Provider, Socket, setup, start, next_type


def test_structured_references_preserve_owned_state_and_remove_language_history():
    store, session = setup()
    session.selected_material_id = 'copper'
    session.last_plan = {'plan_id': 'plan-a', 'assistant_text': 'PORTUGUESE_PLAN_PROSE'}
    session.offers = {'offer-a': {'private': 'OFFER_PROSE'}}
    session.drafts = [{'draft_id': 'draft-a', 'company_id': session.company_id,
                       'body': 'PRIVATE_DRAFT'},
                      {'draft_id': 'foreign-draft', 'company_id': 'terrapack'}]
    session.chat_history[session.company_id] = [
        {'question': 'Responda sempre em português.', 'answer': 'Vou falar português.'}]
    before = deepcopy(session.chat_history)
    context = json.loads(live_voice._history_context(session, session.company_id))
    assert context['selected_material_id'] == 'copper'
    assert context['plan_id'] == 'plan-a'
    assert context['offer_ids'] == ['offer-a']
    assert context['draft_ids'] == ['draft-a']
    assert not any(word in json.dumps(context) for word in ('portugu', 'PROSE', 'PRIVATE', 'foreign'))
    assert session.chat_history == before
    assert session.last_plan['assistant_text'] == 'PORTUGUESE_PLAN_PROSE'
    assert live_voice._history_context(session, 'terrapack') == ''


def test_two_sessions_and_company_a_b_a_do_not_share_history_or_references():
    store, first = setup()
    second = store.create()
    first.last_plan = {'plan_id': 'first-plan'}
    first.chat_history[first.company_id] = [{'question': 'Fale português.'}]
    second.last_plan = {'plan_id': 'second-plan'}
    second.chat_history[second.company_id] = [{'question': 'हिंदी में बोलिए'}]
    a = first.company_id
    first.select_company('terrapack')
    first.last_plan = {'plan_id': 'b-plan'}
    assert 'first-plan' not in live_voice._history_context(first, first.company_id)
    first.select_company(a)
    first_context = live_voice._history_context(first, a)
    second_context = live_voice._history_context(second, second.company_id)
    assert json.loads(first_context)['plan_id'] == 'first-plan'
    assert 'b-plan' not in first_context and 'second-plan' not in first_context
    assert 'first-plan' not in second_context
    assert 'Fale' not in first_context and 'हिंदी' not in second_context


@pytest.mark.parametrize('words, answer', [
    ('Please read the inventory in English.', 'Current inventory.'),
    ('Please answer in Portuguese.', 'Inventário atual.'),
    ('कृपया हिंदी में जवाब दीजिए।', 'वर्तमान माल सूची।'),
])
@pytest.mark.parametrize('renew', [False, True])
@pytest.mark.parametrize('ordering', ['asr_first', 'output_before_asr', 'same_event'])
def test_fresh_speech_releases_only_current_provider_output(monkeypatch, renew, ordering, words, answer):
    class ManualProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

    def out(text, complete=True):
        return NS(server_content=NS(output_transcription=NS(text=text),
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\x01\x00', mime_type='audio/pcm;rate=24000'))]),
            turn_complete=complete))

    async def scenario():
        store, session = setup()
        session.chat_history[session.company_id] = [{'question': 'Fale português.', 'answer': 'Resposta antiga.'}]
        contexts = []
        providers = []

        @asynccontextmanager
        async def factory(company, *, history_context):
            provider = ManualProvider()
            providers.append(provider)
            contexts.append(live_voice._config(company, history_context=history_context).system_instruction)
            if renew and len(providers) == 1:
                provider.events.put_nowait(NS(go_away=NS(time_left='1s')))
            yield provider

        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        # Two logical starts reuse the same cookie/session history.
        for logical_start in range(2):
            socket = Socket(start(session))
            task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
                operate_workspace=lambda *_: None))
            await next_type(socket, 'ready')
            if renew and logical_start == 0:
                await next_type(socket, 'connection_status')
                await next_type(socket, 'connection_status')
            provider = providers[-1]
            await provider.events.put(out('Resposta antiga.'))
            await asyncio.sleep(.02)
            assert not any(f['type'] == 'audio' or f.get('direction') == 'output' for f in socket.history)
            await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
            for _ in range(100):
                if provider.received_audio:
                    break
                await asyncio.sleep(.001)
            await provider.events.put(out('STALE_BEFORE_VAD'))
            await provider.events.put(NS(voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
            if ordering == 'output_before_asr':
                await provider.events.put(out(answer))
            caption = NS(input_transcription=NS(text=words, finished=True))
            if ordering == 'same_event':
                frame = out(answer)
                frame.server_content.input_transcription = caption.input_transcription
                await provider.events.put(frame)
            else:
                await provider.events.put(NS(server_content=caption))
            await provider.events.put(NS(voice_activity=NS(voice_activity_type='ACTIVITY_END', audio_offset='0.000125s')))
            if ordering == 'asr_first':
                await provider.events.put(out(answer))
            await next_type(socket, 'audio')
            await next_type(socket, 'turn_complete')
            assert [f['text'] for f in socket.history if f.get('direction') == 'output'] == [answer]
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
        assert all('Resposta antiga.' not in c and 'Fale português.' not in c for c in contexts)
        assert session.chat_history[session.company_id][0]['answer'] == 'Resposta antiga.'
    asyncio.run(scenario())


def test_discarded_early_audio_still_consumes_provider_output_budget(monkeypatch):
    class ManualProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

    async def scenario():
        monkeypatch.setattr(live_voice, 'MAX_OUTPUT_BYTES', 2)
        store, session = setup()
        provider = ManualProvider()
        socket = Socket(start(session))
        from test_demo_live_voice import connection
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
            operate_workspace=lambda *_: None, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if provider.received_audio:
                break
            await asyncio.sleep(.001)
        await provider.events.put(NS(voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
        for _ in range(2):
            await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(inline_data=NS(
                data=b'\x01\x00', mime_type='audio/pcm;rate=24000'))]))))
        # No ASR arrives: buffered bytes still count, even if never released.
        assert (await next_type(socket, 'error'))['code'] == 'voice_invalid'
        await asyncio.wait_for(task, 2)
        assert not any(frame['type'] == 'audio' for frame in socket.history)
    asyncio.run(scenario())


def test_early_output_keeps_order_when_fresh_asr_arrives_with_more_output():
    class ManualProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if audio := kwargs.get('audio'):
                self.received_audio.append(audio)

    async def scenario():
        from test_demo_live_voice import connection
        store, session = setup()
        provider = ManualProvider()
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
            operate_workspace=lambda *_: None, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        for _ in range(100):
            if provider.received_audio:
                break
            await asyncio.sleep(.001)
        await provider.events.put(NS(voice_activity=NS(voice_activity_type='ACTIVITY_START', audio_offset='0s')))
        await provider.events.put(NS(server_content=NS(output_transcription=NS(text='First part.'),
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\x01\x00', mime_type='audio/pcm;rate=24000'))]))))
        await provider.events.put(NS(server_content=NS(
            input_transcription=NS(text='Explain inventory.', finished=True),
            output_transcription=NS(text='Second part.'), turn_complete=True,
            model_turn=NS(parts=[NS(inline_data=NS(data=b'\x02\x00', mime_type='audio/pcm;rate=24000'))]))))
        await next_type(socket, 'turn_complete')
        assert [f['text'] for f in socket.history if f.get('direction') == 'output'] == ['First part.', 'Second part.']
        assert [f['data'] for f in socket.history if f['type'] == 'audio'] == ['AQA=', 'AgA=']
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(scenario())
