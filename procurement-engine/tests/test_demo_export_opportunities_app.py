"""Export opportunities mounted in the hosted demo app and included in the JSON export (#131)."""
from test_demo_rfq import demo  # noqa: F401  (fixture)


def test_routes_are_mounted_with_csrf_and_the_ranking_is_exported(demo):
    client, headers, *_ = demo
    view = client.get('/api/demo/export-opportunities').json()
    assert view['status'] == 'available' and view['company_id'] == 'solara-cables'
    code = view['markets'][0]['code']
    payload = {'market_code': code, 'inputs': {'unit_cost_per_kg': '1'}}
    assert client.post('/api/demo/export-opportunities/margin', json=payload).status_code == 403
    margin = client.post('/api/demo/export-opportunities/margin', headers=headers, json=payload)
    assert margin.status_code == 200, margin.text
    assert margin.json()['margin']['status'] == 'unavailable'
    exported = client.get('/api/demo/export').json()['export_opportunities']
    assert exported['schema'] == 'export-opportunities-export.v1'
    assert exported['company_id'] == 'solara-cables' and exported['markets'] == view['markets']
