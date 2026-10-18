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


@pytest.mark.parametrize('written_date', ['2026/10/16', '2026 10 16', '२०२६/१०/१६', '२०२६ १० १६'])
def test_full_year_first_date_separator_preserves_provenance(written_date):
    caption = HINDI.replace('2026-10-16', written_date)
    assert grounding.numeric_provenance(CANONICAL, caption)
    assert not grounding.english_exact('What do our models say?', CANONICAL,
                                      CANONICAL.replace('2026-10-16', written_date))


@pytest.mark.parametrize('written_date', [
    '2026/10/17', '2027/10/16', '2026 10 17', '2027 10 16',
    '16/10/2026', '16 10 2026', '2026/10 16', '2026 10/16',
    '2026.10.16', '2026/1/16', '2026 10 1', '2026/02/30',
    'twenty twenty six/10/16', 'x2026/10/16', '2026/10/16x',
])
def test_changed_ambiguous_or_malformed_year_first_date_rejects(written_date):
    assert not grounding.numeric_provenance(CANONICAL,
        HINDI.replace('2026-10-16', written_date))


@pytest.mark.parametrize('caption', [
    HINDI.replace('2026-10-16', '2026/10/16') + ' 500',
    HINDI.replace('2026-10-16', '2026/10/16').replace('कोई ऑर्डर', 'कोई 9 ऑर्डर'),
    HINDI.replace('2026-10-16', '2026 10 16').replace('। लागतें', ' 16। लागतें'),
    HINDI.replace('2026-10-16', ''),
])
def test_alternate_date_cannot_hide_extra_or_missing_numbers(caption):
    assert not grounding.numeric_provenance(CANONICAL, caption)


def test_safe_date_format_diagnostic_has_no_source_values(caplog):
    with caplog.at_level('INFO', logger='procure.demo'):
        assert grounding.numeric_provenance(CANONICAL, HINDI.replace('2026-10-16', '2026/10/16'))
        assert not grounding.numeric_provenance(CANONICAL, HINDI.replace('2026-10-16', '2026 10 17'))
    entries = [json.loads(record.message) for record in caplog.records
        if 'voice_market_numeric_review' in record.message]
    assert entries == [
        {'event': 'voice_market_numeric_review', 'date_format': 'ymd_slash',
         'recognized_date_count': 1, 'remaining_number_count': 0, 'provenance_match': True},
        {'event': 'voice_market_numeric_review', 'date_format': 'ymd_space',
         'recognized_date_count': 1, 'remaining_number_count': 0, 'provenance_match': False},
    ]
    assert '2026' not in ''.join(record.message for record in caplog.records)


def test_safe_diagnostic_counts_remaining_number_without_logging_value(caplog):
    canonical = 'On 2026-10-16, cost 500.50 USD.'
    caption = 'On 2026/10/16, cost 500.51 USD.'
    with caplog.at_level('INFO', logger='procure.demo'):
        assert not grounding.numeric_provenance(canonical, caption)
    entry, = [json.loads(record.message) for record in caplog.records
        if 'voice_market_numeric_review' in record.message]
    assert entry == {'event': 'voice_market_numeric_review', 'date_format': 'ymd_slash',
                     'recognized_date_count': 1, 'remaining_number_count': 1,
                     'provenance_match': False}
    assert '500' not in ''.join(record.message for record in caplog.records)


@pytest.mark.parametrize('written_date', ['2026/10/16', '2026 10 16'])
def test_alternate_date_still_requires_all_four_provider_booleans(written_date):
    caption = HINDI.replace('2026-10-16', written_date)
    for field in grounding._FIELDS:
        flags = dict.fromkeys(grounding._FIELDS, True)
        flags[field] = False
        async def generate_content(**kwargs):
            return response(json.dumps(flags))
        client = NS(aio=NS(models=NS(generate_content=generate_content)))
        assert not asyncio.run(grounding.verify_market_caption(
            'कृपया पूर्वानुमान हिंदी में बताइए', CANONICAL, caption, client=client))


def test_alternate_date_provider_timeout_rejects():
    async def generate_content(**kwargs):
        raise asyncio.TimeoutError
    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    caption = HINDI.replace('2026-10-16', '2026/10/16')
    assert not asyncio.run(grounding.verify_market_caption(
        'कृपया पूर्वानुमान हिंदी में बताइए', CANONICAL, caption, client=client))


@pytest.mark.parametrize('written_date', ['2026/10/16', '2026 10 16'])
def test_alternate_date_only_accepts_after_positive_semantic_verdict(written_date):
    async def generate_content(**kwargs):
        return response(json.dumps(dict.fromkeys(grounding._FIELDS, True)))
    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    caption = HINDI.replace('2026-10-16', written_date)
    assert asyncio.run(grounding.verify_market_caption(
        'कृपया पूर्वानुमान हिंदी में बताइए', CANONICAL, caption, client=client))


