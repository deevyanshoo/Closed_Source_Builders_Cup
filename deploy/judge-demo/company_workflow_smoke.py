#!/usr/bin/env python3
"""Observed hosted ten-company procurement acceptance. --live uses real Gemini."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import argparse, json, sys
from smoke import Client, key, require, denied_status, AcceptanceError, base_url
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'procurement-engine/src'))
from procure.demo.quote_service import fixture_review_values

def run(base, live):
    catalogue = Client(base).json('catalogue', '/api/demo/companies')['companies']
    require(len(catalogue) == 10, 'catalogue', 'company_count')
    rows = []
    c = Client(base)
    session = c.json('session', '/api/demo/session'); csrf = session['csrf_token']; context = session['context_version']
    for profile in catalogue:
        cid = profile['company_id']
        if cid != session['company']['company_id']:
            session = c.json(cid, '/api/demo/company', csrf=csrf, body={
                'company_id':cid,'expected_context_version':context,'idempotency_key':key()})
            context = session['context_version']
        def post(path, body):
            return c.json(cid, path, csrf=csrf, body={**body,'expected_context_version':context})
        offers = []
        provider_calls = []
        for fid in profile['quote_coverage']['fixture_ids']:
            result = post('/api/demo/extract', {'fixture_id':fid,'mode':'gemini' if live else 'rules','idempotency_key':key()})
            offer = result['offer']
            if live:
                require(result['provider']['call']=='real' and offer['extractor']=='gemini', cid, 'provider_not_real')
            provider_calls.append({'fixture_id':fid,'call':result['provider'].get('call'), 'model':result['provider'].get('model'),
                                   'document_id':offer['document']['document_id']})
            for field, value in fixture_review_values(fid).items():
                offer = post('/api/demo/offers/'+offer['offer_id']+'/correct', {
                    'field':field,'value':value,'expected_version':offer['version'],'idempotency_key':key()})['offer']
            offer = post('/api/demo/offers/'+offer['offer_id']+'/save', {
                'expected_version':offer['version'],'idempotency_key':key()})['offer']
            require(offer['status']=='saved' and not offer['blocking_reasons'], cid, 'review_not_saved')
            offers.append(offer)
        inputs = {k:v for k,v in profile['buyer_defaults'].items() if k not in ('offer_ids','expected_versions')}
        inputs = {k:v for k,v in inputs.items() if k not in ('quantity_kg','current_stock_kg','additional_cost_per_kg')}
        versions={o['offer_id']:o['version'] for o in offers}
        body={**inputs,'offer_ids':list(versions),'expected_versions':versions}
        plan=post('/api/demo/plan',body)
        unit=inputs['quantity_unit']
        require(plan['company_id']==cid and plan['quantity_unit']==unit and plan['selected_offer_id'],cid,'no_feasible_baseline')
        require(Decimal(plan['required_purchase'])==Decimal(inputs['quantity'])-Decimal(inputs['current_stock']),cid,'quantity_wrong')
        require(all(a['unit_price_per_unit'] is not None for a in plan['alternatives']),cid,'unit_price_missing')
        bad={**body,'quantity_unit':'kg' if unit!='kg' else 'barrel','expected_context_version':context}
        denied_status(c,cid,'/api/demo/plan',bad,csrf,422)
        delay_body={'baseline_plan_id':plan['plan_id'],'expected_versions':versions,
                    'delay_days_by_offer':{plan['selected_offer_id']:60},'idempotency_key':key()}
        recovery=post('/api/demo/recovery',delay_body)
        revised=recovery['scenario']
        require(recovery['outcome']=='changed' and revised['selected_offer_id']!=plan['selected_offer_id'],cid,'no_recovery')
        require(revised['quantity_unit']==unit and revised['company_id']==cid,cid,'recovery_context_wrong')
        draftbody={'plan_id':revised['plan_id'],'expected_versions':versions,'reviewed':True,'idempotency_key':key()}
        draft=post('/api/demo/rfq',draftbody)
        replay=post('/api/demo/rfq',draftbody)
        require(draft['draft_id']==replay['draft_id'],cid,'rfq_duplicate')
        require(draft['status']=='draft_unsent' and draft['quantity_unit']==unit and draft['company_id']==cid,cid,'rfq_wrong')
        require(draft['quantity']==plan['required_purchase'] and cid in draft['text'],cid,'rfq_quantity_or_buyer_wrong')
        require(c.text(cid,'/api/demo/rfq/'+draft['draft_id']+'/download')==draft['text'],cid,'download_wrong')
        operator = post('/api/demo/operator', {'message':'Inspect this company inventory', 'idempotency_key':key()})
        require(operator['status']=='completed' and operator['company_id']==cid,cid,'operator_unavailable')
        real_roles=[step for step in operator['agent_steps'] if step.get('provider_call')=='real']
        require(len(real_roles)==3,cid,'operator_roles_not_real')
        rows.append({'operator_status':operator['status'],'real_operator_roles':len(real_roles),'company_id':cid,'material':plan['material'],'unit':unit,'purchase':plan['required_purchase'],
                     'baseline_cost':plan['total_cost'],'recovery_cost':revised['total_cost'],'currency':plan['reporting_currency'],
                     'recovery':recovery['outcome'],'rfq_status':draft['status'],'provider_calls':provider_calls,
                     'unit_mismatch_rejected':True,'rfq_replay_same_draft':True})
        print(cid+' verified',flush=True)
    return {'ok':True,'base':base,'checked_at':datetime.now(timezone.utc).isoformat(),'live_gemini':live,'single_browser_session':True,'companies':rows}

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--base',required=True);p.add_argument('--live',action='store_true');p.add_argument('--output',required=True);a=p.parse_args()
    try:result=run(base_url(a.base),a.live)
    except AcceptanceError as exc:
        result={'ok':False,'stage':exc.stage,'code':exc.code,'status':exc.status}
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='companies'}))
    if not result['ok']:sys.exit(1)
