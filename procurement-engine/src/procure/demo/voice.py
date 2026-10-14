"""Short Google Cloud speech turns for the hosted demo.

The injected transport is for offline tests. A returned test fixture does not
prove a real Google call; production always uses server-side ADC.
"""
from __future__ import annotations

import base64
import binascii
import io
import json
import math
import os
import struct
import wave

from .errors import DemoError


STT_URL = 'https://speech.googleapis.com/v1/speech:recognize'
TTS_URL = 'https://texttospeech.googleapis.com/v1/text:synthesize'
MAX_AUDIO_BYTES = 500_000
MAX_AUDIO_SECONDS = 20
MAX_TEXT_CHARS = 1200
MAX_MP3_BYTES = 750_000
STT_RESPONSE_BYTES = 1_000_000
TTS_RESPONSE_BYTES = 1_400_000
TIMEOUT_SECONDS = 15
WEBM_RATES = {8000, 12000, 16000, 24000, 48000}


def _bad_audio() -> DemoError:
    return DemoError('voice_invalid_audio', 'Use a short WebM/Opus or PCM WAV recording.')


def _unavailable() -> DemoError:
    return DemoError('voice_unavailable', 'Google voice is unavailable. Try typed input.', 503)


def _default_transport(url: str, *, payload: dict, timeout: int, max_bytes: int) -> dict:
    # Import and discover credentials only after local validation and offline checks.
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
    session = AuthorizedSession(credentials)
    try:
        with session.post(url, json=payload, timeout=timeout, stream=True) as response:
            if response.status_code != 200:
                raise _unavailable()
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=16_384):
                size += len(chunk)
                if size > max_bytes:
                    raise _unavailable()
                chunks.append(chunk)
            body = b''.join(chunks)
            if not body:
                raise _unavailable()
            return json.loads(body)
    finally:
        session.close()


def _vint(data: bytes, position: int, *, identifier: bool = False):
    if position >= len(data):
        raise _bad_audio()
    first = data[position]
    if not first:
        raise _bad_audio()
    width = 1
    while width <= 8 and not first & (0x80 >> (width - 1)):
        width += 1
    if width > 8 or position + width > len(data):
        raise _bad_audio()
    value = int.from_bytes(data[position:position + width], 'big')
    if not identifier:
        value &= (1 << (7 * width)) - 1
        if value == (1 << (7 * width)) - 1:  # unknown-size Segment/Cluster
            value = None
    return value, position + width


def _webm_metadata(data: bytes):
    # Parse only the EBML containers relevant to codec and duration. Never scan
    # compressed frame bytes for marker sequences.
    containers = {0x1A45DFA3, 0x18538067, 0x1549A966, 0x1654AE6B,
                  0xAE, 0x1F43B675, 0xA0}
    # Unknown-size Clusters end at the next Segment-level element. Chrome
    # MediaRecorder writes sibling Clusters this way instead of sized ones.
    segment_level = {0x114D9B74, 0x1549A966, 0x1654AE6B, 0x1F43B675,
                     0x1C53BB6B, 0x1941A469, 0x1043A770, 0x1254C367}
    metadata = {'codec': None, 'rate': None, 'duration': None,
                'timecode_scale': 1_000_000, 'cluster_timecode': 0,
                'last_block_ms': 0, 'blocks': 0, 'ebml': False, 'segment': False}

    def visit(start, end, depth=0, *, parent=None, unknown=False):
        if depth > 8:
            raise _bad_audio()
        position = start
        while position < end:
            item_start = position
            identifier, position = _vint(data, position, identifier=True)
            size, position = _vint(data, position)
            if unknown and parent == 0x1F43B675 and identifier in segment_level:
                return item_start
            if size is None and identifier not in (0x18538067, 0x1F43B675):
                raise _bad_audio()
            item_end = end if size is None else position + size
            if item_end > end or item_end < position:
                raise _bad_audio()
            # Container ranges can cover every later sibling when their EBML
            # size is unknown. Slice only leaf fields whose bytes we inspect.
            content = (data[position:item_end] if identifier in
                       (0x86, 0x63A2, 0x2AD7B1, 0x4489, 0xE7, 0xA3, 0xA1) else b'')
            if identifier == 0x1A45DFA3:
                metadata['ebml'] = True
            elif identifier == 0x18538067:
                metadata['segment'] = True
            elif identifier == 0x1F43B675:
                metadata['cluster_timecode'] = 0
            elif identifier == 0x86:
                metadata['codec'] = content
            elif identifier == 0x63A2:
                if len(content) < 16 or not content.startswith(b'OpusHead'):
                    raise _bad_audio()
                metadata['rate'] = int.from_bytes(content[12:16], 'little')
            elif identifier == 0x2AD7B1:
                metadata['timecode_scale'] = int.from_bytes(content, 'big')
            elif identifier == 0x4489:
                if len(content) not in (4, 8):
                    raise _bad_audio()
                metadata['duration'] = struct.unpack('>f' if len(content) == 4 else '>d', content)[0]
            elif identifier == 0xE7:
                metadata['cluster_timecode'] = int.from_bytes(content, 'big')
            elif identifier in (0xA3, 0xA1) and len(content) >= 4:
                _, block_start = _vint(content, 0)
                if block_start + 3 < len(content):
                    metadata['blocks'] += 1
                    offset = int.from_bytes(content[block_start:block_start + 2], 'big', signed=True)
                    metadata['last_block_ms'] = max(metadata['last_block_ms'],
                        (metadata['cluster_timecode'] + offset) * metadata['timecode_scale'] / 1_000_000)
            if identifier in containers:
                child_position = visit(position, item_end, depth + 1,
                                       parent=identifier, unknown=size is None)
                position = child_position if size is None else item_end
            else:
                position = item_end
        return position

    visit(0, len(data))
    if not metadata['ebml'] or not metadata['segment'] or metadata['codec'] != b'A_OPUS' or not metadata['blocks']:
        raise _bad_audio()
    duration_ms = metadata['duration']
    if duration_ms is not None:
        duration_ms *= metadata['timecode_scale'] / 1_000_000
    duration_ms = max(duration_ms or 0, metadata['last_block_ms'])
    if not math.isfinite(duration_ms) or duration_ms > MAX_AUDIO_SECONDS * 1000 or duration_ms < 0:
        raise _bad_audio()
    return metadata['rate']


