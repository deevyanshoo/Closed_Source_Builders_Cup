from procure.demo.company import get_company
from procure.demo.integration_tools import integration_request, integration_read
from procure.demo.market_prediction import market_read_model, influence_for_material
from fastapi.testclient import TestClient
from procure.demo.app import create_app
from test_demo_live_voice_clarification import workspace, ask, tool_result


NO_PUBLIC_SEARCH = ('What do the prediction models say for our business? Explain what applies '
                    'to our company and what information is missing. Do not search the internet.')


def test_company_market_question_reads_internal_reference_without_search():
    company = get_company('solara-cables')
    market = market_read_model()
    snapshot = {'company_id': company['company_id'], 'context_version': 1,
                'company': company, 'selected_material_id': 'copper',
                'prediction_context': influence_for_material(company, 'copper', market),
                'plan': None}
    message = 'Should we buy our copper now or wait based on the market forecast?'
    assert integration_request(snapshot, message) == 'read_market'
    result = integration_read(snapshot, 'read_market', message)
    assert result['workspace']['prediction']['recommendation'] is None
    assert '2026-10-16' in result['assistant_text']
    assert 'probability' not in result['assistant_text'].lower()
    assert 'savings' not in result['assistant_text'].lower()


def test_explicit_no_search_market_question_stays_internal_but_positive_search_does_not():
    company = get_company('solara-cables')
    snapshot = {'company_id': company['company_id'], 'context_version': 1,
                'company': company, 'selected_material_id': 'copper', 'plan': None}
    assert integration_request(snapshot, NO_PUBLIC_SEARCH) == 'read_market'
    assert integration_request(snapshot, NO_PUBLIC_SEARCH.replace('Do not search', 'Search')) is None


def test_typed_operator_reads_market_without_public_search_or_model_call():
    class NoProvider:
        available = False
        model = 'unavailable'
        def run(self, *args, **kwargs):
            raise AssertionError('public/model request was not needed')
    client = TestClient(create_app(operator_provider=NoProvider()), base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    response = client.post('/api/demo/operator',
        headers={'Origin':'https://demo.test','X-CSRF-Token':session['csrf_token']},
        json={'message':'Should we buy our copper now or wait based on the market forecast?',
              'expected_context_version':1,'idempotency_key':'market-operator'})
    assert response.status_code == 200, response.text
    outcome = response.json()
    assert outcome['status'] == 'completed'
    assert outcome['workspace']['prediction']['recommendation'] is None
    assert outcome['read_only'] is True
    assert '2026-10-16' in outcome['assistant_text']


def test_typed_no_search_market_query_and_positive_public_request_keep_distinct_paths():
    class NoProvider:
        available = False
        model = 'unavailable'
        def run(self, *args, **kwargs):
            raise AssertionError('model call was not needed')
    client = TestClient(create_app(operator_provider=NoProvider()), base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
    internal = client.post('/api/demo/operator', headers=headers,
        json={'message': NO_PUBLIC_SEARCH, 'expected_context_version': 1,
              'idempotency_key': 'market-no-search'})
    assert internal.status_code == 200, internal.text
    result = internal.json()
    assert result['status'] == 'completed' and result['read_only']
    assert result['workspace']['prediction']['market_influence']['status'] == 'CONTEXT_ONLY'
    assert result['proposal'] is None

    public = client.post('/api/demo/operator', headers=headers,
        json={'message': 'Search the internet for current copper market news.',
              'expected_context_version': 1, 'idempotency_key': 'public-market-search'})
    assert public.status_code == 200, public.text
    assert public.json()['status'] == 'proposal'
    assert public.json()['proposal']['kind'] == 'research'


def test_native_read_workspace_projects_same_market_context_as_typed_market_read(workspace):
    client, provider, operator = workspace
    provider.tool_name = 'read_workspace'
    provider.chunks = (NO_PUBLIC_SEARCH,)
    spoken = tool_result(ask(client))
    assert spoken['status'] == 'completed' and spoken['read_only']
    context = spoken['workspace']['prediction_context']
    assert context['material_id'] == 'copper'
    assert context['status'] == 'CONTEXT_ONLY'
    session = client.get('/api/demo/session').json()
    typed = client.post('/api/demo/operator', headers={'Origin': 'https://demo.test',
        'X-CSRF-Token': session['csrf_token']}, json={'message': NO_PUBLIC_SEARCH,
        'expected_context_version': session['context_version'],
        'idempotency_key': 'typed-voice-market-parity'})
    assert typed.status_code == 200, typed.text
    prediction = typed.json()['workspace']['prediction']
    assert context['market_run_hash'] == prediction['market_run_hash']
    assert context == prediction['market_influence']
    assert operator.calls == []


def test_native_and_typed_market_question_share_internal_read(workspace):
    client, provider, operator = workspace
    provider.tool_name = 'operate_workspace'
    provider.chunks = (NO_PUBLIC_SEARCH,)
    spoken = tool_result(ask(client))
    assert spoken['status'] == 'completed' and spoken['read_only']
    assert spoken['proposal'] is None
    assert spoken['workspace']['prediction']['recommendation'] is None
    session = client.get('/api/demo/session').json()
    typed = client.post('/api/demo/operator', headers={'Origin': 'https://demo.test',
        'X-CSRF-Token': session['csrf_token']}, json={'message': NO_PUBLIC_SEARCH,
        'expected_context_version': session['context_version'],
        'idempotency_key': 'typed-native-market-parity'})
    assert typed.status_code == 200, typed.text
    assert typed.json()['assistant_text'] == spoken['assistant_text']
    assert typed.json()['workspace']['prediction'] == spoken['workspace']['prediction']
    assert operator.calls == []
