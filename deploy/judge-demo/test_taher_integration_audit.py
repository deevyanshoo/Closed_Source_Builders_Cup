"""Offline contract checks for the hosted Taher integration audit."""
import asyncio
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'procurement-engine' / 'src'))

# The borrowed offline test interpreter lacks optional service bootstrap packages.
# Carbon's pure calculator never invokes YAML parsing; the synthetic fixture has no .env.
try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    sys.modules['yaml'] = types.ModuleType('yaml')
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    dotenv = types.ModuleType('dotenv')
    dotenv.load_dotenv = lambda *_args, **_kwargs: None
    sys.modules['dotenv'] = dotenv

import taher_integration_audit as audit
from procure.carbon import compare_plan, load_registry
from procure.carbon.assessment import build_assessment
from smoke import AcceptanceError


PLAN = {'plan_id': 'plan-7', 'company_id': 'solara-cables', 'material': 'copper',
        'quantity_unit': 'kg', 'required_purchase': '8000',
        'offer_versions': {'q1': 2, 'q2': 3},
        'selected_offer_id': 'q1', 'alternatives': [
            {'offer_id': 'q1', 'supplier': 'Swift [SYNTHETIC]', 'quantity': '8000'},
            {'offer_id': 'q2', 'supplier': 'Europa [SYNTHETIC]', 'quantity': '8000'}]}


def comparison(plan=PLAN):
    request = audit.carbon_request(plan)
    result = compare_plan(load_registry(), plan, request['legs_by_offer'],
                          request.get('mass_by_offer'), company_id=plan['company_id'])
    return request, {**result, 'context_version': 7}


def test_carbon_check_rejects_fabricated_number_and_foreign_scope():
    request, result = comparison()
    observed = audit.check_carbon(result, PLAN, 7, request)
    assert observed['supplier_count'] == 2
    assert observed['unknown_component_count'] == 0
    assert observed['source_units'] == ['kg CO2e per tonne', 'kg CO2e per tonne.km']
    assert observed['source_ids'] == ['desnz-2026-full-set',
                                     'ica-copper-environmental-profile-2023']
    assert observed['calibration_8000kg_1000km_verified'] is True
    fabricated = deepcopy(result)
    fabricated['suppliers'][0]['estimate']['transport']['kg_co2e'] = '999999'
    with pytest.raises(AcceptanceError, match='carbon_result_mismatch'):
        audit.check_carbon(fabricated, PLAN, 7, request)
    with pytest.raises(AcceptanceError, match='carbon_scope_mismatch'):
        audit.check_carbon({**result, 'company_id': 'other'}, PLAN, 7, request)


def test_carbon_calibration_independent_of_pinned_calculator(monkeypatch):
    request, result = comparison()
    fabricated = deepcopy(result)
    for supplier in fabricated['suppliers']:
        supplier['estimate']['transport']['kg_co2e'] = '129'
        supplier['estimate']['transport']['legs'][0]['kg_co2e'] = '129'
    monkeypatch.setattr(audit, 'compare_plan', lambda *_args, **_kwargs: {
        key: value for key, value in fabricated.items() if key != 'context_version'})
    with pytest.raises(AcceptanceError, match='carbon_calibration_mismatch'):
        audit.check_carbon(fabricated, PLAN, 7, request)


def test_nonmass_request_has_explicit_mass_and_unknown_is_not_a_total():
    plan = {**PLAN, 'company_id': 'meridian-pharma', 'material': 'vials',
            'quantity_unit': 'unit'}
    request = audit.carbon_request(plan)
    assert request['mass_by_offer']['q1'] == {
        'mass_kg': '1000', 'basis': 'Controlled QA assumption: 1000 kg shipped mass per offer.'}
    assert request['legs_by_offer']['q1'] == [{'mode': 'sea_container', 'distance_km': '1000'}]
    tonne_plan = {**PLAN, 'quantity_unit': 'tonne'}
    assert 'mass_by_offer' not in audit.carbon_request(tonne_plan)
    result = compare_plan(load_registry(), plan, request['legs_by_offer'],
                          request['mass_by_offer'], company_id=plan['company_id'])
    observed = audit.check_carbon({**result, 'context_version': 7}, plan, 7, request)
    assert observed['unknown_component_count'] == 2
    assert observed['component_statuses']['material'] == {'unit_mismatch': 2}


