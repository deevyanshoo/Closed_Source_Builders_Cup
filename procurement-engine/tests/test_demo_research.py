"""Public-only preparation and bounded Google Search response contracts."""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import pytest

from procure.demo.company import get_company, list_companies
from procure.demo.operator import run_turn
from procure.demo import live_voice
from procure.demo.research import (ResearchValidationError, VertexResearchProvider,
                                   prepare_research)


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
COMPANY = get_company('solara-cables')


def test_public_question_prepares_exact_visible_query_without_model_call():
    text = 'How would a Hormuz blockage affect petrochemical supply today?'
    proposal = prepare_research(text, COMPANY, NOW)
    assert proposal['query'] == text
    assert proposal['destination'] == 'Google Search via Vertex AI'
    assert 'query' in proposal['shared_data'].lower()

    def forbidden(*_):
        raise AssertionError('model called before consent')

    snapshot = {'company': COMPANY}
    result = run_turn(snapshot, text, forbidden, model='unused', provider_call='mocked')
    assert result['status'] == 'proposal'
    assert result['candidate'] == {'kind': 'research', 'parameters': proposal}
    assert result['agent_steps'][-1]['provider_call'] == 'none'


@pytest.mark.parametrize(('company_id', 'message', 'expected'), [
    ('solara-cables', 'How would a Hormuz blockage affect our company?',
     'Strait of Hormuz shipping status and potential impacts on the electrical cable manufacturing industry'),
    ('atlas-petrochem', 'What does Hormuz disruption mean for Atlas Petrochem?',
     'Strait of Hormuz shipping status and potential impacts on the petrochemicals industry'),
])
def test_company_framed_public_event_gets_visible_public_reformulation(company_id, message, expected):
    company = get_company(company_id)
    proposal = prepare_research(message, company, NOW)
    assert proposal['query'] == expected
    assert company['name'] not in proposal['query']
    result = run_turn({'company': company}, message, lambda *_: pytest.fail('model called'),
                      model='unused', provider_call='mocked')
    assert result['candidate']['parameters']['query'] == expected


def test_full_event_name_reformulates_company_reference():
    proposal = prepare_research('How would Strait of Hormuz disruption affect our company?',
                                get_company('meridian-pharma'), NOW)
    assert proposal['query'] == ('Strait of Hormuz shipping status and potential impacts on '
                                 'the pharmaceutical manufacturing industry')


def test_event_alias_with_extra_spaces_still_reformulates():
    proposal = prepare_research('How would Strait  of Hormuz affect our company?',
                                get_company('meridian-pharma'), NOW)
    assert proposal['query'].startswith('Strait of Hormuz shipping status')


@pytest.mark.parametrize('profile', list_companies(), ids=lambda row: row['company_id'])
@pytest.mark.parametrize('message', ['Latest Hormuz disruption?', 'Current public shipping news?'])
def test_generic_public_question_shows_selected_industry_context(profile, message):
    proposal = prepare_research(message, profile, NOW)
    sector = 'petrochemicals' if profile['company_id'] == 'atlas-petrochem' else profile['sector'].lower()
    assert proposal['query'].startswith(message)
    assert sector in proposal['query'].lower()
    assert profile['name'] not in proposal['query']
    assert len(proposal['query']) <= 400
    assert prepare_research(proposal['query'], profile, NOW) == proposal


def test_explicit_other_industry_preserves_user_topic():
    message = 'How would a Hormuz blockage affect petrochemical supply today?'
    assert prepare_research(message, COMPANY, NOW)['query'] == message


def test_private_detail_rejected_before_industry_context_can_mask_it():
    with pytest.raises(ResearchValidationError):
        prepare_research('Latest Hormuz disruption and our 12,345 kg stock?', COMPANY, NOW)


def test_explicit_industry_cannot_bypass_private_figure_check():
    with pytest.raises(ResearchValidationError):
        prepare_research('Latest Hormuz impact on pharma with 12,345 kg?',
                         get_company('meridian-pharma'), NOW)


@pytest.mark.parametrize('message', [
    'Latest Strait of Hormuz shipping status in 2026',
    'Latest Strait of Hormuz shipping status on 2026-10-15',
    'Current public shipping news for the X500 model?',
])
def test_public_date_or_model_number_keeps_approved_text_and_adds_industry(message):
    proposal = prepare_research(message, get_company('meridian-pharma'), NOW)
    assert proposal['query'].startswith(message)
    assert 'pharmaceutical manufacturing industry' in proposal['query']
    assert prepare_research(proposal['query'], None, NOW)['query'] == proposal['query']


