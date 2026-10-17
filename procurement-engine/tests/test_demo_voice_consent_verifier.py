"""Completed voice-utterance transcription remains a bounded, text-only consent input."""
import asyncio
import io
import json
from types import SimpleNamespace as NS
import wave

import pytest

from procure.demo.voice_consent import verify_spoken_consent


def _pcm(milliseconds=250):
    return b'\x01\x00' * (16 * milliseconds)


def _response(payload='{"transcript":"Confirm research."}', *, finish='STOP', function=False):
    return NS(candidates=[NS(finish_reason=finish, content=NS(parts=[
        NS(text=payload, function_call=NS(name='confirm_research') if function else None)]))])


class FakeClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response or _response()
        self.error = error
        self.aio = NS(models=self)

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_complete_pcm_clip_is_sent_as_one_mono_16k_wav_without_context():
    client = FakeClient()
    audio = _pcm(250)
    transcript = asyncio.run(verify_spoken_consent(audio, client=client))
    assert transcript == 'Confirm research.'
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call['model'] == 'gemini-3.5-flash-lite'
    assert len(call['contents']) == 2
    assert isinstance(call['contents'][0], str)
    assert 'transcribe' in call['contents'][0].lower()
    assert 'confirm research' not in call['contents'][0].lower()
    inline = call['contents'][1].inline_data
    assert inline.mime_type == 'audio/wav'
    with wave.open(io.BytesIO(inline.data), 'rb') as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.readframes(wav.getnframes()) == audio
    config = call['config']
    assert config.response_mime_type == 'application/json'
    assert config.max_output_tokens == 128
    assert config.automatic_function_calling.disable is True
    assert config.temperature == 0


@pytest.mark.parametrize('audio', [None, 'pcm', b'', b'\x00' * 7998,
                                         b'\x00' * 8001, b'\x00' * 256002])
def test_invalid_pcm_length_is_rejected_without_call(audio):
    client = FakeClient()
    assert asyncio.run(verify_spoken_consent(audio, client=client)) is None
    assert client.calls == []


@pytest.mark.parametrize('response', [
    _response('{"transcript":"Confirm research.","approved":true}'),
    _response('{"transcript":true}'),
    _response('{"transcript":""}'),
    _response(json.dumps({'transcript': 'x' * 161})),
    _response('{"transcript":"yes\\nconfirm"}'),
    _response('not json'),
    _response(finish='MAX_TOKENS'),
    _response(function=True),
    NS(candidates=[]),
])
def test_untrusted_or_incomplete_model_output_is_not_consent_text(response):
    client = FakeClient(response)
    assert asyncio.run(verify_spoken_consent(_pcm(), client=client)) is None
    assert len(client.calls) == 1


def test_provider_failure_logs_reason_only(caplog):
    client = FakeClient(error=TimeoutError('private transcript marker'))
    with caplog.at_level('WARNING', logger='procure.demo.voice_consent'):
        assert asyncio.run(verify_spoken_consent(_pcm(), client=client)) is None
    assert len(client.calls) == 1
    assert 'reason=provider_exception' in caplog.text
    assert 'private transcript marker' not in caplog.text


def test_unavailable_project_does_not_create_client(monkeypatch):
    monkeypatch.delenv('GOOGLE_CLOUD_PROJECT', raising=False)
    monkeypatch.setenv('OFFLINE', '1')
    assert asyncio.run(verify_spoken_consent(_pcm())) is None


def test_real_client_uses_existing_vertex_project_and_one_request(monkeypatch):
    from google import genai
    made = {}
    client = FakeClient()

    def factory(**kwargs):
        made.update(kwargs)
        return client

    monkeypatch.setattr(genai, 'Client', factory)
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'synthetic-test-project')
    monkeypatch.delenv('OFFLINE', raising=False)
    monkeypatch.delenv('PROCURE_OFFLINE', raising=False)
    assert asyncio.run(verify_spoken_consent(_pcm())) == 'Confirm research.'
    assert made['vertexai'] is True
    assert made['project'] == 'synthetic-test-project'
    assert made['location'] == 'global'
    assert made['http_options'].api_version == 'v1'
    assert made['http_options'].timeout == 10000
    assert made['http_options'].retry_options.attempts == 1
    assert len(client.calls) == 1
