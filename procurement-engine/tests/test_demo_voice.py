"""Voice service boundary tests. Injected transport is fake; no Google call occurs."""
import base64
import io
import struct
import wave

import pytest

from procure.demo.errors import DemoError
from procure.demo.voice import VoiceService, _webm_metadata


def _wav(seconds=1, rate=16000):
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\0\0' * int(seconds * rate))
    return out.getvalue()


def _element(identifier, payload):
    return identifier + bytes([0x80 | len(payload)]) + payload


def _webm(rate=48000, duration_ms=1000, *, block=True, cluster_ms=0):
    codec = _element(b'\x86', b'A_OPUS')
    opus = b'OpusHead' + b'\x01\x01\0\0' + struct.pack('<I', rate) + b'\0\0\0'
    track = _element(b'\xae', codec + _element(b'\x63\xa2', opus))
    tracks = _element(b'\x16\x54\xae\x6b', track)
    info = _element(b'\x15\x49\xa9\x66', _element(b'\x44\x89', struct.pack('>d', float(duration_ms)))) if duration_ms is not None else b''
    cluster = _element(b'\x1f\x43\xb6\x75', _element(b'\xe7', cluster_ms.to_bytes(2, 'big')) +
                       (_element(b'\xa3', b'\x81\0\0\x80\xf8\xff\xfe') if block else b''))
    segment = _element(b'\x18\x53\x80\x67', info + tracks + cluster)
    return _element(b'\x1a\x45\xdf\xa3', b'') + segment


def _chrome_style_webm(cluster_times):
    """Chrome MediaRecorder layout: unknown-size Segment and sibling Clusters."""
    opus = b'OpusHead\x01\x01\0\0' + struct.pack('<I', 48000) + b'\0\0\0'
    track = _element(b'\xae', _element(b'\x86', b'A_OPUS') + _element(b'\x63\xa2', opus))
    tracks = _element(b'\x16\x54\xae\x6b', track)
    unknown_size = b'\x01' + b'\xff' * 7
    cluster = lambda ms: (b'\x1f\x43\xb6\x75' + unknown_size +
                          _element(b'\xe7', ms.to_bytes(2, 'big')) +
                          _element(b'\xa3', b'\x81\0\0\x80\xf8\xff\xfe'))
    return (_element(b'\x1a\x45\xdf\xa3', b'') + b'\x18\x53\x80\x67' + unknown_size +
            tracks + b''.join(cluster(ms) for ms in cluster_times))


def test_wav_recognition_returns_only_actual_provider_transcript():
    calls = []
    def fake(url, *, payload, timeout, max_bytes):
        calls.append((url, payload, timeout, max_bytes))
        return {'results': [{'alternatives': [{'transcript': 'Buy copper.'}]}]}
    result = VoiceService(transport=fake, offline=False).transcribe(_wav(), 'audio/wav', 16000)
    assert result == {'transcript': 'Buy copper.', 'provider': 'google_cloud_stt', 'model': 'latest_short', 'call': 'real'}
    assert calls[0][0] == 'https://speech.googleapis.com/v1/speech:recognize'
    assert calls[0][1]['config']['encoding'] == 'LINEAR16'
    assert calls[0][1]['config']['sampleRateHertz'] == 16000
    assert base64.b64decode(calls[0][1]['audio']['content']) == _wav()
    assert calls[0][2] <= 20 and calls[0][3] <= 1_000_000


def test_webm_recognition_uses_opus_encoding():
    calls = []
    def fake(url, *, payload, **limits):
        calls.append(payload)
        return {'results': [{'alternatives': [{'transcript': '  Watch steel  '}]}]}
    result = VoiceService(transport=fake, offline=False).transcribe(_webm(), 'audio/webm;codecs=opus', 48000)
    assert result['transcript'] == 'Watch steel'
    assert calls[0]['config']['encoding'] == 'WEBM_OPUS'


def test_chrome_unknown_size_sibling_clusters_are_valid_audio():
    calls = []
    def fake(url, *, payload, **limits):
        calls.append(payload)
        return {'results': [{'alternatives': [{'transcript': 'Chrome works'}]}]}
    result = VoiceService(transport=fake, offline=False).transcribe(
        _chrome_style_webm(tuple(range(0, 10_000, 1000))), 'audio/webm;codecs=opus', 48000)
    assert result['transcript'] == 'Chrome works'
    assert len(calls) == 1


