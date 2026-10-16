"""Native carbon and shipment briefs state only the current planning evidence."""
from copy import deepcopy
import re

from procure.carbon import compare_plan, load_registry
from procure.demo.company import get_company
from procure.demo.dispatch_view import build_dispatch_view
from procure.demo.integration_tools import _spoken_kg, integration_read
from procure.demo.live_voice import _config, _selected_purchase_model_result
from test_demo_dispatch_view import reviewed_copper, selected_copper
from test_demo_integration_grounding import snapshot as copper_snapshot


def _snapshot_for(company_id, material_id, unit, quantity):
    plan = {'plan_id': 'reviewed-plan', 'material': material_id, 'quantity_unit': unit,
            'selected_offer_id': 'chosen', 'alternatives': [
                {'offer_id': 'chosen', 'supplier': 'Example Supplier [SYNTHETIC]',
                 'quantity': quantity, 'feasible': True}]}
    result = compare_plan(load_registry(), plan,
                          {'chosen': [{'mode': 'sea_container', 'distance_km': '1000'}]},
                          company_id=company_id)
    return {'company_id': company_id, 'company': get_company(company_id), 'plan': plan,
            'carbon_assessment': {'result': result}, 'shipments': {'shipments': []}}


def test_spoken_rounding_keeps_small_nonzero_values_exact():
    assert _spoken_kg('0.00555') == '0.00555'
    assert _spoken_kg('30892.66008') == '30892.66'
    assert _spoken_kg('100000000000000000000000000000') == '100000000000000000000000000000'


def test_selected_copper_brief_marks_plan_and_attributes_each_component_next_to_its_amount():
    value = copper_snapshot()
    saved = deepcopy(value['carbon_assessment']['result'])
    result = integration_read(value, 'read_carbon')
    answer = result['assistant_text']

    assert 'reviewed plan' in answer.lower()
    assert 'not a confirmed order' in answer.lower()
    assert "Cables's" not in answer
    assert 'Europa Copper SA' in answer and '[SYNTHETIC]' not in answer
    assert 'Swift Copper Trading Ltd' not in answer
    assert re.search(r'31720(?:\.00)? kg CO2e.{0,80}International Copper Association', answer)
    assert re.search(r'128\.96 kg CO2e.{0,95}Department for Energy Security and Net Zero', answer)
    assert '2026-10-28' not in answer
    assert result['workspace']['carbon'] == saved

    model = {'assistant_text': answer, 'workspace': result['workspace']}
    _selected_purchase_model_result('read_carbon', {'status': 'completed', **result}, model, value['plan'])
    assert model['selected_purchase_brief'] == answer
    assert 'plan' in model['workspace']['carbon']['carbon_scope'].lower()
    assert 'source' in model['workspace']['carbon']['carbon_scope'].lower()
    instruction = _config().system_instruction.lower()
    assert 'read_carbon.selected_purchase_brief' in instruction
    assert 'not a confirmed order' in instruction
    assert 'read_shipments.current_purchase_brief' in instruction


def test_proxy_and_overlap_reasons_are_spoken_for_this_saved_assessment():
    pet = _snapshot_for('terrapack', 'pet-resin', 'kg', '8000')
    answer = integration_read(pet, 'read_carbon')['assistant_text']
    assert 'proxy' in answer.lower() and 'forming' in answer.lower()
    assert 'not an exact' in answer.lower()
    assert 'transport to the point of sale' in answer.lower()
    assert 'double count' in answer.lower()
    assert 'No whole-scenario total' in answer
    assert '30892.66 kg CO2e' in answer
    assert pet['carbon_assessment']['result']['suppliers'][0]['estimate']['material']['kg_co2e'] == '30892.66008'

    steel = _snapshot_for('forgeline-components', 'steel', 'tonne', '700')
    steel_answer = integration_read(steel, 'read_carbon')['assistant_text']
    assert 'steel cans' in steel_answer.lower() and 'proxy' in steel_answer.lower()


def test_missing_factor_and_mass_are_scoped_to_saved_assessment():
    value = _snapshot_for('atlas-petrochem', 'naphtha', 'barrel', '100')
    answer = integration_read(value, 'read_carbon')['assistant_text']
    assert 'this saved assessment' in answer.lower()
    assert 'naphtha' in answer.lower()
    assert '100 barrels' in answer
    assert 'shipping mass' in answer.lower()
    assert 'no emissions were calculated' not in answer.lower()
    assert 'no factor exists' not in answer.lower()
    assert 'No whole-scenario total' in answer


def test_circuitpeak_spoken_quantity_pluralizes_units_without_changing_saved_unit():
    value = _snapshot_for('circuitpeak-systems', 'chips', 'unit', '63000')
    result = integration_read(value, 'read_carbon')
    assert '63000 units' in result['assistant_text']
    assert value['plan']['quantity_unit'] == 'unit'


def test_shipment_brief_keeps_selected_plan_recovery_baseline_and_no_plan_distinct():
    company, offer, plan = selected_copper()
    selected = {'company_id': company['company_id'], 'company': company,
                'shipments': build_dispatch_view(company, [offer], plan, None)}
    answer = integration_read(selected, 'read_shipments')['assistant_text']
    assert 'planning scenario' in answer.lower() and 'not a confirmed order' in answer.lower()
    assert '2026-10-28' in answer

    company, offers, recovery = reviewed_copper({'eu-copper': 20, 'express-copper': 10})
    infeasible = {'company_id': company['company_id'], 'company': company,
                  'shipments': build_dispatch_view(company, offers, recovery['scenario'], recovery)}
    answer = integration_read(infeasible, 'read_shipments')['assistant_text']
    assert 'baseline' in answer.lower() and 'no feasible supplier' in answer.lower()
    assert 'not a current selected purchase' in answer.lower()

    no_plan = {'company_id': company['company_id'], 'company': company,
               'shipments': build_dispatch_view(company, offers, None, None)}
    answer = integration_read(no_plan, 'read_shipments')['assistant_text']
    assert 'No current reviewed supplier plan' in answer
    assert 'not confirmed orders' in answer.lower()
