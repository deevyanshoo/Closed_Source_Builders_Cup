"""Versioned, bounded carbon_assessment record for session persistence (review finding 2)."""
import json
from copy import deepcopy
from pathlib import Path

import pytest

from procure.carbon import compare_plan, load_registry
from procure.carbon.assessment import (MAX_RECORD_BYTES, SCHEMA, build_assessment, current_assessment,
                                       validate_assessment)

FIXTURE = Path(__file__).parent / 'fixtures' / 'carbon_assessment_v1.json'
PLAN = {'plan_id': 'plan-1', 'material': 'copper', 'quantity_unit': 'kg', 'offer_versions': {'q1': 2, 'q2': 1},
        'alternatives': [{'offer_id': 'q1', 'supplier': 'Swift [SYNTHETIC]', 'quantity': '8000'},
                         {'offer_id': 'q2', 'supplier': 'Europa [SYNTHETIC]', 'quantity': '8000'}]}
LEGS = {'q1': [{'mode': 'sea_container', 'distance_km': '1000'}]}


def record():
    comparison = compare_plan(load_registry(), PLAN, LEGS, company_id='solara-cables')
    comparison['context_version'] = 3
    return build_assessment(company_id='solara-cables', context_version=3, plan=PLAN, comparison=comparison,
                            legs_by_offer=LEGS, mass_by_offer={}, created_at='2026-10-16T09:00:00+00:00')


def test_record_is_versioned_and_bound_to_company_plan_and_offer_versions():
    value = record()
    assert value['schema'] == SCHEMA == 'carbon-assessment.v1'
    assert (value['company_id'], value['context_version'], value['plan_id']) == ('solara-cables', 3, 'plan-1')
    assert value['offer_versions'] == {'q1': 2, 'q2': 1}
    assert value['registry_version'] == 'carbon-factors-v1' and value['calculator_version'] == 'carbon-calculator-v2'
    assert value['result']['plan_id'] == 'plan-1'


def test_cold_restore_through_json_returns_the_same_valid_record():
    value = record()
    restored = json.loads(json.dumps(value))  # what a fresh process reads back from the session store
    assert validate_assessment(restored) == value


def test_committed_fixture_is_a_valid_record_for_the_coordinator():
    fixture = json.loads(FIXTURE.read_text(encoding='utf-8'))
    assert validate_assessment(fixture) == fixture
    assert fixture == json.loads(json.dumps(record()))


@pytest.mark.parametrize('change', [
    lambda r: r.update(schema='carbon-assessment.v0'),
    lambda r: r.pop('result'),
    lambda r: r.update(extra=1),
    lambda r: r.update(company_id=''),
    lambda r: r.update(context_version='3'),
    lambda r: r['result'].update(plan_id='other'),
    lambda r: r['result'].update(material_id='other'),
    lambda r: r['result'].update(registry_version='other'),
    lambda r: r['result'].update(calculator_version='other'),
    lambda r: r['result'].update(context_version=4),
    lambda r: r['result'].update(suppliers=r['result']['suppliers'][:1]),
    lambda r: r['result']['suppliers'][0]['estimate'].update(material_id='other'),
    lambda r: r['inputs'].update(legs_by_offer={'q1': [{'mode': 'x', 'distance_km': '1'}] * 6}),
    lambda r: r.update(created_at='yesterday'),
])
def test_malformed_records_are_rejected(change):
    value = deepcopy(record())
    change(value)
    with pytest.raises(ValueError):
        validate_assessment(value)


def test_oversized_record_is_rejected():
    value = deepcopy(record())
    value['inputs']['mass_by_offer'] = {'q1': {'mass_kg': '1', 'basis': 'x' * 200}}
    value['result']['scope'] = 'y' * MAX_RECORD_BYTES
    with pytest.raises(ValueError, match='too large'):
        validate_assessment(value)


def test_only_the_current_company_plan_and_versions_get_the_record_back():
    value = record()
    assert current_assessment(value, company_id='solara-cables', plan=PLAN) == value
    assert current_assessment(value, company_id='terrapack', plan=PLAN) is None
    assert current_assessment(value, company_id='solara-cables', plan={**PLAN, 'plan_id': 'plan-2'}) is None
    assert current_assessment(value, company_id='solara-cables', plan={**PLAN, 'offer_versions': {'q1': 3, 'q2': 1}}) is None
    assert current_assessment(None, company_id='solara-cables', plan=PLAN) is None
    assert current_assessment({'schema': 'broken'}, company_id='solara-cables', plan=PLAN) is None