@pytest.mark.parametrize('message', [
    'How would Strait of Hormuz affect our company with 500 kg inventory?',
    'Latest Hormuz impact on pharma with 12,345 kg?',
    'Latest Hormuz news from alice@example.com',
    'Latest Hormuz news with API key sk-abcdefghijklmnop',
])
def test_company_quantities_credentials_and_contacts_still_reject(message):
    with pytest.raises(ResearchValidationError):
        prepare_research(message, get_company('meridian-pharma'), NOW)


def test_revalidated_visible_query_reaches_provider_exactly_once():
    proposal = prepare_research('Latest Hormuz disruption?', get_company('meridian-pharma'), NOW)
    client = FakeClient(_response())
    result = VertexResearchProvider(client=client, clock=lambda: NOW).research(proposal['query'])
    assert result['status'] == 'completed'
    assert len(client.calls) == 1
    assert client.calls[0]['contents'] == proposal['query']


@pytest.mark.parametrize('message', [
    'How would a Hormuz blockage affect our company with 12,345 kg stock?',
    'What does Hormuz disruption mean for Atlas Petrochem with order 42?',
    'How would Hormuz affect our company? Contact alice@example.com',
    'How would Hormuz affect our company? API key sk-abcdefghijklmnop',
    'How would Hormuz affect our company and Swift Copper Trading Ltd?',
    'How would Hormuz affect our company Project Compass?',
])
def test_reformulation_never_masks_private_details(message):
    with pytest.raises(ResearchValidationError):
        prepare_research(message, get_company('atlas-petrochem'), NOW)


@pytest.mark.parametrize('message', [
    'What is our copper inventory?',
    'Which supplier has the shortest lead time?',
    'Prepare an RFQ for copper',
    'What is the latest news in our workspace?',
    'Search inventory for copper',
    'Search suppliers by lead time',
])
def test_ordinary_workspace_questions_are_not_research(message):
    assert prepare_research(message, COMPANY, NOW) is None


@pytest.mark.parametrize('message', [
    'Search online for Solara Cables supplier costs',
    'Look up Swift Copper Trading Ltd [SYNTHETIC] contact',
    'Research our inventory for the latest prices',
    'Search the web for my API key sk-abcdefghijklmnop',
    'Find news about alice@example.com',
    'Research what happened to order solara-cables-order-1',
    'Search latest Hormuz news ' + 'x' * 400,
])
def test_private_or_oversized_public_request_is_rejected(message):
    with pytest.raises(ResearchValidationError):
        prepare_research(message, COMPANY, NOW)


def test_unsafe_research_intent_yields_safe_clarification_without_model_call():
    result = run_turn({'company': COMPANY}, 'Search our inventory online',
                      lambda *_: pytest.fail('model called'), model='unused',
                      provider_call='mocked')
    assert result['status'] == 'needs_clarification'
    assert result['candidate'] is None
    assert 'public' in result['assistant_text'].lower()


def _response(*, uri='https://example.org/hormuz', supports=True, finish='STOP',
              answer='Reports describe shipping risks, not a confirmed closure.',
              suggestions_html='<div>Google Search</div>'):
    metadata = NS(grounding_chunks=[NS(web=NS(uri=uri, title='Shipping report'))],
                  grounding_supports=[NS(grounding_chunk_indices=[0],
                                         segment=NS(text='shipping risks'))] if supports else [],
                  search_entry_point=NS(rendered_content=suggestions_html))
    candidate = NS(finish_reason=finish, content=NS(parts=[NS(text=answer, function_call=None)]),
                   grounding_metadata=metadata)
    return NS(candidates=[candidate])


class FakeClient:
    def __init__(self, response):
        self.calls = []
        self.models = self
        self.response = response

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_grounded_result_uses_one_search_tool_and_preserves_suggestions():
    client = FakeClient(_response())
    result = VertexResearchProvider(client=client, clock=lambda: NOW).research(
        'What is the current Hormuz shipping situation?')
    assert result['status'] == 'completed'
    assert result['answer'].startswith('Reports describe')
    assert result['sources'] == [{'source_id': 'source-1', 'title': 'Shipping report',
                                  'url': 'https://example.org/hormuz'}]
    assert result['search_suggestions_html'] == '<div>Google Search</div>'
    assert result['checked_at'] == NOW.isoformat()
    assert len(client.calls) == 1
    assert len(client.calls[0]['config'].tools) == 1
    assert client.calls[0]['config'].tools[0].google_search is not None
    assert client.calls[0]['model'] == 'gemini-3.5-flash-lite'
    assert client.calls[0]['config'].thinking_config.thinking_level == 'MINIMAL'
    assert client.calls[0]['config'].automatic_function_calling.disable is True
    assert client.calls[0]['config'].max_output_tokens >= 1500
    instruction = client.calls[0]['config'].system_instruction
    assert all(term in instruction.lower() for term in
               ('reported facts', 'business implications', 'qualitative confidence',
                'recommended next steps', 'calibrated probabilities'))
    assert 'named industry' in instruction.lower()
    assert 'direct' in instruction.lower() and 'indirect' in instruction.lower()
    assert 'two short sentences' in instruction.lower()
    assert 'generic lpg' in instruction.lower()


