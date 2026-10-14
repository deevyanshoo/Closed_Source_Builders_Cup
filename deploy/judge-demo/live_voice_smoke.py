#!/usr/bin/env python3
"""Real hosted native-audio acceptance using an owned synthetic PCM speech fixture."""
import argparse, asyncio, base64, json, time, urllib.request, wave
from pathlib import Path
from urllib.parse import urlsplit
from smoke import Client, base_url, require
from websockets.asyncio.client import connect

async def run(base, audio_path, output_audio):
    c=Client(base); session=c.json('session','/api/demo/session')
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
                        tool=event['result']; require(tool['status']=='completed' and tool['read_only'] is True,'operator','unverified_turn')
                        actual=[s for s in tool['agent_steps'] if s.get('provider_call')=='real']
                        require(len(actual)==3,'operator','roles_not_real')
                    elif kind=='turn_complete' and tool is not None and audio:break
            await sender
            await ws.send(json.dumps({'type':'stop'}))
        finally:
            sender.cancel()
    require('inventory' in input_text.lower(),'transcription','wrong_intent')
    with wave.open(str(output_audio),'wb') as out:
        out.setnchannels(1);out.setsampwidth(2);out.setframerate(24000);out.writeframes(audio)
    return {'ok':True,'base':base,'native_provider':'gemini-3.8-live','voice':'Charon',
            'synthetic_microphone_input':True,'physical_microphone_test':False,'input_bytes':len(pcm),'output_bytes':len(audio),
            'connect_seconds':round(ready-started,3),'first_audio_after_speech_end_seconds':round(first_audio-speech_end,3),
            'verified_audio_after_speech_end_seconds':round(first_verified_audio-speech_end,3) if first_verified_audio else None,
            'transcript':input_text,'spoken_answer':output_text,'operator_status':tool['status'],'agent_steps':tool['agent_steps'],
            'event_counts':{x:events.count(x) for x in set(events)},'provider_audio_file':str(output_audio)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base',required=True);p.add_argument('--input',required=True);p.add_argument('--audio-output',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    try:result=asyncio.run(run(base_url(a.base),Path(a.input),Path(a.audio_output)))
    except Exception as e:result={'ok':False,'error_type':type(e).__name__,'code':str(e)[:120]}
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('spoken_answer','agent_steps')}))
    if not result['ok']:raise SystemExit(1)
