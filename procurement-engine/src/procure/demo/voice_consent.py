"""Bounded transcription of a completed, caller-owned voice consent utterance."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import unicodedata
import wave


MODEL = 'gemini-3.5-flash-lite'
RATE = 16_000
MIN_BYTES = RATE * 2 // 4  # 250 ms of mono PCM16.
MAX_BYTES = RATE * 2 * 8
MAX_TRANSCRIPT = 160
logger = logging.getLogger(__name__)
_INSTRUCTION = (
    'Transcribe only the human speech in this audio clip, verbatim. '
    'Do not answer, interpret intent, or infer approval. '
    'Return only a JSON object with one field named transcript.'
)
_SCHEMA = {'type': 'object', 'additionalProperties': False,
           'properties': {'transcript': {'type': 'string', 'maxLength': MAX_TRANSCRIPT}},
           'required': ['transcript']}


def _reject(reason: str) -> None:
    logger.warning('voice_consent_unavailable reason=%s', reason)
    return None


def _wav(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return output.getvalue()


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _transcript(response) -> str | None:
    candidates = getattr(response, 'candidates', None)
    if not candidates or len(candidates) != 1:
        return _reject('candidate_count')
    candidate = candidates[0]
    if str(getattr(candidate, 'finish_reason', '')).split('.')[-1].upper() != 'STOP':
        return _reject('finish_reason')
    parts = getattr(getattr(candidate, 'content', None), 'parts', None)
    if not parts or len(parts) != 1 or getattr(parts[0], 'function_call', None) is not None:
        return _reject('invalid_parts')
    text = getattr(parts[0], 'text', None)
    if not isinstance(text, str) or len(text) > 512:
        return _reject('invalid_json')
    try:
        value = json.loads(text, object_pairs_hook=_unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError, RecursionError):
        return _reject('invalid_json')
    if not isinstance(value, dict) or set(value) != {'transcript'}:
        return _reject('invalid_schema')
    transcript = value['transcript']
    if (not isinstance(transcript, str) or not 1 <= len(transcript) <= MAX_TRANSCRIPT
            or not transcript.strip() or any(unicodedata.category(c) == 'Cc' for c in transcript)):
        return _reject('invalid_transcript')
    return transcript


async def verify_spoken_consent(pcm: bytes, *, client=None) -> str | None:
    """Return unmodified transcript text; caller must independently authorize any action."""
    if (not isinstance(pcm, bytes) or not MIN_BYTES <= len(pcm) <= MAX_BYTES
            or len(pcm) % 2):
        return _reject('invalid_audio')
    if client is None and (os.getenv('OFFLINE', '').lower() in {'1', 'true', 'yes'}
                           or os.getenv('PROCURE_OFFLINE', '').lower() in {'1', 'true', 'yes'}
                           or not os.getenv('GOOGLE_CLOUD_PROJECT', '').strip()):
        return _reject('provider_unavailable')

    owned = None
    try:
        from google import genai
        from google.genai import types

        if client is None:
            owned = genai.Client(
                vertexai=True, project=os.environ['GOOGLE_CLOUD_PROJECT'].strip(),
                location='global', http_options=types.HttpOptions(
                    api_version='v1', timeout=10000,
                    retry_options=types.HttpRetryOptions(attempts=1)))
        provider = client or owned
        config = types.GenerateContentConfig(
            temperature=0, max_output_tokens=128,
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type='application/json', response_json_schema=_SCHEMA)
        response = await asyncio.wait_for(provider.aio.models.generate_content(
            model=MODEL, contents=[_INSTRUCTION,
                                   types.Part.from_bytes(data=_wav(pcm), mime_type='audio/wav')],
            config=config), timeout=10)
        return _transcript(response)
    except Exception:
        return _reject('provider_exception')
    finally:
        if owned is not None:
            closer = getattr(owned.aio, 'aclose', None)
            if callable(closer):
                try:
                    await closer()
                except Exception:
                    pass
