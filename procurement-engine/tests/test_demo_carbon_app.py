"""Hosted demo carbon estimate: read-only, scoped to the active plan, included in the export."""
from test_demo_rfq import demo  # noqa: F401  (fixture)


def legs(plan, mode='sea_container', km='12000'):
    return {plan['alternatives'][0]['offer_id']: [{'mode': mode, 'distance_km': km}]}


def test_factor_registry_is_readable_with_sources(demo):
    client, *_ = demo
    response = client.get('/api/demo/carbon/factors')
    assert response.status_code == 200
    body = response.json()
    assert body['registry_version'] == 'carbon-factors-v1'
    assert body['label'] == 'estimate, not a certified footprint'
    assert body['transport_modes']['sea_container']['factor'] == '0.01612'
    assert body['sources']['desnz-2026-full-set']['licence'] == 'Open Government Licence v3.0'


def test_estimate_uses_the_active_plan_and_is_exported(demo):
    client, headers, _offers, _request, plan = demo
    response = client.post('/api/demo/carbon/estimate', headers=headers,
                           json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['plan_id'] == plan['plan_id']
    assert result['material_id'] == 'copper'
    first = result['suppliers'][0]['estimate']
    # 8000 kg = 8 t; production 8 t x 3965 = 31720 (ICA copper cathode, cradle-to-gate)
    assert first['material']['kg_co2e'] == '31720'
    # transport 8 t x 12000 km x 0.01612 = 1547.52 kg CO2e
    assert first['transport']['kg_co2e'] == '1547.52'
    assert first['total_kg_co2e'] == '33267.52'
    exported = client.get('/api/demo/export').json()
    assert exported['carbon'] == result
    assert exported['carbon']['registry_version'] == 'carbon-factors-v1'


def test_estimate_for_an_old_plan_is_refused_and_dropped_from_export(demo):
    client, headers, _offers, request, plan = demo
    assert client.post('/api/demo/carbon/estimate', headers=headers,
                       json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}).status_code == 200
    newer = client.post('/api/demo/plan', headers=headers, json=request).json()
    assert client.get('/api/demo/export').json()['carbon'] is None
    stale = client.post('/api/demo/carbon/estimate', headers=headers,
                        json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)})
    assert stale.status_code == 409
    assert newer['plan_id'] != plan['plan_id']


def test_invalid_legs_are_rejected_without_csrf_or_with_bad_input(demo):
    client, headers, _offers, _request, plan = demo
    assert client.post('/api/demo/carbon/estimate', headers={'Origin': 'https://demo.test'},
                       json={'plan_id': plan['plan_id'], 'legs_by_offer': {}}).status_code == 403
    bad = client.post('/api/demo/carbon/estimate', headers=headers,
                      json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan, km='-5')})
    assert bad.status_code == 422
    unknown = client.post('/api/demo/carbon/estimate', headers=headers,
                          json={'plan_id': plan['plan_id'], 'legs_by_offer': {'not-in-plan': []}})
    assert unknown.status_code == 422
    extra = client.post('/api/demo/carbon/estimate', headers=headers,
                        json={'plan_id': plan['plan_id'], 'legs_by_offer': {}, 'company': 'x'})
    assert extra.status_code == 422


def test_deploy_package_ships_the_carbon_registry(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    destination = tmp_path / 'package'
    prepared = subprocess.run([sys.executable, str(repo / 'deploy/judge-demo/prepare_package.py'), str(destination)],
                              capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    packaged = destination / 'procurement-engine/config/carbon_factors_v1.json'
    assert json.loads(packaged.read_text())['registry_version'] == 'carbon-factors-v1'


def test_missing_registry_only_disables_carbon_routes(monkeypatch):
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from procure.demo import carbon_api
    from procure.demo.app import create_app

    def missing():
        raise FileNotFoundError('carbon_factors_v1.json')
    monkeypatch.setattr(carbon_api, 'load_registry', missing)
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)),
                    base_url='https://demo.test') as client:
        assert client.get('/api/demo/session').status_code == 200
        response = client.get('/api/demo/carbon/factors')
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'carbon_unavailable'
