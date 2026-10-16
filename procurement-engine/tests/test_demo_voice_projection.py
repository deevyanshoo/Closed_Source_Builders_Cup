"""The live model sees current purchase facts without unrelated scenario rows."""
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.live_voice import _selected_purchase_model_result
from test_demo_live_voice_rfq import plan_for_copper
from test_demo_dispatch_view import reviewed_copper, selected_copper
from test_demo_taher_native import NamedProvider, voice_tool


def test_many_expected_orders_disclose_the_partial_model_list():
    rows = [{'shipment_id': f'expected-{index}', 'company_id': 'atlas',
             'order_id': f'order-{index}', 'material_id': 'copper', 'material_name': 'Copper',
             'quantity': '100', 'quantity_unit': 'kg', 'need_by': '2026-12-01',
             'expected_arrival': None, 'status': 'expected_order_scenario',
             'source': 'fictional_company_profile_expected_order'} for index in range(23)]
    result = {'status': 'completed', 'assistant_text': 'There are 23 expected-order records.',
              'workspace': {'shipments': {'company_id': 'atlas', 'plan_id': None,
                                          'baseline_plan_id': None, 'synthetic': True,
                                          'source': 'fictional_company_profile_and_plan',
                                          'limitations': [], 'shipments': rows}}}
    model = dict(result)

    _selected_purchase_model_result('read_shipments', result, model, None)

    view = model['workspace']['shipments']
    assert view['shipments'] == []
    assert view['expected_order_count'] == 23
    assert len(view['expected_order_scenarios']) == 8
    assert view['expected_order_omitted'] == 15
    assert 'partial' in view['selected_purchase_scope'].lower()
    assert '15' in view['selected_purchase_scope']
    assert 'disclose' in view['selected_purchase_scope'].lower()
    assert 'required_spoken_response' not in model
    assert 'assistant_text' not in model
    assert model['current_purchase_brief'] == result['assistant_text']
    assert len(json.dumps(model, ensure_ascii=True)) < 64_000


def test_shipment_projection_excludes_another_offer_in_the_same_plan():
    selected_plan = {'plan_id': 'current-plan', 'selected_offer_id': 'chosen-offer'}
    chosen = {'shipment_id': 'chosen-shipment', 'plan_id': 'current-plan',
              'offer_id': 'chosen-offer', 'source': 'synthetic_plan_demand_scenario',
              'expected_arrival': '2026-10-26', 'recovery_available': True}
    unrelated = {**chosen, 'shipment_id': 'other-shipment', 'offer_id': 'other-offer',
                 'expected_arrival': '2026-12-05'}
    expected_order = {'shipment_id': 'profile-demand-1', 'company_id': 'company-1',
                      'order_id': 'order-1', 'material_id': 'copper', 'material_name': 'Copper',
                      'quantity': '100', 'quantity_unit': 'kg', 'need_by': '2026-12-01',
                      'expected_arrival': '2026-12-05', 'status': 'expected_order_scenario',
                      'source': 'fictional_company_profile_expected_order'}
    result = {'status': 'completed', 'assistant_text': 'Planned arrival is 2026-10-26.',
              'workspace': {'shipments': {'company_id': 'company-1', 'plan_id': 'current-plan',
                                          'baseline_plan_id': 'current-plan', 'synthetic': True,
                                          'source': 'fictional_company_profile_and_plan',
                                          'limitations': [], 'shipments': [unrelated, chosen, expected_order]}}}
    model = dict(result)

    _selected_purchase_model_result('read_shipments', result, model, selected_plan)

    assert model['workspace']['shipments']['shipments'] == [chosen]
    assert model['workspace']['shipments']['expected_order_count'] == 1
    assert model['workspace']['shipments']['expected_order_scenarios'][0]['expected_arrival'] == '2026-12-05'
    assert model['workspace']['shipments']['expected_order_scenarios'][0]['source'] == expected_order['source']
    assert model['workspace']['shipments']['expected_order_scenarios'][0]['selected_purchase'] is False
    assert '2026-12-05' not in str(model['workspace']['shipments']['shipments'])