def test_sdk_instruction_uses_trusted_lookup_date_and_source_scope():
    client = FakeClient(_response(uri='https://mining.com.au/copper-report'))
    result = VertexResearchProvider(client=client, clock=lambda: NOW).research(
        'Latest public copper market news?')
    assert result['status'] == 'completed'
    assert result['checked_at'] == NOW.isoformat()
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call['contents'] == 'Latest public copper market news?'
    assert call['model'] == 'gemini-3.5-flash-lite'
    config = call['config']
    assert len(config.tools) == 1 and config.tools[0].google_search is not None
    assert config.automatic_function_calling.disable is True
    assert config.max_output_tokens == 1800
    instruction = config.system_instruction.lower()
    assert '2026-10-15' in instruction
    assert 'as of 2026-10-15 utc' in instruction
    assert 'lookup' in instruction and 'publication date' in instruction
    assert 'unverified' in instruction
    assert 'actual publisher' in instruction
    assert 'official lme' in instruction and 'primary source' in instruction
    assert 'hedg' in instruction and 'secondary' in instruction
    assert 'conditional' in instruction and 'verify' in instruction
    assert 'two short sentences' in instruction and '80 to 130 words' in instruction


def test_sdk_client_is_reused_per_adapter_and_logs_only_success_counts(monkeypatch, caplog):
    from google import genai
    response = _response()
    response.usage_metadata = NS(prompt_token_count=120, cached_content_token_count=30,
                                 candidates_token_count=80)
    made = []

    def client_factory(**kwargs):
        client = FakeClient(response)
        made.append(client)
        return client

    monkeypatch.setattr(genai, 'Client', client_factory)
    monkeypatch.setenv('OFFLINE', '0')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'synthetic-test-project')
    provider = VertexResearchProvider(clock=lambda: NOW)
    with caplog.at_level('INFO', logger='procure.demo.research'):
        first = provider.research('Latest Hormuz shipping news? opaque-canary')
        second = provider.research('Latest public copper prices?')
    assert first['status'] == second['status'] == 'completed'
    assert len(made) == 1
    assert len(made[0].calls) == 2
    assert 'prompt_token_count=120' in caplog.text
    assert 'cached_content_token_count=30' in caplog.text
    assert 'output_token_count=80' in caplog.text
    assert 'elapsed_ms=' in caplog.text
    assert 'opaque-canary' not in caplog.text


def test_concurrent_calls_initialize_one_sdk_client(monkeypatch):
    from google import genai
    from time import sleep
    made = []

    def client_factory(**kwargs):
        sleep(0.01)
        client = FakeClient(_response())
        made.append(client)
        return client

    monkeypatch.setattr(genai, 'Client', client_factory)
    monkeypatch.setenv('OFFLINE', '0')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'synthetic-test-project')
    provider = VertexResearchProvider(clock=lambda: NOW)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(provider.research, ['Latest Hormuz news?'] * 6))
    assert len(made) == 1
    assert len(made[0].calls) == 6
    assert all(row['status'] == 'completed' for row in results)


def test_realistic_grounded_report_length_is_preserved_without_truncation():
    answer = 'Public source findings and conditional business implications. ' * 80
    assert 4000 < len(answer) < 8000
    result = VertexResearchProvider(client=FakeClient(_response(answer=answer)),
                                    clock=lambda: NOW).research('Latest Hormuz shipping news?')
    assert result['status'] == 'completed'
    assert result['answer'] == answer.strip()


def test_unreferenced_extra_chunk_does_not_hide_grounded_answer():
    response = _response()
    response.candidates[0].grounding_metadata.grounding_chunks.extend(
        NS(web=NS(uri='http://127.0.0.1/private', title='Unused')) for _ in range(8))
    result = VertexResearchProvider(client=FakeClient(response), clock=lambda: NOW).research(
        'Latest Hormuz shipping news?')
    assert result['status'] == 'completed'
    assert len(result['sources']) == 1


