from copy import deepcopy

from procure.demo.judge_impact import build_judge_impact


def fixture():
    baseline = {'company_id': 'solara-cables', 'plan_id': 'base', 'material': 'copper',
                'selected_offer_id': 'o1', 'offer_versions': {'o1': 2, 'o2': 1},
                'quantity_unit': 'kg', 'reporting_currency': 'USD', 'decision_date': '2026-10-14',
                'need_by': '2026-11-05', 'total_cost': '825',
                'alternatives': [{'offer_id': 'o1', 'unit_price_per_unit': '8.25',
                                  'quantity_unit': 'kg', 'feasible': True}]}
    scenario = {**baseline, 'plan_id': 'scenario', 'selected_offer_id': 'o2',
                'total_cost': '980', 'required_purchase': '100',
                'alternatives': [{'offer_id': 'o2', 'feasible': True,
                                  'quantity_unit': 'kg', 'total_cost': '980'}]}
    request = {'company_id': 'solara-cables', 'offer_ids': ['o1', 'o2'],
               'expected_versions': {'o1': 2, 'o2': 1}, 'material': 'copper',
               'quantity_kg': '100', 'current_stock_kg': '0', 'additional_cost_per_kg': '0',
               'reporting_currency': 'USD', 'decision_date': '2026-10-14',
               'need_by': '2026-11-05', 'budget': '1000'}
    return ({'company_id': 'solara-cables', 'context_version': 3, 'company': {
        'company_id': 'solara-cables', 'orders': [
            {'order_id': 'a', 'material_id': 'copper', 'quantity': '10.5', 'unit': 'kg'},
            {'order_id': 'b', 'material_id': 'copper', 'quantity': '2', 'unit': 'kg'}]},
             'plan': baseline, 'plan_request': deepcopy(request),
             'shipments': {'shipments': [{'order_id': 'a', 'company_id': 'solara-cables',
                 'material_id': 'copper', 'plan_id': 'base', 'offer_id': 'o1',
                 'offer_version': 2, 'quantity_unit': 'kg', 'recovery_available': True},
                {'order_id': 'b', 'company_id': 'solara-cables', 'material_id': 'copper',
                 'plan_id': 'base', 'offer_id': 'o1', 'offer_version': 2,
                 'quantity_unit': 'kg', 'recovery_available': True}]},
             'carbon_assessment': None},
            {'baseline': baseline, 'scenario': scenario, 'cost_delta': '155',
             'outcome': 'changed', 'baseline_request': deepcopy(request),
             'scenario_request': {**deepcopy(request), 'delay_days_by_offer': {'o1': 7}}})


def test_decimal_exposure_deduplicates_and_cost_is_additional():
    state, recovery = fixture()
    result = build_judge_impact(state, recovery, ['a', 'a', 'b'])
    assert result['planned_spend_exposed']['value'] == '103.125'
    assert result['planned_spend_exposed']['status'] == 'available'
    assert result['additional_recovery_cost']['value'] == '155'
    assert result['additional_recovery_cost']['label'] != 'loss'
    assert result['gap_days']['value'] == 7
    assert result['scenario_plan_id'] == 'scenario'


def test_mismatched_unit_and_infeasible_result_are_unavailable():
    state, recovery = fixture()
    state['company']['orders'][0]['unit'] = 'tonne'
    recovery['scenario'] = {**recovery['scenario'], 'selected_offer_id': None,
                            'total_cost': None, 'required_purchase': '100'}
    recovery['cost_delta'] = None
    result = build_judge_impact(state, recovery, ['a'])
    assert result['planned_spend_exposed']['status'] == 'unavailable'
    assert result['additional_recovery_cost']['status'] == 'unavailable'
    assert result['scenario_feasible'] is False


def test_foreign_order_is_never_priced():
    state, recovery = fixture()
    result = build_judge_impact(state, recovery, ['foreign'])
    assert result['planned_spend_exposed']['status'] == 'unavailable'


def test_owned_order_without_matching_affected_shipment_is_not_priced():
    state, recovery = fixture()
    state['shipments']['shipments'] = state['shipments']['shipments'][:1]
    result = build_judge_impact(state, recovery, ['a', 'b'])
    assert result['planned_spend_exposed']['status'] == 'unavailable'


def test_same_company_unrelated_scenario_invalidates_paired_metrics():
    state, recovery = fixture()
    recovery['scenario'] = {**recovery['scenario'], 'material': 'aluminium',
                            'quantity_unit': 'tonne', 'offer_versions': {'other': 9}}
    result = build_judge_impact(state, recovery, ['a'])
    assert result['scenario_plan_id'] is None
    assert result['scenario_feasible'] is False
    assert result['gap_days']['status'] == 'unavailable'
    assert result['additional_recovery_cost']['status'] == 'unavailable'


def test_mismatched_quote_price_currency_or_as_of_is_unavailable():
    state, recovery = fixture()
    recovery['baseline']['alternatives'][0]['price_currency'] = 'EUR'
    assert build_judge_impact(state, recovery, ['a'])['planned_spend_exposed']['status'] == 'unavailable'
    recovery['baseline']['alternatives'][0]['price_currency'] = 'USD'
    recovery['baseline']['alternatives'][0]['price_as_of'] = '2026-10-16'
    assert build_judge_impact(state, recovery, ['a'])['planned_spend_exposed']['status'] == 'unavailable'


def test_scenario_request_or_selected_alternative_must_link_to_baseline():
    state, recovery = fixture()
    recovery['baseline_request'] = {'material': 'copper', 'quantity': '12.5'}
    recovery['scenario_request'] = {'material': 'aluminium', 'quantity': '12.5',
                                    'delay_days_by_offer': {'o1': 7}}
    assert build_judge_impact(state, recovery, ['a'])['additional_recovery_cost']['status'] == 'unavailable'
    recovery['scenario_request']['material'] = 'copper'
    recovery['scenario']['alternatives'] = [{'offer_id': 'o2', 'feasible': False,
                                             'quantity_unit': 'kg'}]
    assert build_judge_impact(state, recovery, ['a'])['scenario_feasible'] is False


def test_invalid_pair_with_dated_price_returns_unavailable_instead_of_error():
    state, recovery = fixture()
    recovery['baseline']['alternatives'][0]['price_as_of'] = '2026-10-14'
    recovery['scenario']['material'] = 'aluminium'
    result = build_judge_impact(state, recovery, ['a'])
    assert result['planned_spend_exposed']['status'] == 'unavailable'


def test_missing_paired_request_or_current_request_blocks_cost_only():
    state, recovery = fixture()
    recovery.pop('baseline_request')
    result = build_judge_impact(state, recovery, ['a'])
    assert result['additional_recovery_cost']['status'] == 'unavailable'
    assert result['gap_days']['value'] == 7
    state, recovery = fixture()
    state.pop('plan_request')
    assert build_judge_impact(state, recovery, ['a'])['additional_recovery_cost']['status'] == 'unavailable'


def test_missing_authoritative_total_or_wrong_delta_blocks_cost():
    state, recovery = fixture()
    recovery['baseline'].pop('total_cost')
    assert build_judge_impact(state, recovery, ['a'])['additional_recovery_cost']['status'] == 'unavailable'
    state, recovery = fixture()
    recovery['cost_delta'] = '154.99'
    assert build_judge_impact(state, recovery, ['a'])['additional_recovery_cost']['status'] == 'unavailable'
