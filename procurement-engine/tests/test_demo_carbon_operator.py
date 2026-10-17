"""The operator calculates against the current owned plan without a provider."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from procure.demo.app import create_app
from procure.demo.carbon_operator import estimate_turn
from procure.demo.company import get_company
from procure.demo.sessions import SessionStore
from procure.demo.session_persistence import MemorySessionPersistence
from test_demo_live_voice_rfq import plan_for_copper


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def turn(client, session, message, key):
    return client.post('/api/demo/operator', json={
        'message': message, 'expected_context_version': session['context_version'],
        'idempotency_key': key}, headers={'Origin': 'https://demo.test',
        'X-CSRF-Token': session['csrf_token']})


def test_material_only_estimate_is_saved_and_reports_missing_freight():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                       'X-CSRF-Token': session['csrf_token']})
        response = turn(client, session, 'Calculate carbon for our current copper purchase', 'carbon-first')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'completed'
        assert result['action_result']['tool'] == 'estimate_carbon'
        assert result['session']['carbon_assessment']['result']['plan_id'] == plan['plan_id']
        selected = next(row for row in result['session']['carbon_assessment']['result']['suppliers']
                        if row['offer_id'] == plan['selected_offer_id'])
        assert selected['estimate']['material']['status'] == 'estimated'
        assert selected['estimate']['transport']['status'] == 'not_entered'
        assert 'mode' in result['assistant_text'].lower() and 'distance' in result['assistant_text'].lower()
        http = client.post('/api/demo/carbon/estimate', json={'plan_id': plan['plan_id'],
            'legs_by_offer': {}, 'expected_context_version': session['context_version']},
            headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        assert http.status_code == 200
        assert http.json() == result['workspace']['carbon']


def test_carbon_followup_rejects_stale_company_context():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Estimate emissions for this purchase', 'carbon-pending')
        value = app.state.sessions.get(session['session_id'])
        assert value.pending_carbon is not None
        value.select_company('terrapack')
        assert value.pending_carbon is None


def test_freight_reply_completes_each_owned_offer_without_guessing_distance():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test',
                                       'X-CSRF-Token': session['csrf_token']})
        first = turn(client, session, 'Compare carbon across all suppliers for our current purchase', 'carbon-start').json()
        assert first['status'] == 'completed'
        for index, row in enumerate(plan['alternatives']):
            reply = turn(client, session, f"{row['offer_id']}: road 125 km", f'carbon-leg-{index}')
            assert reply.status_code == 200, reply.text
            result = reply.json()
            assert result['status'] == 'completed'
        assessment = result['session']['carbon_assessment']['result']
        assert {row['offer_id'] for row in assessment['suppliers']} == set(plan['offer_versions'])
        assert all(row['estimate']['transport']['legs'][0]['distance_km'] == '125'
                   for row in assessment['suppliers'])
        assert app.state.sessions.get(session['session_id']).pending_carbon is None


def test_pending_followup_rejects_replaced_plan():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Estimate carbon for our current purchase', 'carbon-start')
        value = app.state.sessions.get(session['session_id'])
        value.last_plan['plan_id'] = 'replaced-plan'
        response = turn(client, session, 'road 125 km', 'stale-leg')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'stale_plan'
        assert value.pending_carbon is None


def test_nonmass_quantity_requires_entered_shipping_mass_and_basis():
    value = SessionStore(clock=lambda: NOW).create()
    value.select_company('loomworks')
    plan = {'plan_id': 'cotton-plan', 'company_id': 'loomworks', 'material': 'cotton',
            'quantity_unit': 'bale', 'selected_offer_id': 'cotton-quote',
            'offer_versions': {'cotton-quote': 2},
            'alternatives': [{'offer_id': 'cotton-quote', 'supplier': 'Fictional supplier',
                              'quantity': '10', 'feasible': True}]}
    value.last_plan = plan
    kwargs = {'active_plan': lambda _: value.last_plan,
              'validate_plan_current': lambda *_: None,
              'compare_quotes': lambda *_: None, 'company': get_company('loomworks'), 'now': NOW}
    first = estimate_turn(value, 'Calculate carbon for cotton', **kwargs)
    assert first['comparison']['suppliers'][0]['estimate']['material']['status'] == 'factor_unavailable'
    assert 'shipping mass' in first['assistant_text'].lower()
    missing = estimate_turn(value, 'road 100 km', **kwargs)
    assert missing['status'] == 'needs_clarification'
    assert value.carbon_assessment['result']['suppliers'][0]['estimate']['transport']['status'] == 'not_entered'
    completed = estimate_turn(value, 'road 100 km shipping mass 500 kg basis supplier packing list', **kwargs)
    estimate = completed['comparison']['suppliers'][0]['estimate']
    assert estimate['transport']['status'] == 'estimated'
    assert estimate['mass_basis']['basis'] == 'supplier packing list'
    assert estimate['material']['status'] == 'factor_unavailable'


def test_missing_plan_is_built_from_existing_reviewed_owned_quotes():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        value.last_plan = None
        value.plan_request = None
        result = turn(client, session, 'Calculate carbon for copper', 'rebuild-carbon').json()
        assert result['status'] == 'completed'
        assert value.last_plan is not None
        assert result['session']['carbon_assessment']['plan_id'] == value.last_plan['plan_id']


def test_read_carbon_stays_read_only_while_freight_is_pending():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate carbon for our purchase', 'carbon-start')
        value = app.state.sessions.get(session['session_id'])
        before = value.carbon_assessment
        read = turn(client, session, 'What are the sources of our current emissions assessment?', 'carbon-read').json()
        assert read['read_only'] is True
        assert 'action_result' not in read
        assert value.carbon_assessment == before
        assert value.pending_carbon is not None


def test_foreign_offer_cannot_receive_freight_input():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test',
                                 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate carbon for our purchase', 'carbon-start')
        value = app.state.sessions.get(session['session_id'])
        before = value.carbon_assessment
        response = turn(client, session, 'foreign-offer: road 100 km', 'foreign-leg')
        assert response.status_code == 422
        assert value.carbon_assessment == before
        assert value.pending_carbon['legs_by_offer'] == {}


def test_tonne_plan_derives_shipping_mass_without_override():
    value = SessionStore(clock=lambda: NOW).create()
    value.select_company('stonebridge-materials')
    plan = {'plan_id': 'tonne-plan', 'company_id': 'stonebridge-materials', 'material': 'aggregate',
            'quantity_unit': 'tonne', 'selected_offer_id': 'tonne-quote',
            'offer_versions': {'tonne-quote': 1},
            'alternatives': [{'offer_id': 'tonne-quote', 'supplier': 'Fictional supplier',
                              'quantity': '2', 'feasible': True}]}
    value.last_plan = plan
    kwargs = {'active_plan': lambda _: value.last_plan,
              'validate_plan_current': lambda *_: None,
              'compare_quotes': lambda *_: None, 'company': get_company('stonebridge-materials'), 'now': NOW}
    estimate_turn(value, 'Calculate carbon for aggregate', **kwargs)
    reply = estimate_turn(value, 'road 100 km', **kwargs)
    estimate = reply['comparison']['suppliers'][0]['estimate']
    assert estimate['material']['mass_tonnes'] == '2'
    assert estimate['transport']['legs'][0]['tonne_km'] == '200'
    assert estimate['mass_basis'] is None


def test_plain_footprint_question_calculates_without_ui_handoff():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        result = turn(client, session, 'What is our carbon footprint for this purchase?', 'plain-footprint').json()
        assert result['action_result']['tool'] == 'estimate_carbon'
        assert result['session']['carbon_assessment'] is not None
        assert 'Open Procurement' not in result['assistant_text']
        assert 'offer_id' not in result['assistant_text']


def test_reduction_question_starts_owned_comparison_while_method_question_reads():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        action = turn(client, session, 'How can we reduce carbon for this purchase?', 'reduce-carbon').json()
        assert action['action_result']['tool'] == 'estimate_carbon'
        value = app.state.sessions.get(session['session_id'])
        assert set(value.pending_carbon['targets']) == set(plan['offer_versions'])
        method = turn(client, session, 'How is our current carbon assessment calculated?', 'carbon-method').json()
        assert method['read_only'] is True
        assert 'action_result' not in method


def test_normal_request_prompts_selected_supplier_only_and_reuses_valid_saved_freight():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        first = turn(client, session, 'Assess carbon for this purchase', 'assess').json()
        selected = next(row for row in plan['alternatives'] if row['offer_id'] == plan['selected_offer_id'])
        assert selected['supplier'].split(' [')[0] in first['assistant_text']
        assert plan['selected_offer_id'] not in first['assistant_text']
        reply = turn(client, session, 'road 100 km', 'freight-one').json()
        assert reply['session']['carbon_assessment']['inputs']['legs_by_offer'] == {
            plan['selected_offer_id']: [{'mode': 'road_hgv', 'distance_km': '100'}]}
        repeated = turn(client, session, 'Estimate emissions for our purchase', 'repeat-assess').json()
        assert repeated['session']['carbon_assessment']['inputs']['legs_by_offer'] == reply['session']['carbon_assessment']['inputs']['legs_by_offer']
        assert 'saved' in repeated['assistant_text'].lower()


def test_explicit_zero_distance_reports_zero_without_guessing():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate carbon for this purchase', 'start')
        result = turn(client, session, 'road 0 km', 'zero-road').json()
        assert 'freight 0 kg CO2e' in result['assistant_text']
        assert result['session']['carbon_assessment']['inputs']['legs_by_offer']


@pytest.mark.parametrize(('reply', 'expected'), [
    ('Use road transport for 125 kilometers.', [('road_hgv', '125')]),
    ('road freight for 125 km', [('road_hgv', '125')]),
    ('125 km by road', [('road_hgv', '125')]),
    ('road transport for 0 kilometers', [('road_hgv', '0')]),
    ('125.50 km by road', [('road_hgv', '125.50')]),
    ('sea 8000 km and 125 km by road', [('sea_container', '8000'), ('road_hgv', '125')]),
    ('road freight for 125 km and rail 40 km', [('road_hgv', '125'), ('rail_freight', '40')]),
])
def test_freight_natural_reply_api_preserves_explicit_legs(reply, expected):
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        first = turn(client, session,
                     'Calculate the carbon footprint for our current purchase using our company data.',
                     'carbon-natural-start')
        assert first.status_code == 200, first.text
        assert '31720' in first.json()['assistant_text'].replace(',', '')
        assert app.state.sessions.get(session['session_id']).pending_carbon is not None
        response = turn(client, session, reply, 'carbon-natural-leg')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'completed'
        legs = result['session']['carbon_assessment']['inputs']['legs_by_offer'][plan['selected_offer_id']]
        assert [(leg['mode'], leg['distance_km']) for leg in legs] == expected
        assert app.state.sessions.get(session['session_id']).pending_carbon is None


@pytest.mark.parametrize('reply', [
    'do not use road transport for 125 km',
    "don't use road for 125 km",
    'not road 125 km',
    'without road 125 km',
    'road -125 km',
    'minus 125 km by road',
    'negative 125 km by road',
    'road minus 125 km',
    'road transport for 125 km and rail',
    'road transport for 125 km and 40 km',
    '125 km',
    'road route is 125 km',
    'road transport for 125 km and rail 40 km and sea 30 km and air 20 km and truck 10 km and rail 5 km',
])
def test_invalid_freight_reply_preserves_assessment_and_server_pending(reply):
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        first = turn(client, session, 'Calculate carbon for our current purchase', 'carbon-boundary-start')
        assert first.status_code == 200, first.text
        value = app.state.sessions.get(session['session_id'])
        assessment = deepcopy(value.carbon_assessment)
        pending = deepcopy(value.pending_carbon)
        response = turn(client, session, reply, 'carbon-boundary-reply')
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'needs_clarification'
        assert value.carbon_assessment == assessment
        assert value.pending_carbon == pending


def test_multileg_reply_is_preserved_and_foreign_supplier_is_rejected():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate emissions for this purchase', 'start')
        foreign = turn(client, session, 'TerraPack Films: sea 8000 km and road 200 km', 'foreign-name')
        assert foreign.status_code == 422
        result = turn(client, session, 'sea 8000 km and road 200 km', 'two-legs').json()
        selected = next(row for row in result['workspace']['carbon']['suppliers']
                        if row['offer_id'] == result['session']['plan']['selected_offer_id'])
        assert [leg['distance_km'] for leg in selected['estimate']['transport']['legs']] == ['8000', '200']


def test_negated_estimate_and_cancel_leave_state_unchanged():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        turn(client, session, 'Do not estimate carbon yet', 'negative-estimate')
        assert value.carbon_assessment is None and value.pending_carbon is None
        turn(client, session, 'Calculate carbon now', 'positive-estimate')
        pending = deepcopy(value.pending_carbon)
        turn(client, session, 'Do not cancel', 'negative-cancel')
        assert value.pending_carbon == pending
        assessment = deepcopy(value.carbon_assessment)
        cancelled = turn(client, session, 'Cancel', 'cancel-carbon').json()
        assert cancelled['action_result']['tool'] == 'estimate_carbon'
        assert cancelled['session']['carbon_assessment'] == assessment
        assert value.pending_carbon is None
        assert value.carbon_assessment == assessment


def test_missing_plan_failure_does_not_discard_business_state(monkeypatch):
    from procure.demo import carbon_operator
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        value.last_plan = None
        value.plan_request = None
        value.rfq = {'accepted': 'retained'}
        before = deepcopy((value.last_plan, value.plan_request, value.rfq, value.buyer_draft))
        monkeypatch.setattr(carbon_operator, 'load_registry', lambda: (_ for _ in ()).throw(ValueError('factor unavailable')))
        response = turn(client, session, 'Calculate carbon for copper', 'factor-failure')
        assert response.status_code >= 400
        assert (value.last_plan, value.plan_request, value.rfq, value.buyer_draft) == before


def test_missing_plan_success_preserves_existing_rfq_record():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        value.last_plan = None
        value.plan_request = None
        value.rfq = {'accepted': 'retained'}
        result = turn(client, session, 'Assess carbon for copper', 'preserve-rfq').json()
        assert result['status'] == 'completed'
        assert value.rfq == {'accepted': 'retained'}


def test_invalid_http_estimate_keeps_pending_conversation():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan = plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate carbon for this purchase', 'start')
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy(value.pending_carbon)
        response = client.post('/api/demo/carbon/estimate', json={'plan_id': plan['plan_id'],
            'legs_by_offer': {'foreign-offer': []}, 'expected_context_version': session['context_version']},
            headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        assert response.status_code == 422
        assert value.pending_carbon == before


def test_current_routes_read_stays_on_company_profile_and_never_infers_freight():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        result = turn(client, session, 'What are our current routes and chokepoints?', 'route-read').json()
        assert result['read_only'] is True
        assert result['workspace']['routes']
        assert all(row['route_id'].startswith('solara-cables-') for row in result['workspace']['routes'])
        assert 'distance' in result['assistant_text']


def test_text_navigation_opens_supported_operations_views_without_provider():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        for view in ('overview', 'inventory', 'routes', 'shipments'):
            result = turn(client, session, f'Open {view}', f'open-{view}').json()
            assert result['action_result']['view'] == view
            assert result['session']['company_id'] == 'solara-cables'


def test_missing_plan_uses_single_reviewed_material_without_repeating_verb():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        value = app.state.sessions.get(session['session_id'])
        value.last_plan = None
        value.plan_request = None
        response = turn(client, session, 'What is our carbon footprint?', 'auto-material').json()
        assert response['action_result']['tool'] == 'estimate_carbon'
        assert response['session']['carbon_assessment']['material_id'] == 'copper'


def test_missing_material_selection_continues_on_reply():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        first = turn(client, session, 'Assess our carbon footprint', 'pick-material').json()
        assert first['status'] == 'needs_clarification'
        value = app.state.sessions.get(session['session_id'])
        assert value.pending_carbon['kind'] == 'material_selection'
        second = turn(client, session, 'Copper cathode', 'material-reply').json()
        assert second['status'] == 'needs_clarification'
        assert 'No reviewed quote' in second['assistant_text']
        assert value.last_plan is None


def test_failed_freight_validation_does_not_mutate_pending_inputs(monkeypatch):
    from procure.demo import carbon_operator
    from procure.demo.sessions import SessionError
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate carbon for this purchase', 'start')
        value = app.state.sessions.get(session['session_id'])
        before = deepcopy(value.pending_carbon)
        monkeypatch.setattr(carbon_operator, 'estimate_current', lambda *_args, **_kwargs:
                            (_ for _ in ()).throw(SessionError('invalid_carbon_input', 'invalid', 422)))
        response = turn(client, session, 'road 100 km', 'failed-freight')
        assert response.status_code == 422
        assert value.pending_carbon == before


def test_ambiguous_or_negated_freight_does_not_save_a_leg():
    app = create_app(clock=lambda: NOW)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        turn(client, session, 'Calculate carbon for this purchase', 'start')
        value = app.state.sessions.get(session['session_id'])
        for index, message in enumerate(('sea 8000 km and road', 'not road 100 km', 'road -100 km')):
            response = turn(client, session, message, f'ambiguous-{index}').json()
            assert response['status'] == 'needs_clarification'
            assert value.pending_carbon['legs_by_offer'] == {}


def test_pending_freight_survives_cold_session_restore():
    backend = MemorySessionPersistence()
    app = create_app(clock=lambda: NOW, session_backend=backend)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        plan_for_copper(client, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']})
        result = turn(client, session, 'Calculate carbon for this purchase', 'persist-carbon').json()
        assert result['status'] == 'completed'
        restored = SessionStore(clock=lambda: NOW, persistence_backend=backend).get(session['session_id'])
        assert restored.pending_carbon['kind'] == 'freight'
        assert restored.pending_carbon['plan_id'] == restored.last_plan['plan_id']


def test_pending_freight_expiry_clamps_to_near_session_end_and_persists():
    backend = MemorySessionPersistence()
    app = create_app(clock=lambda: NOW, session_backend=backend)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        plan_for_copper(client, headers)
        value = app.state.sessions.get(session['session_id'])
        value.expires_at = NOW + timedelta(minutes=2)
        response = turn(client, session, 'Calculate carbon for this purchase', 'near-expiry-freight')
        assert response.status_code == 200, response.text
        assert datetime.fromisoformat(value.pending_carbon['expires_at']) == value.expires_at
        restored = SessionStore(clock=lambda: NOW, persistence_backend=backend).get(session['session_id'])
        assert restored.pending_carbon == value.pending_carbon


def test_pending_material_selection_expiry_clamps_to_near_session_end_and_persists():
    backend = MemorySessionPersistence()
    store = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    value = store.create()
    value.expires_at = NOW + timedelta(minutes=3)
    result = estimate_turn(value, 'Estimate carbon for this purchase',
        active_plan=lambda _: None, validate_plan_current=lambda *_: None,
        compare_quotes=lambda *_: None, company=get_company(value.company_id), now=NOW)
    assert result['status'] == 'needs_clarification'
    assert datetime.fromisoformat(value.pending_carbon['expires_at']) == value.expires_at
    store.save(value)
    restored = SessionStore(clock=lambda: NOW, persistence_backend=backend).get(value.session_id)
    assert restored.pending_carbon == value.pending_carbon