def test_chrome_unknown_size_clusters_still_enforce_duration():
    calls = []
    def fake(url, *, payload, **limits):
        calls.append(payload)
        return {'results': []}
    with pytest.raises(DemoError) as error:
        VoiceService(transport=fake, offline=False).transcribe(
            _chrome_style_webm((0, 21000)), 'audio/webm;codecs=opus', 48000)
    assert error.value.code == 'voice_invalid_audio'
    assert calls == []


def test_many_chrome_clusters_parse_without_copying_each_remaining_segment():
    class CountedBytes(bytes):
        copied = 0

        def __getitem__(self, key):
            value = super().__getitem__(key)
            if isinstance(key, slice):
                self.copied += len(value)
            return value

    audio = CountedBytes(_chrome_style_webm(range(0, 20_000, 100)))
    assert _webm_metadata(audio) == 48000
    # The parser may copy small leaf fields, but a container's entire remaining
    # Segment must not be copied once per Cluster.
    assert audio.copied < len(audio) * 5


@pytest.mark.parametrize('audio,mime,rate', [
    (b'not audio', 'audio/webm', 48000),
    (_wav(), 'audio/webm', 16000),
    (_wav(), 'audio/wav', 48000),
    (_wav(seconds=16), 'audio/wav', 16000),
    (_webm(duration_ms=21000), 'audio/webm', 48000),
    (_webm(duration_ms=None, cluster_ms=21000), 'audio/webm', 48000),
    (_webm(block=False), 'audio/webm', 48000),
    (_webm(), 'audio/webm', 16000),
    (_wav(), 'audio/mp3', 16000),
])
def test_invalid_or_oversized_audio_never_reaches_provider(audio, mime, rate):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(1)
        return {'results': []}
    with pytest.raises(DemoError):
        VoiceService(transport=forbidden, offline=False).transcribe(audio, mime, rate)
    assert calls == []


def test_provider_silence_and_malformed_results_are_unavailable():
    for response in ({'results': []}, {'results': [{'alternatives': [{'transcript': ''}]}]},
                     {'results': [{'alternatives': 'bad'}]}, {'results': [{'alternatives': [{'transcript': 'x' * 1201}]}]}):
        with pytest.raises(DemoError):
            VoiceService(transport=lambda *args, **kwargs: response, offline=False).transcribe(_wav(), 'audio/wav', 16000)


def test_speak_returns_bounded_valid_mp3_from_google():
    calls = []
    mp3 = b'\xff\xfb\x90\x64' + b'\0' * 32
    def fake(url, *, payload, **limits):
        calls.append((url, payload))
        return {'audioContent': base64.b64encode(mp3).decode('ascii')}
    result = VoiceService(transport=fake, offline=False).speak('Verified answer.')
    assert result == {'audio_base64': base64.b64encode(mp3).decode('ascii'), 'mime_type': 'audio/mpeg', 'provider': 'google_cloud_tts', 'call': 'real'}
    assert calls[0][0] == 'https://texttospeech.googleapis.com/v1/text:synthesize'
    assert calls[0][1]['audioConfig']['audioEncoding'] == 'MP3'


def test_speak_rejects_invalid_text_and_provider_audio():
    for value in ('', ' ' * 3, 'x' * 1201):
        with pytest.raises(DemoError):
            VoiceService(transport=lambda *args, **kwargs: {}, offline=False).speak(value)
    for response in ({}, {'audioContent': '%%%code'}, {'audioContent': base64.b64encode(b'not mp3').decode()},
                     {'audioContent': base64.b64encode(b'\xff\xfb\x90\x64' + b'0' * 1_000_001).decode()}):
        with pytest.raises(DemoError):
            VoiceService(transport=lambda *args, **kwargs: response, offline=False).speak('Hello')


def test_offline_and_provider_failure_never_fabricate_speech():
    service = VoiceService(transport=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('called')), offline=True)
    with pytest.raises(DemoError) as error:
        service.transcribe(_wav(), 'audio/wav', 16000)
    assert error.value.code == 'voice_unavailable'
    with pytest.raises(DemoError):
        service.speak('Hello')
    def failed(*args, **kwargs):
        raise TimeoutError('private detail')
    with pytest.raises(DemoError) as error:
        VoiceService(transport=failed, offline=False).transcribe(_wav(), 'audio/wav', 16000)
    assert error.value.code == 'voice_unavailable' and 'private detail' not in str(error.value)