def test_same_id_assessment_does_not_cross_material_or_offer_identity():
    value = record()
    assert current_assessment(value, company_id='solara-cables',
                              plan={**PLAN, 'material': 'aluminium'}) is None
    changed_offers = {**PLAN, 'alternatives': [{'offer_id': 'q1'}, {'offer_id': 'other'}]}
    assert current_assessment(value, company_id='solara-cables', plan=changed_offers) is None


def test_cold_restore_rejects_same_id_but_incomplete_result():
    from datetime import datetime, timezone
    from procure.demo.session_persistence import MemorySessionPersistence
    from procure.demo.sessions import SessionError, SessionStore

    now = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    backend = MemorySessionPersistence()
    first = SessionStore(clock=now, persistence_backend=backend)
    session = first.create()
    session.context_version = 3
    session.last_plan = PLAN
    session.carbon_assessment = record()
    first.save(session)
    state, version = backend.load(session.session_id)
    state['carbon_assessment']['result'] = {
        'plan_id': 'plan-1', 'company_id': 'solara-cables'}
    backend.save(session.session_id, state, version)

    with pytest.raises(SessionError) as error:
        SessionStore(clock=now, persistence_backend=backend).get(session.session_id)
    assert error.value.code == 'session_storage_unavailable'


def test_cold_restore_rejects_identity_complete_result_missing_estimate_status():
    from datetime import datetime, timezone
    from procure.demo.session_persistence import MemorySessionPersistence
    from procure.demo.sessions import SessionError, SessionStore

    now = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    backend = MemorySessionPersistence()
    first = SessionStore(clock=now, persistence_backend=backend)
    session = first.create()
    session.context_version = 3
    session.last_plan = PLAN
    session.carbon_assessment = record()
    first.save(session)
    state, version = backend.load(session.session_id)
    state['carbon_assessment']['result']['suppliers'][0]['estimate'].pop('combined_status')
    backend.save(session.session_id, state, version)

    with pytest.raises(SessionError) as error:
        SessionStore(clock=now, persistence_backend=backend).get(session.session_id)
    assert error.value.code == 'session_storage_unavailable'


@pytest.mark.parametrize('field,value', [
    ('product_match', 'proxy'),
    ('includes_transport_to_point_of_sale', True),
])
def test_cold_restore_rejects_combined_total_with_proxy_or_overlapping_material(field, value):
    from datetime import datetime, timezone
    from procure.demo.session_persistence import MemorySessionPersistence
    from procure.demo.sessions import SessionError, SessionStore

    now = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    backend = MemorySessionPersistence()
    first = SessionStore(clock=now, persistence_backend=backend)
    session = first.create()
    session.context_version = 3
    session.last_plan = PLAN
    session.carbon_assessment = record()
    first.save(session)
    state, version = backend.load(session.session_id)
    estimate = state['carbon_assessment']['result']['suppliers'][0]['estimate']
    assert estimate['combined_status'] == 'combined'
    material = estimate['material']
    if field == 'product_match':
        material[field] = value
    else:
        material['factor'][field] = value
    backend.save(session.session_id, state, version)

    with pytest.raises(SessionError) as error:
        SessionStore(clock=now, persistence_backend=backend).get(session.session_id)
    assert error.value.code == 'session_storage_unavailable'


@pytest.mark.parametrize('contradiction', ['coverage', 'embedded_factor'])
def test_cold_restore_rejects_combined_total_with_contradictory_product_match(contradiction):
    from datetime import datetime, timezone
    from procure.demo.session_persistence import MemorySessionPersistence
    from procure.demo.sessions import SessionError, SessionStore

    now = lambda: datetime(2026, 10, 16, tzinfo=timezone.utc)
    backend = MemorySessionPersistence()
    first = SessionStore(clock=now, persistence_backend=backend)
    session = first.create()
    session.context_version = 3
    session.last_plan = PLAN
    session.carbon_assessment = record()
    first.save(session)
    state, version = backend.load(session.session_id)
    result = state['carbon_assessment']['result']
    assert result['suppliers'][0]['estimate']['combined_status'] == 'combined'
    if contradiction == 'coverage':
        result['coverage']['state'] = 'proxy'
        result['coverage']['reason'] = 'Proxy match'
    else:
        result['suppliers'][0]['estimate']['material']['factor']['product_match'] = 'proxy'
    backend.save(session.session_id, state, version)

    with pytest.raises(SessionError) as error:
        SessionStore(clock=now, persistence_backend=backend).get(session.session_id)
    assert error.value.code == 'session_storage_unavailable'


