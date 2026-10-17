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
    assert body['label'] == 'estimate from published average factors'
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


def test_estimate_is_scoped_to_the_session_company_and_reports_coverage(demo):
    client, headers, _offers, _request, plan = demo
    body = client.post('/api/demo/carbon/estimate', headers=headers,
                       json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}).json()
    assert body['company_id'] == 'solara-cables'
    assert body['coverage']['state'] == 'exact'
    assert body['suppliers'][0]['estimate']['combined_status'] == 'combined'
    # copper is stated in kg, so a shipping mass is refused rather than silently replacing the quantity
    refused = client.post('/api/demo/carbon/estimate', headers=headers, json={
        'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan),
        'mass_by_offer': {plan['alternatives'][0]['offer_id']: {'mass_kg': '10', 'basis': 'guess'}}})
    assert refused.status_code == 422


def test_assessment_get_and_export_retain_exact_post_result(demo):
    client, headers, _offers, _request, plan = demo
    before = client.get('/api/demo/carbon/assessment').json()
    assert before == {'company_id': 'solara-cables', 'context_version': 1, 'assessment': None}
    posted = client.post('/api/demo/carbon/estimate', headers=headers,
                         json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan),
                               'expected_context_version': 1})
    assert posted.status_code == 200, posted.text
    comparison = posted.json()
    assert comparison['company_id'] == 'solara-cables' and comparison['context_version'] == 1
    record = client.get('/api/demo/carbon/assessment').json()['assessment']
    assert record['schema'] == 'carbon-assessment.v1'
    assert record['plan_id'] == plan['plan_id']
    assert record['offer_versions'] == plan['offer_versions']
    assert record['inputs']['legs_by_offer'] == legs(plan)
    assert record['result'] == comparison
    assert client.get('/api/demo/export').json()['carbon'] == comparison


def test_old_assessment_is_unavailable_after_new_plan(demo):
    client, headers, _offers, request, plan = demo
    assert client.post('/api/demo/carbon/estimate', headers=headers,
                       json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}).status_code == 200
    assert client.post('/api/demo/plan', headers=headers, json=request).status_code == 200
    assert client.get('/api/demo/carbon/assessment').json()['assessment'] is None
    assert client.get('/api/demo/export').json()['carbon'] is None


def test_company_revisit_wraps_retained_assessment_in_new_context(demo):
    client, headers, _offers, _request, plan = demo
    posted = client.post('/api/demo/carbon/estimate', headers=headers,
                         json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}).json()
    first = client.get('/api/demo/carbon/assessment').json()['assessment']
    switched = client.post('/api/demo/company', headers=headers,
                           json={'company_id': 'terrapack', 'expected_context_version': 1,
                                 'idempotency_key': 'carbon-switch-away'})
    assert switched.status_code == 200, switched.text
    assert client.get('/api/demo/carbon/assessment').json() == {
        'company_id': 'terrapack', 'context_version': 2, 'assessment': None}
    assert client.get('/api/demo/export').json()['carbon'] is None
    back = client.post('/api/demo/company', headers=headers,
                       json={'company_id': 'solara-cables', 'expected_context_version': 2,
                             'idempotency_key': 'carbon-switch-back'})
    assert back.status_code == 200, back.text
    current = client.get('/api/demo/carbon/assessment').json()
    assert current['context_version'] == 3
    assert current['assessment'] == first
    assert current['assessment']['context_version'] == 1
    assert client.get('/api/demo/export').json()['carbon'] == posted


