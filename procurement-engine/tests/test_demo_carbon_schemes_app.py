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
        'inputs_by_scheme': {'in-msme-zed': {'enterprise_category': 'medium', 'certification_cost': '100000'}}})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['company_id'] == 'solara-cables'
    assert body['label'] == 'indicative, not financial or legal advice'
    zed = next(row for row in body['matches'] if row['scheme_id'] == 'in-msme-zed')
    assert zed['eligibility'] == 'possibly eligible, check conditions'
    assert zed['saving']['amount'] == '50000'
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
    assert empty.json()['matches'] == [] and 'not evidence that no scheme exists' in empty.json()['no_match_reason']


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