def test_plan_check_rejects_fabricated_cost_and_quantity():
    defaults = {'material': 'copper', 'quantity': '12', 'current_stock': '2',
                'quantity_unit': 'kg', 'additional_cost_per_unit': '0.4'}
    plan = {**PLAN, 'synthetic': True, 'required_purchase': '10',
            'total_cost': '104', 'alternatives': [
                {'offer_id': 'q1', 'supplier': 'Swift [SYNTHETIC]', 'quantity': '10',
                 'quantity_unit': 'kg', 'unit_price_per_unit': '10',
                 'total_cost': '104', 'feasible': True},
                {'offer_id': 'q2', 'supplier': 'Europa [SYNTHETIC]', 'quantity': '10',
                 'quantity_unit': 'kg', 'unit_price_per_unit': '20',
                 'total_cost': '204', 'feasible': True}]}
    assert audit.check_plan(plan, 'solara-cables', defaults, {'q1': 2, 'q2': 3})['purchase'] == '10'
    wrong = deepcopy(plan)
    wrong['alternatives'][0]['total_cost'] = '999'
    with pytest.raises(AcceptanceError, match='plan_cost_mismatch'):
        audit.check_plan(wrong, 'solara-cables', defaults, {'q1': 2, 'q2': 3})
    wrong = deepcopy(plan)
    wrong['required_purchase'] = '9'
    with pytest.raises(AcceptanceError, match='plan_context_mismatch'):
        audit.check_plan(wrong, 'solara-cables', defaults, {'q1': 2, 'q2': 3})


def test_assessment_requires_exact_saved_inputs_versions_and_result():
    request, response = comparison()
    record = build_assessment(company_id='solara-cables', context_version=7,
                              plan=PLAN, comparison=response,
                              legs_by_offer=request['legs_by_offer'], mass_by_offer={},
                              created_at='2026-10-16T09:00:00+00:00')
    wrapper = {'company_id': 'solara-cables', 'context_version': 7,
               'assessment': record}
    assert audit.check_assessment(wrapper, PLAN, 7, request, response)['readback_verified'] is True
    wrong = deepcopy(wrapper)
    wrong['assessment']['offer_versions']['q1'] = 99
    with pytest.raises(AcceptanceError, match='carbon_assessment_mismatch'):
        audit.check_assessment(wrong, PLAN, 7, request, response)
    wrong = deepcopy(wrapper)
    wrong['assessment']['result']['suppliers'][0]['estimate']['transport']['kg_co2e'] = '12'
    with pytest.raises(AcceptanceError, match='carbon_assessment_mismatch'):
        audit.check_assessment(wrong, PLAN, 7, request, response)


def dispatch():
    linked = {'shipment_id': 'shipment-7', 'company_id': 'solara-cables',
              'material_id': 'copper', 'material_name': 'Copper cathode',
              'order_id': None, 'demand_reference': 'plan-demand:plan-7',
              'quantity': '8000', 'quantity_unit': 'kg',
              'need_by': '2026-11-02', 'status': 'planning_scenario',
              'expected_arrival': '2026-10-27', 'offer_id': 'q1',
              'offer_version': 2, 'plan_id': 'plan-7',
              'recovery_available': True, 'recovery_reason': None,
              'delay_days': 0, 'timeline': [], 'source': 'synthetic_plan_demand_scenario'}
    return {'company_id': 'solara-cables', 'context_version': 7,
            'plan_id': 'plan-7', 'synthetic': True,
            'source': 'fictional_company_profile_and_plan', 'as_of': '2026-10-16',
            'shipments': [linked], 'limitations': ['Synthetic planning scenario.']}


def test_dispatch_requires_own_current_plan_link_and_source():
    assert audit.check_dispatch(dispatch(), PLAN, 7)['shipment_id'] == 'shipment-7'
    wrong = dispatch()
    wrong['shipments'][0]['company_id'] = 'other'
    with pytest.raises(AcceptanceError, match='foreign_shipment'):
        audit.check_dispatch(wrong, PLAN, 7)
    wrong = dispatch()
    wrong['shipments'][0]['plan_id'] = 'old-plan'
    with pytest.raises(AcceptanceError, match='invalid_recovery_link'):
        audit.check_dispatch(wrong, PLAN, 7)
    wrong = dispatch()
    wrong['shipments'][0]['source'] = None
    with pytest.raises(AcceptanceError, match='shipment_provenance_missing'):
        audit.check_dispatch(wrong, PLAN, 7)


