"""Only current workspace facts enter deterministic carbon and dispatch reads."""
from datetime import datetime, timezone

import pytest

from procure.demo.integration_tools import integration_read, integration_request
from procure.demo.company import list_companies
from procure.demo.dispatch_view import build_dispatch_view
from procure.demo.news_brief import is_workspace_news_question
from procure.demo.operator import run_turn
from procure.demo.research import prepare_research
from test_demo_operator import Provider, snapshot
from test_demo_dispatch_view import reviewed_copper


@pytest.mark.parametrize('message', [
    'What news about carbon policy could affect procurement?',
    'Find grants for reducing emissions',
    'Research external shipment delays',
    'Explain emissions and send email',
])
def test_non_workspace_or_mixed_requests_do_not_read_current_assessment(message):
    assert integration_request(snapshot(), message) is None


def test_news_research_and_model_routes_keep_their_existing_boundaries():
    state = snapshot()
    news = 'What news about carbon policy could affect procurement?'
    assert is_workspace_news_question(news)
    assert integration_request(state, news) is None

    external = 'Research external shipment delays'
    assert prepare_research(external, state['company'], datetime(2026, 10, 14, tzinfo=timezone.utc))
    research = run_turn(state, external, None, model='test-model', provider_call='none')
    assert research['candidate']['kind'] == 'research'

    grants = 'Find grants for reducing emissions'
    provider = Provider(intent='inspect', action='none')
    run_turn(state, grants, provider, model='test-model', provider_call='mocked')
    assert provider.calls and provider.calls[0][0] == 'coordinator'


@pytest.mark.parametrize(('message', 'expected'), [
    ('What are the emissions for this purchase?', 'read_carbon'),
    ('Show me the shipments for this company', 'read_shipments'),
])
def test_current_workspace_reads_stay_deterministic(message, expected):
    assert integration_request(snapshot(), message) == expected


@pytest.mark.parametrize('scope', ['the current', 'my', 'the reviewed'])
def test_planned_shipment_read_with_reviewed_recovery_routes_for_every_company(scope):
    for company in list_companies():
        state = snapshot()
        state['company_id'] = company['company_id']
        state['company'] = company
        state['recovery'] = {'scenario': {'plan_id': 'reviewed-scenario'}}
        message = f'Summarize {scope} planned shipment and the reviewed recovery. Do not change anything.'
        assert integration_request(state, message) == 'read_shipments', company['company_id']


def test_planned_shipment_grammar_keeps_external_action_and_cross_company_boundaries():
    state = snapshot()
    question = 'Summarize the current planned shipment and the reviewed recovery.'
    assert integration_request(state, question + ' Include current news.') is None
    assert integration_request(state, question + ' Search the web.') is None
    assert integration_request(state, question + ' Change the plan.') is None
    assert integration_request(state, question + ' Confirm the order.') is None
    other = next(company for company in list_companies() if company['company_id'] != state['company_id'])
    assert integration_request(state, question + f" Include {other['name']} shipments.") is None


def test_reviewed_shipment_brief_names_original_and_scenario_supplier_arrivals():
    company, offers, recovery = reviewed_copper({'eu-copper': 20})
    state = {'company_id': company['company_id'], 'company': company,
             'shipments': build_dispatch_view(company, offers, recovery['scenario'], recovery)}
    answer = integration_read(state, 'read_shipments')['assistant_text']
    assert 'original baseline' in answer.lower()
    assert 'Europa Copper SA' in answer and '2026-10-24' in answer
    assert 'reviewed scenario' in answer.lower()
    assert 'Swift Copper Trading Ltd' in answer and '2026-10-28' in answer
    assert answer.index('Europa Copper SA') < answer.index('Swift Copper Trading Ltd')
    assert '[SYNTHETIC]' not in answer

    company, offers, recovery = reviewed_copper({'eu-copper': 20, 'express-copper': 10})
    state['shipments'] = build_dispatch_view(company, offers, recovery['scenario'], recovery)
    answer = integration_read(state, 'read_shipments')['assistant_text']
    assert 'original baseline' in answer.lower() and '2026-10-24' in answer
    assert 'no feasible supplier' in answer.lower()
    assert 'not a current selected purchase' in answer.lower()
    assert '2026-10-28' not in answer


def test_native_operator_uses_read_only_shipment_tool_without_provider_after_recovery():
    company, offers, recovery = reviewed_copper({'eu-copper': 20})
    state = snapshot()
    state.update(company_id=company['company_id'], company=company, recovery=recovery,
                 shipments=build_dispatch_view(company, offers, recovery['scenario'], recovery))
    provider = Provider()
    result = run_turn(state, 'Summarize the current planned shipment and the reviewed recovery. '
                     'Do not change anything.', provider, model='test-model', provider_call='mocked')
    assert result['status'] == 'completed'
    assert result['agent_steps'][0]['tool'] == 'read_shipments'
    assert result['candidate'] is None and result['proposal_kind'] is None
    assert provider.calls == []
    assert 'Europa Copper SA' in result['assistant_text']
    assert 'Swift Copper Trading Ltd' in result['assistant_text']
