"""Hosted demo scheme finder: company-scoped, read-only, exported with its inputs."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app


@pytest.fixture
def client():
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 15, tzinfo=timezone.utc)),
                    base_url='https://demo.test') as value:
        token = value.get('/api/demo/session').json()['csrf_token']
        yield value, {'Origin': 'https://demo.test', 'X-CSRF-Token': token}


def test_schemes_for_the_current_company_are_exported(client):
    value, headers = client
    response = value.post('/api/demo/carbon/schemes', headers=headers, json={
        'inputs_by_scheme': {'in-sidbi-green-finance': {'loan_amount': '1000000', 'current_rate_percent': '10',
                                                        'offered_rate_percent': '9'}}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['company_id'] == 'solara-cables'
    assert body['label'] == 'indicative, not financial or legal advice'
    sidbi = next(row for row in body['matches'] if row['scheme_id'] == 'in-sidbi-green-finance')
    assert sidbi['eligibility'] == 'possibly eligible, check conditions'
    assert (sidbi['benefit']['amount'], sidbi['benefit']['direction']) == ('10000', 'interest_reduction')
    zed = next(row for row in body['matches'] if row['scheme_id'] == 'in-msme-zed')
    assert zed['benefit_status'] == 'unavailable' and zed['benefit'] is None
    assert value.get('/api/demo/export').json()['carbon_schemes'] == body


def test_company_switch_drops_the_old_result_from_export(client):
    value, headers = client
    assert value.post('/api/demo/carbon/schemes', headers=headers, json={'inputs_by_scheme': {}}).status_code == 200
    switched = value.post('/api/demo/company', headers=headers, json={'company_id': 'forgeline-components',
                                                                       'expected_context_version': 1,
                                                                       'idempotency_key': 'switchcompany'})
    assert switched.status_code == 200, switched.text
    assert value.get('/api/demo/export').json()['carbon_schemes'] is None
    version = switched.json()['context_version']
    empty = value.post('/api/demo/carbon/schemes', headers=headers,
                       json={'inputs_by_scheme': {}, 'expected_context_version': version})
    assert empty.json()['company_id'] == 'forgeline-components'
    assert [row['scheme_id'] for row in empty.json()['matches']] == ['mx-ets-pilot']


def test_bad_inputs_and_missing_csrf_are_rejected(client):
    value, headers = client
    assert value.post('/api/demo/carbon/schemes', headers={'Origin': 'https://demo.test'},
                      json={'inputs_by_scheme': {}}).status_code == 403
    assert value.post('/api/demo/carbon/schemes', headers=headers,
                      json={'inputs_by_scheme': {'eu-cbam': {}}}).status_code == 422
    assert value.post('/api/demo/carbon/schemes', headers=headers,
                      json={'inputs_by_scheme': {'in-msme-zed': {'certification_cost': '-3'}}}).status_code == 422


def test_deploy_package_ships_the_scheme_registry(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    destination = tmp_path / 'package'
    prepared = subprocess.run([sys.executable, str(repo / 'deploy/judge-demo/prepare_package.py'), str(destination)],
                              capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    packaged = destination / 'procurement-engine/config/carbon_schemes_v1.json'
    assert json.loads(packaged.read_text())['registry_version'] == 'carbon-schemes-v1'


def test_a_to_b_to_a_never_brings_back_the_old_scheme_result(client):
    value, headers = client
    first = value.post('/api/demo/carbon/schemes', headers=headers, json={'inputs_by_scheme': {}}).json()
    assert first['context_version'] == 1
    away = value.post('/api/demo/company', headers=headers, json={'company_id': 'harvest-foods', 'expected_context_version': 1,
                                                                   'idempotency_key': 'switch-away'}).json()
    back = value.post('/api/demo/company', headers=headers, json={
        'company_id': 'solara-cables', 'expected_context_version': away['context_version'], 'idempotency_key': 'switch-back'})
    assert back.status_code == 200, back.text
    assert value.get('/api/demo/export').json()['carbon_schemes'] is None
    again = value.post('/api/demo/carbon/schemes', headers=headers, json={
        'inputs_by_scheme': {}, 'expected_context_version': back.json()['context_version']}).json()
    assert again['context_version'] == back.json()['context_version']
    assert value.get('/api/demo/export').json()['carbon_schemes'] == again


def test_stored_schemes_are_safely_absent_after_a_session_state_round_trip():
    from procure.demo.carbon_api import schemes_export
    from procure.demo.sessions import SessionStore
    store = SessionStore(clock=lambda: datetime(2026, 10, 15, tzinfo=timezone.utc))
    session = store.create()
    session.carbon_schemes = {'company_id': session.company_id, 'context_version': 1, 'result': {'matches': []}}
    restored = SessionStore._restore(session.session_id, SessionStore._snapshot(session), None,
                                     now=datetime(2026, 10, 15, tzinfo=timezone.utc))
    restored = restored[0] if isinstance(restored, tuple) else restored
    assert getattr(restored, 'carbon_schemes', None) is None and schemes_export(restored) is None