def test_recovery_must_bind_selected_shipment_and_company():
    linked = dispatch()['shipments'][0]
    response = {'outcome': 'changed', 'scenario': {
        **PLAN, 'plan_id': 'revised-7', 'selected_offer_id': 'q2'},
        'scenario_request': {'delay_days_by_offer': {'q1': 60}}}
    assert audit.check_recovery(response, PLAN, linked, 60)['changed'] is True
    wrong = deepcopy(response)
    wrong['scenario_request']['delay_days_by_offer'] = {'q2': 60}
    with pytest.raises(AcceptanceError, match='recovery_not_bound'):
        audit.check_recovery(wrong, PLAN, linked, 60)
    wrong = deepcopy(response)
    wrong['scenario']['company_id'] = 'other'
    with pytest.raises(AcceptanceError, match='recovery_not_bound'):
        audit.check_recovery(wrong, PLAN, linked, 60)


def test_recovery_rfq_uses_reviewed_endpoint_and_checks_unsent_readback():
    revised = {**PLAN, 'plan_id': 'revised-7', 'selected_offer_id': 'q2'}
    draft = {'draft_id': 'draft-7', 'status': 'draft_unsent',
             'company_id': 'solara-cables', 'material': 'copper', 'synthetic': True}

    class Client:
        def __init__(self):
            self.calls = []
            self.outbox = []

        def json(self, stage, path, **kwargs):
            self.calls.append((stage, path, kwargs))
            if path == '/api/demo/rfq':
                return draft
            assert path == '/api/demo/session'
            return {'company': {'company_id': 'solara-cables'}, 'context_version': 7,
                    'plan': revised, 'rfq': draft,
                    'drafts': [{'draft_id': 'draft-7', 'status': 'draft_unsent'}],
                    'simulated_outbox': self.outbox}

    client = Client()
    assert audit.create_reviewed_rfq(client, 'solara-cables', 'csrf', 7,
                                     revised, PLAN['offer_versions']) == {
        'draft_id': 'draft-7', 'unsent': True, 'outbox_empty': True}
    assert client.calls[0][1] == '/api/demo/rfq'
    assert client.calls[0][2]['body']['reviewed'] is True
    assert client.calls[0][2]['body']['plan_id'] == 'revised-7'
    assert client.calls[0][2]['body']['expected_versions'] == PLAN['offer_versions']
    client.outbox.append({'message_id': 'sent-1'})
    with pytest.raises(AcceptanceError, match='rfq_or_outbound_action_present'):
        audit.create_reviewed_rfq(client, 'solara-cables', 'csrf', 7,
                                  revised, PLAN['offer_versions'])


def test_typed_operator_reads_use_normal_endpoint_and_preserve_action_state():
    _, carbon = comparison()
    view = dispatch()
    state = {'company_id': 'solara-cables', 'company': {'company_id': 'solara-cables'},
             'context_version': 7, 'offers': [{'offer_id': 'q1'}], 'plan': PLAN,
             'plan_request': {'material': 'copper'}, 'recovery': None, 'rfq': None,
             'drafts': [], 'simulated_outbox': [], 'buyer_draft': None,
             'carbon_assessment': {'result': carbon}, 'dispatch': view}

    class Client:
        def __init__(self):
            self.calls = []
            self.changed = False
            self.mutate_on_post = False

        def json(self, stage, path, **kwargs):
            self.calls.append((path, kwargs))
            if path == '/api/demo/session':
                result = deepcopy(state)
                if self.changed:
                    result['drafts'] = [{'draft_id': 'unexpected'}]
                return result
            assert path == '/api/demo/operator'
            if self.mutate_on_post:
                self.changed = True
            message = kwargs['body']['message']
            tool = ('read_carbon' if 'emissions' in message else 'read_shipments')
            answer = ('Solara Cables copper: 8000 kg has 128.96 kg CO2e freight from a published factor.'
                      if tool == 'read_carbon' else
                      'Solara Cables: 8000 kg of Copper cathode is an inbound planning scenario. '
                      'Planned arrival is 2026-10-27; not live carrier tracking.')
            return {'company_id': 'solara-cables', 'context_version': 7,
                    'status': 'completed', 'read_only': True, 'proposal': None,
                    'assistant_text': answer, 'workspace': {
                        'carbon' if tool == 'read_carbon' else 'shipments':
                        carbon if tool == 'read_carbon' else view},
                    'agent_steps': [{'agent': 'workspace', 'role': 'deterministic',
                                     'status': 'completed', 'model': 'none',
                                     'provider_call': 'none', 'tool': tool}]}

    client = Client()
    observed = audit.check_typed_operator_reads(client, 'solara-cables', 'Solara Cables',
                                                 'Copper cathode', 'csrf', 7,
                                                 PLAN, carbon, view)
    assert [item['tool'] for item in observed] == ['read_carbon', 'read_shipments']
    assert '128.96 kg CO2e' in observed[0]['assistant_text']
    assert [path for path, _ in client.calls].count('/api/demo/operator') == 2
    assert all(kwargs['body']['expected_context_version'] == 7
               and kwargs['body']['idempotency_key']
               for path, kwargs in client.calls if path == '/api/demo/operator')
    client = Client()
    client.mutate_on_post = True
    with pytest.raises(AcceptanceError, match='operator_action_state_changed'):
        audit.check_typed_operator_reads(client, 'solara-cables', 'Solara Cables',
                                         'Copper cathode', 'csrf', 7, PLAN, carbon, view)


