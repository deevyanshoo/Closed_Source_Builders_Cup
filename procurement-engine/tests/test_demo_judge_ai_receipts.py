import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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


def test_vertex_hindi_provider_requests_output_contract_and_retains_actual_receipt_metadata(monkeypatch):
    from google import genai
    import procure.demo.operator as operator
    from procure.demo.experience_service import VertexHindiProvider, hindi_payload, sanitize_receipt

    seen = {}
    class Boundary:
        available = True
        model = 'gemini-3.5-flash-lite'
    class Models:
        def generate_content(self, *, model, contents, config):
            seen.update(model=model, payload=json.loads(contents), config=config)
            return SimpleNamespace(
                candidates=[SimpleNamespace(finish_reason='STOP')],
                text=json.dumps({'output':'English rendering: a cotton policy notice. '
                    'Conditional implication: regional sourcing may need review. '
                    'Limitation: the title alone does not establish supplier prices.',
                    'model_reviewed_flags':['public_policy_change','review_source']}),
                model_version='gemini-3.5-flash-lite-actual', response_id='provider-call-1')
    class Client:
        def __init__(self, **kwargs):
            seen['client_args'] = kwargs
            self.models = Models()
        def close(self):
            seen['closed'] = True

    monkeypatch.setattr(operator, 'VertexOperatorProvider', Boundary)
    monkeypatch.setattr(genai, 'Client', Client)
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'synthetic-project')
    monkeypatch.setenv('GOOGLE_CLOUD_LOCATION', 'global')
    raw = VertexHindiProvider()(hindi_payload())

    schema = seen['config'].response_json_schema
    assert seen['config'].response_mime_type == 'application/json'
    assert seen['config'].temperature == 0 and seen['config'].max_output_tokens == 640
    assert seen['config'].automatic_function_calling.disable is True
    assert schema['type'] == 'object'
    assert set(schema['required']) == {'output', 'model_reviewed_flags'}
    assert schema['properties']['output']['type'] == 'string'
    flags = schema['properties']['model_reviewed_flags']
    assert flags['type'] == 'array' and flags['maxItems'] == 3
    assert flags['items']['type'] == 'string'
    assert set(flags['items']['enum']) == {'public_policy_change', 'regional_import_exposure', 'review_source'}
    assert '"output"' in seen['payload']['instruction']
    assert '"model_reviewed_flags"' in seen['payload']['instruction']
    assert seen['model'] == 'gemini-3.5-flash-lite'
    assert seen['client_args']['http_options'].timeout == 15000
    assert seen['client_args']['http_options'].api_version == 'v1'
    assert seen['closed'] is True
    receipt = sanitize_receipt(raw, datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert receipt['model_version'] == 'gemini-3.5-flash-lite-actual'
    assert receipt['provider_call_id'] == 'provider-call-1'
    assert receipt['output'].startswith('English rendering:')
    assert receipt['model_reviewed_flags'] == ['public_policy_change', 'review_source']
    assert sanitize_receipt({**raw, 'output': None}, datetime(2026, 10, 17, tzinfo=timezone.utc)) is None
