import json
from pathlib import Path

from procure.demo.judge_ai_receipts import summarize_gemini_receipts


CONFIG = Path(__file__).parents[1] / 'config' / 'judge_hindi_case_v1.json'


def test_only_successful_real_retained_calls_count_once():
    valid = {'status': 'success', 'provider': 'vertex_ai', 'model_id': 'gemini-test',
             'model_version': 'v1', 'called_at': '2026-10-17T00:00:00Z',
             'input_source_id': 'pib-2267065', 'output': 'Regional cotton import duty change.',
             'evidence_refs': ['pib-2267065'], 'source_title': 'Official cotton release',
             'source_url': 'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2267065',
             'source_as_of': '2026-05-30',
             'model_reviewed_flags': ['regional_import_exposure']}
    result = summarize_gemini_receipts([valid, valid, {**valid, 'status': 'error'},
                                       {**valid, 'input_source_id': 'other', 'provider': 'deterministic'}])
    assert result['interpreted_articles'] == 1
    assert result['flagged_for_review'] == 1
    assert result['receipts'][0]['model_id'] == 'gemini-test'
    assert result['receipts'][0]['source_url'].startswith('https://www.pib.gov.in/')


def test_unproved_flags_and_missing_output_do_not_count():
    result = summarize_gemini_receipts([{'status': 'success', 'provider': 'vertex_ai',
                                         'input_source_id': 'pib-2267065', 'model_id': 'gemini-test',
                                         'reviewed_flags': ['supplier_price_changed']}])
    assert result['interpreted_articles'] == 0
    assert result['flagged_for_review'] == 0


def test_deterministic_flags_do_not_become_model_flags():
    valid = {'status': 'success', 'provider': 'vertex_ai', 'model_id': 'gemini-test',
             'model_version': 'v1', 'called_at': '2026-10-17T00:00:00Z',
             'input_source_id': 'pib-2267065', 'output': 'Cotton policy context.',
             'evidence_refs': ['pib-2267065'], 'reviewed_flags': ['regional_import_exposure'],
             'source_title': 'Official cotton release',
             'source_url': 'https://www.pib.gov.in/PressReleasePage.aspx?PRID=2267065',
             'source_as_of': '2026-05-30'}
    assert summarize_gemini_receipts([valid])['flagged_for_review'] == 0
    assert summarize_gemini_receipts([{**valid, 'model_id': 'not-gemini'}])['interpreted_articles'] == 0


def test_source_id_self_reference_or_unsafe_url_is_not_evidence():
    record = {'status': 'success', 'provider': 'vertex_ai', 'model_id': 'gemini-test',
              'model_version': 'v1', 'called_at': '2026-10-17T00:00:00Z',
              'input_source_id': 'pib-2267065', 'output': 'Cotton policy context.',
              'evidence_refs': ['pib-2267065']}
    assert summarize_gemini_receipts([record])['interpreted_articles'] == 0
    record.update(source_title='Official cotton release', source_as_of='2026-05-30',
                  source_url='javascript:alert(1)')
    assert summarize_gemini_receipts([record])['interpreted_articles'] == 0
    record['source_url'] = 'http://127.0.0.1/private'
    assert summarize_gemini_receipts([record])['interpreted_articles'] == 0


def test_hindi_fixture_has_public_scope_without_fabricated_translation():
    source = json.loads(CONFIG.read_text())
    assert source['publisher'] == 'Press Information Bureau, Government of India'
    assert source['language'] == 'hi'
    assert source['publication_at'] == '2026-05-30T18:46:00+05:30'
    assert source['scope']['hs_heading'] == '5201'
    assert source['scope']['import_country'] == 'India'
    assert source['translation']['status'] == 'unavailable'
    assert source['gemini_receipt'] is None