def test_failed_revision_receipt_records_exact_observation_without_secret(tmp_path, monkeypatch):
    class FakeClient:
        def __init__(self, _base):
            pass

        def json(self, stage, path):
            assert (stage, path) == ('health', '/-/health')
            return {'ready': True, 'revision': 'deployed-other', 'persistence': 'firestore',
                    'csrf_token': 'do-not-log-this'}

    monkeypatch.setattr(audit, 'Client', FakeClient)
    target = tmp_path / 'receipt.json'
    assert audit.run('https://demo.example', 'expected-revision', target) == 1
    stored = target.read_text(encoding='utf-8')
    assert 'do-not-log-this' not in stored
    receipt = json.loads(stored)
    assert receipt['expected_revision'] == 'expected-revision'
    assert receipt['deployed_revision'] == 'deployed-other'
    assert receipt['error_code'] == 'wrong_revision_or_storage'
    assert receipt['automatic_retries'] == 0


def test_invalid_url_still_leaves_a_sanitized_failure_receipt(tmp_path):
    target = tmp_path / 'invalid.json'
    assert audit.run('http://not-https.example', 'revision-7', target) == 1
    receipt = json.loads(target.read_text(encoding='utf-8'))
    assert receipt['expected_revision'] == 'revision-7'
    assert receipt['error_code'] == 'invalid_url'
    assert receipt['deployed_revision'] is None


def test_full_run_requires_and_records_ten_distinct_company_visits(tmp_path, monkeypatch):
    profiles = [{'company_id': f'company-{index}'} for index in range(10)]

    class FakeClient:
        def __init__(self, _base):
            pass

        def json(self, _stage, path):
            if path == '/-/health':
                return {'ready': True, 'revision': 'revision-7', 'persistence': 'firestore'}
            assert path == '/api/demo/companies'
            return {'companies': profiles}

    visited = []

    def visit(_base, profile, row, checkpoint):
        visited.append(profile['company_id'])
        row.update(ok=True, stage='completed', carbon={
            'calibration_8000kg_1000km_verified': profile['company_id'] == 'company-0'})
        checkpoint()

    monkeypatch.setattr(audit, 'Client', FakeClient)
    monkeypatch.setattr(audit, 'prepare_company', visit)
    output = tmp_path / 'complete.json'
    assert audit.run('https://demo.example', 'revision-7', output) == 0
    receipt = json.loads(output.read_text(encoding='utf-8'))
    assert visited == [p['company_id'] for p in profiles]
    assert receipt['deployed_revision'] == 'revision-7'
    assert len(receipt['companies']) == 10 and all(r['ok'] for r in receipt['companies'])

    monkeypatch.setattr(audit, 'prepare_company', lambda _base, _profile, row, _checkpoint:
                        row.update(ok=True, stage='completed', carbon={
                            'calibration_8000kg_1000km_verified': False}))
    missing_calibration = tmp_path / 'missing-calibration.json'
    assert audit.run('https://demo.example', 'revision-7', missing_calibration) == 1
    assert json.loads(missing_calibration.read_text(encoding='utf-8'))['error_code'] == 'calibration_not_observed'

    profiles.pop()
    shorter = tmp_path / 'incomplete.json'
    assert audit.run('https://demo.example', 'revision-7', shorter) == 1
    assert json.loads(shorter.read_text(encoding='utf-8'))['error_code'] == 'company_coverage_mismatch'


