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