def test_reviewed_recovery_separates_feasible_selection_from_infeasible_baseline():
    baseline = {'shipment_id': 'baseline-shipment', 'source': 'synthetic_plan_demand_scenario',
                'company_id': 'company-1', 'scenario_selected_offer_id': None,
                'plan_id': 'baseline-plan', 'offer_id': 'baseline-offer',
                'baseline_plan_id': 'baseline-plan', 'baseline_offer_id': 'baseline-offer',
                'scenario_plan_id': 'scenario-plan', 'scenario_status': 'infeasible',
                'recovery_available': False, 'expected_arrival': '2026-10-26'}
    other = {**baseline, 'shipment_id': 'unrelated', 'baseline_offer_id': 'other-offer'}
    foreign = {**baseline, 'shipment_id': 'foreign', 'company_id': 'other-company'}
    selected_scenario = {**baseline, 'shipment_id': 'selected-scenario',
                         'scenario_selected_offer_id': 'some-offer'}
    view = {'company_id': 'company-1', 'plan_id': 'scenario-plan',
            'baseline_plan_id': 'baseline-plan', 'synthetic': True,
            'source': 'fictional_company_profile_and_plan', 'limitations': [],
            'shipments': [other, foreign, selected_scenario, baseline]}
    result = {'status': 'completed', 'assistant_text': 'The reviewed disruption has no feasible supplier.',
              'workspace': {'shipments': view}}
    model = dict(result)

    _selected_purchase_model_result('read_shipments', result, model,
                                    {'plan_id': 'scenario-plan', 'selected_offer_id': None})

    projected = model['workspace']['shipments']
    assert projected['shipments'] == []
    assert projected['baseline_reviewed_infeasible']['row'] == baseline
    assert projected['baseline_reviewed_infeasible']['selected_purchase'] is False
    assert projected['baseline_reviewed_infeasible']['scope'] == 'reviewed_infeasible_baseline_context'
    assert 'unrelated' not in str(projected)
    assert result['workspace']['shipments'] == view

    stale_model = dict(result)
    _selected_purchase_model_result('read_shipments', result, stale_model,
                                    {'plan_id': 'older-plan', 'selected_offer_id': None})
    assert stale_model['workspace']['shipments']['baseline_reviewed_infeasible'] is None

    feasible = {**baseline, 'shipment_id': 'feasible-shipment', 'plan_id': 'scenario-plan',
                'offer_id': 'scenario-offer', 'scenario_status': 'selected'}
    feasible_view = {**view, 'shipments': [feasible]}
    feasible_result = {**result, 'workspace': {'shipments': feasible_view}}
    feasible_model = dict(feasible_result)
    _selected_purchase_model_result('read_shipments', feasible_result, feasible_model,
                                    {'plan_id': 'scenario-plan', 'selected_offer_id': 'scenario-offer'})
    assert feasible_model['workspace']['shipments']['shipments'] == [feasible]
    assert feasible_model['workspace']['shipments']['baseline_reviewed_infeasible'] is None


def test_real_dispatch_recovery_rows_preserve_feasible_or_infeasible_context():
    from procure.demo.dispatch_view import build_dispatch_view

    company, offers, recovery = reviewed_copper({'eu-copper': 20, 'express-copper': 10})
    infeasible_plan = recovery['scenario']
    assert infeasible_plan['selected_offer_id'] is None
    view = build_dispatch_view(company, offers, infeasible_plan, recovery)
    result = {'status': 'completed', 'assistant_text': 'No feasible supplier remains.',
              'workspace': {'shipments': view}}
    model = dict(result)
    _selected_purchase_model_result('read_shipments', result, model, infeasible_plan)
    projected = model['workspace']['shipments']
    assert projected['shipments'] == []
    baseline = projected['baseline_reviewed_infeasible']['row']
    assert baseline['plan_id'] == recovery['baseline']['plan_id']
    assert baseline['scenario_plan_id'] == infeasible_plan['plan_id']
    assert baseline['recovery_available'] is False

    company, offer, baseline_plan = selected_copper()
    scenario = deepcopy(baseline_plan)
    scenario['plan_id'] = 'reviewed-feasible-plan'
    feasible_recovery = {'baseline': baseline_plan, 'scenario': scenario, 'outcome': 'unchanged'}
    view = build_dispatch_view(company, [offer], scenario, feasible_recovery)
    result = {'status': 'completed', 'assistant_text': 'The reviewed scenario is feasible.',
              'workspace': {'shipments': view}}
    model = dict(result)
    _selected_purchase_model_result('read_shipments', result, model, scenario)
    projected = model['workspace']['shipments']
    assert len(projected['shipments']) == 1
    assert projected['shipments'][0]['plan_id'] == scenario['plan_id']
    assert projected['baseline_reviewed_infeasible'] is None


