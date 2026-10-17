#!/usr/bin/env python3
"""One controlled-speech inventory/email workflow per hosted company.

No HTTP operator model turn or Internet research is requested. Simulated email
approval happens only after the unsent draft has been inspected. Never writes
cookies, CSRF tokens or raw caller audio to the receipt. No automatic retries.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import time
import wave

from websockets.asyncio.client import connect

from company_intelligence_news_smoke import PooledClient
from smoke import base_url, key
from procure.demo.agent_tools import BUYER_FIELDS


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                audio.getcomptype()) == (1, 2, 16000, 'NONE'), 'input_format'
        assert 16000 <= audio.getnframes() <= 20 * 16000, 'input_duration'
        return audio.readframes(audio.getnframes())


async def speak(socket, data):
    for at in range(0, len(data), 3200):
        await socket.send(json.dumps({'type': 'audio', 'data':
            base64.b64encode(data[at:at + 3200]).decode('ascii')}))
        await asyncio.sleep(.1)
    await socket.send(json.dumps({'type': 'audio_end'}))


async def turn(socket, data, company, context, kind, trace):
    sender = asyncio.create_task(speak(socket, data))
    counts, tools, captions = Counter(), [], []
    trace.update(events=counts, tools=tools, captions=captions)
    audio_bytes = 0
    qualifying = None
    started = time.monotonic()
    try:
        async with asyncio.timeout(100):
            while True:
                event = json.loads(await socket.recv())
                event_type = event['type']
                counts[event_type] += 1
                assert event_type != 'error', event.get('code', 'voice_error')
                if event_type == 'tool_result':
                    result = event['result']
                    assert result['company_id'] == company['company_id'], 'wrong_tool_company'
                    assert result['context_version'] == context, 'wrong_tool_context'
                    labels = [s.get('tool') for s in result.get('agent_steps', [])]
                    tools.extend(labels)
                    if kind == 'inventory' and result.get('analytics', {}).get('materials'):
                        profile = {m['material_id']: m for m in company['materials']}
                        rows = result['analytics']['materials']
                        for row in rows:
                            expected = profile[row['material_id']]
                            assert row['stock'] == expected['stock'], 'wrong_stock'
                            assert row['unit'] == expected['quantity_unit'], 'wrong_unit'
                            assert abs(Decimal(row['stock_cover_days']) -
                                       Decimal(expected['stock']) /
                                       Decimal(expected['consumption_per_day'])) < Decimal('.000001')
                        qualifying = {'material_ids': [r['material_id'] for r in rows],
                                      'facts_match_profile': True}
                    proposal = result.get('proposal')
                    if kind == 'email' and isinstance(proposal, dict) and proposal.get('kind') == 'simulate_email':
                        assert proposal['parameters']['material_id'] in {m['material_id'] for m in company['materials']}
                        assert proposal['parameters']['recipient_label'] in {s['name'] for s in company['suppliers']}
                        qualifying = proposal
                elif event_type == 'transcript':
                    captions.append({'direction': event['direction'], 'text': event.get('text', '')})
                elif event_type == 'audio' and qualifying:
                    assert event.get('sample_rate_hertz') == 24000
                    audio_bytes += len(base64.b64decode(event['data'], validate=True))
                if event_type == 'turn_complete' and qualifying and audio_bytes >= 10000:
                    await sender
                    return {'ok': True, 'events': dict(counts), 'tools': tools,
                            'captions': captions, 'output_audio_bytes': audio_bytes,
                            'elapsed_ms': round((time.monotonic() - started) * 1000),
                            'result': qualifying}
    finally:
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def company_visit(base, profile, question, email, row, save):
    client = PooledClient(base, max_transport_attempts=1)
    try:
        session = await asyncio.to_thread(client.json, 'session', '/api/demo/session')
        if session['company_id'] != profile['company_id']:
            session = await asyncio.to_thread(client.json, 'company', '/api/demo/company',
                csrf=session['csrf_token'], body={'company_id': profile['company_id'],
                'expected_context_version': session['context_version'], 'idempotency_key': key()})
        context = session['context_version']
        scenario = profile['quote_coverage']['scenarios'][0]
        draft = {field: scenario['buyer_defaults'][field] for field in BUYER_FIELDS}
        assert draft['material'] == scenario['material_id'], 'scenario_material_mismatch'
        session = await asyncio.to_thread(client.json, 'buyer_draft', '/api/demo/buyer-draft',
            csrf=session['csrf_token'], body={'buyer_draft': draft,
            'expected_context_version': context, 'idempotency_key': key()})
        assert (session['company_id'] == profile['company_id'] and
                session['context_version'] == context and
                session['selected_material_id'] == scenario['material_id'] and
                session['buyer_draft'] == draft), 'buyer_material_not_selected'
        row['stage'] = 'buyer_material_selected'
        save()
        cookie = '; '.join(c.name + '=' + c.value for c in client.session.cookies)
        row['stage'] = 'voice_start_authorized'
        save()
        async with connect(base.replace('https:', 'wss:') + '/api/demo/voice/live',
                           origin=base, additional_headers={'Cookie': cookie},
                           max_size=1_024_000, open_timeout=20) as socket:
            await socket.send(json.dumps({'type': 'start', 'csrf_token': session['csrf_token'],
                                          'expected_context_version': context}))
            ready = json.loads(await asyncio.wait_for(socket.recv(), 25))
            assert ready['type'] == 'ready', ready.get('code', 'not_ready')
            row['inventory'] = {}
            row['inventory'] = await turn(socket, question, profile, context, 'inventory', row['inventory'])
            row['stage'] = 'inventory_passed'
            save()
            if email:
                row['email'] = {}
                row['email'] = await turn(socket, email, profile, context, 'email', row['email'])
            await socket.send(json.dumps({'type': 'stop'}))
        if email:
            proposal = row['email'].pop('result')
            state = await asyncio.to_thread(client.json, 'drafts', '/api/demo/session')
            preview = proposal['parameters']['draft_preview']
            assert preview in state['drafts'] and state['simulated_outbox'] == [], 'draft_not_visible_unsent'
            row['stage'] = 'simulated_email_reviewed'
            save()
            body = {'proposal_id': proposal['proposal_id'], 'reviewed': True,
                    'expected_context_version': context, 'idempotency_key': key()}
            applied = await asyncio.to_thread(client.json, 'confirm', '/api/demo/operator/confirm',
                                             csrf=session['csrf_token'], body=body)
            replay = await asyncio.to_thread(client.json, 'replay', '/api/demo/operator/confirm',
                                            csrf=session['csrf_token'], body=body)
            assert applied['result'] == replay['result'], 'replayed_email_changed'
            state = await asyncio.to_thread(client.json, 'outbox', '/api/demo/session')
            assert len(state['simulated_outbox']) == 1, 'duplicate_or_missing_email'
            sent = state['simulated_outbox'][0]
            assert sent['external_delivery'] is False and sent['simulated'] is True
            row['email'].update(draft_visible_before_review=True, simulation_recorded_once=True,
                                external_delivery=False, recipient=preview['recipient_label'])
        row.update(ok=True, stage='completed', same_websocket=True)
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--inventory-wav', type=Path, required=True)
    parser.add_argument('--email-wav', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--company', action='append')
    args = parser.parse_args()
    base = base_url(args.base)
    question, email = pcm(args.inventory_wav), pcm(args.email_wav) if args.email_wav else None
    receipt = {'started_at': datetime.now(timezone.utc).isoformat(), 'revision': args.expected_revision,
               'controlled_speech': True, 'physical_microphone_test': False, 'automatic_retries': 0,
               'ok': False, 'companies': []}
    with args.output.open('x') as stream:
        json.dump(receipt, stream)

    def save():
        with args.output.open('w') as stream:
            json.dump(receipt, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())

    client = PooledClient(base, max_transport_attempts=1)
    try:
        health = client.json('health', '/-/health')
        assert health['revision'] == args.expected_revision, 'wrong_revision'
        profiles = client.json('companies', '/api/demo/companies')['companies']
        assert len(profiles) == 10
        if args.company:
            profiles = [p for p in profiles if p['company_id'] in args.company]
            assert len(profiles) == len(set(args.company)), 'unknown_company'
        for profile in profiles:
            row = {'company_id': profile['company_id'], 'ok': False, 'stage': 'started'}
            receipt['companies'].append(row)
            save()
            try:
                asyncio.run(company_visit(base, profile, question, email, row, save))
            except Exception as exc:
                row.update(error_type=type(exc).__name__, error=str(exc)[:180])
            save()
            print(json.dumps({k: row.get(k) for k in ('company_id', 'ok', 'stage', 'error')}), flush=True)
            if row.get('error') in ('voice_process_limit', 'operator_budget_exhausted'):
                break
        receipt['ok'] = len(receipt['companies']) == len(profiles) and all(r['ok'] for r in receipt['companies'])
        receipt['completed_at'] = datetime.now(timezone.utc).isoformat()
        save()
    finally:
        client.close()
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
