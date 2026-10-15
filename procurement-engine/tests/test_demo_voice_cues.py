"""A fixed, bounded cue cannot acquire Live tool or consent authority."""
import asyncio
import base64
import wave

import pytest

from procure.demo import live_voice
from test_demo_voice_research_consent import QUERY, finish, prepare, scenario, send_pcm, turn


def cue_file(tmp_path, *, rate=24000, channels=1, width=2, seconds=0.05):
    path = tmp_path / 'research-checking.wav'
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(width)
        output.setframerate(rate)
        output.writeframes(b'\x01' * int(rate * seconds) * channels * width)
    return path


async def claimed_research(research):
    store, session, socket, provider, calls, task = await scenario(research=research)
    await prepare(socket, provider)
    await provider.spoken('output', f'I can search for: {QUERY} Say confirm research to start.', complete=True)
    await socket.next('turn_complete')
    await send_pcm(socket, provider)
    await provider.spoken('input', 'confirm research', finished=True)
    assert (await socket.next('research_progress'))['phase'] == 'checking_consent'
    return store, session, socket, provider, calls, task


@pytest.mark.parametrize('rate,channels,width,seconds', [
    (16000, 1, 2, 0.1), (24000, 2, 2, 0.1),
    (24000, 1, 1, 0.1), (24000, 1, 2, 3.1),
])
def test_cue_loader_rejects_wrong_format_or_duration(tmp_path, rate, channels, width, seconds):
    assert live_voice.load_cue_pcm(cue_file(tmp_path, rate=rate, channels=channels,
                                             width=width, seconds=seconds)) is None


def test_cue_loader_omits_missing_or_oversized_asset(tmp_path):
    assert live_voice.load_cue_pcm(tmp_path / 'missing.wav') is None
    oversized = tmp_path / 'oversized.wav'
    oversized.write_bytes(b'0' * 160_001)
    assert live_voice.load_cue_pcm(oversized) is None


def test_bundled_charon_cue_is_valid_and_under_three_seconds():
    pcm = live_voice.load_cue_pcm(live_voice.VOICE_CUE_FILE)
    assert pcm is not None
    assert 0 < len(pcm) <= 3 * 24_000 * 2


def test_slow_research_streams_one_spoken_cue_without_provider_turn_or_tool(monkeypatch, tmp_path):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.02)
    monkeypatch.setattr(live_voice, 'CUE_CHUNK_BYTES', 800, raising=False)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        research_calls = []

        async def research(args, version, key, on_progress):
            research_calls.append(args)
            await on_progress('started')
            entered.set()
            await release.wait()
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, calls, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        caption = await socket.next('transcript', 'output')
        assert caption['text'] == live_voice.CUE_SPOKEN_TEXT
        first = await socket.next('audio')
        assert first['sample_rate_hertz'] == 24000
        assert base64.b64decode(first['data']) == b'\x01' * 800
        await socket.next('turn_complete')
        await asyncio.sleep(0.06)
        assert len([item for item in socket.history if item['type'] == 'transcript' and
                    item.get('text') == live_voice.CUE_SPOKEN_TEXT]) == 1
        assert provider.client_content.empty()
        assert [name for name, *_ in calls] == ['prepare_research']
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        turns, _ = await asyncio.wait_for(provider.client_content.get(), 2)
        assert 'trusted_research_result' in turns[0].parts[0].text
        assert research_calls == [{'proposal_id': 'proposal-1'}]
        await finish(socket, task)

    asyncio.run(check())


def test_fast_research_suppresses_cue_and_answers_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.1)

    async def check():
        async def research(args, version, key, on_progress):
            await on_progress('started')
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await claimed_research(research)
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        turns, _ = await asyncio.wait_for(provider.client_content.get(), 2)
        assert 'trusted_research_result' in turns[0].parts[0].text
        await asyncio.sleep(0.12)
        assert not any(item['type'] == 'transcript' and
                       item.get('text') == live_voice.CUE_SPOKEN_TEXT for item in socket.history)
        await finish(socket, task)

    asyncio.run(check())


