"""Current voice/tool summaries keep quote scenarios separate from planned facts."""
from copy import deepcopy

from procure.carbon import compare_plan, load_registry
from procure.demo.company import get_company
from procure.demo.integration_tools import integration_read


def snapshot(*, selected='q2', entered_distance='1000'):
    plan = {
        'plan_id': 'selected-plan', 'material': 'copper', 'quantity_unit': 'kg',
        'selected_offer_id': selected, 'offer_versions': {'q1': 1, 'q2': 1},
        'alternatives': [
            {'offer_id': 'q1', 'supplier': 'Swift Copper Trading Ltd [SYNTHETIC]',
             'quantity': '8000', 'feasible': True, 'arrival_date': '2026-10-28'},
            {'offer_id': 'q2', 'supplier': 'Europa Copper SA [SYNTHETIC]',
             'quantity': '8000', 'feasible': True, 'arrival_date': '2026-10-24'},
        ],
    }
    legs = {'q2': [{'mode': 'sea_container', **({'distance_km': entered_distance}
                                             if entered_distance is not None else {})}]}
    result = compare_plan(load_registry(), plan, legs, company_id='solara-cables')
    return {'company_id': 'solara-cables', 'company': get_company('solara-cables'),
            'plan': plan, 'carbon_assessment': {'result': result}, 'shipments': {'shipments': []}}


def test_selected_supplier_summary_names_only_the_entered_freight_leg():
    answer = integration_read(snapshot(), 'read_carbon',
                              'Explain the entered freight assumptions for this purchase')['assistant_text']
    assert 'Europa Copper SA' in answer and 'Swift Copper Trading Ltd' not in answer
    assert '31848.96 kg CO2e' in answer
    assert 'sea container, 1000 km' in answer
    assert 'entered by you' in answer
    assert 'standard distance' not in answer.lower()
    assert '2026-10-28' not in answer


def test_no_selected_supplier_does_not_use_first_quote_as_purchase():
    value = snapshot(selected=None)
    answer = integration_read(value, 'read_carbon')['assistant_text']
    assert 'No selected feasible purchase' in answer
    assert 'Swift Copper Trading Ltd' not in answer
    assert 'Europa Copper SA' not in answer
    assert '31848.96 kg CO2e' not in answer


def test_unentered_freight_distance_is_not_described_as_a_route_length():
    value = snapshot(entered_distance=None)
    answer = integration_read(value, 'read_carbon',
                              'Explain the freight assumptions for this purchase')['assistant_text']
    assert 'distance not entered' in answer
    assert '1000 km' not in answer
    assert 'standard distance' not in answer.lower()


def test_infeasible_selected_quote_is_not_a_selected_feasible_purchase():
    value = snapshot()
    value['plan'] = deepcopy(value['plan'])
    value['plan']['alternatives'][1]['feasible'] = False
    answer = integration_read(value, 'read_carbon')['assistant_text']
    assert 'No selected feasible purchase' in answer
    assert '31848.96 kg CO2e' not in answer
