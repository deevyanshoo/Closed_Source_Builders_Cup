#!/usr/bin/env python3
"""Bounded hosted inventory/sourcing audit for one exact deployed revision.

Two read-only typed questions are asked per selected fictional company. A
caller-owned WAV adds one controlled Gemini Live turn per selected company.
Run only after deployment; no automatic retries, approvals, outbound research,
or quote writes. A transport failure leaves that turn's outcome uncertain.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import time
import wave

from websockets.asyncio.client import connect

from company_intelligence_news_smoke import PooledClient
from smoke import base_url, key, require


BAD_ANSWER = re.compile(r'\{\{|\}\}|\b(?:need(?:s)? clarification|please specify)\b', re.I)
NUMBERS = re.compile(r'(?<![\w.-])\d[\d,]*(?:\.\d+)?(?![\w.-])')
UNREVIEWED_PRICE = re.compile(
    r'(?:[$€£₹]\s*\d|\b(?:price|pricing|cost|landed|unit rate)\b\s*'
    r'(?:(?:is|of|at)\s*)?(?:[A-Z]{3}\s*)?\d)', re.I)
VOICE_ACTION_CLAIM = re.compile(
    r'\b(?:i|we)\s+(?:have\s+)?(?:sent|emailed|placed|ordered|approved|'
    r'purchased|saved|created|submitted)\b|\b(?:i|we)\s+'
    r'(?:(?:will|shall)\s+|(?:am|are)\s+going\s+to\s+)'
    r'(?:send|email|place|order|approve|purchase|save|create|submit)\b|'
    r'\b(?:rfq|email|order|purchase)\s+'
    r'(?:has\s+been|was|is)\s+(?:sent|placed|approved|created|submitted)\b', re.I)
ONES = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
        'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
        'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
        'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
        'eighteen': 18, 'nineteen': 19}
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
        'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90}
SPOKEN_NUMBER = re.compile(r'\b(?:' + '|'.join((*ONES, *TENS)) +
                           r')(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?\b', re.I)
SPOKEN_PRICE = re.compile(
    r'\b(?:price|pricing|cost|landed|unit rate)\b\s*'
    r'(?:(?:is|of|at)\s*)?(?:[A-Z]{3}\s*)?'
    r'(?:' + '|'.join((*ONES, *TENS)) +
    r')(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?\b|'
    r'\b(?:' + '|'.join((*ONES, *TENS)) +
    r')(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?\s+'
    r'(?:dollars?|euros?|rupees?|pounds?)\b', re.I)


def now():
    return datetime.now(timezone.utc).isoformat()


def decimal(value):
    try:
        number = Decimal(str(value).replace(',', ''))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def selected(profiles, requested):
    ids = [p['company_id'] for p in profiles]
    require(len(ids) == 10 and len(ids) == len(set(ids)), 'catalogue', 'invalid_catalogue')
    if not requested:
        return profiles
    require(len(requested) == len(set(requested)) and set(requested) <= set(ids),
            'catalogue', 'unknown_or_duplicate_company')
    return [p for p in profiles if p['company_id'] in requested]


def questions(profile):
    material = profile['materials'][0]
    return (
        ('primary', f"Explain the inventory position and sourcing options for "
         f"{material['name']} at {profile['name']}."),
        ('companywide', f"Give an inventory and sourcing overview for {profile['name']}: "
         "stock and units for materials, stock cover versus supplier lead times, "
         "and listed supplier options. Distinguish current profile figures from "
         "reviewed quotes. Do not change anything."),
    )


def expected_cover(material):
    stock, daily = decimal(material['stock']), decimal(material['consumption_per_day'])
    require(stock is not None and daily is not None and daily > 0,
            material['material_id'], 'invalid_profile_stock')
    return stock / daily


def allowed_numbers(profile):
    values = set()
    for part in profile.get('as_of', '').split('-'):
        number = decimal(part)
        if number is not None:
            values.add(number)
    for material in profile['materials']:
        for field in ('stock', 'consumption_per_day', 'monthly_requirement'):
            number = decimal(material.get(field))
            if number is not None:
                values.add(number)
        cover = expected_cover(material)
        values.add(cover)
        values.update(cover.quantize(Decimal(1).scaleb(-places)) for places in range(5))
        leads = [decimal(s['lead_time_days']) for s in profile['suppliers']
                 if material['material_id'] in s['materials']]
        for lead in leads:
            gap = abs(lead - cover)
            values.add(gap)
            values.update(gap.quantize(Decimal(1).scaleb(-places)) for places in range(5))
    for supplier in profile['suppliers']:
        number = decimal(supplier.get('lead_time_days'))
        if number is not None:
            values.add(number)
    values.update({Decimal(len(profile['materials'])), Decimal(len(profile['suppliers']))})
    return values


def near_fact(answer, label, expected, *, distance=70, tolerance=Decimal(0)):
    """Require a factual number near its label, avoiding a company-wide value bag."""
    labels = list(re.finditer(label, answer, re.I))
    return any(number is not None and abs(number - expected) <= tolerance and
               min(abs(match.start() - tag.end()), abs(tag.start() - match.end())) <= distance
               for tag in labels for match in NUMBERS.finditer(answer)
               if (number := decimal(match.group())) is not None)


def spoken_facts(caption):
    for match in NUMBERS.finditer(caption):
        yield match.start(), match.end(), decimal(match.group())
    for match in SPOKEN_NUMBER.finditer(caption):
        words = re.split(r'[- ]', match.group().casefold())
        value = TENS.get(words[0], ONES.get(words[0]))
        if len(words) == 2:
            value += ONES[words[1]]
        yield match.start(), match.end(), Decimal(value)


def spoken_near(caption, label, expected, *, distance=80, tolerance=Decimal(0)):
    return any(number is not None and abs(number - expected) <= tolerance and
               min(abs(start - tag.end()), abs(tag.start() - end)) <= distance
               for tag in re.finditer(label, caption, re.I)
               for start, end, number in spoken_facts(caption))


def check_answer(result, profile, context, kind):
    stage = profile['company_id'] + ':' + kind
    require(result.get('company_id') == profile['company_id'] and
            result.get('context_version') == context, stage, 'wrong_context')
    answer = result.get('assistant_text')
    require(result.get('status') == 'completed' and result.get('read_only') is True
            and result.get('proposal') is None and not result.get('action_result')
            and isinstance(answer, str) and len(answer.strip()) >= 60
            and not BAD_ANSWER.search(answer), stage, 'not_useful_read_only_answer')
    folded = answer.casefold()
    require(profile['name'].casefold() in folded or profile['company_id'].casefold() in folded,
            stage, 'company_not_named')
    require(re.search(r'\b(?:stock|inventory)\b', folded) and
            re.search(r'\b(?:supplier|source|sourcing|lead)\b', folded),
            stage, 'inventory_or_sourcing_missing')
    quote_label = r'\breviewed(?:\s+\w+){0,2}\s+quotes?\b'
    quote_boundary = (re.search(r'\b(?:no|none|not|without)\b.{0,80}' + quote_label, folded)
                      or re.search(quote_label + r'.{0,80}\b(?:not|none|absent|unavailable)\b', folded))
    require(re.search(r'\bprofile\b', folded) and quote_boundary,
            stage, 'profile_quote_boundary_missing')
    require(not UNREVIEWED_PRICE.search(answer), stage, 'unreviewed_price_claim')
    # A factual value may be rounded to four places. Dates are not accepted as
    # numerical inventory claims; as-of context is checked separately.
    without_dates = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', answer)
    numerals = [decimal(match.group()) for match in NUMBERS.finditer(without_dates)]
    require(bool(numerals) and all(value in allowed_numbers(profile) for value in numerals),
            stage, 'unverified_number')
    if kind == 'primary':
        material = profile['materials'][0]
        suppliers = [s for s in profile['suppliers']
                     if material['material_id'] in s['materials']]
        require(material['name'].casefold() in folded and
                material['quantity_unit'].casefold() in folded and
                near_fact(answer, r'\bstock\b', decimal(material['stock'])) and
                any(s['name'].replace(' [SYNTHETIC]', '').casefold() in folded and
                    near_fact(answer, re.escape(s['name'].replace(' [SYNTHETIC]', '')),
                              decimal(s['lead_time_days']), distance=90)
                    for s in suppliers),
                stage, 'primary_facts_missing')
        cover = expected_cover(material)
        require(near_fact(answer, r'\bcover\b', cover, tolerance=Decimal('0.1')),
                stage, 'stock_cover_missing')
    else:
        named = [m for m in profile['materials'] if m['name'].casefold() in folded]
        tightest = sorted(profile['materials'], key=lambda m: (
            expected_cover(m) - min(s['lead_time_days'] for s in profile['suppliers']
                                    if m['material_id'] in s['materials']), m['material_id']))[:2]
        supplier_names = [s['name'].replace(' [SYNTHETIC]', '').casefold()
                          for s in profile['suppliers']]
        require(len(named) >= min(2, len(profile['materials'])) and
                any(decimal(m['stock']) in numerals and m['quantity_unit'].casefold() in folded
                    for m in named) and any(m in named for m in tightest) and
                any(name in folded for name in supplier_names),
                stage, 'companywide_facts_missing')
    return {'answer': answer, 'numbers_checked': len(numerals),
            'materials_named': [m['material_id'] for m in profile['materials']
                                if m['name'].casefold() in folded]}


def check_analytics(result, profile, context):
    stage = profile['company_id'] + ':voice'
    require(result.get('company_id') == profile['company_id'] and
            result.get('context_version') == context and
            result.get('status') == 'completed' and result.get('read_only') is True
            and result.get('proposal') is None and not result.get('action_result'),
            stage, 'voice_wrong_context_or_action')
    analytics = result.get('analytics') or {}
    require(analytics.get('company_id') == profile['company_id'] and
            analytics.get('source') == 'fictional_company_profile',
            stage, 'voice_missing_profile_analytics')
    by_id = {m['material_id']: m for m in profile['materials']}
    rows = analytics.get('materials')
    require(isinstance(rows, list) and bool(rows), stage, 'voice_missing_materials')
    checked = []
    for row in rows:
        mid = row.get('material_id')
        require(mid in by_id, stage, 'voice_foreign_material')
        material = by_id[mid]
        leads = [s['lead_time_days'] for s in profile['suppliers'] if mid in s['materials']]
        require(row.get('stock') == material['stock'] and
                row.get('unit') == material['quantity_unit'] and
                decimal(row.get('stock_cover_days')) is not None and
                abs(decimal(row['stock_cover_days']) - expected_cover(material)) < Decimal('.000001')
                and row.get('shortest_supplier_lead_days') == min(leads),
                stage, 'voice_profile_fact_mismatch')
        suppliers = row.get('suppliers')
        expected_suppliers = {s['supplier_id']: s for s in profile['suppliers']
                              if mid in s['materials']}
        require(isinstance(suppliers, list) and bool(suppliers) and
                all(s.get('supplier_id') in expected_suppliers and
                    s.get('name') == expected_suppliers[s['supplier_id']]['name'] and
                    s.get('lead_time_days') == expected_suppliers[s['supplier_id']]['lead_time_days']
                    for s in suppliers), stage, 'voice_supplier_fact_mismatch')
        checked.append(mid)
    require(profile['materials'][0]['material_id'] in checked,
            stage, 'voice_primary_material_missing')
    return checked


def check_voice_caption(caption, profile, material_ids):
    """Screen provider captions; this does not transcribe the output audio."""
    stage = profile['company_id'] + ':voice_caption'
    require(isinstance(caption, str) and len(caption.strip()) >= 25 and
            not BAD_ANSWER.search(caption) and not UNREVIEWED_PRICE.search(caption) and
            not SPOKEN_PRICE.search(caption) and
            not VOICE_ACTION_CLAIM.search(caption), stage, 'voice_caption_not_grounded')
    without_dates = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', caption)
    require(all(decimal(match.group()) in allowed_numbers(profile)
                for match in NUMBERS.finditer(without_dates)),
            stage, 'voice_caption_unverified_number')
    folded = caption.casefold()
    materials = [m for m in profile['materials'] if m['material_id'] in material_ids and
                 (m['name'].casefold() in folded or
                  re.search(r'\b' + re.escape(m['material_id'].casefold()) + r'\b', folded))]
    require(bool(materials), stage, 'voice_caption_material_missing')
    for material in materials:
        mid = material['material_id']
        suppliers = [s for s in profile['suppliers'] if mid in s['materials']]
        cover = expected_cover(material)
        cover_help = (spoken_near(caption, r'\bcover\b', cover, tolerance=Decimal('.1'))
                      and re.search(r'\b(?:supplier|sourcing|lead)\b', folded))
        supplier_help = any(
            (s['name'].replace(' [SYNTHETIC]', '').casefold() in folded or
             ' '.join(s['name'].split()[:2]).casefold() in folded) and
            spoken_near(caption, r'\b(?:lead|days?)\b',
                        Decimal(s['lead_time_days']), distance=65)
            for s in suppliers)
        shortest_help = (re.search(r'\b(?:shortest|fastest|minimum|quickest)\b.{0,30}\blead\b',
                                   folded) and
                         spoken_near(caption, r'\blead\b',
                                     Decimal(min(s['lead_time_days'] for s in suppliers))))
        if cover_help or supplier_help or shortest_help:
            return {'material_id': mid, 'numbers_checked': len(list(NUMBERS.finditer(without_dates))),
                    'output_caption_grounding_verified': True}
    require(False, stage, 'voice_caption_missing_sourcing_fact')


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                 audio.getcomptype()) == (1, 2, 16000, 'NONE') and
                16000 <= audio.getnframes() <= 20 * 16000,
                'wav', 'invalid_owned_audio')
        return audio.readframes(audio.getnframes())


async def voice_turn(socket, audio, profile, context, trace=None, checkpoint=None):
    async def send_audio():
        for at in range(0, len(audio), 3200):
            await socket.send(json.dumps({'type': 'audio', 'data':
                base64.b64encode(audio[at:at + 3200]).decode('ascii')}))
            await asyncio.sleep(.1)
        await socket.send(json.dumps({'type': 'audio_end'}))

    sender = asyncio.create_task(send_audio())
    started = time.monotonic()
    material_ids, audio_bytes, input_text, output_text = None, 0, '', ''
    try:
        async with asyncio.timeout(100):
            while True:
                event = json.loads(await socket.recv())
                kind = event.get('type')
                require(kind != 'error', profile['company_id'] + ':voice', 'voice_error')
                if kind == 'transcript' and event.get('direction') == 'input':
                    input_text += ' ' + str(event.get('text', ''))
                elif kind == 'transcript' and event.get('direction') == 'output':
                    output_text += ' ' + str(event.get('text', ''))
                elif kind == 'tool_result':
                    material_ids = check_analytics(event.get('result') or {}, profile, context)
                elif kind == 'audio' and material_ids:
                    require(event.get('sample_rate_hertz') == 24000,
                            profile['company_id'] + ':voice', 'wrong_audio_format')
                    audio_bytes += len(base64.b64decode(event['data'], validate=True))
                elif kind == 'turn_complete':
                    if material_ids is not None and audio_bytes >= 10000:
                        require(re.search(r'\b(?:inventory|stock|sourc|supplier)\w*\b',
                                          input_text, re.I),
                                profile['company_id'] + ':voice', 'wrong_spoken_prompt')
                        if trace is not None:
                            trace.update(output_transcript=output_text.strip()[:4000],
                                         output_audio_bytes=audio_bytes,
                                         tool_material_ids=material_ids)
                            if checkpoint is not None:
                                checkpoint()
                        caption_check = check_voice_caption(output_text.strip(), profile, material_ids)
                        await sender
                        return {'material_ids': material_ids, 'output_audio_bytes': audio_bytes,
                                'input_transcript_verified': True,
                                'output_transcript': output_text.strip()[:4000],
                                **caption_check,
                                'output_audio_semantics_verified': False,
                                'elapsed_ms': round((time.monotonic() - started) * 1000)}
    finally:
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def voice_visit(base, client, session, profile, audio, trace, checkpoint):
    cookie = '; '.join(c.name + '=' + c.value for c in client.session.cookies)
    require(bool(cookie), profile['company_id'], 'voice_cookie_missing')
    async with connect(base.replace('https:', 'wss:') + '/api/demo/voice/live',
                       origin=base, additional_headers={'Cookie': cookie},
                       max_size=1_024_000, open_timeout=20) as socket:
        await socket.send(json.dumps({'type': 'start', 'csrf_token': session['csrf_token'],
                                      'expected_context_version': session['context_version']}))
        ready = json.loads(await asyncio.wait_for(socket.recv(), 25))
        require(ready.get('type') == 'ready', profile['company_id'], 'voice_not_ready')
        result = await voice_turn(socket, audio, profile, session['context_version'],
                                  trace, checkpoint)
        await socket.send(json.dumps({'type': 'stop'}))
        return result


def visit(base, profile, audio, row, save):
    client = PooledClient(base, max_transport_attempts=1)
    try:
        row['stage'] = 'session_pending'; save()
        session = client.json('session', '/api/demo/session')
        require(session.get('synthetic') is True and session.get('csrf_token') and
                not session.get('offers') and not session.get('drafts') and
                not session.get('simulated_outbox'), profile['company_id'], 'session_not_fresh')
        if session['company_id'] != profile['company_id']:
            row['stage'] = 'company_select_pending'; save()
            session = client.json('company', '/api/demo/company', csrf=session['csrf_token'],
                body={'company_id': profile['company_id'],
                      'expected_context_version': session['context_version'],
                      'idempotency_key': key()})
        require(session['company_id'] == profile['company_id'],
                profile['company_id'], 'company_select_failed')
        context = session['context_version']
        protected = ('offers', 'drafts', 'simulated_outbox', 'plan', 'rfq',
                     'buyer_draft', 'selected_material_id')
        baseline = {field: session.get(field) for field in protected}
        for kind, question in questions(profile):
            row['stage'] = kind + '_pending'; save()
            result = client.json(kind, '/api/demo/operator', csrf=session['csrf_token'],
                body={'message': question, 'expected_context_version': context,
                      'idempotency_key': key()})
            # Preserve the bounded observed answer before any assertion. A failed
            # release gate remains diagnosable without cookies, CSRF, or session IDs.
            observed = {'status': result.get('status'),
                        'assistant_text': str(result.get('assistant_text', ''))[:4000],
                        'read_only': result.get('read_only')}
            row['typed'][kind] = observed; save()
            row['typed'][kind] = {**observed, **check_answer(result, profile, context, kind)}
            row['stage'] = kind + '_passed'; save()
        if audio is not None:
            row['stage'] = 'voice_pending'; row['voice'] = {}; save()
            row['voice'].update(asyncio.run(voice_visit(
                base, client, session, profile, audio, row['voice'], save)))
            row['stage'] = 'voice_passed'; save()
        row['stage'] = 'state_check_pending'; save()
        final = client.json('final_session', '/api/demo/session')
        require(final.get('company_id') == profile['company_id'] and
                final.get('context_version') == context and
                all(final.get(field) == baseline[field] for field in protected),
                profile['company_id'], 'read_only_state_changed')
        row['read_only_state_verified'] = True
        row.update(ok=True, stage='completed')
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--company', action='append')
    parser.add_argument('--inventory-wav', type=Path)
    args = parser.parse_args()
    base = base_url(args.base)
    audio = pcm(args.inventory_wav) if args.inventory_wav else None
    receipt = {'started_at': now(), 'expected_revision': args.expected_revision,
               'controlled_speech': audio is not None, 'automatic_retries': 0,
               'ok': False, 'companies': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)

    def save():
        temporary = args.output.with_name(args.output.name + '.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, args.output)

    catalogue = PooledClient(base, max_transport_attempts=1)
    try:
        receipt['stage'] = 'health_pending'; save()
        health = catalogue.json('health', '/-/health')
        require(health.get('revision') == args.expected_revision, 'health', 'wrong_revision')
        receipt['stage'] = 'catalogue_pending'; save()
        profiles = selected(catalogue.json('companies', '/api/demo/companies')['companies'], args.company)
        for profile in profiles:
            row = {'company_id': profile['company_id'], 'ok': False,
                   'stage': 'started', 'typed': {}}
            receipt['companies'].append(row); save()
            try:
                visit(base, profile, audio, row, save)
            except Exception as exc:
                row.update(error_type=type(exc).__name__, error=str(exc)[:180])
            save()
            print(json.dumps({k: row.get(k) for k in ('company_id', 'ok', 'stage', 'error')}), flush=True)
            if row.get('error') in ('operator_budget_exhausted', 'voice_process_limit'):
                break
        receipt['ok'] = len(receipt['companies']) == len(profiles) and all(
            row['ok'] for row in receipt['companies'])
        receipt.update(stage='completed', completed_at=now()); save()
    finally:
        catalogue.close()
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
