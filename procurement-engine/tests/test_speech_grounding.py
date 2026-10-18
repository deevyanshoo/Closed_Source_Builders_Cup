"""Strict text review transport with synthetic provider responses (no real acceptance)."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from procure.demo import speech_grounding as grounding

CANONICAL = 'Reference 2026-10-16. Costs stay fixed. No order was made.'
HINDI = 'संदर्भ 2026-10-16। लागतें स्थिर हैं। कोई ऑर्डर नहीं हुआ।'
PORTUGUESE = 'Referência 2026-10-16. Custos fixos. Nenhum pedido foi feito.'


def response(text):
    return NS(candidates=[NS(finish_reason='STOP', content=NS(parts=[NS(text=text)]))])


@pytest.mark.parametrize('caption', [HINDI, PORTUGUESE])
def test_fake_provider_review_sends_only_current_texts_and_strict_schema(caption):
    calls = []
    async def generate_content(**kwargs):
        calls.append(kwargs)
        return response(json.dumps({key: True for key in grounding._FIELDS}))
    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    assert asyncio.run(grounding.verify_market_caption('What do our models say?', CANONICAL, caption, client=client))
    call, = calls
    assert json.loads(call['contents']) == {'current_utterance': 'What do our models say?',
        'canonical_answer': CANONICAL, 'actual_caption': caption}
    assert call['model'] == grounding.MODEL
    assert call['config'].response_json_schema == grounding._SCHEMA
    assert call['config'].automatic_function_calling.disable is True


@pytest.mark.parametrize('field', grounding._FIELDS)
def test_any_semantic_or_language_failure_rejects(field):
    flags = dict.fromkeys(grounding._FIELDS, True)
    flags[field] = False
    assert not grounding._accepted(response(json.dumps(flags)))


@pytest.mark.parametrize('text', ['{}', 'true', '{"faithful_meaning":true,"faithful_meaning":true}',
    json.dumps(dict.fromkeys(grounding._FIELDS, 1)),
    json.dumps({**dict.fromkeys(grounding._FIELDS, True), 'extra': True})])
def test_invalid_schema_never_accepts(text):
    assert not grounding._accepted(response(text))


def test_number_and_date_provenance_cannot_be_waived():
    assert grounding.numeric_provenance(CANONICAL, HINDI)
    assert not grounding.numeric_provenance(CANONICAL, HINDI + ' बचत 500।')
    assert not grounding.numeric_provenance(CANONICAL, HINDI.replace('2026-10-16', '2026-10-17'))
    assert not grounding.numeric_provenance(CANONICAL, HINDI.replace('2026-10-16', '16-10-2026'))


def test_english_fastpath_does_not_override_current_language_request():
    assert grounding.english_exact('What do our models say?', CANONICAL, CANONICAL)
    for utterance in ('What do our models say? Answer in Hindi.', 'कृपया बताइए',
                      'O que dizem nossos modelos?', 'What do our models say? Translate to French.'):
        assert not grounding.english_exact(utterance, CANONICAL, CANONICAL)


def test_unavailable_provider_fails_closed(monkeypatch):
    monkeypatch.setenv('PROCURE_OFFLINE', 'true')
    assert not asyncio.run(grounding.verify_market_caption('What do our models say?', CANONICAL, PORTUGUESE))