def test_twenty_four_referenced_public_sources_are_accepted():
    response = _response()
    metadata = response.candidates[0].grounding_metadata
    metadata.grounding_chunks = [NS(web=NS(uri=f'https://example.org/report/{i}',
                                            title=f'Report {i}')) for i in range(24)]
    metadata.grounding_supports = [NS(grounding_chunk_indices=list(range(24)))]
    result = VertexResearchProvider(client=FakeClient(response), clock=lambda: NOW).research(
        'Latest Hormuz shipping news?')
    assert result['status'] == 'completed'
    assert len(result['sources']) == 24
    assert result['sources'][-1]['source_id'] == 'source-24'


@pytest.mark.parametrize('indices', [[24], [True], [-1], ['0']])
def test_malformed_reference_still_rejected(indices):
    response = _response()
    response.candidates[0].grounding_metadata.grounding_supports = [
        NS(grounding_chunk_indices=indices)]
    result = VertexResearchProvider(client=FakeClient(response), clock=lambda: NOW).research(
        'Latest Hormuz shipping news?')
    assert result['status'] == 'unavailable'


def test_too_many_referenced_sources_still_rejected():
    response = _response()
    metadata = response.candidates[0].grounding_metadata
    metadata.grounding_chunks = [NS(web=NS(uri=f'https://example.org/report/{i}',
                                            title=f'Report {i}')) for i in range(25)]
    metadata.grounding_supports = [NS(grounding_chunk_indices=list(range(25)))]
    result = VertexResearchProvider(client=FakeClient(response), clock=lambda: NOW).research(
        'Latest Hormuz shipping news?')
    assert result['status'] == 'unavailable'


def test_safe_google_suggestions_html_is_preserved_exactly():
    html = ("<style>.x{background:url('https://www.gstatic.com/search.png')}</style>"
            '<div class="x"><a href="https://www.google.com/search?q=hormuz&amp;hl=en" '
            'target="_blank">Search</a><img src="https://www.gstatic.com/search.png"/></div>')
    result = VertexResearchProvider(client=FakeClient(_response(suggestions_html=html)),
                                    clock=lambda: NOW).research('Latest Hormuz shipping news?')
    assert result['status'] == 'completed'
    assert result['search_suggestions_html'] == html


@pytest.mark.parametrize('html', [
    '<a href="javascript:alert(1)">Search</a>',
    '<a href="&#106;avascript:alert(1)">Search</a>',
    '<a href="http://www.google.com/search?q=hormuz">Search</a>',
    '<a href="https://127.0.0.1/private">Search</a>',
    '<a href="https://127.1/private">Search</a>',
    '<a href="https://0177.0.0.1/private">Search</a>',
    '<a href="https://localhost./private">Search</a>',
    '<a href="https://user:pass@example.com/private">Search</a>',
    '<img src="http://example.com/search.png"/>',
    '<svg><use xlink:href="javascript:alert(1)"/></svg>',
    '<script>alert(1)</script>',
    '<iframe src="https://example.com"></iframe>',
    '<object data="https://example.com"></object>',
    '<embed src="https://example.com"/>',
    '<form action="https://example.com"><button>Go</button></form>',
    '<base href="https://example.com"/>',
    '<meta http-equiv="refresh" content="0;url=https://example.com"/>',
    '<div onclick="window.open(\'https://example.com\')">Search</div>',
    '<img srcset="https://www.gstatic.com/search.png 1x"/>',
    '<link rel="stylesheet" href="https://example.com/style.css"/>',
    '<![CDATA[<a href="javascript:alert(1)">Search</a>]]>',
    '<style>.x{background:url(https://www.gstatic.com/search.png);@\\69mport url(https://example.com)}</style>',
    '<style>@import url(https://www.gstatic.com/search.css)</style>',
    '<style>&#64;import url(https://www.gstatic.com/search.css)</style>',
    '<style>.x{background:url(javascript:alert(1))}</style>',
    '<style>.x{background:u&#114;l(javascript:alert(1))}</style>',
    '<style>.x{background:url(https://127.0.0.1/private)}</style>',
    '<div style="background:url(http://example.com/search.png)">Search</div>',
    '<a href="https://example.com"',
    '<a href="https://example.com">Search',
])
def test_unsafe_or_malformed_suggestions_are_omitted_without_losing_grounded_answer(html):
    result = VertexResearchProvider(client=FakeClient(_response(suggestions_html=html)),
                                    clock=lambda: NOW).research('Latest Hormuz shipping news?')
    assert result['status'] == 'completed'
    assert result['answer'] == 'Reports describe shipping risks, not a confirmed closure.'
    assert len(result['sources']) == 1
    assert result['search_suggestions_html'] is None