def test_current_read_recomputes_stored_result_from_plan_inputs_and_registry():
    value = deepcopy(record())
    estimate = value['result']['suppliers'][0]['estimate']
    estimate['material']['factor']['factor'] = '1'
    assert validate_assessment(value) == value  # still structurally valid

    assert current_assessment(value, company_id='solara-cables', plan=PLAN) is None


@pytest.mark.parametrize('component,field,value', [
    ('material', 'factor_year', 2020),
    ('material', 'checked_on', '2026-10-15'),
    ('transport', 'factor_year', 2025),
    ('transport', 'checked_on', '2026-10-14'),
])
def test_used_factor_metadata_must_agree_with_its_version(component, field, value):
    assessment = deepcopy(record())
    estimate = assessment['result']['suppliers'][0]['estimate']
    factor = (estimate['material']['factor'] if component == 'material'
              else estimate['transport']['legs'][0]['factor'])
    source_id = factor['source_id']
    version = estimate['factor_versions'][assessment['registry_version']][source_id]
    assert value != factor[field]
    version[field] = value

    with pytest.raises(ValueError, match='factor source'):
        validate_assessment(assessment)


@pytest.mark.parametrize('change', [
    lambda r: r['result']['suppliers'][0]['estimate'].update(total_kg_co2e='NaN'),
    lambda r: r['result']['suppliers'][0]['estimate'].update(total_kg_co2e='1e99'),
    lambda r: r['result']['suppliers'][0]['estimate']['material'].pop('status'),
    lambda r: r['result']['suppliers'][0]['estimate']['transport'].pop('legs'),
    lambda r: r['result'].pop('coverage'),
    lambda r: r['result']['suppliers'][0]['estimate'].pop('factor_versions'),
])
def test_identity_complete_but_unusable_comparison_is_rejected(change):
    value = deepcopy(record())
    change(value)
    with pytest.raises(ValueError):
        validate_assessment(value)


@pytest.mark.parametrize('company,material,unit,quantity,legs,mass', [
    ('solara-cables', 'copper', 'kg', '8000', LEGS['q1'], {}),
    ('terrapack', 'pet-resin', 'kg', '8000', LEGS['q1'], {}),
    ('solara-cables', 'polymer', 'kg', '8000', [], {}),
    ('meridian-pharma', 'vials', 'unit', '100000', LEGS['q1'],
     {'mass_kg': '2500', 'basis': 'Supplier packing list'}),
])
def test_valid_exact_proxy_unavailable_and_unit_mismatch_results_remain_supported(
        company, material, unit, quantity, legs, mass):
    plan = {'plan_id': 'plan-coverage', 'material': material, 'quantity_unit': unit,
            'offer_versions': {'q1': 1},
            'alternatives': [{'offer_id': 'q1', 'supplier': 'Synthetic supplier', 'quantity': quantity}]}
    comparison = compare_plan(load_registry(), plan, {'q1': legs}, {'q1': mass} if mass else {},
                              company_id=company)
    comparison['context_version'] = 3
    value = build_assessment(company_id=company, context_version=3, plan=plan, comparison=comparison,
                             legs_by_offer={'q1': legs}, mass_by_offer={'q1': mass} if mass else {},
                             created_at='2026-10-16T09:00:00+00:00')
    assert current_assessment(value, company_id=company, plan=plan) == value


def test_all_catalogue_lines_can_store_their_calculator_result():
    from procure.demo.company import get_company, list_companies

    registry = load_registry()
    count = 0
    for company in list_companies():
        company_id = company['company_id']
        for material in get_company(company_id)['materials']:
            plan = {'plan_id': 'plan-coverage', 'material': material['material_id'],
                    'quantity_unit': material['quantity_unit'], 'offer_versions': {'q1': 1},
                    'alternatives': [{'offer_id': 'q1', 'supplier': 'Synthetic supplier', 'quantity': '1'}]}
            comparison = compare_plan(registry, plan, {'q1': [{'mode': 'sea_container'}]},
                                      company_id=company_id)
            comparison['context_version'] = 3
            value = build_assessment(company_id=company_id, context_version=3, plan=plan,
                                     comparison=comparison, legs_by_offer={'q1': [{'mode': 'sea_container'}]},
                                     mass_by_offer={}, created_at='2026-10-16T09:00:00+00:00')
            assert current_assessment(value, company_id=company_id, plan=plan) == value
            count += 1
    assert count == 52