def test_cold_session_restore_reuses_exact_assessment_and_export():
    from datetime import datetime, timezone
    from fastapi.testclient import TestClient
    from procure.demo.app import create_app
    from procure.demo.session_persistence import MemorySessionPersistence

    backend = MemorySessionPersistence()
    now = lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)
    with TestClient(create_app(clock=now, session_backend=backend), base_url='https://demo.test') as first:
        view = first.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': view['csrf_token']}
        offered = first.post('/api/demo/extract', headers=headers,
                             json={'fixture_id': 'express-copper', 'mode': 'rules',
                                   'idempotency_key': 'carbon-cold-extract'}).json()['offer']
        saved = first.post(f"/api/demo/offers/{offered['offer_id']}/save", headers=headers,
                           json={'expected_version': offered['version'],
                                 'idempotency_key': 'carbon-cold-save'}).json()['offer']
        request = {'offer_ids': [saved['offer_id']], 'expected_versions': {saved['offer_id']: saved['version']},
                   'material': 'copper', 'quantity_kg': '10000', 'current_stock_kg': '2000',
                   'need_by': '2026-11-05', 'decision_date': '2026-10-14',
                   'reporting_currency': 'USD', 'budget': '100000', 'additional_cost_per_kg': '0.25'}
        planned = first.post('/api/demo/plan', headers=headers, json=request)
        assert planned.status_code == 200, planned.text
        plan = planned.json()
        posted = first.post('/api/demo/carbon/estimate', headers=headers,
                            json={'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)})
        assert posted.status_code == 200, posted.text
        comparison = posted.json()
        record = first.get('/api/demo/carbon/assessment').json()['assessment']

        with TestClient(create_app(clock=now, session_backend=backend), base_url='https://demo.test') as second:
            second.cookies.update(first.cookies)
            assert second.get('/api/demo/carbon/assessment').json()['assessment'] == record
            assert second.get('/api/demo/export').json()['carbon'] == comparison


def test_changed_saved_quote_fails_closed_for_read_export_and_reestimate(demo):
    client, headers, _offers, _request, plan = demo
    payload = {'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}
    assert client.post('/api/demo/carbon/estimate', headers=headers, json=payload).status_code == 200
    session_id = client.get('/api/demo/session').json()['session_id']
    value = client.app.state.sessions.get(session_id)
    offer_id = next(iter(plan['offer_versions']))
    with value.lock:
        value.offers[offer_id]['version'] += 1
    assert client.get('/api/demo/carbon/assessment').json()['assessment'] is None
    assert client.get('/api/demo/export').json()['carbon'] is None
    response = client.post('/api/demo/carbon/estimate', headers=headers, json=payload)
    assert response.status_code == 409 and response.json()['error']['code'] in ('stale_plan', 'stale_version')


def test_changed_authoritative_buyer_draft_fails_closed(demo):
    client, headers, _offers, _request, plan = demo
    payload = {'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}
    assert client.post('/api/demo/carbon/estimate', headers=headers, json=payload).status_code == 200
    session_id = client.get('/api/demo/session').json()['session_id']
    value = client.app.state.sessions.get(session_id)
    with value.lock:
        value.buyer_draft = {
            'material': 'copper', 'quantity': '10000', 'current_stock': '2000',
            'quantity_unit': 'kg', 'budget': '90000', 'additional_cost_per_unit': '0.25',
            'decision_date': '2026-10-14', 'need_by': '2026-11-05', 'reporting_currency': 'USD'}
    assert client.get('/api/demo/carbon/assessment').json()['assessment'] is None
    assert client.get('/api/demo/export').json()['carbon'] is None
    assert client.post('/api/demo/carbon/estimate', headers=headers, json=payload).status_code == 409


def test_malformed_in_memory_assessment_is_unavailable(demo):
    client, headers, _offers, _request, plan = demo
    payload = {'plan_id': plan['plan_id'], 'legs_by_offer': legs(plan)}
    assert client.post('/api/demo/carbon/estimate', headers=headers, json=payload).status_code == 200
    session_id = client.get('/api/demo/session').json()['session_id']
    value = client.app.state.sessions.get(session_id)
    with value.lock:
        value.carbon_assessment['inputs']['mass_by_offer'] = {'invalid': {1, 2}}
    assert client.get('/api/demo/carbon/assessment').json()['assessment'] is None
    assert client.get('/api/demo/export').json()['carbon'] is None
