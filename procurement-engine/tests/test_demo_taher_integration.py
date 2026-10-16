"""Shared hosted emissions, shipment facts and reviewed actions."""
from test_demo_rfq import demo  # noqa: F401
from procure.demo import live_voice


def estimate(client, headers, plan):
    return client.post('/api/demo/carbon/estimate', headers=headers, json={
        'plan_id': plan['plan_id'], 'expected_context_version': 1,
        'legs_by_offer': {r['offer_id']: [{'mode': 'sea_container', 'distance_km': '1000'}]
                          for r in plan['alternatives']}})


def ask(client, headers, text, key):
    return client.post('/api/demo/operator', headers=headers, json={
        'message': text, 'idempotency_key': key, 'expected_context_version': 1})


def test_company_shipments_are_owned_and_link_current_plan(demo):
    client, headers, offers, request, plan = demo
    response = client.get('/api/demo/dispatch')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['company_id'] == 'solara-cables' and body['context_version'] == 1
    linked = [r for r in body['shipments'] if r['recovery_available']]
    assert len(linked) == 1
    row = linked[0]
    assert row['plan_id'] == plan['plan_id'] and row['offer_id'] == plan['selected_offer_id']
    assert row['quantity'] == '8000' and row['order_demand_quantity'] == '10000'
    assert row['source'] == 'synthetic_plan_demand_scenario'
    detail = client.get('/api/demo/dispatch/' + row['shipment_id'])
    assert detail.status_code == 200 and detail.json()['shipment'] == row
    assert client.get('/api/demo/dispatch/foreign-shipment').status_code == 404


def test_linked_recovery_rejects_foreign_shipment_without_mutation(demo):
    client, headers, offers, request, plan = demo
    before = client.get('/api/demo/session').json()
    response = client.post('/api/demo/recovery', headers=headers, json={
        'baseline_plan_id': plan['plan_id'], 'expected_versions': plan['offer_versions'],
        'delay_days_by_offer': {plan['selected_offer_id']: 20},
        'shipment_id': 'foreign-shipment', 'idempotency_key': 'foreign-shipment',
        'expected_context_version': 1})
    assert response.status_code in (404, 409), response.text
    after = client.get('/api/demo/session').json()
    assert before['plan'] == after['plan'] and after['recovery'] is None


def test_current_carbon_and_shipments_are_deterministic_text_reads(demo):
    client, headers, offers, request, plan = demo
    assert estimate(client, headers, plan).status_code == 200
    for text, term in [('What are the emissions for this purchase?', '31720'),
                       ('Show me the shipments for this company', '8000')]:
        result = ask(client, headers, text, term)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['status'] == 'completed' and body['read_only'] is True, body
        assert term in body['assistant_text'] and body['proposal'] is None
        assert all(step['provider_call'] == 'none' for step in body['agent_steps'])
    assert client.get('/api/demo/session').json()['recovery'] is None


def test_typed_shipment_delay_prepares_reviewed_recovery_and_unsent_rfq(demo):
    client, headers, offers, request, baseline = demo
    assert estimate(client, headers, baseline).status_code == 200
    response = client.get('/api/demo/dispatch')
    assert response.status_code == 200, response.text
    shipment = next(r for r in response.json()['shipments'] if r['recovery_available'])
    response = ask(client, headers,
        f"Prepare recovery for shipment {shipment['shipment_id']} delayed by 20 days", 'shipment-proposal')
    assert response.status_code == 200, response.text
    proposal = response.json()['proposal']
    assert proposal and proposal['kind'] == 'recovery'
    assert proposal['parameters']['shipment_id'] == shipment['shipment_id']
    assert client.get('/api/demo/session').json()['recovery'] is None
    confirm = {'proposal_id': proposal['proposal_id'], 'reviewed': True,
               'expected_context_version': 1, 'idempotency_key': 'confirm-shipment'}
    applied = client.post('/api/demo/operator/confirm', headers=headers, json=confirm)
    assert applied.status_code == 200, applied.text
    assert applied.json()['status'] == 'applied'
    saved = client.get('/api/demo/session').json()
    assert saved['recovery']['baseline']['plan_id'] == baseline['plan_id']
    assert saved['recovery']['scenario']['selected_offer_id'] != baseline['selected_offer_id']
    assert client.get('/api/demo/export').json()['carbon'] is None
    assert client.post('/api/demo/operator/confirm', headers=headers, json=confirm).status_code == 200
    active = saved['plan']
    draft = client.post('/api/demo/rfq', headers=headers, json={
        'plan_id': active['plan_id'], 'expected_versions': active['offer_versions'],
        'reviewed': True, 'expected_context_version': 1, 'idempotency_key': 'shipment-rfq'})
    assert draft.status_code == 200 and draft.json()['status'] == 'draft_unsent'
    assert client.get('/api/demo/session').json()['simulated_outbox'] == []


def test_native_voice_declares_grounded_integration_tools():
    config = live_voice._config()
    tools = {tool.name for tool in config.tools[0].function_declarations}
    assert {'read_carbon', 'read_shipments', 'prepare_shipment_recovery'} <= tools