def test_carbon_projection_keeps_selected_estimate_and_compact_quote_alternatives():
    selected = {'offer_id': 'selected', 'supplier': 'Selected Supplier', 'estimate': {
        'quantity': '100', 'unit': 'kg', 'material': {'kg_co2e': '20',
            'factor': {'publisher': 'Material Source', 'published_on': '2026-09-01'}},
        'transport': {'kg_co2e': '5', 'legs': [{'factor': {'publisher': 'Freight Source'}}]},
        'total_kg_co2e': None, 'combined_status': 'components_shown_separately',
        'combined_reason': 'overlapping factor'}}
    alternative = {'offer_id': 'other', 'supplier': 'Other Supplier', 'estimate': {
        'quantity': '100', 'unit': 'kg', 'material': {'kg_co2e': '30',
            'factor': {'publisher': 'Other Material Source', 'published_on': '2025-01-01'}},
        'transport': {'kg_co2e': '7', 'legs': [{'factor': {'publisher': 'Other Freight Source'}}]},
        'total_kg_co2e': '37', 'combined_status': 'combined', 'combined_reason': 'exact match'}}
    carbon = {'plan_id': 'plan-1', 'company_id': 'company-1', 'suppliers': [selected, alternative]}
    result = {'status': 'completed', 'assistant_text': 'Selected estimate from Material Source.',
              'workspace': {'carbon': carbon}}
    model = dict(result)
    plan = {'plan_id': 'plan-1', 'selected_offer_id': 'selected',
            'alternatives': [{'offer_id': 'selected', 'feasible': True},
                             {'offer_id': 'other', 'feasible': False}]}

    _selected_purchase_model_result('read_carbon', result, model, plan)

    projected = model['workspace']['carbon']
    assert projected['suppliers'] == [selected]
    assert projected['selected_purchase_state'] == 'selected'
    assert projected['alternative_quote_count'] == 1
    assert projected['alternative_quote_omitted'] == 0
    assert projected['alternative_quote_estimates'] == [{
        'offer_id': 'other', 'supplier': 'Other Supplier', 'feasible': False,
        'quantity': '100', 'unit': 'kg', 'production_kg_co2e': '30',
        'freight_kg_co2e': '7', 'total_kg_co2e': '37', 'combined_status': 'combined',
        'combined_reason': 'exact match',
        'source_publishers': ['Other Material Source', 'Other Freight Source']}]
    assert '2025-01-01' not in str(model)
    assert 'required_spoken_response' not in model
    assert model['selected_purchase_brief'] == result['assistant_text']
    assert 'quote alternatives' in projected['carbon_scope'].lower()
    assert 'cost' in projected['carbon_scope'].lower()
    assert result['workspace']['carbon'] == carbon

    no_selection = dict(result)
    no_selection_model = dict(no_selection)
    _selected_purchase_model_result('read_carbon', no_selection, no_selection_model,
                                    {**plan, 'selected_offer_id': None})
    no_selection_view = no_selection_model['workspace']['carbon']
    assert no_selection_view['suppliers'] == []
    assert no_selection_view['selected_purchase_state'] == 'no_selected_offer'
    assert no_selection_view['alternative_quote_count'] == 2

    infeasible_model = dict(result)
    _selected_purchase_model_result('read_carbon', result, infeasible_model,
                                    {**plan, 'alternatives': [
                                        {'offer_id': 'selected', 'feasible': False},
                                        {'offer_id': 'other', 'feasible': False}]})
    infeasible_view = infeasible_model['workspace']['carbon']
    assert infeasible_view['suppliers'] == []
    assert infeasible_view['selected_purchase_state'] == 'no_feasible_selected_offer'
    assert infeasible_view['alternative_quote_count'] == 2
    assert any(row['offer_id'] == 'selected' and row['feasible'] is False
               for row in infeasible_view['alternative_quote_estimates'])

    inconsistent = {**alternative, 'estimate': {**alternative['estimate'],
                    'total_kg_co2e': '999', 'combined_status': 'components_shown_separately'}}
    separate_result = {**result, 'workspace': {'carbon': {**carbon,
                        'suppliers': [selected, inconsistent]}}}
    separate_model = dict(separate_result)
    _selected_purchase_model_result('read_carbon', separate_result, separate_model, plan)
    assert separate_model['workspace']['carbon']['alternative_quote_estimates'][0]['total_kg_co2e'] is None