def test_manual_company_subset_still_checks_full_catalogue(tmp_path, monkeypatch):
    profiles = [{'company_id': f'company-{index}'} for index in range(10)]

    class FakeClient:
        def __init__(self, _base):
            pass

        def json(self, _stage, path):
            return ({'ready': True, 'revision': 'revision-7', 'persistence': 'firestore'}
                    if path == '/-/health' else {'companies': profiles})

    visited = []

    def visit(_base, profile, row, _checkpoint):
        visited.append(profile['company_id'])
        row.update(ok=True, stage='completed', carbon={
            'calibration_8000kg_1000km_verified': profile['company_id'] == 'company-0'})

    monkeypatch.setattr(audit, 'Client', FakeClient)
    monkeypatch.setattr(audit, 'prepare_company', visit)
    target = tmp_path / 'subset.json'
    assert audit.run('https://demo.example', 'revision-7', target,
                     companies=['company-0', 'company-3']) == 0
    assert visited == ['company-0', 'company-3']
    receipt = json.loads(target.read_text(encoding='utf-8'))
    assert receipt['selected_company_ids'] == ['company-0', 'company-3']
    assert len(receipt['companies']) == 2
    visited.clear()
    other_only = tmp_path / 'other-only.json'
    assert audit.run('https://demo.example', 'revision-7', other_only,
                     companies=['company-3']) == 0
    assert visited == ['company-3']
    profiles.pop()
    invalid = tmp_path / 'short-catalogue.json'
    assert audit.run('https://demo.example', 'revision-7', invalid,
                     companies=['company-0']) == 1
    assert json.loads(invalid.read_text(encoding='utf-8'))['error_code'] == 'company_coverage_mismatch'


def test_voice_result_and_caption_reject_foreign_scope_action_and_fabrication():
    _, carbon = comparison()
    view = dispatch()
    result = {'turn_id': 'turn-7', 'company_id': 'solara-cables', 'context_version': 7,
              'status': 'completed', 'read_only': True, 'proposal': None,
              'agent_steps': [{'agent': 'workspace', 'role': 'deterministic',
                               'status': 'completed', 'model': 'none',
                               'provider_call': 'none', 'tool': 'read_carbon'}],
              'workspace': {'carbon': carbon},
              'assistant_text': 'Solara Cables carbon facts are available.'}
    assert audit.check_voice_result(result, PLAN, 7, carbon, view) == 'read_carbon'
    shipment_result = deepcopy(result)
    shipment_result['agent_steps'][0]['tool'] = 'read_shipments'
    shipment_result['workspace'] = {'shipments': view}
    assert audit.check_voice_result(shipment_result, PLAN, 7, carbon, view) == 'read_shipments'
    good = ('At Solara Cables, the synthetic copper shipment is a planning scenario. '
            'The controlled sea carbon assumption is 1000 km, and estimated freight '
            'is 128.96 kg CO2e from a published average factor.')
    assert audit.check_voice_caption(good, 'Solara Cables', PLAN, carbon, view)['numbers_checked'] == 2
    assert audit.check_voice_caption(good + ' Planned arrival is October 27.',
                                     'Solara Cables', PLAN, carbon, view)['numbers_checked'] == 3
    natural = ('At Solara Cables, the copper inbound delivery is a hypothetical plan. '
               'The estimated greenhouse gas emissions use a published conversion factor '
               'and an entered 1000 kilometre sea distance. Freight is 128.96 kilograms '
               'of carbon dioxide equivalent.')
    assert audit.check_voice_caption(natural, 'Solara Cables', PLAN, carbon,
                                     view)['caption_grounded'] is True
    for provenance in ('Sources', 'factors', 'assumptions'):
        equivalent = (f'At Solara Cables, the copper shipment is a planning scenario. '
                      f'Published {provenance} support the entered 1000 km sea distance; '
                      'estimated freight is 128.96 kilograms of CO2e.')
        assert audit.check_voice_caption(equivalent, 'Solara Cables', PLAN, carbon,
                                         view)['numbers_checked'] == 2
    planned = ('For Solara Cables copper, the planned inbound arrival is 2026-10-27. '
               'The shipment estimate uses a published carbon factor and the entered '
               '1000 km sea distance; freight is 128.96 kg CO2e.')
    assert audit.check_voice_caption(planned, 'Solara Cables', PLAN, carbon,
                                     view)['caption_grounded'] is True
    with pytest.raises(AcceptanceError, match='voice_live_tracking_claim'):
        audit.check_voice_caption(planned + ' Live carrier tracking confirms the delivery.',
                                  'Solara Cables', PLAN, carbon, view)
    with pytest.raises(AcceptanceError, match='voice_unverified_number'):
        audit.check_voice_caption(good + ' Freight is 999999 kg CO2e.',
                                  'Solara Cables', PLAN, carbon, view)
    with pytest.raises(AcceptanceError, match='voice_action_claim'):
        audit.check_voice_caption(good + ' I have sent an RFQ.',
                                  'Solara Cables', PLAN, carbon, view)
    with pytest.raises(AcceptanceError, match='voice_action_claim'):
        audit.check_voice_caption(good + ' I will place the order.',
                                  'Solara Cables', PLAN, carbon, view)
    with pytest.raises(AcceptanceError, match='voice_price_claim'):
        audit.check_voice_caption(good + ' Price is USD 77.',
                                  'Solara Cables', PLAN, carbon, view)
    with pytest.raises(AcceptanceError, match='voice_price_claim'):
        audit.check_voice_caption(good + ' The price is 77 USD.',
                                  'Solara Cables', PLAN, carbon, view)
    unavailable = deepcopy(carbon)
    for supplier in unavailable['suppliers']:
        supplier['estimate']['total_kg_co2e'] = None
    with pytest.raises(AcceptanceError, match='voice_unknown_total_claim'):
        audit.check_voice_caption(good + ' The combined total footprint is 128.96 kg CO2e.',
                                  'Solara Cables', PLAN, unavailable, view)
    with pytest.raises(AcceptanceError, match='voice_wrong_scope'):
        audit.check_voice_result({**result, 'company_id': 'other'}, PLAN, 7, carbon, view)
    wrong = deepcopy(result)
    wrong['workspace']['carbon']['suppliers'][0]['estimate']['transport']['kg_co2e'] = '999'
    with pytest.raises(AcceptanceError, match='voice_workspace_mismatch'):
        audit.check_voice_result(wrong, PLAN, 7, carbon, view)