@pytest.mark.parametrize('change', ['interrupt', 'company', 'disconnect'])
def test_queued_cue_cancels_on_interruption_or_scope_change(monkeypatch, tmp_path, change):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.08)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            entered.set()
            await release.wait()
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        if change == 'interrupt':
            await socket.frames.put('{"type":"interrupt"}')
            await socket.next('interrupted')
        elif change == 'company':
            with session.lock:
                session.select_company('terrapack')
            assert (await socket.next('error'))['code'] == 'stale_context'
        else:
            await socket.frames.put('{"type":"stop"}')
            await asyncio.wait_for(task, 2)
        await asyncio.sleep(0.1)
        assert not any(item['type'] == 'transcript' and
                       item.get('text') == live_voice.CUE_SPOKEN_TEXT for item in socket.history)
        assert provider.client_content.empty()
        release.set()
        if change == 'interrupt':
            await finish(socket, task)
        elif change == 'company':
            await asyncio.wait_for(task, 2)

    asyncio.run(check())


def test_result_interrupts_cue_stream_before_real_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path, seconds=1.0))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.02)
    monkeypatch.setattr(live_voice, 'CUE_CHUNK_BYTES', 800, raising=False)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            entered.set()
            await release.wait()
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        await socket.next('audio')
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        turns, _ = await asyncio.wait_for(provider.client_content.get(), 2)
        assert 'trusted_research_result' in turns[0].parts[0].text
        history_after_result = len(socket.history)
        await asyncio.sleep(0.07)
        assert not any(item['type'] == 'audio' for item in socket.history[history_after_result:])
        assert any(item['type'] == 'interrupted' for item in socket.history)
        await finish(socket, task)

    asyncio.run(check())


def test_result_clears_browser_queue_after_cue_stream_completes(monkeypatch, tmp_path):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.02)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            entered.set()
            await release.wait()
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        await socket.next('audio')
        await socket.next('turn_complete')
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        frame_types = [item['type'] for item in socket.history]
        result_index = max(index for index, kind in enumerate(frame_types) if kind == 'tool_result')
        assert frame_types.index('interrupted') < result_index
        assert 'audio' not in frame_types[result_index:]
        turns, _ = await asyncio.wait_for(provider.client_content.get(), 2)
        assert 'trusted_research_result' in turns[0].parts[0].text
        await finish(socket, task)

    asyncio.run(check())


def test_provider_acknowledgment_before_delay_suppresses_local_cue(monkeypatch, tmp_path):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.08)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            entered.set()
            await release.wait()
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        await provider.spoken('output', 'I am checking that now.', complete=True)
        assert (await socket.next('transcript', 'output'))['text'] == 'I am checking that now.'
        await socket.next('turn_complete')
        await asyncio.sleep(0.1)
        assert not any(item.get('text') == live_voice.CUE_SPOKEN_TEXT for item in socket.history)
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        await finish(socket, task)

    asyncio.run(check())


def test_provider_output_interrupts_local_cue_before_its_caption_and_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(live_voice, 'VOICE_CUE_FILE', cue_file(tmp_path, seconds=1.0))
    monkeypatch.setattr(live_voice, 'CUE_DELAY_SECONDS', 0.02)
    monkeypatch.setattr(live_voice, 'CUE_CHUNK_BYTES', 800)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def research(args, version, key, on_progress):
            await on_progress('started')
            entered.set()
            await release.wait()
            return turn(session, 'completed', text='Sourced answer ready.')

        _, session, socket, provider, _, task = await claimed_research(research)
        await asyncio.wait_for(entered.wait(), 2)
        assert (await socket.next('transcript', 'output'))['text'] == live_voice.CUE_SPOKEN_TEXT
        await socket.next('audio')
        await provider.spoken('output', 'Gemini has begun checking.', complete=True)
        assert (await socket.next('transcript', 'output'))['text'] == 'Gemini has begun checking.'
        await socket.next('turn_complete')
        history = socket.history
        cue_caption = next(i for i, item in enumerate(history) if item.get('text') == live_voice.CUE_SPOKEN_TEXT)
        provider_caption = next(i for i, item in enumerate(history) if item.get('text') == 'Gemini has begun checking.')
        assert any(item['type'] == 'interrupted' for item in history[cue_caption + 1:provider_caption])
        boundary = next(i for i in range(cue_caption + 1, provider_caption)
                        if history[i]['type'] == 'interrupted')
        assert not any(item['type'] == 'audio' for item in history[boundary + 1:provider_caption])
        assert history[provider_caption + 1]['type'] == 'audio'
        await asyncio.sleep(0.07)
        assert not any(item['type'] == 'audio' for item in socket.history[provider_caption + 2:])
        release.set()
        assert (await socket.next('tool_result'))['result']['status'] == 'completed'
        await finish(socket, task)

    asyncio.run(check())