def test_native_reads_keep_full_browser_evidence_but_focus_model_on_selected_purchase():
    provider = NamedProvider()

    @asynccontextmanager
    async def factory():
        yield provider

    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        missing, missing_model = voice_tool(client, provider, 'read_carbon', {}, 'carbon-missing')
        assert missing['workspace']['carbon'] is None
        assert missing_model['selected_purchase_brief'] == missing['assistant_text']
        assert missing_model['workspace']['carbon']['selected_purchase_state'] == 'unavailable'
        assert 'required_spoken_response' not in missing_model
        orders, orders_model = voice_tool(client, provider, 'read_shipments', {}, 'shipments-unlinked')
        assert orders['workspace']['shipments']['shipments']
        assert orders_model['workspace']['shipments']['shipments'] == []
        no_plan_view = orders_model['workspace']['shipments']
        assert no_plan_view['expected_order_count'] == len(orders['workspace']['shipments']['shipments'])
        assert no_plan_view['expected_order_omitted'] == 0
        assert all(row['source'] == 'fictional_company_profile_expected_order'
                   and row['selected_purchase'] is False
                   for row in no_plan_view['expected_order_scenarios'])
        assert orders_model['current_purchase_brief'] == orders['assistant_text']
        assert 'required_spoken_response' not in orders_model
        assert 'assistant_text' not in orders_model
        plan = plan_for_copper(client, headers)
        estimate = client.post('/api/demo/carbon/estimate', headers=headers, json={
            'plan_id': plan['plan_id'], 'expected_context_version': 1,
            'legs_by_offer': {row['offer_id']: [{'mode': 'sea_container', 'distance_km': '1000'}]
                              for row in plan['alternatives']}})
        assert estimate.status_code == 200, estimate.text

        carbon, carbon_model = voice_tool(client, provider, 'read_carbon', {}, 'carbon-projection')
        full_carbon = carbon['workspace']['carbon']
        selected_id = plan['selected_offer_id']
        selected = next(row for row in full_carbon['suppliers'] if row['offer_id'] == selected_id)
        assert len(full_carbon['suppliers']) == len(plan['alternatives']) > 1
        assert carbon_model['workspace']['carbon']['suppliers'] == [selected]
        assert carbon_model['workspace']['carbon']['plan_id'] == plan['plan_id']
        assert carbon_model['workspace']['carbon']['selected_offer_id'] == selected_id
        assert carbon_model['selected_purchase_brief'] == carbon['assistant_text']
        assert 'required_spoken_response' not in carbon_model
        assert len(carbon_model['workspace']['carbon']['alternative_quote_estimates']) == len(full_carbon['suppliers']) - 1
        assert selected['estimate']['combined_reason'] in str(carbon_model)
        publisher = selected['estimate']['material']['factor']['publisher']
        assert publisher in str(carbon_model) and publisher in carbon_model['selected_purchase_brief']
        assert session['csrf_token'] not in str(carbon_model)

        shipments, shipment_model = voice_tool(client, provider, 'read_shipments', {}, 'shipments-projection')
        full_view = shipments['workspace']['shipments']
        linked = [row for row in full_view['shipments'] if row.get('plan_id') == plan['plan_id']]
        assert len(full_view['shipments']) > len(linked) == 1
        model_view = shipment_model['workspace']['shipments']
        assert model_view['shipments'] == linked
        assert model_view['plan_id'] == plan['plan_id']
        assert model_view['expected_order_count'] == len(full_view['shipments']) - 1
        assert model_view['expected_order_scenarios']
        assert 'as_of' not in model_view
        for row in full_view['shipments']:
            if row not in linked and row.get('expected_arrival'):
                assert row['expected_arrival'] not in str(model_view['shipments'])
        assert linked[0]['recovery_available'] is True
        assert linked[0]['shipment_id'].startswith('demo-plan-demand:')
        assert linked[0]['offer_id'] == selected_id
        assert linked[0]['expected_arrival'] in str(model_view)
        assert shipment_model['current_purchase_brief'] == shipments['assistant_text']
        assert 'required_spoken_response' not in shipment_model
        assert 'assistant_text' not in shipment_model
        assert 'answer' in model_view['selected_purchase_scope'].lower()
        assert 'selected purchase' in model_view['selected_purchase_scope'].lower()
        assert 'company expected orders' in model_view['selected_purchase_scope'].lower()
        assert session['session_id'] not in str(shipment_model)