def test_r67_solara_caption_grounded_without_lexical_source_or_shipment_tokens():
    _, carbon = comparison()
    plan = {**PLAN, 'selected_offer_id': 'q2'}
    view = dispatch()
    view['shipments'][0]['offer_id'] = 'q2'
    view['shipments'][0]['expected_arrival'] = '2026-10-24'
    caption = ('For Solara Cables and  their copper cathode  purchase, the estimated  '
               'emissions  are 31,848.96  kg  CO2e,  including  production  averages  '
               'and  freight from Europa  Copper SA.  This relies on figures  from '
               "the International  Copper Association  and the UK's DESNZ,  though it  "
               'remains an estimate  rather than supplier-specific  data. The planned  '
               'arrival  is set for October  24, 2026.')
    assert audit.check_voice_caption(caption, 'Solara Cables', plan, carbon,
                                     view)['caption_grounded'] is True
    with pytest.raises(AcceptanceError, match='voice_unverified_number'):
        audit.check_voice_caption(caption.replace('31,848.96', '31,849.96'),
                                  'Solara Cables', plan, carbon, view)
    with pytest.raises(AcceptanceError, match='voice_unverified_number'):
        audit.check_voice_caption(caption.replace('October  24', 'October  28'),
                                  'Solara Cables', plan, carbon, view)
    without_source = caption.replace('This relies on figures  from '
                                     "the International  Copper Association  and the UK's DESNZ,  ", '')
    with pytest.raises(AcceptanceError, match='voice_caption_not_grounded'):
        audit.check_voice_caption(without_source, 'Solara Cables', plan, carbon, view)
    unavailable = deepcopy(carbon)
    for supplier in unavailable['suppliers']:
        supplier['estimate']['total_kg_co2e'] = None
    unsupported_total = caption.replace('31,848.96', '128.96').replace(
        'estimated  emissions', 'combined total  emissions')
    with pytest.raises(AcceptanceError, match='voice_unknown_total_claim'):
        audit.check_voice_caption(unsupported_total, 'Solara Cables', plan,
                                  unavailable, view)


