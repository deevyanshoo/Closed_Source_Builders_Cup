"""A single bounded visit can operate every company without resetting its state."""
from datetime import datetime, timezone
from copy import deepcopy
from fastapi.testclient import TestClient
from procure.demo import quote_service
from procure.demo.app import create_app
from test_demo_operator import Provider

NOW=datetime(2026,10,14,tzinfo=timezone.utc)

def test_one_visit_can_extract_review_plan_recover_and_draft_for_all_ten(monkeypatch):
    # Stub only the provider boundary; explicit marker is a test receipt, not live proof.
    read=quote_service.extract_fixture
    calls=[]
    def successful_provider(fixture_id, *, mode, as_of):
        assert mode=='gemini'
        calls.append(fixture_id)
        payload=read(fixture_id,mode='rules',as_of=as_of)
        payload['extractor']='gemini'
        payload['extractor_detail']={'requested':'gemini','call':'real','model':'offline-test-stub'}
        return payload
    monkeypatch.setattr(quote_service,'extract_fixture',successful_provider)
    monkeypatch.delenv('OFFLINE',raising=False)
    with TestClient(create_app(clock=lambda:NOW,operator_provider=Provider(intent='inspect',action='none',evidence=(),material=None)),base_url='https://demo.test') as client:
        session=client.get('/api/demo/session').json()
        session_id=session['session_id']
        headers={'Origin':'https://demo.test','X-CSRF-Token':session['csrf_token']}
        context=1; sequence=0; first_plan=None; first_rfq=None
        def post(path, data, *, key=True):
            nonlocal sequence
            sequence+=1
            body={**data,'expected_context_version':context}
            if key:body['idempotency_key']=str(sequence)
            response=client.post('/api/demo'+path,headers=headers,json=body)
            assert response.status_code==200,response.text
            return response.json()
        companies=client.get('/api/demo/companies').json()['companies']
        for profile in companies:
            if profile['company_id']!=session['company']['company_id']:
                session=post('/company',{'company_id':profile['company_id']})
                context=session['context_version']
            offers=[]
            for fid in profile['quote_coverage']['scenarios'][0]['fixture_ids']:
                offer=post('/extract',{'fixture_id':fid,'mode':'gemini'})['offer']
                for field,value in quote_service.fixture_review_values(fid).items():
                    offer=post('/offers/'+offer['offer_id']+'/correct',{'field':field,'value':value,'expected_version':offer['version']})['offer']
                offer=post('/offers/'+offer['offer_id']+'/save',{'expected_version':offer['version']})['offer']
                offers.append(offer)
            versions={o['offer_id']:o['version'] for o in offers}
            plan=post('/plan',{**profile['buyer_defaults'],'offer_ids':list(versions),'expected_versions':versions},key=False)
            assert plan['company_id']==profile['company_id'] and plan['selected_offer_id']
            recovered=post('/recovery',{'baseline_plan_id':plan['plan_id'],'expected_versions':versions,'delay_days_by_offer':{plan['selected_offer_id']:20}})
            assert recovered['selection_changed']
            draft=post('/rfq',{'plan_id':recovered['scenario']['plan_id'],'expected_versions':versions,'reviewed':True})
            assert draft['company_id']==profile['company_id'] and draft['status']=='draft_unsent'
            if profile['company_id']=='solara-cables':
                owned=client.app.state.sessions.get(session_id).drafts
                first_rfq=deepcopy(next(row for row in owned if row.get('draft_id')==draft['draft_id']))
            # Ten distinct operator turns also fit one visit, without applying actions.
            result=post('/operator',{'message':'Inspect company inventory'})
            assert result['status']=='completed' and result['company_id']==profile['company_id']
            if first_plan is None:first_plan=recovered['scenario']['plan_id']
        assert len(calls)==30 and len(set(calls))==30
        restored=post('/company',{'company_id':'solara-cables'})
        assert restored['plan']['plan_id']==first_plan
        assert restored['rfq'] is None
        assert all(row['draft_id']!=first_rfq['draft_id'] for row in restored['drafts'])
        assert client.get(f"/api/demo/rfq/{first_rfq['draft_id']}/download").status_code==404
        stored=client.app.state.sessions.get(session_id)
        retained=[row for row in stored.drafts if row.get('draft_id')==first_rfq['draft_id']]
        assert retained==[first_rfq]
