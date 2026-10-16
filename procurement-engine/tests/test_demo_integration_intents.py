"""Only current workspace facts enter deterministic carbon and dispatch reads."""
from datetime import datetime, timezone

import pytest

from procure.demo.integration_tools import integration_request
from procure.demo.news_brief import is_workspace_news_question
from procure.demo.operator import run_turn
from procure.demo.research import prepare_research
from test_demo_operator import Provider, snapshot


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
