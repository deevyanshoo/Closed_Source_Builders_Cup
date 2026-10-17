"""Carbon evidence survives a cold session restore and stays company scoped."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from procure.carbon import compare_plan, load_registry
from procure.carbon.assessment import build_assessment
from procure.demo.session_persistence import MemorySessionPersistence, StorageInvalid
from procure.demo.sessions import SessionStore


NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


def assessment(company='solara-cables'):
    plan = {'plan_id': 'plan-1', 'material': 'copper', 'quantity_unit': 'kg',
            'offer_versions': {'quote-1': 2},
            'alternatives': [{'offer_id': 'quote-1', 'supplier': 'Synthetic supplier', 'quantity': '8000'}]}
    comparison = compare_plan(load_registry(), plan, {}, company_id=company)
    comparison['context_version'] = 1
    return build_assessment(company_id=company, context_version=1, plan=plan,
                            comparison=comparison, legs_by_offer={}, mass_by_offer={},
                            created_at=NOW.isoformat())


def test_cold_restore_and_company_revisit_keep_exact_carbon_record():
    backend = MemorySessionPersistence()
    first = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    value = first.create()
    record = assessment()
    value.carbon_assessment = record
    value.select_company('terrapack')
    assert value.carbon_assessment is None
    first.save(value)

    restored = SessionStore(clock=lambda: NOW, persistence_backend=backend).get(value.session_id)
    assert restored.carbon_assessment is None
    assert restored.workspaces['solara-cables']['carbon_assessment'] == record
    restored.select_company('solara-cables')
    assert restored.carbon_assessment == record
    assert restored.carbon_assessment is not record


def test_legacy_session_without_carbon_field_restores_as_none():
    backend = MemorySessionPersistence()
    first = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    value = first.create()
    state = first._snapshot(value)
    state.pop('carbon_assessment', None)
    state['workspaces']['terrapack'] = {'offers': {}}
    backend.save(value.session_id, state, value.storage_version)
    restored = SessionStore(clock=lambda: NOW, persistence_backend=backend).get(value.session_id)
    assert restored.carbon_assessment is None
    restored.select_company('terrapack')
    assert restored.carbon_assessment is None


@pytest.mark.parametrize('malformed', [
    {'schema': 'unknown'},
    {**assessment(), 'company_id': 'another-company'},
    {**assessment(), 'result': {'plan_id': 'other', 'company_id': 'solara-cables'}},
    {**assessment(), 'result': {'plan_id': 'plan-1', 'company_id': 'solara-cables', 'x': 'z' * 70000}},
])
def test_malformed_or_oversized_carbon_record_is_rejected_before_save(malformed):
    first = SessionStore(clock=lambda: NOW, persistence_backend=MemorySessionPersistence())
    value = first.create()
    value.carbon_assessment = deepcopy(malformed)
    with pytest.raises(StorageInvalid):
        first._snapshot(value)


def test_reset_clears_carbon_record():
    store = SessionStore()
    value = store.create()
    value.carbon_assessment = assessment()
    store.invalidate(value)
    assert value.carbon_assessment is None


def test_pending_carbon_restores_only_in_its_original_company_and_session():
    backend = MemorySessionPersistence()
    store = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    value = store.create()
    value.pending_carbon = {'kind': 'material_selection', 'session_id': value.session_id, 'company_id': value.company_id,
        'context_version': value.context_version, 'plan_id': None,
        'offer_versions': {}, 'expires_at': '2026-10-16T00:10:00+00:00',
        'material_ids': ['copper'], 'targets': [], 'legs_by_offer': {}, 'mass_by_offer': {}}
    store.save(value)
    restored = SessionStore(clock=lambda: NOW, persistence_backend=backend).get(value.session_id)
    assert restored.pending_carbon == value.pending_carbon
    restored.select_company('terrapack')
    assert restored.pending_carbon is None


@pytest.mark.parametrize('change', [
    lambda row: row.update(expires_at='2099-01-01T00:00:00+00:00'),
    lambda row: row.update(expires_at='2026-10-16T00:10:00+05:30'),
    lambda row: row.update(plan_id='foreign-plan'),
    lambda row: row.update(legs_by_offer={'foreign': [{'mode': 'road_hgv', 'distance_km': '10'}]}),
    lambda row: row.update(material_ids=['foreign-material']),
])
def test_malformed_pending_carbon_fails_closed(change):
    store = SessionStore(clock=lambda: NOW, persistence_backend=MemorySessionPersistence())
    value = store.create()
    value.pending_carbon = {'kind': 'material_selection', 'session_id': value.session_id,
        'company_id': value.company_id, 'context_version': value.context_version,
        'plan_id': None, 'offer_versions': {}, 'expires_at': '2026-10-16T00:10:00+00:00',
        'material_ids': ['copper'], 'targets': [], 'legs_by_offer': {}, 'mass_by_offer': {}}
    change(value.pending_carbon)
    with pytest.raises(StorageInvalid):
        store._snapshot(value)