def test_r67_generic_prompt_caption_accepts_spoken_co2_equivalent_unit():
    _, carbon = comparison()
    plan = {**PLAN, 'selected_offer_id': 'q2'}
    view = dispatch()
    view['shipments'][0]['offer_id'] = 'q2'
    view['shipments'][0]['expected_arrival'] = '2026-10-24'
    caption = ('For Solara Cables  and their copper  cathode purchase, the planned  '
               'arrival  with Europa Copper  SA is October  24, 2026.  The estimated  '
               'carbon  emissions for this shipment  total 31,848.96  kilograms  of CO2  '
               'equivalent,  based  on International  Copper Association  and UK DESNZ  '
               'factors,  excluding supplier-specific  data.  No action  has been taken.')
    assert audit.check_voice_caption(caption, 'Solara Cables', plan, carbon,
                                     view)['caption_grounded'] is True


def test_voice_turn_requires_both_actual_read_results_and_audio():
    _, carbon = comparison()
    view = dispatch()
    base = {'turn_id': 'turn-7', 'company_id': 'solara-cables', 'context_version': 7,
            'status': 'completed', 'read_only': True, 'proposal': None,
            'assistant_text': 'Solara Cables has synthetic workspace evidence.'}
    step = {'agent': 'workspace', 'role': 'deterministic', 'status': 'completed',
            'model': 'none', 'provider_call': 'none'}
    carbon_result = {**base, 'agent_steps': [{**step, 'tool': 'read_carbon'}],
                     'workspace': {'carbon': carbon}}
    shipment_result = {**base, 'agent_steps': [{**step, 'tool': 'read_shipments'}],
                       'workspace': {'shipments': view}}
    workspace_result = {**base, 'agent_steps': [{**step, 'tool': 'read_workspace'}],
                        'workspace': {'company': {'company_id': 'solara-cables'},
                                      'plan': PLAN, 'selected_material_id': None,
                                      'drafts': [], 'simulated_outbox': []}}
    assert audit.check_voice_result(workspace_result, PLAN, 7, carbon, view) == 'read_workspace'
    foreign = deepcopy(workspace_result)
    foreign['workspace']['company']['company_id'] = 'other'
    with pytest.raises(AcceptanceError, match='voice_workspace_mismatch'):
        audit.check_voice_result(foreign, PLAN, 7, carbon, view)
    action = deepcopy(workspace_result)
    action['read_only'] = False
    with pytest.raises(AcceptanceError, match='voice_wrong_scope'):
        audit.check_voice_result(action, PLAN, 7, carbon, view)
    caption = ('At Solara Cables the synthetic copper shipment is a planning scenario. '
               'The carbon source is a published factor and the 1000 km assumption '
               'gives freight of 128.96 kg CO2e.')
    events = [
        {'type': 'transcript', 'direction': 'input',
         'text': 'Read my carbon estimate and shipments.'},
        {'type': 'tool_result', 'result': workspace_result},
        {'type': 'turn_complete'},
        {'type': 'tool_result', 'result': carbon_result},
        {'type': 'turn_complete'},
        {'type': 'tool_result', 'result': shipment_result},
        {'type': 'turn_complete'},
        {'type': 'transcript', 'direction': 'output', 'text': caption},
        {'type': 'audio', 'sample_rate_hertz': 24000,
         'data': base64.b64encode(b'\0' * 10000).decode('ascii')},
        {'type': 'turn_complete'},
    ]

    class Socket:
        async def send(self, message):
            assert json.loads(message)['type'] in {'audio', 'audio_end'}

        async def recv(self):
            if not events:
                raise TimeoutError
            return json.dumps(events.pop(0))

    trace = {}
    checkpoints = []
    result = asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200,
                                          'Solara Cables', PLAN, 7, carbon, view,
                                          trace=trace, checkpoint=lambda: checkpoints.append(deepcopy(trace))))
    assert result['tools_verified'] == ['read_carbon', 'read_shipments']
    assert result['output_audio_bytes'] == 10000
    assert result['output_audio_semantics_verified'] is False
    assert trace['input_caption'] == 'Read my carbon estimate and shipments.'
    assert trace['output_caption'] == caption
    assert trace['qualifying_output_caption'] == caption
    assert [entry['payload']['agent_steps'][0]['tool'] for entry in trace['tool_results']] == [
        'read_workspace', 'read_carbon', 'read_shipments']
    assert trace['tool_results'][1]['payload']['workspace']['carbon'] == carbon
    assert trace['audio_bytes'] == 10000
    assert trace['qualifying_audio_bytes'] == 10000
    assert trace['status'] == 'completed' and len(checkpoints) >= 3
    assert trace['intermediate_count'] == 3

    events[:] = [
        {'type': 'transcript', 'direction': 'input',
         'text': 'Read my carbon estimate and shipments.'},
        {'type': 'transcript', 'direction': 'output', 'text': caption},
        {'type': 'audio', 'sample_rate_hertz': 24000,
         'data': base64.b64encode(b'\0' * 10000).decode('ascii')},
        {'type': 'tool_result', 'result': carbon_result},
        {'type': 'tool_result', 'result': shipment_result},
        {'type': 'turn_complete'},
    ]
    trace = {}
    with pytest.raises(AcceptanceError, match='voice_timeout'):
        asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200,
                                     'Solara Cables', PLAN, 7, carbon, view,
                                     trace=trace))
    assert trace['output_caption'] == caption
    assert trace['audio_bytes'] == 10000
    assert trace['qualifying_output_caption'] == ''
    assert trace['qualifying_audio_bytes'] == 0

    events[:] = [
        {'type': 'transcript', 'direction': 'input',
         'text': 'Read my carbon estimate and shipments.'},
        {'type': 'tool_result', 'result': workspace_result},
        {'type': 'turn_complete'},
        {'type': 'turn_complete'},
        {'type': 'turn_complete'},
    ]
    trace = {}
    checkpoints = []
    with pytest.raises(AcceptanceError, match='voice_timeout'):
        asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200,
                                     'Solara Cables', PLAN, 7, carbon, view,
                                     trace=trace, checkpoint=lambda: checkpoints.append(deepcopy(trace))))
    assert trace['error_code'] == 'voice_timeout'
    assert trace['input_caption'] == 'Read my carbon estimate and shipments.'
    assert trace['output_caption'] == ''
    assert trace['audio_bytes'] == 0
    assert len(trace['tool_results']) == 1 and checkpoints[-1]['error_code'] == 'voice_timeout'
    assert trace['tools_verified'] == []
    assert trace['intermediate_count'] == 3


