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
    # Copper has no registered production factor: shown as unavailable, never guessed.
    assert first['material']['status'] == 'factor_unavailable'
    # 8000 kg = 8 t; 8 t x 12000 km x 0.01612 = 1547.52 kg CO2e
    assert first['transport']['kg_co2e'] == '1547.52'
    assert first['total_kg_co2e'] is None
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
