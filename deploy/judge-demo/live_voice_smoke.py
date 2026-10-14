#!/usr/bin/env python3
"""Real hosted native-audio acceptance using an owned synthetic PCM speech fixture."""
import argparse, asyncio, base64, json, time, urllib.request, wave
from pathlib import Path
from urllib.parse import urlsplit
from smoke import Client, base_url, require
from websockets.asyncio.client import connect

async def run(base, audio_path, output_audio, *, rfq=False):
    c=Client(base); session=c.json('session','/api/demo/session')
    purchase=None
    if rfq:
        from procure.demo.quote_service import fixture_review_values
        from smoke import key
        session=c.json('switch','/api/demo/company',csrf=session['csrf_token'],body={
            'company_id':'atlas-petrochem','expected_context_version':session['context_version'],'idempotency_key':key()})
        profile=session['company']; csrf=session['csrf_token']; context=session['context_version']
        def post(path,body):return c.json('rfq prep',path,csrf=csrf,body={**body,'expected_context_version':context})
        offers=[]
        for fid in profile['quote_coverage']['scenarios'][0]['fixture_ids']:
            offer=post('/api/demo/extract',{'fixture_id':fid,'mode':'rules','idempotency_key':key()})['offer']
            for field,value in fixture_review_values(fid).items():
                offer=post('/api/demo/offers/'+offer['offer_id']+'/correct',{'field':field,'value':value,'expected_version':offer['version'],'idempotency_key':key()})['offer']
            offer=post('/api/demo/offers/'+offer['offer_id']+'/save',{'expected_version':offer['version'],'idempotency_key':key()})['offer']
            offers.append(offer)
        versions={o['offer_id']:o['version'] for o in offers}
        inputs={k:v for k,v in profile['buyer_defaults'].items() if k not in ('quantity_kg','current_stock_kg','additional_cost_per_kg')}
        purchase=post('/api/demo/plan',{**inputs,'offer_ids':list(versions),'expected_versions':versions})
        require(bool(purchase['selected_offer_id']),'plan','no_selection')
    jar=next(h.cookiejar for h in c.opener.handlers if isinstance(h,urllib.request.HTTPCookieProcessor))
    cookie='; '.join(x.name+'='+x.value for x in jar)
    with wave.open(str(audio_path),'rb') as wav:
        require(wav.getnchannels()==1 and wav.getsampwidth()==2 and wav.getframerate()==16000,'fixture','invalid_audio')
        pcm=wav.readframes(wav.getnframes())
    started=time.monotonic(); ready=None; speech_end=None; first_audio=None; first_verified_audio=None
    input_text='';output_text='';audio=bytearray();tool=None;events=[]
    async with connect('wss://'+urlsplit(base).netloc+'/api/demo/voice/live',
                       origin=base,additional_headers={'Cookie':cookie},max_size=200000,open_timeout=20) as ws:
        await ws.send(json.dumps({'type':'start','csrf_token':session['csrf_token'],'expected_context_version':session['context_version']}))
        data=json.loads(await asyncio.wait_for(ws.recv(),20)); require(data['type']=='ready','ready','live_unavailable');ready=time.monotonic()
        async def send():
            nonlocal speech_end
            for index in range(0,len(pcm),3200):
                await ws.send(json.dumps({'type':'audio','data':base64.b64encode(pcm[index:index+3200]).decode()}))
                await asyncio.sleep(.1)
            speech_end=time.monotonic()
            await ws.send(json.dumps({'type':'audio_end'}))
        sender=asyncio.create_task(send())
        try:
            async with asyncio.timeout(60):
                while True:
                    event=json.loads(await ws.recv());kind=event['type'];events.append(kind)
                    if kind=='error':raise RuntimeError('sanitized_live_error:'+str(event.get('code')))
                    if kind=='audio':
                        decoded=base64.b64decode(event['data'],validate=True);audio.extend(decoded)
                        if first_audio is None:first_audio=time.monotonic()
                        if tool is not None and first_verified_audio is None:first_verified_audio=time.monotonic()
                    elif kind=='transcript':
                        if event['direction']=='input':input_text+=event['text']
                        else:output_text+=event['text']
                    elif kind=='tool_result':
                        tool=event['result']; require(tool['status']==('proposal' if rfq else 'completed') and tool['read_only'] is True,'operator','unverified_turn')
                        actual=[s for s in tool['agent_steps'] if s.get('provider_call')=='real']
                        require(len(actual)==3,'operator','roles_not_real')
                    # An early spoken acknowledgement is not the verified
                    # business answer. Wait for audio after the tool result.
                    elif kind=='turn_complete' and tool is not None and first_verified_audio is not None:break
            await sender
            await ws.send(json.dumps({'type':'stop'}))
        finally:
            sender.cancel()
    require(('rfq' in input_text.lower() or 'quotation' in input_text.lower()) if rfq else 'inventory' in input_text.lower(),'transcription','wrong_intent')
    action=None
    if rfq:
        require(tool.get('proposal',{}).get('kind')=='rfq','proposal','wrong_action')
        require(c.json('before confirm','/api/demo/session').get('rfq') is None,'proposal','action_before_review')
        body={'proposal_id':tool['proposal']['proposal_id'],'reviewed':True,'expected_context_version':session['context_version'],'idempotency_key':key()}
        action=c.json('confirm','/api/demo/operator/confirm',csrf=session['csrf_token'],body=body)
        replay=c.json('replay','/api/demo/operator/confirm',csrf=session['csrf_token'],body=body)
        require(action['result']['status']=='draft_unsent' and action['result']['quantity_unit']=='barrel','confirm','wrong_draft')
        require(action['result']['quantity']==purchase['required_purchase'],'confirm','wrong_quantity')
        require(action['result']['draft_id']==replay['result']['draft_id'],'confirm','duplicate_draft')
    with wave.open(str(output_audio),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(audio)
    return {'ok':True,'base':base,'native_provider':'gemini-3.8-live','voice':'Charon',
            'synthetic_microphone_input':True,'physical_microphone_test':False,'input_bytes':len(pcm),'output_bytes':len(audio),
            'connect_seconds':round(ready-started,3),'first_audio_after_speech_end_seconds':round(first_audio-speech_end,3),
            'verified_audio_after_speech_end_seconds':round(first_verified_audio-speech_end,3) if first_verified_audio else None,
            'transcript':input_text,'spoken_answer':output_text,'operator_status':tool['status'],'reviewed_business_action':({'kind':action['kind'],'status':action['result']['status'],'company_id':action['result']['company_id'],'quantity':action['result']['quantity'],'unit':action['result']['quantity_unit'],'replay_same_draft':True} if action else None),'agent_steps':tool['agent_steps'],
            'event_counts':{x:events.count(x) for x in set(events)},'provider_audio_file':str(output_audio)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rfq',action='store_true');p.add_argument('--base',required=True);p.add_argument('--input',required=True);p.add_argument('--audio-output',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    try:result=asyncio.run(run(base_url(a.base),Path(a.input),Path(a.audio_output),rfq=a.rfq))
    except Exception as e:result={'ok':False,'error_type':type(e).__name__,'code':str(e)[:120]}
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('spoken_answer','agent_steps')}))
    if not result['ok']:raise SystemExit(1)