def test_sdk_client_disables_hidden_http_retries(monkeypatch):
    from google import genai
    made = {}

    def client_factory(**kwargs):
        made.update(kwargs)
        return FakeClient(_response())

    monkeypatch.setattr(genai, 'Client', client_factory)
    monkeypatch.setenv('OFFLINE', '0')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT', 'synthetic-test-project')
    result = VertexResearchProvider(clock=lambda: NOW).research('What is the current Hormuz shipping situation?')
    assert result['status'] == 'completed'
    assert made['http_options'].retry_options.attempts == 1


@pytest.mark.parametrize('response', [
    _response(supports=False),
    _response(uri='http://example.org/hormuz'),
    _response(uri='https://user:pass@example.org/hormuz'),
    _response(uri='https://127.0.0.1/private'),
    _response(uri='https://127.1/private'),
    _response(finish='MAX_TOKENS'),
    _response(answer=''),
    _response(answer='x' * 8001),
])
def test_ungrounded_or_unsafe_result_is_unavailable(response):
    result = VertexResearchProvider(client=FakeClient(response), clock=lambda: NOW).research(
        'What is the latest public shipping situation?')
    assert result['status'] == 'unavailable'
    assert result['sources'] == []
    assert result['answer'] == ''


def test_provider_failure_is_unavailable_without_retry():
    class Broken(FakeClient):
        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError()

    client = Broken(None)
    result = VertexResearchProvider(client=client, clock=lambda: NOW).research('Latest public copper prices?')
    assert result['status'] == 'unavailable'
    assert len(client.calls) == 1


def test_validation_failure_logs_only_bounded_reason_and_counts(caplog):
    response = _response()
    response.candidates[0].grounding_metadata.grounding_supports = [
        NS(grounding_chunk_indices=[99])]
    secret_query = 'Latest public copper prices? opaque-canary'
    with caplog.at_level('WARNING', logger='procure.demo.research'):
        result = VertexResearchProvider(client=FakeClient(response), clock=lambda: NOW).research(secret_query)
    assert result['status'] == 'unavailable'
    assert 'reason=invalid_reference' in caplog.text
    assert 'chunks=1' in caplog.text
    assert secret_query not in caplog.text


def test_provider_exception_logs_class_and_status_without_message(caplog):
    class Broken(FakeClient):
        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            error = TimeoutError('private-marker from upstream')
            error.status_code = 504
            raise error

    with caplog.at_level('WARNING', logger='procure.demo.research'):
        result = VertexResearchProvider(client=Broken(None), clock=lambda: NOW).research(
            'Latest public copper prices?')
    assert result['status'] == 'unavailable'
    assert 'reason=provider_exception' in caplog.text
    assert 'error_class=TimeoutError' in caplog.text
    assert 'http_status=504' in caplog.text
    assert 'private-marker' not in caplog.text


def test_google_client_error_code_is_logged_without_private_details(caplog):
    from google.genai.errors import ClientError

    class RateLimited(FakeClient):
        def generate_content(self, **kwargs):
            self.calls.append(kwargs)
            raise ClientError(429, {'error': {'message': 'private-marker from upstream',
                                              'status': 'RESOURCE_EXHAUSTED'}})

    client = RateLimited(None)
    query = 'Latest public copper prices? opaque-canary'
    with caplog.at_level('WARNING', logger='procure.demo.research'):
        result = VertexResearchProvider(client=client, clock=lambda: NOW).research(query)
    assert result['status'] == 'unavailable'
    assert len(client.calls) == 1
    assert 'error_class=ClientError' in caplog.text
    assert 'http_status=429' in caplog.text
    assert query not in caplog.text
    assert 'private-marker' not in caplog.text


def test_provider_rejects_private_query_before_call():
    client = FakeClient(_response())
    result = VertexResearchProvider(client=client, clock=lambda: NOW).research(
        'Search Solara Cables inventory')
    assert result['status'] == 'unavailable'
    assert client.calls == []


def test_voice_declares_preparation_and_read_only_retrieval_tools():
    declarations = live_voice._config(COMPANY).tools[0].function_declarations
    specs = {item.name: item.parameters_json_schema for item in declarations}
    assert specs['prepare_research']['required'] == ['message']
    assert specs['read_research']['required'] == []
    assert 'never confirm' in live_voice.INSTRUCTION.lower()