def _audio_encoding(audio: bytes, mime_type: str, sample_rate_hertz: int) -> str:
    if not isinstance(audio, bytes) or not audio or len(audio) > MAX_AUDIO_BYTES:
        raise _bad_audio()
    if isinstance(sample_rate_hertz, bool) or not isinstance(sample_rate_hertz, int):
        raise _bad_audio()
    if mime_type == 'audio/wav':
        try:
            with wave.open(io.BytesIO(audio), 'rb') as stream:
                if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getcomptype() != 'NONE':
                    raise _bad_audio()
                rate = stream.getframerate()
                duration = stream.getnframes() / rate
                if rate != sample_rate_hertz or duration <= 0 or duration > MAX_AUDIO_SECONDS:
                    raise _bad_audio()
                if len(stream.readframes(stream.getnframes())) != stream.getnframes() * 2:
                    raise _bad_audio()
        except (wave.Error, EOFError, ZeroDivisionError, ValueError) as exc:
            raise _bad_audio() from exc
        return 'LINEAR16'
    if mime_type in ('audio/webm', 'audio/webm;codecs=opus'):
        if sample_rate_hertz not in WEBM_RATES:
            raise _bad_audio()
        rate = _webm_metadata(audio)
        if rate and rate != sample_rate_hertz:
            raise _bad_audio()
        return 'WEBM_OPUS'
    raise _bad_audio()


class VoiceService:
    """Bounded synchronous voice calls. Transport injection is test-only."""

    def __init__(self, *, transport=None, offline=None):
        self._transport = transport or _default_transport
        self.offline = (any(os.getenv(name, '').lower() in ('1', 'true', 'yes')
                            for name in ('OFFLINE', 'PROCURE_OFFLINE'))
                        if offline is None else bool(offline))

    def _request(self, url: str, payload: dict, max_bytes: int):
        try:
            result = self._transport(url, payload=payload, timeout=TIMEOUT_SECONDS, max_bytes=max_bytes)
            if not isinstance(result, dict):
                raise _unavailable()
            # Also bound fakes/custom transports that skip the streaming cap.
            if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > max_bytes:
                raise _unavailable()
            return result
        except DemoError:
            raise
        except Exception as exc:
            raise _unavailable() from exc

    def transcribe(self, audio: bytes, mime_type: str, sample_rate_hertz: int) -> dict:
        if self.offline:
            raise _unavailable()
        encoding = _audio_encoding(audio, mime_type, sample_rate_hertz)
        payload = {
            'config': {'encoding': encoding, 'sampleRateHertz': sample_rate_hertz,
                       'languageCode': 'en-US', 'model': 'latest_short',
                       'enableAutomaticPunctuation': True},
            'audio': {'content': base64.b64encode(audio).decode('ascii')},
        }
        response = self._request(STT_URL, payload, STT_RESPONSE_BYTES)
        results = response.get('results')
        if not isinstance(results, list) or not results or len(results) > 50:
            raise _unavailable()
        pieces = []
        for result in results:
            if not isinstance(result, dict):
                raise _unavailable()
            alternatives = result.get('alternatives')
            if not isinstance(alternatives, list) or not alternatives or not isinstance(alternatives[0], dict):
                raise _unavailable()
            piece = alternatives[0].get('transcript')
            if not isinstance(piece, str):
                raise _unavailable()
            pieces.append(piece.strip())
        transcript = ' '.join(piece for piece in pieces if piece).strip()
        if not transcript or len(transcript) > MAX_TEXT_CHARS or any(ord(c) < 32 and c not in '\n\t' for c in transcript):
            raise _unavailable()
        return {'transcript': transcript, 'provider': 'google_cloud_stt', 'model': 'latest_short', 'call': 'real'}

    def speak(self, text: str) -> dict:
        if self.offline:
            raise _unavailable()
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS or '\0' in text:
            raise DemoError('voice_invalid_text', 'Use a short nonempty response.')
        payload = {'input': {'text': text.strip()},
                   'voice': {'languageCode': 'en-US', 'name': 'en-US-Standard-A'},
                   'audioConfig': {'audioEncoding': 'MP3'}}
        response = self._request(TTS_URL, payload, TTS_RESPONSE_BYTES)
        encoded = response.get('audioContent')
        if not isinstance(encoded, str) or not encoded or len(encoded) > (MAX_MP3_BYTES + 2) // 3 * 4:
            raise _unavailable()
        try:
            audio = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _unavailable() from exc
        if not 4 <= len(audio) <= MAX_MP3_BYTES or not (audio.startswith(b'ID3') or
                (audio[0] == 0xff and audio[1] & 0xe0 == 0xe0)):
            raise _unavailable()
        return {'audio_base64': encoded, 'mime_type': 'audio/mpeg',
                'provider': 'google_cloud_tts', 'call': 'real'}
