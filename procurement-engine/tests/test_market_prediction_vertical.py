"""Reference forecast and operations preview boundaries."""
from fastapi.testclient import TestClient
from procure.demo.app import create_app
from procure.demo.company import list_companies
from procure.demo.market_prediction import market_read_model, influence_for_material
from procure.demo.operations_decision import preview_from_snapshot
from procure.demo import market_prediction as market_module
from copy import deepcopy


def test_reference_is_verified_and_all_catalog_materials_have_explicit_influence():
    market = market_read_model()
    assert market['status'] == 'REFERENCE_REPLAY'
    assert market['forecast_count'] == 18
    assert market['artifact_role_count'] == 27
    assert market['anchor_market_session_date'] == '2026-10-16'
    assert all(cell['target_market_session_date'] is None for cell in market['forecasts'])
    assert all(not cell['current_outcomes_observed'] for cell in market['forecasts'])
    rows = [influence_for_material(c, m['material_id'], market) for c in list_companies() for m in c['materials']]
    assert len(rows) == 52
    assert all(row['status'] in {'CONTEXT_ONLY', 'UNSUPPORTED', 'STALE', 'UNAVAILABLE'} for row in rows)
    assert not any(row['direct_joint_cost_match'] for row in rows)
    assert influence_for_material(list_companies()[0], 'copper', market)['status'] == 'CONTEXT_ONLY'


def test_all_52_company_scenarios_preserve_units_and_block_missing_facts():
    market = market_read_model()
    outcomes = []
    for company in list_companies():
        for material in company['materials']:
            snapshot = {'company_id': company['company_id'], 'context_version': 1,
                        'company': company, 'plan': None}
            outcome = preview_from_snapshot(snapshot, material['material_id'], market)
            assert outcome['quantity_unit'] == material['quantity_unit']
            assert outcome['recommendation'] is None
            assert outcome['market_influence']['status'] in {'CONTEXT_ONLY', 'UNSUPPORTED'}
            outcomes.append(outcome)
    assert len(outcomes) == 52


def test_hypothetical_override_is_bounded_and_cannot_become_an_order():
    company = list_companies()[0]
    snapshot = {'company_id': company['company_id'], 'context_version': 1,
                'company': company, 'plan': None}
    assumptions = {'stock': '30', 'daily_demand': '10', 'safety': '10',
                   'start': '2026-10-19', 'end': '2026-10-25',
                   'receipts': [{'arrival_date': '2026-10-21', 'quantity': '20', 'firm': True},
                                {'arrival_date': '2026-10-23', 'quantity': '80', 'firm': True}]}
    result = preview_from_snapshot(snapshot, 'copper', market_read_model(), overrides=assumptions)
    assert result['status'] == 'REPLAN_REQUIRED'
    assert result['service_trace']['first_gap_date'] == '2026-10-21'
    assert result['recommendation'] is None
    assert 'HYPOTHETICAL' in result['evidence']['source']


def test_accepted_quote_is_not_a_firm_usable_receipt():
    company = list_companies()[0]
    plan = {'plan_id':'p','material':'copper','current_stock':'2000',
            'required_purchase':'10000','decision_date':'2026-10-14','need_by':'2026-10-20',
            'selected_offer_id':'q','alternatives':[{'offer_id':'q','arrival_date':'2026-10-14'}],
            'offer_versions':{'q':1},'reporting_currency':'USD','total_cost':'90000',
            'supply_coverage':{'status':'covered','missing_inputs':[]}}
    snapshot = {'company_id':company['company_id'],'context_version':1,'company':company,'plan':plan}
    result = preview_from_snapshot(snapshot,'copper',market_read_model())
    assert result['status'] == 'REPLAN_REQUIRED'
    assert 'firm_usable_receipt' in result['missing_inputs']
    assert all(day['firm_receipts_usable']=='0' for day in result['service_trace']['trace'])


def test_remote_failure_uses_only_same_bucket_last_verified_reference(monkeypatch):
    raw = deepcopy(market_module._reference())
    raw['run_id'] = 'reference-test'
    raw['publication_mode'] = 'REFERENCE_REPLAY'
    raw['job_image_digest'] = 'sha256:test'
    for cell in raw['forecasts']:
        cell['run_id'] = raw['run_id']
    class Good:
        def __init__(self, bucket): pass
        def read_latest(self):
            return raw, {'snapshot':{'sha256':'a'*64}}, '7'
    monkeypatch.setenv('ZOORQ_PREDICTION_BUCKET','bucket-a')
    monkeypatch.setattr(market_module,'GcsMarketStore',Good)
    market_module._remote_cache.update(bucket=None,until=0,value=None,last_good=None)
    assert market_read_model()['delivery_state'] == 'VERIFIED_GCS_REFERENCE'
    class Failed:
        def __init__(self, bucket): raise OSError('offline')
    monkeypatch.setattr(market_module,'GcsMarketStore',Failed)
    market_module._remote_cache['until'] = 0
    assert market_read_model()['delivery_state'] == 'LAST_VERIFIED_GCS_REMOTE_UNAVAILABLE'
    monkeypatch.setenv('ZOORQ_PREDICTION_BUCKET','bucket-b')
    assert market_read_model()['delivery_state'] == 'BUNDLED_REFERENCE_REMOTE_UNAVAILABLE'
    market_module._remote_cache.update(bucket=None,until=0,value=None,last_good=None)


def test_preview_requires_context_and_never_invents_executable_recommendation():
    client = TestClient(create_app(), base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    assert client.get('/api/demo/v1/market-forecasts').json()['forecast_count'] == 18
    body = {'expected_context_version': 1, 'idempotency_key': 'preview-a', 'material_id': 'copper'}
    headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
    assert client.post('/api/demo/v1/company-decisions/preview', json=body, headers={**headers, 'X-CSRF-Token': 'wrong'}).status_code == 403
    response = client.post('/api/demo/v1/company-decisions/preview', json=body, headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['company_id'] == session['company']['company_id']
    assert result['status'] in {'MISSING_OPERATIONS_INPUT', 'REPLAN_REQUIRED'}
    assert result['recommendation'] is None
    assert result['market_influence']['status'] == 'CONTEXT_ONLY'
    assert client.post('/api/demo/v1/company-decisions/preview', json=body, headers=headers).json()['decision_id'] == result['decision_id']
    body['expected_context_version'] = 2
    assert client.post('/api/demo/v1/company-decisions/preview', json=body, headers=headers).status_code == 409


def test_preview_api_accepts_bounded_what_if_and_rejects_foreign_material():
    client = TestClient(create_app(), base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    headers = {'Origin':'https://demo.test','X-CSRF-Token':session['csrf_token']}
    assumptions = {'stock':'30','daily_demand':'10','safety':'10','start':'2026-10-19',
                   'end':'2026-10-25','receipts':[{'arrival_date':'2026-10-21','quantity':'20','firm':True}]}
    body = {'expected_context_version':1,'idempotency_key':'whatif-1','material_id':'copper','overrides':assumptions}
    response = client.post('/api/demo/v1/company-decisions/preview',json=body,headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'REPLAN_REQUIRED'
    assert response.json()['recommendation'] is None
    assert client.get('/api/demo/session').json()['plan'] is None
    body['idempotency_key'] = 'whatif-2'
    body['material_id'] = 'copper-foil'
    assert client.post('/api/demo/v1/company-decisions/preview',json=body,headers=headers).status_code == 404
    body['material_id'] = 'copper'
    body['overrides']['cash_limit'] = '0'
    assert client.post('/api/demo/v1/company-decisions/preview',json=body,headers=headers).status_code == 422
