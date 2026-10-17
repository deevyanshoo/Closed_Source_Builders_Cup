"""Current company risk questions use owned alerts, not stock-only model prose."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.company import get_company, list_companies
from procure.demo.risk_read import is_current_risk_question, summarize_risks
from test_demo_taher_native import NamedProvider, voice_tool


NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
QUESTION = 'Analyze our inventory and sourcing risks across all materials.'


class NoOperatorModel:
    available = False

    def __init__(self):
        self.calls = 0

    def __call__(self, role, prompt):
        self.calls += 1
        raise AssertionError('Owned risk reads must not call the operator model')


class NoNewsRefresh:
    def __init__(self):
        self.calls = 0

    def get(self, company, *args, **kwargs):
        self.calls += 1
        raise AssertionError('Inventory risk reads must not refresh public news')


class RecordedRiskNews:
    def __init__(self):
        self.calls = []

    def get(self, company, force=False):
        self.calls.append(company['company_id'])
        material = company['materials'][0]
        return {'checked_at': NOW.isoformat(), 'cache_state': 'fresh',
            'sources': [{'source_id': 'risk-public-feed', 'name': 'Public risk feed',
                         'state': 'fresh', 'last_checked_at': NOW.isoformat(),
                         'reason': 'Controlled current test source'}],
            'items': [{'item_id': company['company_id'] + '-public-shortage',
                'title': material['name'] + ' supply shortage',
                'url': 'https://example.org/owned-material-shortage',
                'provider': 'Public risk feed', 'publisher': 'Test publisher',
                'source_id': 'risk-public-feed', 'source_reliability': 'unverified',
                'retrieved_at': NOW.isoformat(), 'published_at': NOW.isoformat(),
                'matched_materials': [material['material_id']], 'matched_routes': [],
                'matched_currencies': [], 'synthetic': False,
                'matched_operations': ['supply'], 'relevance_kind': 'direct_material'}]}


def risk_app(news_service=None):
    model = NoOperatorModel()
    voice = NamedProvider()
    news = news_service if news_service is not None else NoNewsRefresh()

    @asynccontextmanager
    async def factory():
        yield voice

    return create_app(clock=lambda: NOW, operator_provider=model,
                      live_client_factory=factory, news_service=news), model, voice, news


def select(client, session, company_id):
    changed = client.post('/api/demo/company',
        headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
        json={'company_id': company_id, 'expected_context_version': session['context_version'],
              'idempotency_key': 'select-' + company_id})
    assert changed.status_code == 200, changed.text
    return client.get('/api/demo/session').json()


def ask(client, session, message, key):
    return client.post('/api/demo/operator',
        headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
        json={'message': message, 'expected_context_version': session['context_version'],
              'idempotency_key': key})


def assert_owned_risk_read(result, company_id):
    assert result['status'] == 'completed' and result['read_only'] is True
    assert result['proposal'] is None and not result.get('action_result')
    assert 'session' not in result
    companies = result['workspace']['risks']['companies']
    assert len(companies) == 1 and companies[0]['company_id'] == company_id
    alert = companies[0]['alerts'][0]
    assert alert['category'] in {'inventory', 'supplier_delay'}
    assert alert['material_ids']
    answer = result['assistant_text']
    assert companies[0]['company_name'] in answer
    material = next(row for row in get_company(company_id)['materials']
                    if row['material_id'] == alert['material_ids'][0])
    if alert['category'] == 'inventory':
        assert alert['title'] == f"{material['name']} stock cover meets or falls below supplier lead"
        assert alert['provenance'] == 'synthetic_profile'
        assert alert['evidence'][0]['kind'] == 'profile'
        assert alert['title'] in answer
        assert ('Potential stockout before a new delivery under the stated use and lead assumptions; '
                'verify the current inventory and supplier schedule.') in answer
    else:
        assert alert['title'] == f"{material['name']} has a potentially late fictional supplier option"
        assert alert['provenance'] == 'synthetic_delay_scenario'
        assert alert['evidence'][0]['kind'] == 'order'
        assert f"{material['name']} has a potentially late supplier option" in answer
        assert ('Potential lateness applies only if this supplier option is used; '
                'faster listed options meet the date and are available for review. '
                'No supplier selection or actual shipment delay is confirmed.') in answer
    assert any(action['label'].lower() in answer.lower()
               for action in alert['recovery_actions'])
    assert '28.36734693877551020408163265' not in answer
    assert '77.14285714285714285714285714' not in answer
    assert result['evidence_ids']


def test_atlas_exact_question_is_grounded_for_typed_and_native_operator():
    app, model, voice, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy((value.last_plan, value.recovery, value.rfq,
                           value.pending_proposal, value.drafts, value.simulated_outbox,
                           value.risk_report))
        typed = ask(client, session, QUESTION, 'atlas-risk-typed')
        assert typed.status_code == 200, typed.text
        native, spoken = voice_tool(client, voice, 'operate_workspace',
                                    {'message': QUESTION}, 'atlas-risk-native')
        for result in (typed.json(), native):
            assert_owned_risk_read(result, 'atlas-petrochem')
            assert 'late' in result['assistant_text'].lower()
            assert 'review' in result['assistant_text'].lower()
        assert spoken['assistant_text'] == native['assistant_text']
        assert model.calls == 0
        assert news.calls == 0
        assert '11 days late' in typed.json()['assistant_text']
        assert before == (value.last_plan, value.recovery, value.rfq,
                          value.pending_proposal, value.drafts, value.simulated_outbox,
                          value.risk_report)


def test_native_read_risks_uses_same_grounded_brief_and_owned_projection():
    app, model, voice, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        result, spoken = voice_tool(client, voice, 'read_risks',
                                    {'message': QUESTION}, 'atlas-risk-reader')
        assert_owned_risk_read(result, 'atlas-petrochem')
        assert spoken['assistant_text'] == result['assistant_text']
        assert model.calls == 0
        assert news.calls == 0


def test_native_read_risks_rejects_named_foreign_company():
    app, model, voice, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        result, _ = voice_tool(client, voice, 'read_risks',
                               {'message': 'Analyze our LoomWorks inventory and sourcing risks.'},
                               'foreign-native-risk')
        assert result['status'] == 'needs_clarification'
        assert result['proposal'] is None and result['read_only'] is True
        assert 'workspace' not in result
        assert model.calls == 0 and news.calls == 0


@pytest.mark.parametrize('company_id', [row['company_id'] for row in list_companies()])
def test_current_risk_read_stays_with_each_of_ten_company_profiles(company_id):
    app, model, _, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        if session['company_id'] != company_id:
            session = select(client, session, company_id)
        result = ask(client, session, 'Summarize our current inventory and supplier alerts.',
                     'risk-' + company_id)
        assert result.status_code == 200, result.text
        assert_owned_risk_read(result.json(), company_id)
        assert model.calls == 0
        assert news.calls == 0
        if company_id == 'solara-cables':
            assert 'about 6 days' in result.json()['assistant_text']
            assert '10-day supplier lead' in result.json()['assistant_text']


@pytest.mark.parametrize('message', [
    'Analyze our inventory and sourcing risks across all materials and latest news.',
    'Analyze our inventory and sourcing risks across all materials and latest Suez news.',
    'Analyze our LoomWorks inventory and sourcing risks.',
    'Apply a 7-day supplier delay to our inventory risk plan.',
    'Prepare an RFQ for our supplier risk.',
    'Email our supplier about the inventory alert.',
    'Watch our sourcing risks.',
    'Cancel the current proposal and show our inventory risks.',
    'Remove our inventory risk alert.',
    'Delete our supplier risk watch.',
    'Confirm our inventory risk action.',
    'What if our supplier is delayed and inventory is at risk?',
    'Analyze our inventory and sourcing across all materials.',
])
def test_risk_intent_does_not_claim_foreign_action_public_or_plain_inventory(message):
    assert is_current_risk_question(message, 'atlas-petrochem') is False


def test_foreign_company_risk_question_keeps_existing_context_rejection():
    app, model, _, _ = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        response = ask(client, session, 'Analyze our LoomWorks inventory and sourcing risks.',
                       'foreign-risk')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'needs_clarification'
        assert 'workspace' not in result or 'risks' not in result['workspace']
        assert model.calls == 0


@pytest.mark.parametrize('message', [
    'Apply a 7-day supplier delay to our inventory risk plan.',
    'Prepare an RFQ for our supplier risk.',
    'Analyze our inventory and sourcing risks across all materials and latest news.',
])
def test_action_and_mixed_news_requests_do_not_become_local_risk_scans(message):
    app, _, _, _ = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        response = ask(client, session, message, 'non-risk-' + str(len(message)))
        assert response.status_code == 200, response.text
        result = response.json()
        assert 'risks' not in result.get('workspace', {})
        assert all(step.get('tool') != 'read_risks' for step in result['agent_steps'])


@pytest.mark.parametrize('message', [
    'Search online for our current inventory and supplier risks',
    'Browse the web for our current inventory and supplier risks',
    'Look up our current inventory and supplier risks',
    'Research our current inventory and supplier risks',
    'Check the internet for our current inventory and supplier risks',
])
def test_explicit_external_research_never_becomes_local_risk_read(message):
    assert is_current_risk_question(message, 'atlas-petrochem') is False
    app, model, voice, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        typed = ask(client, session, message, 'external-risk-' + str(len(message)))
        assert typed.status_code == 200, typed.text
        native, _ = voice_tool(client, voice, 'operate_workspace',
                               {'message': message}, 'native-external-' + str(len(message)))
        for result in (typed.json(), native):
            assert result['status'] in {'needs_clarification', 'proposal'}
            assert 'risks' not in result.get('workspace', {})
            assert result.get('action_result') is None
            if result['proposal'] is not None:
                assert result['proposal']['kind'] == 'research'
        assert model.calls == 0 and news.calls == 0


@pytest.mark.parametrize('message', [
    'Cancel the current proposal and show our inventory risks',
    'Remove the current proposal and show our inventory risks',
    'Delete the current proposal and show our inventory risks',
    'Confirm the current proposal and show our inventory risks',
])
def test_combined_action_and_risk_read_preserves_pending_proposal(message):
    assert is_current_risk_question(message, 'atlas-petrochem') is False
    app, _, _, _ = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        value = app.state.sessions.get(session['session_id'])
        value.pending_proposal = {'proposal_id': 'existing-review', 'kind': 'watch'}
        before = deepcopy(value.pending_proposal)
        response = ask(client, session, message, 'combined-cancel-risk')
        assert response.status_code == 200, response.text
        result = response.json()
        assert 'risks' not in result.get('workspace', {})
        assert result.get('action_result') is None
        assert value.pending_proposal == before


@pytest.mark.parametrize('args', [
    {'scope': 'current', 'message': 'Summarize our current supplier risk headlines'},
    {'message': 'Summarize our current supplier risk headlines'},
])
def test_native_public_risk_read_scans_current_company_news(args):
    news = RecordedRiskNews()
    app, model, voice, _ = risk_app(news)
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        result, _ = voice_tool(client, voice, 'read_risks', args,
                               'public-risk-' + str(len(args)))
        assert result['status'] == 'completed' and result['read_only'] is True
        assert result['proposal'] is None and result.get('action_result') is None
        assert news.calls == ['atlas-petrochem']
        companies = result['workspace']['risks']['companies']
        assert len(companies) == 1 and companies[0]['company_id'] == 'atlas-petrochem'
        headline = next(alert for alert in companies[0]['alerts'] if alert['category'] == 'news')
        assert headline['evidence'][0]['ref_id'] == 'atlas-petrochem-public-shortage'
        assert headline['impact']
        assert headline['title'] in result['assistant_text']
        assert headline['impact'] in result['assistant_text']
        assert 'atlas-petrochem-public-shortage' in result['evidence_ids']
        assert model.calls == 0


def test_native_read_risks_cannot_turn_explicit_research_into_a_scan():
    news = RecordedRiskNews()
    app, model, voice, _ = risk_app(news)
    with TestClient(app, base_url='https://demo.test') as client:
        select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        result, _ = voice_tool(client, voice, 'read_risks',
                               {'message': 'Search online for our current inventory and supplier risks'},
                               'native-research-risk')
        assert result['status'] == 'needs_clarification'
        assert result['proposal'] is None and 'workspace' not in result
        assert news.calls == [] and model.calls == 0


def test_atlas_spoken_brief_uses_human_source_label_without_changing_alert_ledger():
    app, model, _, news = risk_app()
    with TestClient(app, base_url='https://demo.test') as client:
        session = select(client, client.get('/api/demo/session').json(), 'atlas-petrochem')
        response = ask(client, session, QUESTION, 'atlas-natural-copy')
        assert response.status_code == 200, response.text
        result = response.json()
        alert = result['workspace']['risks']['companies'][0]['alerts'][0]
        assert alert['title'] == 'Naphtha feedstock has a potentially late fictional supplier option'
        assert alert['provenance'] == 'synthetic_delay_scenario'
        assert alert['impact'] == ('Potential lateness applies only if this fictional supplier option is used; '
                                   'faster listed options meet the date and are available for review. '
                                   'No supplier selection or actual shipment delay is confirmed.')
        assert alert['metrics']['lateness_days'] == 11
        assert alert['evidence'][0]['kind'] == 'order'
        assert alert['evidence'][0]['ref_id']
        answer = result['assistant_text']
        assert 'Naphtha feedstock has a potentially late supplier option' in answer
        assert 'Potential lateness applies only if this supplier option is used' in answer
        assert 'faster listed options meet the date' in answer
        assert 'No supplier selection or actual shipment delay is confirmed.' in answer
        assert '(medium; planning scenario)' in answer
        assert '11 days late' in answer and 'Review faster alternatives' in answer
        assert 'fictional' not in answer.lower() and 'synthetic_' not in answer
        assert model.calls == 0 and news.calls == 0


@pytest.mark.parametrize(('category', 'title', 'impact', 'provenance', 'expected_title',
                          'expected_impact', 'expected_source'), [
    ('inventory', 'Synthetic resin stock cover meets or falls below supplier lead',
     'Potential stockout before a new delivery under the stated fictional use and lead assumptions; verify the current inventory and supplier schedule.',
     'synthetic_profile', 'Synthetic resin stock cover meets or falls below supplier lead',
     'Potential stockout before a new delivery under the stated use and lead assumptions; verify the current inventory and supplier schedule.',
     'company profile'),
    ('supplier_delay', 'Synthetic resin has a potentially late fictional supplier option',
     'Potential lateness applies only if this fictional supplier option is used; faster listed options meet the date and are available for review. No supplier selection or actual shipment delay is confirmed.',
     'synthetic_delay_scenario', 'Synthetic resin has a potentially late supplier option',
     'Potential lateness applies only if this supplier option is used; faster listed options meet the date and are available for review. No supplier selection or actual shipment delay is confirmed.',
     'planning scenario'),
    ('supplier_delay', 'Synthetic resin order has synthetic lead-time exposure',
     'Potential late arrival under fictional order and supplier assumptions; no actual shipment delay has been observed.',
     'synthetic_delay_scenario', 'Synthetic resin order has lead-time exposure',
     'Potential late arrival under order and supplier assumptions; no actual shipment delay has been observed.',
     'planning scenario'),
    ('supplier_delay', 'Synthetic resin recorded synthetic recovery',
     'The hypothetical delay may change sourcing feasibility or cost; this is a synthetic projection, not an observed shipment event.',
     'synthetic_plan', 'Synthetic resin recorded recovery',
     'The hypothetical delay may change sourcing feasibility or cost; no shipment event is confirmed.',
     'reviewed recovery scenario'),
    ('news', 'Observed headline: Synthetic biology feedstock shortage',
     'Synthetic biology source says supply is tight; conditional exposure only, with no confirmed company delay.',
     'observed_news', 'Observed headline: Synthetic biology feedstock shortage',
     'Synthetic biology source says supply is tight; conditional exposure only, with no confirmed company delay.',
     'public headline'),
    ('supplier_delay', 'Synthetic resin supply watch',
     'A conditional risk based on a stated assumption; no shipment event is confirmed.',
     'future_internal_kind', 'Synthetic resin supply watch',
     'A conditional risk based on a stated assumption; no shipment event is confirmed.',
     'workspace evidence'),
])
def test_spoken_copy_normalizes_only_known_operational_fixture_phrases(
        category, title, impact, provenance, expected_title, expected_impact, expected_source):
    alert = {'category': category, 'severity': 'medium', 'title': title,
             'impact': impact, 'provenance': provenance,
             'metrics': {'lateness_days': 11},
             'recovery_actions': [{'label': 'Review alternatives'}],
             'evidence': [{'kind': 'profile', 'ref_id': 'source-1'}]}
    projection = {'companies': [{'company_id': 'example', 'company_name': 'Example Company',
                 'counts': {'high': 0, 'medium': 1, 'low': 0}, 'alerts': [alert]}]}
    original = deepcopy(projection)
    answer = summarize_risks(projection, include_news=category == 'news')
    assert expected_title in answer
    assert expected_impact in answer
    assert f'(medium; {expected_source})' in answer
    assert 'Synthetic resin' in answer if category != 'news' else 'Synthetic biology' in answer
    assert 'Review alternatives' in answer
    assert projection == original
    assert alert['title'] == title and alert['impact'] == impact
    assert alert['provenance'] == provenance
