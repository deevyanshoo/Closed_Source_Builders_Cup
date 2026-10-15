"""Public-only preparation and bounded Google Search response contracts."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

from procure.demo.company import get_company
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


def test_realistic_grounded_report_length_is_preserved_without_truncation():
    answer = 'Public source findings and conditional business implications. ' * 80
    assert 4000 < len(answer) < 8000
    result = VertexResearchProvider(client=FakeClient(_response(answer=answer)),
                                    clock=lambda: NOW).research('Latest Hormuz shipping news?')
    assert result['status'] == 'completed'
    assert result['answer'] == answer.strip()


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
def test_unsafe_or_malformed_suggestions_make_research_unavailable(html):
    result = VertexResearchProvider(client=FakeClient(_response(suggestions_html=html)),
                                    clock=lambda: NOW).research('Latest Hormuz shipping news?')
    assert result['status'] == 'unavailable'
    assert result['answer'] == ''
    assert result['sources'] == []
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