def test_voice_failure_trace_sanitizes_tool_payload_and_caps_caption():
    _, carbon = comparison()
    view = dispatch()
    result = {'company_id': 'other', 'context_version': 7, 'status': 'completed',
              'read_only': True, 'proposal': None, 'csrf_token': 'secret-do-not-log',
              'agent_steps': [{'agent': 'workspace', 'role': 'deterministic',
                               'status': 'completed', 'model': 'none',
                               'provider_call': 'none', 'tool': 'read_carbon'}],
              'workspace': {'carbon': carbon}}
    events = [{'type': 'transcript', 'direction': 'input', 'text': 'X' * 5000},
              {'type': 'tool_result', 'result': result}]

    class Socket:
        async def send(self, _message):
            pass

        async def recv(self):
            return json.dumps(events.pop(0))

    trace = {}
    with pytest.raises(AcceptanceError, match='voice_wrong_scope'):
        asyncio.run(audit.voice_turn(Socket(), b'\0' * 3200,
                                     'Solara Cables', PLAN, 7, carbon, view, trace=trace))
    assert len(trace['input_caption']) <= 2000 and trace['input_caption_truncated'] is True
    assert trace['tool_results'][0]['payload']['company_id'] == 'other'
    assert 'secret-do-not-log' not in json.dumps(trace)
    assert trace['error_code'] == 'voice_wrong_scope'


def test_optional_voice_audio_is_passed_to_every_company_visit(tmp_path, monkeypatch):
    profiles = [{'company_id': f'company-{index}'} for index in range(10)]

    class FakeClient:
        def __init__(self, _base):
            pass

        def json(self, _stage, path):
            return ({'ready': True, 'revision': 'revision-7', 'persistence': 'firestore'}
                    if path == '/-/health' else {'companies': profiles})

    received = []

    def visit(_base, profile, row, checkpoint, voice_audio):
        received.append((profile['company_id'], voice_audio))
        row.update(ok=True, stage='completed', carbon={
            'calibration_8000kg_1000km_verified': profile['company_id'] == 'company-0'})
        checkpoint()

    monkeypatch.setattr(audit, 'Client', FakeClient)
    monkeypatch.setattr(audit, 'prepare_company', visit)
    output = tmp_path / 'voice.json'
    audio = b'controlled-audio'
    assert audit.run('https://demo.example', 'revision-7', output, voice_audio=audio) == 0
    assert received == [(p['company_id'], audio) for p in profiles]
    receipt = json.loads(output.read_text(encoding='utf-8'))
    assert receipt['controlled_speech'] is True
    assert 'controlled-audio' not in output.read_text(encoding='utf-8')
