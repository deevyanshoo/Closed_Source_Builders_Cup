"""Independent review regressions for ASR delivery and pending call identity."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
import pytest
from procure.demo import live_voice
from test_demo_live_voice import Provider, Socket, setup, start, next_type

class ManualProvider(Provider):
    def __init__(self):
        super().__init__()
        self.responses = []
    async def send_realtime_input(self, **kwargs):
        if audio := kwargs.get('audio'):
            self.received_audio.append(audio)
    async def send_tool_response(self, *, function_responses):
        self.responses.append(function_responses)

def vad(kind):
    return NS(voice_activity=NS(voice_activity_type=kind,
        audio_offset='0s' if kind == 'ACTIVITY_START' else '0.000125s'))

def output(text):
    return NS(server_content=NS(output_transcription=NS(text=text), turn_complete=True,
        model_turn=NS(parts=[NS(inline_data=NS(data=b'\x01\x00', mime_type='audio/pcm;rate=24000'))])))

@pytest.mark.parametrize('renew', [False, True])
@pytest.mark.parametrize('evidence', ['none', 'blank', 'unfinished'])
@pytest.mark.parametrize('same_event', [False, True])
def test_vad_end_requires_nonempty_complete_asr_before_output(monkeypatch, renew, evidence, same_event):
    async def check():
        store, session = setup()
        providers = []
        @asynccontextmanager
        async def factory(company, *, history_context):
            provider = ManualProvider()
            providers.append(provider)
            if renew and len(providers) == 1:
                provider.events.put_nowait(NS(go_away=NS(time_left='1s')))
            yield provider
        monkeypatch.setattr(live_voice, '_vertex_connection', factory)
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
            operate_workspace=lambda *_: None))
        await next_type(socket, 'ready')
        if renew:
            await next_type(socket, 'connection_status')
            await next_type(socket, 'connection_status')
        provider = providers[-1]
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        while not provider.received_audio:
            await asyncio.sleep(.001)
        await provider.events.put(vad('ACTIVITY_START'))
        if evidence != 'none':
            await provider.events.put(NS(server_content=NS(input_transcription=NS(
                text=' ' if evidence == 'blank' else 'Explain inventory', finished=evidence != 'unfinished'))))
        frame = output('Buffered answer.')
        if same_event:
            frame.voice_activity = vad('ACTIVITY_END').voice_activity
        await provider.events.put(frame)
        if not same_event:
            await provider.events.put(vad('ACTIVITY_END'))
        await asyncio.sleep(.025)
        assert not any(f['type'] == 'audio' or f.get('direction') == 'output' for f in socket.history)
        # Explicit unfinished input cannot be unlocked by a later omitted flag.
        if evidence == 'unfinished':
            await provider.events.put(NS(server_content=NS(input_transcription=NS(text=' please'))))
            await provider.events.put(output('Still unfinished.'))
            await asyncio.sleep(.025)
            assert not any(f['type'] == 'audio' or f.get('direction') == 'output' for f in socket.history)
        # Late final ASR after VAD END releases the current answer.
        await provider.events.put(NS(server_content=NS(input_transcription=NS(text='Explain inventory.', finished=True))))
        if evidence == 'unfinished':
            await provider.events.put(output('Current answer.'))
        await next_type(socket, 'audio')
        await next_type(socket, 'turn_complete')
        assert [f['text'] for f in socket.history if f.get('direction') == 'output'] == [
            'Current answer.' if evidence == 'unfinished' else 'Buffered answer.']
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(check())

@pytest.mark.parametrize('mode', ['identical', 'changed', 'distinct'])
def test_pending_call_identity_and_distinct_cap(monkeypatch, mode):
    async def check():
        from test_demo_live_voice import connection
        store, session = setup()
        provider = ManualProvider()
        calls = []
        async def workspace(name, args, version, key):
            calls.append((name, args, key))
            return {'turn_id': 'read-1', 'company_id': session.company_id, 'context_version': version,
                'status': 'completed', 'assistant_text': 'Current inventory.', 'agent_steps': [],
                'evidence_ids': [], 'proposal': None, 'read_only': True}
        socket = Socket(start(session))
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
            operate_workspace=lambda *_: None, workspace_tools=workspace, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        def call(index):
            return NS(tool_call=NS(function_calls=[NS(name='read_workspace',
                args={'changed': True} if mode == 'changed' and index else {},
                id=f'held-{index}' if mode == 'distinct' else 'held')]))
        for index in range(5):
            await provider.events.put(call(index))
        if mode != 'identical':
            assert (await next_type(socket, 'error'))['code'] == 'voice_invalid'
            await asyncio.wait_for(task, 2)
            assert not calls
            return
        await asyncio.sleep(.025)
        assert not task.done()
        await socket.frames.put('{"type":"audio","data":"AQACAA=="}')
        while not provider.received_audio:
            await asyncio.sleep(.001)
        await provider.events.put(vad('ACTIVITY_START'))
        await provider.events.put(NS(server_content=NS(input_transcription=NS(text='Read inventory.', finished=True))))
        await provider.events.put(vad('ACTIVITY_END'))
        await next_type(socket, 'tool_result')
        await provider.events.put(call(0))
        await next_type(socket, 'tool_result')
        assert len(calls) == 1
        assert len(provider.responses) == 2
        assert provider.responses[0].response == provider.responses[1].response
        await socket.frames.put('{"type":"stop"}')
        await asyncio.wait_for(task, 2)
    asyncio.run(check())
