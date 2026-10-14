"""Bounded, server-authenticated Gemini Live bridge for the hosted demo.

The route owns Origin and cookie validation before accepting the WebSocket.
This bridge verifies the first-frame CSRF token and workspace version. Its only
tool delegates to the same validated operator turn used by typed requests.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import base64
import binascii
import json
import os
import secrets
import threading
from time import monotonic
from typing import Awaitable, Callable

from starlette.websockets import WebSocketDisconnect

from .sessions import SessionError


MODEL = 'gemini-3.8-live'
LOCATION = 'us-central1'
MAX_SECONDS = 180
MAX_CONNECTIONS = 2
MAX_COMPANY_STARTS = 2
MAX_PROCESS_STARTS = 24
MAX_INPUT_CHUNK = 32_000  # One second of mono PCM16 at 16 kHz.
MAX_INPUT_BYTES = MAX_SECONDS * 32_000
MAX_OUTPUT_BYTES = MAX_SECONDS * 48_000
MAX_OUTPUT_CHUNK = 96_000
MAX_FRAMES = 18_000
MAX_TOOLS = 6
MAX_TRANSCRIPT_CHARS = 1200
_SLOTS = threading.BoundedSemaphore(MAX_CONNECTIONS)
_START_LOCK = threading.Lock()
_PROCESS_STARTS = 0

INSTRUCTION = (
    'You are an original procurement workspace voice assistant. Speak in '
    'clear, concise English with a light, dry British wit when appropriate; do '
    'not imitate a character, celebrity, or actor. For every workspace question '
    'or proposed action, call operate_workspace with the user\'s request. Treat '
    'its returned OperatorTurn as authoritative. Explain only supported facts '
    'from its assistant_text and result; say when verification is unavailable. '
    'If a proposal is returned, say it awaits review in the interface. Never '
    'claim a proposal was applied, confirm it by voice, or invent prices, dates, '
    'latencies, evidence, or outcomes. Briefly acknowledge a business request '
    'before calling tools when a check is needed. Keep answers brief and composed.'
)


class _End(Exception):
    """Normal client stop or disconnect."""


class _Invalid(Exception):
    """Invalid browser frame or provider event."""


class _Stale(Exception):
    """Session or workspace changed during the connection."""


class _Capacity(Exception):
    """The process or session has exhausted its voice allowance."""


class _Busy(Exception):
    """The two concurrent live slots are occupied."""


def _current(session, store, context_version: int) -> None:
    try:
        current = store.get(session.session_id)
    except SessionError:
        raise _Stale() from None
    with session.lock:
        if current is not session or session.context_version != context_version:
            raise _Stale()


def _frame(raw: str, *, limit: int) -> dict:
    if not isinstance(raw, str) or len(raw) > limit:
        raise _Invalid()
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise _Invalid() from None
    if not isinstance(data, dict):
        raise _Invalid()
    return data


def _audio(data: dict) -> bytes:
    if set(data) != {'type', 'data'} or not isinstance(data['data'], str):
        raise _Invalid()
    if len(data['data']) > ((MAX_INPUT_CHUNK + 2) // 3) * 4:
        raise _Invalid()
    try:
        chunk = base64.b64decode(data['data'], validate=True)
    except (binascii.Error, ValueError):
        raise _Invalid() from None
    if not chunk or len(chunk) > MAX_INPUT_CHUNK or len(chunk) % 2:
        raise _Invalid()
    return chunk


def _config():
    from google.genai import types

    declaration = types.FunctionDeclaration(
        name='operate_workspace',
        description='Get the verified read-only workspace answer or reviewable proposal for the spoken request.',
        parameters_json_schema={
            'type': 'object', 'additionalProperties': False,
            'properties': {'message': {'type': 'string', 'minLength': 1, 'maxLength': 1200}},
            'required': ['message'],
        },
    )
    return types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name='Charon'))),
        system_instruction=INSTRUCTION,
        tools=[types.Tool(function_declarations=[declaration])],
    )


@asynccontextmanager
async def _vertex_connection():
    """Use runtime ADC on the server; never issue a browser token."""
    project = os.getenv('GOOGLE_CLOUD_PROJECT', '').strip()
    if (os.getenv('OFFLINE', '').lower() in {'1', 'true', 'yes'} or
            os.getenv('PROCURE_OFFLINE', '').lower() in {'1', 'true', 'yes'} or
            not project):
        raise RuntimeError('Live provider unavailable')
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=project, location=LOCATION,
                          http_options=types.HttpOptions(api_version='v1beta1'))
    try:
        async with client.aio.live.connect(model=MODEL, config=_config()) as connection:
            yield connection
    finally:
        await client.aio.aclose()


async def run_live_voice(
    websocket, *, session, store,
    operate_workspace: Callable[[str, int, str], Awaitable[dict]],
    client_factory=None,
) -> None:
    """Accept a pre-Origin/cookie-checked socket and bridge one bounded session.

    The browser's first frame must be {type:start, csrf_token,
    expected_context_version}. The route-supplied callback must execute the
    existing operator-turn boundary, including its idempotency and limits.
    """
    global _PROCESS_STARTS
    await websocket.accept()
    started = monotonic()
    input_bytes = output_bytes = input_frames = tool_calls = 0
    tool_results: dict[str, tuple[str, dict]] = {}
    accepted = False
    reserved = False
    context_version = None
    try:
        hello = _frame(await asyncio.wait_for(websocket.receive_text(), 5), limit=512)
        if set(hello) != {'type', 'csrf_token', 'expected_context_version'} or hello['type'] != 'start':
            raise _Invalid()
        token = hello['csrf_token']
        context_version = hello['expected_context_version']
        if (not isinstance(token, str) or not secrets.compare_digest(token, session.csrf_token)
                or type(context_version) is not int or context_version < 1):
            raise _Invalid()
        _current(session, store, context_version)
        if not _SLOTS.acquire(blocking=False):
            raise _Busy()
        reserved = True
        with _START_LOCK, session.lock:
            if (not session.active or session.expires_at <= store.now() or
                    session.context_version != context_version):
                raise _Stale()
            by_company = session.live_voice_starts_by_company
            company_starts = by_company.get(session.company_id, 0)
            if _PROCESS_STARTS >= MAX_PROCESS_STARTS or company_starts >= MAX_COMPANY_STARTS:
                raise _Capacity()
            _PROCESS_STARTS += 1
            session.live_voice_sessions += 1
            by_company[session.company_id] = company_starts + 1
        accepted = True

        factory = client_factory or _vertex_connection
        async with asyncio.timeout(max(0, MAX_SECONDS - (monotonic() - started))), factory() as provider:
            await websocket.send_json({'type': 'ready', 'input_mime_type': 'audio/pcm;rate=16000',
                                       'output_mime_type': 'audio/pcm;rate=24000',
                                       'context_version': context_version})

            async def from_browser():
                nonlocal input_bytes, input_frames
                from google.genai import types
                while True:
                    _current(session, store, context_version)
                    data = _frame(await websocket.receive_text(), limit=48_000)
                    kind = data.get('type')
                    if kind == 'stop' and set(data) == {'type'}:
                        raise _End()
                    if kind == 'audio_end' and set(data) == {'type'}:
                        await provider.send_realtime_input(audio_stream_end=True)
                        continue
                    if kind == 'interrupt' and set(data) == {'type'}:
                        # Clear browser playback immediately; Google VAD handles
                        # interruption once the following input audio arrives.
                        await websocket.send_json({'type': 'interrupted'})
                        continue
                    if kind != 'audio':
                        raise _Invalid()
                    chunk = _audio(data)
                    input_bytes += len(chunk)
                    input_frames += 1
                    if input_bytes > MAX_INPUT_BYTES or input_frames > MAX_FRAMES:
                        raise _Invalid()
                    await provider.send_realtime_input(
                        audio=types.Blob(data=chunk, mime_type='audio/pcm;rate=16000'))

            async def from_provider():
                nonlocal output_bytes, tool_calls
                from google.genai import types
                while True:
                    async for event in provider.receive():
                        _current(session, store, context_version)
                        calls = getattr(getattr(event, 'tool_call', None), 'function_calls', None) or []
                        for call in calls:
                            if getattr(call, 'name', None) != 'operate_workspace':
                                raise _Invalid()
                            call_id = getattr(call, 'id', None)
                            if not isinstance(call_id, str) or not 1 <= len(call_id) <= 128:
                                raise _Invalid()
                            args = getattr(call, 'args', None)
                            if (not isinstance(args, dict) or set(args) != {'message'} or
                                    not isinstance(args['message'], str) or
                                    not 0 < len(args['message'].strip()) <= 1200):
                                raise _Invalid()
                            if call_id in tool_results:
                                prior_message, result = tool_results[call_id]
                                if prior_message != args['message']:
                                    raise _Invalid()
                            else:
                                tool_calls += 1
                                if tool_calls > MAX_TOOLS:
                                    raise _Invalid()
                                key = 'live-' + secrets.token_urlsafe(24)
                                try:
                                    result = await operate_workspace(args['message'], context_version, key)
                                except Exception:
                                    _current(session, store, context_version)
                                    raise RuntimeError('operator unavailable') from None
                            _current(session, store, context_version)
                            if (not isinstance(result, dict) or
                                    set(result) != {'turn_id', 'company_id', 'context_version',
                                                    'status', 'assistant_text', 'agent_steps',
                                                    'evidence_ids', 'proposal', 'read_only'} or
                                    result.get('context_version') != context_version or
                                    result.get('company_id') != session.company_id or
                                    result.get('read_only') is not True or
                                    len(json.dumps(result, ensure_ascii=True)) > 64_000):
                                raise _Invalid()
                            tool_results[call_id] = (args['message'], result)
                            await websocket.send_json({'type': 'tool_result', 'result': result})
                            await provider.send_tool_response(function_responses=types.FunctionResponse(
                                name='operate_workspace', id=call_id, response=result))
                        content = getattr(event, 'server_content', None)
                        if content is None:
                            continue
                        if getattr(content, 'interrupted', False):
                            await websocket.send_json({'type': 'interrupted'})
                        for speaker, field in (('input', 'input_transcription'),
                                               ('output', 'output_transcription')):
                            transcription = getattr(content, field, None)
                            value = getattr(transcription, 'text', None)
                            if isinstance(value, str) and value:
                                await websocket.send_json({'type': 'transcript', 'direction': speaker,
                                                           'text': value[:MAX_TRANSCRIPT_CHARS]})
                        model_turn = getattr(content, 'model_turn', None)
                        for part in (getattr(model_turn, 'parts', None) or []):
                            inline = getattr(part, 'inline_data', None)
                            audio = getattr(inline, 'data', None)
                            if audio is None:
                                continue
                            if (not isinstance(audio, bytes) or not audio or len(audio) % 2 or
                                    len(audio) > MAX_OUTPUT_CHUNK or
                                    getattr(inline, 'mime_type', '') not in
                                    {'audio/pcm', 'audio/pcm;rate=24000'}):
                                raise _Invalid()
                            output_bytes += len(audio)
                            if output_bytes > MAX_OUTPUT_BYTES:
                                raise _Invalid()
                            await websocket.send_json({'type': 'audio', 'sample_rate_hertz': 24000,
                                                       'data': base64.b64encode(audio).decode('ascii')})
                        if getattr(content, 'turn_complete', False):
                            await websocket.send_json({'type': 'turn_complete'})

            async def watch_context():
                while True:
                    await asyncio.sleep(0.5)
                    _current(session, store, context_version)

            tasks = {asyncio.create_task(from_browser()), asyncio.create_task(from_provider()),
                     asyncio.create_task(watch_context())}
            try:
                done, pending = await asyncio.wait(tasks, timeout=MAX_SECONDS,
                                                   return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    raise TimeoutError()
                for task in done:
                    task.result()
                raise RuntimeError('Live provider ended')
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except _End:
        pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        # No provider exception, token, or raw audio is sent to the browser.
        code = ('stale_context' if isinstance(exc, _Stale) else
                'voice_busy' if isinstance(exc, _Busy) else
                'voice_limit' if isinstance(exc, _Capacity) else
                'voice_invalid' if isinstance(exc, _Invalid) else
                'voice_limit' if isinstance(exc, TimeoutError) else 'voice_unavailable')
        message = ('Workspace changed. Start a new voice session.' if code == 'stale_context' else
                   'Live voice is busy. Try again shortly.' if code == 'voice_busy' else
                   'Live voice is unavailable. Try typed input.' if code == 'voice_unavailable' else
                   'Live voice ended. Start a new session.' if code == 'voice_limit' else
                   'Invalid live voice request.')
        try:
            await websocket.send_json({'type': 'error', 'code': code, 'message': message})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        if accepted:
            try:
                await websocket.send_json({'type': 'metrics',
                                           'duration_ms': round((monotonic() - started) * 1000),
                                           'input_audio_bytes': input_bytes,
                                           'output_audio_bytes': output_bytes,
                                           'input_frames': input_frames, 'tool_calls': tool_calls})
            except (RuntimeError, WebSocketDisconnect):
                pass
        try:
            await websocket.close(code=1000)
        except (RuntimeError, WebSocketDisconnect):
            pass
        if reserved:
            _SLOTS.release()