@pytest.mark.parametrize('written_date', [
    'October 16, 2026', 'October16,2026', '16 October 2026',
    '16 de outubro de 2026', '16deoutubrode2026',
    '16 अक्टूबर 2026', '16अक्टूबर2026', '१६ अक्टूबर २०२६',
])
def test_written_caption_date_has_same_provenance_as_iso(written_date):
    assert grounding.numeric_provenance(CANONICAL, HINDI.replace('2026-10-16', written_date))


@pytest.mark.parametrize('written_date', [
    'October 17, 2026', 'November 16, 2026',
    '16 de setembro de 2026', '16 नवंबर 2026',
    '32 October 2026', '16-10-2026',
])
def test_changed_invalid_or_ambiguous_caption_date_rejects(written_date):
    assert not grounding.numeric_provenance(CANONICAL, HINDI.replace('2026-10-16', written_date))


def test_all_dates_and_date_multiplicity_are_bound():
    canonical = 'From 2026-10-16 through 2026-11-16, with 16 units.'
    assert grounding.numeric_provenance(canonical,
        'From October 16, 2026 through 16 de novembro de 2026, with 16 units.')
    assert not grounding.numeric_provenance(canonical,
        'From October 16, 2026 through October 16, 2026, with 16 units.')
    assert not grounding.numeric_provenance(canonical,
        'From October 16, 2026 through 16 de novembro de 2026, with 16 units on 17 October 2026.')
    assert not grounding.numeric_provenance('On 2026-10-16 and 2026-10-16, 16 units.',
        'On October 16, 2026, 16 units.')


def test_date_equivalence_does_not_waive_other_numeric_claims():
    canonical = 'On 2026-10-16 the amount is 500.50 USD; no order was made.'
    assert grounding.numeric_provenance(canonical,
        'Em 16 de outubro de 2026 o valor é 500.50 USD; nenhuma encomenda foi feita.')
    assert not grounding.numeric_provenance(canonical,
        'Em 16 de outubro de 2026 o valor é 500.51 USD; nenhuma encomenda foi feita.')
    assert not grounding.numeric_provenance(canonical,
        'Em 16 de outubro de 2026 o valor é 500.50 USD e 20 USD extra.')
    assert grounding.numeric_provenance(canonical,
        '१६ अक्टूबर २०२६ को राशि ५००.५० USD है और कोई आदेश नहीं दिया गया।')


def test_written_date_can_reach_semantic_verifier_but_missing_no_action_qualifier_rejects():
    calls = []
    async def generate_content(**kwargs):
        calls.append(kwargs)
        return response(json.dumps({**dict.fromkeys(grounding._FIELDS, True),
            'qualifications_preserved': False}))
    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    caption = 'संदर्भ 16 अक्टूबर 2026। लागतें स्थिर हैं।'
    assert grounding.numeric_provenance(CANONICAL, caption)
    assert not asyncio.run(grounding.verify_market_caption(
        'कृपया पूर्वानुमान हिंदी में बताइए', CANONICAL, caption, client=client))
    assert len(calls) == 1


def test_written_date_passes_only_with_positive_semantic_verdict():
    calls = []
    async def generate_content(**kwargs):
        calls.append(kwargs)
        return response(json.dumps(dict.fromkeys(grounding._FIELDS, True)))
    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    caption = HINDI.replace('2026-10-16', '16 अक्टूबर 2026')
    assert asyncio.run(grounding.verify_market_caption(
        'कृपया पूर्वानुमान हिंदी में बताइए', CANONICAL, caption, client=client))
    assert len(calls) == 1


@pytest.mark.parametrize('failed_field', grounding._FIELDS)
def test_written_date_still_requires_every_semantic_verdict(failed_field):
    flags = dict.fromkeys(grounding._FIELDS, True)
    flags[failed_field] = False
    async def generate_content(**kwargs):
        return response(json.dumps(flags))
    client = NS(aio=NS(models=NS(generate_content=generate_content)))
    caption = HINDI.replace('2026-10-16', '16 अक्टूबर 2026')
    assert not asyncio.run(grounding.verify_market_caption(
        'कृपया पूर्वानुमान हिंदी में बताइए', CANONICAL, caption, client=client))


def test_english_fastpath_does_not_override_current_language_request():
    assert grounding.english_exact('What do our models say?', CANONICAL, CANONICAL)
    for utterance in ('What do our models say? Answer in Hindi.', 'कृपया बताइए',
                      'O que dizem nossos modelos?', 'What do our models say? Translate to French.'):
        assert not grounding.english_exact(utterance, CANONICAL, CANONICAL)


def test_unavailable_provider_fails_closed(monkeypatch):
    monkeypatch.setenv('PROCURE_OFFLINE', 'true')
    assert not asyncio.run(grounding.verify_market_caption('What do our models say?', CANONICAL, PORTUGUESE))