def test_native_voice_reads_active_feasible_and_infeasible_recovery_scenarios():
    provider = NamedProvider()

    @asynccontextmanager
    async def factory():
        yield provider

    app = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                     live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        baseline = plan_for_copper(client, headers)
        selected_id = baseline['selected_offer_id']
        feasible_response = client.post('/api/demo/recovery', headers=headers, json={
            'baseline_plan_id': baseline['plan_id'], 'expected_versions': baseline['offer_versions'],
            'delay_days_by_offer': {selected_id: 20}, 'idempotency_key': 'feasible-recovery'})
        assert feasible_response.status_code == 200, feasible_response.text
        feasible = feasible_response.json()['scenario']
        assert feasible['selected_offer_id'] is not None
        feasible_estimate = client.post('/api/demo/carbon/estimate', headers=headers, json={
            'plan_id': feasible['plan_id'], 'expected_context_version': session['context_version'],
            'legs_by_offer': {row['offer_id']: [{'mode': 'sea_container', 'distance_km': '1000'}]
                              for row in feasible['alternatives']}})
        assert feasible_estimate.status_code == 200, feasible_estimate.text
        carbon_browser, carbon_model = voice_tool(client, provider, 'read_carbon', {}, 'feasible-carbon-read')
        full_carbon = carbon_browser['workspace']['carbon']
        assert full_carbon['plan_id'] == feasible['plan_id']
        assert full_carbon == feasible_estimate.json()
        selected_carbon = [row for row in full_carbon['suppliers']
                           if row['offer_id'] == feasible['selected_offer_id']]
        assert len(selected_carbon) == 1
        projected_carbon = carbon_model['workspace']['carbon']
        assert projected_carbon['plan_id'] == feasible['plan_id']
        assert projected_carbon['selected_offer_id'] == feasible['selected_offer_id']
        assert projected_carbon['suppliers'] == selected_carbon
        assert projected_carbon['alternative_quote_count'] == len(full_carbon['suppliers']) - 1
        assert all(row['offer_id'] != feasible['selected_offer_id']
                   for row in projected_carbon['alternative_quote_estimates'])
        assert 'quote alternatives' in projected_carbon['carbon_scope'].lower()
        browser, model = voice_tool(client, provider, 'read_shipments', {}, 'feasible-recovery-read')
        canonical = browser['workspace']['shipments']
        selected_rows = [row for row in canonical['shipments']
                         if row.get('scenario_plan_id') == feasible['plan_id']
                         and row.get('offer_id') == feasible['selected_offer_id']]
        assert len(selected_rows) == 1
        assert model['workspace']['shipments']['shipments'] == selected_rows
        assert model['workspace']['shipments']['baseline_reviewed_infeasible'] is None
        assert model['workspace']['shipments']['plan_id'] == feasible['plan_id']

        infeasible_response = client.post('/api/demo/recovery', headers=headers, json={
            'baseline_plan_id': baseline['plan_id'], 'expected_versions': baseline['offer_versions'],
            'delay_days_by_offer': {offer_id: 100 for offer_id in baseline['offer_versions']},
            'idempotency_key': 'infeasible-recovery'})
        assert infeasible_response.status_code == 200, infeasible_response.text
        infeasible = infeasible_response.json()['scenario']
        assert infeasible['selected_offer_id'] is None
        assert client.get('/api/demo/carbon/assessment').json()['assessment'] is None
        carbon_browser, carbon_model = voice_tool(client, provider, 'read_carbon', {}, 'infeasible-carbon-read')
        assert carbon_browser['workspace']['carbon'] is None
        assert carbon_model['workspace']['carbon']['selected_purchase_state'] == 'unavailable'
        assert carbon_model['workspace']['carbon']['suppliers'] == []
        browser, model = voice_tool(client, provider, 'read_shipments', {}, 'infeasible-recovery-read')
        canonical = browser['workspace']['shipments']
        baseline_rows = [row for row in canonical['shipments']
                         if row.get('scenario_plan_id') == infeasible['plan_id']
                         and row.get('plan_id') == baseline['plan_id']
                         and row.get('scenario_status') == 'infeasible']
        assert len(baseline_rows) == 1
        projection = model['workspace']['shipments']
        assert projection['plan_id'] == infeasible['plan_id']
        assert projection['shipments'] == []
        assert projection['baseline_reviewed_infeasible']['row'] == baseline_rows[0]
        assert projection['baseline_reviewed_infeasible']['selected_purchase'] is False
