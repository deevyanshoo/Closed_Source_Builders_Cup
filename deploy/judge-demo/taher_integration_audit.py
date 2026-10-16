#!/usr/bin/env python3
"""Bounded hosted cost, carbon and company-linked shipment acceptance audit.

Run against one exact deployed HTTPS revision after release. Uses only bundled
synthetic quote fixtures and rule extraction. It makes no provider or cloud
configuration calls directly; optional WAV exercises hosted voice. It never
retries an uncertain request automatically.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import wave

from smoke import AcceptanceError, Client, base_url, key, require

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'procurement-engine' / 'src'))
from procure.carbon import compare_plan, load_registry
from procure.carbon.assessment import validate_assessment


ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
MASS_BASIS = 'Controlled QA assumption: 1000 kg shipped mass per offer.'
VOICE_NUMBERS = re.compile(r'(?<![\w.-])\d[\d,]*(?:\.\d+)?(?!\w|\.\d)')
VOICE_ACTION = re.compile(
    r'\b(?:i|we)\s+(?:have\s+)?(?:sent|emailed|placed|ordered|approved|'
    r'purchased|saved|created|submitted)\b|\b(?:i|we)\s+'
    r'(?:(?:will|shall)\s+|(?:am|are)\s+going\s+to\s+)'
    r'(?:send|email|place|order|approve|purchase|save|create|submit)\b|'
    r'\b(?:rfq|email|order|purchase)\s+'
    r'(?:has\s+been|was|is)\s+(?:sent|placed|approved|created|submitted)\b', re.I)
VOICE_PRICE = re.compile(
    r'(?:[$€£₹]\s*\d|(?i:\b(?:price|pricing|cost|savings|unit rate)\b\s*'
    r'(?:(?:is|of|at)\s*)?)(?:[A-Z]{3}\s*)?\d|'
    r'\b(?:USD|EUR|GBP|INR|JPY|CNY|AUD|CAD)\s*\d|'
    r'\b\d[\d,.]*\s*(?:USD|EUR|GBP|INR|JPY|CNY|AUD|CAD)\b)')


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def number(value, stage):
    require(isinstance(value, str), stage, 'non_decimal_value')
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise AcceptanceError(stage, 'non_decimal_value') from None
    require(parsed.is_finite(), stage, 'non_decimal_value')
    return parsed


def identifier(value, stage):
    require(isinstance(value, str) and ID.fullmatch(value) is not None,
            stage, 'unsafe_identifier')
    return value


def corrected_field(offer, field):
    if field.startswith('items[0].'):
        return offer['items'][0][field[len('items[0].'):]]
    return offer['fields'][field]


def check_plan(plan, company_id, defaults, versions):
    stage = 'plan'
    purchase = number(defaults['quantity'], stage) - number(defaults['current_stock'], stage)
    require(purchase > 0 and isinstance(plan, dict) and plan.get('synthetic') is True
            and plan.get('company_id') == company_id
            and plan.get('material') == defaults['material']
            and plan.get('quantity_unit') == defaults['quantity_unit']
            and plan.get('offer_versions') == versions
            and number(plan.get('required_purchase'), stage) == purchase
            and plan.get('selected_offer_id') in versions
            and isinstance(plan.get('alternatives'), list)
            and len(plan['alternatives']) == len(versions)
            and {a['offer_id'] for a in plan['alternatives']} == set(versions),
            stage, 'plan_context_mismatch')
    extra = number(defaults['additional_cost_per_unit'], stage)
    alternatives = {a['offer_id']: a for a in plan['alternatives']}
    for candidate in alternatives.values():
        require(candidate.get('quantity_unit') == defaults['quantity_unit']
                and number(candidate.get('quantity'), stage) == purchase,
                stage, 'plan_context_mismatch')
        price = number(candidate.get('unit_price_per_unit'), stage)
        if candidate.get('total_cost') is not None:
            require(number(candidate['total_cost'], stage) == purchase * (price + extra),
                    stage, 'plan_cost_mismatch')
    selected = alternatives[plan['selected_offer_id']]
    require(selected.get('feasible') is True and selected.get('total_cost') is not None
            and number(plan.get('total_cost'), stage) == number(selected['total_cost'], stage),
            stage, 'plan_cost_mismatch')
    return {'purchase': str(purchase), 'alternatives_checked': len(alternatives)}


def carbon_request(plan):
    """One explicit 1000 km sea leg per offer; mass for non-mass units."""
    ids = [identifier(row['offer_id'], 'carbon_input') for row in plan['alternatives']]
    require(len(ids) > 0 and len(ids) == len(set(ids)), 'carbon_input', 'offer_set_invalid')
    body = {'plan_id': identifier(plan['plan_id'], 'carbon_input'),
            'legs_by_offer': {offer_id: [{'mode': 'sea_container', 'distance_km': '1000'}]
                              for offer_id in ids}}
    if plan['quantity_unit'] not in {'kg', 'tonne'}:
        body['mass_by_offer'] = {offer_id: {'mass_kg': '1000', 'basis': MASS_BASIS}
                                 for offer_id in ids}
    return body


def check_carbon(response, plan, context, request):
    stage = 'carbon'
    require(isinstance(response, dict) and response.get('company_id') == plan['company_id']
            and response.get('plan_id') == plan['plan_id']
            and response.get('context_version') == context,
            stage, 'carbon_scope_mismatch')
    core = {field: value for field, value in response.items() if field != 'context_version'}
    expected = compare_plan(load_registry(), plan, request['legs_by_offer'],
                            request.get('mass_by_offer'), company_id=plan['company_id'])
    require(core == expected, stage, 'carbon_result_mismatch')
    rows = core['suppliers']
    require([row['offer_id'] for row in rows] == [row['offer_id'] for row in plan['alternatives']],
            stage, 'carbon_offer_set_mismatch')
    statuses = {'material': {}, 'transport': {}, 'legs': {}}
    source_ids = set()
    source_units = set()
    unknown = 0
    calibration = False
    for row in rows:
        estimate = row['estimate']
        for component in ('material', 'transport'):
            detail = estimate[component]
            status = detail['status']
            statuses[component][status] = statuses[component].get(status, 0) + 1
            if status != 'estimated':
                unknown += 1
                require(detail['kg_co2e'] is None, stage, 'carbon_unknown_has_number')
            else:
                number(detail['kg_co2e'], stage)
        factors = [estimate['material'].get('factor')]
        for leg in estimate['transport']['legs']:
            status = leg['status']
            statuses['legs'][status] = statuses['legs'].get(status, 0) + 1
            factors.append(leg.get('factor'))
            if status in {'estimated', 'zero_distance'}:
                number(leg['kg_co2e'], stage)
            else:
                require(leg['kg_co2e'] is None, stage, 'carbon_unknown_has_number')
            if (plan['company_id'] == 'solara-cables' and plan['material'] == 'copper'
                    and estimate['quantity'] == '8000' and estimate['unit'] == 'kg'
                    and leg['mode'] == 'sea_container' and leg['distance_km'] == '1000'):
                expected_freight = (Decimal('8000') / Decimal('1000')
                                    * Decimal('1000') * Decimal('0.01612'))
                require(expected_freight == Decimal('128.96')
                        and leg['factor']['factor'] == '0.01612'
                        and number(leg['kg_co2e'], stage) == expected_freight
                        and number(estimate['transport']['kg_co2e'], stage) == expected_freight,
                        stage, 'carbon_calibration_mismatch')
                calibration = True
        for factor in factors:
            if factor is None:
                continue
            require(isinstance(factor, dict) and isinstance(factor.get('source_id'), str)
                    and bool(factor['source_id']) and isinstance(factor.get('unit'), str)
                    and bool(factor['unit']) and isinstance(factor.get('publisher'), str)
                    and bool(factor['publisher']), stage, 'carbon_source_unit_missing')
            source_ids.add(factor['source_id'])
            source_units.add(factor['unit'])
    return {'supplier_count': len(rows), 'registry_version': core['registry_version'],
            'component_statuses': statuses, 'unknown_component_count': unknown,
            'source_ids': sorted(source_ids), 'source_units': sorted(source_units),
            'calibration_8000kg_1000km_verified': calibration,
            'explicit_distance_km': '1000',
            'mass_basis_explicit': plan['quantity_unit'] not in {'kg', 'tonne'}}


def check_assessment(wrapper, plan, context, request, response):
    stage = 'carbon_assessment'
    require(isinstance(wrapper, dict) and wrapper.get('company_id') == plan['company_id']
            and wrapper.get('context_version') == context,
            stage, 'carbon_assessment_mismatch')
    record = wrapper.get('assessment')
    try:
        validate_assessment(record)
    except ValueError:
        raise AcceptanceError(stage, 'carbon_assessment_mismatch') from None
    require(record['company_id'] == plan['company_id']
            and record['plan_id'] == plan['plan_id']
            and record['context_version'] == context
            and record['offer_versions'] == plan['offer_versions']
            and record['material_id'] == plan['material']
            and record['inputs'] == {'legs_by_offer': request['legs_by_offer'],
                                     'mass_by_offer': request.get('mass_by_offer', {})}
            and record['result'] == response,
            stage, 'carbon_assessment_mismatch')
    return {'readback_verified': True, 'plan_bound': True, 'offer_versions_bound': True}


def check_dispatch(view, plan, context):
    stage = 'dispatch'
    require(isinstance(view, dict) and view.get('company_id') == plan['company_id']
            and view.get('context_version') == context and view.get('plan_id') == plan['plan_id']
            and view.get('synthetic') is True
            and view.get('source') == 'fictional_company_profile_and_plan'
            and isinstance(view.get('as_of'), str) and view['as_of']
            and isinstance(view.get('limitations'), list)
            and isinstance(view.get('shipments'), list), stage, 'dispatch_scope_mismatch')
    linked = []
    seen = set()
    for row in view['shipments']:
        require(isinstance(row, dict) and row.get('company_id') == plan['company_id'],
                stage, 'foreign_shipment')
        shipment_id = identifier(row.get('shipment_id'), stage)
        require(shipment_id not in seen, stage, 'duplicate_shipment')
        seen.add(shipment_id)
        require(row.get('source') in {'fictional_company_profile_expected_order',
                                      'synthetic_plan_demand_scenario'}
                and isinstance(row.get('timeline'), list),
                stage, 'shipment_provenance_missing')
        require((isinstance(row.get('order_id'), str) and row['order_id'])
                or (row.get('order_id') is None and isinstance(row.get('demand_reference'), str)
                    and row['demand_reference']), stage, 'shipment_identity_missing')
        require(isinstance(row.get('material_name'), str) and row['material_name']
                and isinstance(row.get('quantity'), str)
                and isinstance(row.get('quantity_unit'), str),
                stage, 'shipment_identity_missing')
        number(row['quantity'], stage)
        if row['source'] == 'fictional_company_profile_expected_order':
            require(row.get('recovery_available') is False and row.get('offer_id') is None
                    and row.get('plan_id') is None, stage, 'expected_order_actionable')
        if row.get('recovery_available') is True:
            require(row.get('plan_id') == plan['plan_id']
                    and row.get('offer_id') in plan['offer_versions']
                    and row.get('offer_version') == plan['offer_versions'][row['offer_id']]
                    and row.get('material_id') == plan['material']
                    and row.get('quantity_unit') == plan['quantity_unit']
                    and row.get('source') == 'synthetic_plan_demand_scenario'
                    and number(row['quantity'], stage)
                    == number(plan['required_purchase'], stage),
                    stage, 'invalid_recovery_link')
        if (row.get('plan_id') == plan['plan_id']
                and row.get('offer_id') == plan['selected_offer_id']
                and row.get('offer_version') == plan['offer_versions'][plan['selected_offer_id']]
                and row.get('material_id') == plan['material']
                and row.get('recovery_available') is True):
            linked.append(row)
    require(len(linked) == 1, stage, 'linked_shipment_missing')
    return linked[0]


def check_recovery(response, plan, linked, days):
    stage = 'recovery'
    revised = response.get('scenario') if isinstance(response, dict) else None
    request = response.get('scenario_request') if isinstance(response, dict) else None
    require(response.get('outcome') == 'changed' and isinstance(revised, dict)
            and isinstance(request, dict)
            and request.get('delay_days_by_offer') == {linked['offer_id']: days}
            and revised.get('company_id') == plan['company_id']
            and revised.get('material') == plan['material']
            and revised.get('quantity_unit') == plan['quantity_unit']
            and revised.get('offer_versions') == plan['offer_versions']
            and revised.get('selected_offer_id') != plan['selected_offer_id']
            and revised.get('selected_offer_id') in plan['offer_versions']
            and revised.get('plan_id') != plan['plan_id'],
            stage, 'recovery_not_bound')
    return {'changed': True, 'shipment_bound': True, 'delay_days': days}


def create_reviewed_rfq(client, stage, csrf, context, revised, versions):
    draft = _post(client, stage, '/api/demo/rfq', csrf, context,
                  {'plan_id': revised['plan_id'], 'expected_versions': versions,
                   'reviewed': True, 'idempotency_key': key()})
    draft_id = identifier(draft.get('draft_id'), stage)
    require(draft.get('status') == 'draft_unsent'
            and draft.get('company_id') == stage
            and draft.get('material') == revised['material']
            and draft.get('synthetic') is True,
            stage, 'rfq_wrong')
    current = client.json(stage, '/api/demo/session')
    require(current.get('company', {}).get('company_id') == stage
            and current.get('context_version') == context
            and current.get('plan', {}).get('plan_id') == revised['plan_id']
            and current.get('rfq', {}).get('draft_id') == draft_id
            and any(item.get('draft_id') == draft_id and item.get('status') == 'draft_unsent'
                    for item in current.get('drafts', []))
            and current.get('simulated_outbox') == [],
            stage, 'rfq_or_outbound_action_present')
    return {'draft_id': draft_id, 'unsent': True, 'outbox_empty': True}


def _fact_numbers(carbon, dispatch):
    allowed = {Decimal('1000'), Decimal(len(carbon.get('suppliers', []))),
               Decimal(len(dispatch.get('shipments', [])))}

    def visit(value):
        if isinstance(value, dict):
            for inner in value.values():
                visit(inner)
        elif isinstance(value, list):
            for inner in value:
                visit(inner)
        elif isinstance(value, (int, float, str)) and not isinstance(value, bool):
            if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                allowed.update(Decimal(part) for part in value.split('-'))
            for match in VOICE_NUMBERS.findall(str(value)):
                try:
                    allowed.add(Decimal(match.replace(',', '')))
                except InvalidOperation:
                    pass

    visit(carbon)
    visit(dispatch)
    return allowed


def _guard_voice_facts(answer, carbon, dispatch):
    require(isinstance(answer, str) and answer.strip(), 'voice', 'voice_empty_answer')
    require(VOICE_ACTION.search(answer) is None, 'voice', 'voice_action_claim')
    require(VOICE_PRICE.search(answer) is None, 'voice', 'voice_price_claim')
    allowed = _fact_numbers(carbon, dispatch)
    claims = [Decimal(raw.replace(',', '')) for raw in VOICE_NUMBERS.findall(answer)]
    require(all(value in allowed for value in claims), 'voice', 'voice_unverified_number')
    return claims


def check_voice_result(result, plan, context, carbon, dispatch):
    require(isinstance(result, dict) and result.get('company_id') == plan['company_id']
            and result.get('context_version') == context
            and result.get('status') == 'completed' and result.get('read_only') is True
            and result.get('proposal') is None,
            'voice', 'voice_wrong_scope')
    steps = result.get('agent_steps')
    require(isinstance(steps, list) and len(steps) == 1 and isinstance(steps[0], dict),
            'voice', 'voice_wrong_tool')
    step = steps[0]
    tool = step.get('tool')
    require(tool in {'read_carbon', 'read_shipments'}
            and step.get('agent') == 'workspace' and step.get('role') == 'deterministic'
            and step.get('status') == 'completed' and step.get('model') == 'none'
            and step.get('provider_call') == 'none', 'voice', 'voice_wrong_tool')
    expected_key = 'carbon' if tool == 'read_carbon' else 'shipments'
    expected = carbon if tool == 'read_carbon' else dispatch
    require(result.get('workspace') == {expected_key: expected},
            'voice', 'voice_workspace_mismatch')
    _guard_voice_facts(result.get('assistant_text'), carbon, dispatch)
    return tool


def check_voice_caption(caption, company_name, plan, carbon, dispatch):
    stage = 'voice'
    claims = _guard_voice_facts(caption, carbon, dispatch)
    linked = next((row for row in dispatch['shipments']
                   if row.get('plan_id') == plan['plan_id']
                   and row.get('offer_id') == plan['selected_offer_id']), None)
    material_name = linked.get('material_name', '') if isinstance(linked, dict) else ''
    lowered = caption.lower()
    require(company_name.lower() in lowered
            and (plan['material'].lower() in lowered
                 or (material_name and material_name.lower() in lowered))
            and re.search(r'\b(?:carbon|co2e|greenhouse gas emissions?)\b', caption, re.I)
            and re.search(r'\b(?:ship(?:ment|ping)|dispatch|inbound delivery)\b', caption, re.I)
            and re.search(r'\b(?:synthetic|fictional|planning scenario|hypothetical plan)\b', caption, re.I)
            and re.search(r'\b(?:source|factor|assumption|entered)\b', caption, re.I)
            and re.search(r'\b(?:kg\s*CO2e|kilograms?\s+(?:of\s+)?carbon dioxide equivalent)\b',
                          caption, re.I)
            and len(claims) >= 1,
            stage, 'voice_caption_not_grounded')
    totals = [row['estimate'].get('total_kg_co2e') for row in carbon['suppliers']]
    if all(value is None for value in totals):
        require(re.search(r'\b(?:total|combined)\b[^.;]{0,60}\b\d', caption, re.I) is None,
                stage, 'voice_unknown_total_claim')
    return {'numbers_checked': len(claims), 'caption_grounded': True}


def pcm(path):
    with wave.open(str(path), 'rb') as audio:
        require((audio.getnchannels(), audio.getsampwidth(), audio.getframerate(),
                 audio.getcomptype()) == (1, 2, 16000, 'NONE')
                and 16000 <= audio.getnframes() <= 20 * 16000,
                'wav', 'invalid_controlled_audio')
        return audio.readframes(audio.getnframes())


def _voice_trace_payload(result):
    """Keep synthetic workspace evidence while omitting transport/auth fields."""
    if not isinstance(result, dict):
        return {'invalid_result_type': type(result).__name__}
    allowed = ('company_id', 'context_version', 'status', 'read_only', 'proposal',
               'assistant_text', 'agent_steps', 'workspace')
    excluded = ('token', 'cookie', 'secret', 'authorization', 'session_id',
                'idempotency_key', 'turn_id', 'proposal_id')

    def clean(value, depth=0):
        if depth >= 12:
            return '[depth limit]'
        if isinstance(value, dict):
            return {str(key): clean(item, depth + 1) for key, item in list(value.items())[:200]
                    if not any(label in str(key).casefold() for label in excluded)}
        if isinstance(value, list):
            return [clean(item, depth + 1) for item in value[:100]]
        if isinstance(value, str):
            return value[:2000]
        return value if value is None or type(value) in (bool, int, float) else str(type(value).__name__)

    payload = clean({key: result[key] for key in allowed if key in result})
    size = len(json.dumps(payload, default=str))
    if size > 100_000:
        return {'payload_omitted_oversize_bytes': size,
                'workspace_keys': sorted(result.get('workspace', {}))
                if isinstance(result.get('workspace'), dict) else []}
    return payload


async def voice_turn(socket, audio, company_name, plan, context, carbon, dispatch,
                     *, trace=None, checkpoint=None):
    async def send_audio():
        for at in range(0, len(audio), 3200):
            await socket.send(json.dumps({'type': 'audio', 'data':
                base64.b64encode(audio[at:at + 3200]).decode('ascii')}))
            await asyncio.sleep(.1)
        await socket.send(json.dumps({'type': 'audio_end'}))

    sender = asyncio.create_task(send_audio())
    started = time.monotonic()
    tools = set()
    input_text = ''
    output_text = ''
    audio_bytes = 0
    if trace is not None:
        trace.update(status='running', input_caption='', output_caption='',
                     input_caption_truncated=False, output_caption_truncated=False,
                     tool_results=[], tools_verified=[], audio_bytes=0, event_count=0)

    def note():
        if trace is None:
            return
        trace['input_caption'] = input_text.strip()[:2000]
        trace['output_caption'] = output_text.strip()[:2000]
        trace['input_caption_truncated'] = len(input_text.strip()) > 2000
        trace['output_caption_truncated'] = len(output_text.strip()) > 2000
        trace['tools_verified'] = sorted(tools)
        trace['audio_bytes'] = audio_bytes
        if checkpoint is not None:
            checkpoint()

    try:
        async with asyncio.timeout(100):
            while True:
                event = json.loads(await socket.recv())
                kind = event.get('type')
                if trace is not None:
                    trace['event_count'] += 1
                    require(trace['event_count'] <= 1000, 'voice', 'voice_event_limit')
                require(kind != 'error', 'voice', 'voice_error')
                if kind == 'transcript':
                    direction = event.get('direction')
                    if direction == 'input':
                        input_text += ' ' + str(event.get('text', ''))
                    elif direction == 'output':
                        output_text += ' ' + str(event.get('text', ''))
                elif kind == 'tool_result':
                    if trace is not None:
                        require(len(trace['tool_results']) < 8, 'voice', 'voice_tool_result_limit')
                        trace['tool_results'].append({'payload': _voice_trace_payload(event.get('result'))})
                        note()
                    tools.add(check_voice_result(event.get('result'), plan, context,
                                                 carbon, dispatch))
                elif kind == 'audio':
                    require(event.get('sample_rate_hertz') == 24000,
                            'voice', 'voice_audio_format')
                    try:
                        audio_bytes += len(base64.b64decode(event['data'], validate=True))
                    except (KeyError, ValueError):
                        raise AcceptanceError('voice', 'voice_audio_format') from None
                elif kind == 'turn_complete':
                    note()
                    require(tools == {'read_carbon', 'read_shipments'},
                            'voice', 'voice_missing_tools')
                    require(re.search(r'\bcarbon\b', input_text, re.I)
                            and re.search(r'\bship(?:ment|ping)s?\b|\bdispatch\b',
                                          input_text, re.I),
                            'voice', 'voice_wrong_spoken_prompt')
                    require(audio_bytes >= 10000, 'voice', 'voice_no_output_audio')
                    grounded = check_voice_caption(output_text.strip(), company_name,
                                                   plan, carbon, dispatch)
                    await sender
                    if trace is not None:
                        trace['status'] = 'completed'
                        note()
                    return {'tools_verified': sorted(tools),
                            'input_transcript_verified': True,
                            'output_audio_bytes': audio_bytes,
                            'output_audio_semantics_verified': False,
                            'caption_grounded': grounded['caption_grounded'],
                            'numbers_checked': grounded['numbers_checked'],
                            'elapsed_ms': round((time.monotonic() - started) * 1000)}
    except TimeoutError:
        if trace is not None:
            trace.update(status='failed', error_code='voice_timeout')
            note()
        raise AcceptanceError('voice', 'voice_timeout') from None
    except AcceptanceError as exc:
        if trace is not None:
            trace.update(status='failed', error_code=exc.code)
            note()
        raise
    except Exception as exc:
        if trace is not None:
            trace.update(status='failed', error_type=type(exc).__name__)
            note()
        raise
    finally:
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def voice_visit(base, client, csrf, context, company_name, plan, carbon, dispatch,
                      audio, *, trace=None, checkpoint=None):
    from http.cookiejar import CookieJar
    from urllib.request import HTTPCookieProcessor
    from websockets.asyncio.client import connect

    processors = [handler for handler in client.opener.handlers
                  if isinstance(handler, HTTPCookieProcessor)]
    require(len(processors) == 1 and isinstance(processors[0].cookiejar, CookieJar),
            'voice', 'voice_cookie_missing')
    cookie = '; '.join(item.name + '=' + item.value for item in processors[0].cookiejar)
    require(bool(cookie), 'voice', 'voice_cookie_missing')
    async with connect(base.replace('https:', 'wss:') + '/api/demo/voice/live',
                       origin=base, additional_headers={'Cookie': cookie},
                       max_size=1_024_000, open_timeout=20) as socket:
        await socket.send(json.dumps({'type': 'start', 'csrf_token': csrf,
                                      'expected_context_version': context}))
        ready = json.loads(await asyncio.wait_for(socket.recv(), 25))
        require(ready.get('type') == 'ready', 'voice', 'voice_not_ready')
        result = await voice_turn(socket, audio, company_name, plan, context, carbon, dispatch,
                                  trace=trace, checkpoint=checkpoint)
        await socket.send(json.dumps({'type': 'stop'}))
        return result


def _post(client, stage, path, csrf, context, data):
    return client.json(stage, path, csrf=csrf,
                       body={**data, 'expected_context_version': context})


_OPERATOR_ACTION_FIELDS = ('company_id', 'context_version', 'offers', 'plan',
                           'plan_request', 'recovery', 'rfq', 'drafts',
                           'simulated_outbox', 'buyer_draft', 'carbon_assessment',
                           'dispatch')


def _operator_action_state(session):
    return {field: session.get(field) for field in _OPERATOR_ACTION_FIELDS}


def check_typed_operator_reads(client, stage, company_name, material_name, csrf,
                               context, plan, carbon, dispatch):
    """Exercise the normal text endpoint and retain only grounded observations."""
    before = client.json(stage, '/api/demo/session')
    require(before.get('company_id') == stage and before.get('context_version') == context
            and before.get('plan', {}).get('plan_id') == plan['plan_id']
            and before.get('dispatch') == dispatch
            and before.get('carbon_assessment', {}).get('result') == carbon,
            stage, 'operator_read_baseline_mismatch')
    baseline = _operator_action_state(before)
    observed = []
    for message, tool, workspace_key in (
            ('What are the emissions for this purchase?', 'read_carbon', 'carbon'),
            ('Show me the shipments for this company', 'read_shipments', 'shipments')):
        result = _post(client, stage, '/api/demo/operator', csrf, context,
                       {'message': message, 'idempotency_key': key()})
        step = result.get('agent_steps')
        require(result.get('company_id') == stage
                and result.get('context_version') == context
                and result.get('status') == 'completed'
                and result.get('read_only') is True
                and result.get('proposal') is None
                and isinstance(step, list) and len(step) == 1
                and isinstance(step[0], dict)
                and all(step[0].get(field) == expected for field, expected in {
                    'agent': 'workspace', 'role': 'deterministic',
                    'status': 'completed', 'model': 'none',
                    'provider_call': 'none', 'tool': tool}.items())
                and result.get('workspace') == {
                    workspace_key: carbon if tool == 'read_carbon' else dispatch},
                stage, 'operator_read_wrong_route')
        answer = result.get('assistant_text')
        require(isinstance(answer, str) and 0 < len(answer.strip()) <= 3000,
                stage, 'operator_read_empty_text')
        _guard_voice_facts(answer, carbon, dispatch)
        lowered = answer.casefold()
        require(company_name.casefold() in lowered
                and (material_name.casefold() in lowered
                     or plan['material'].casefold() in lowered),
                stage, 'operator_read_ungrounded_text')
        if tool == 'read_carbon':
            selected = next((row for row in carbon['suppliers']
                             if row['offer_id'] == plan['selected_offer_id']), None)
            selected = selected or carbon['suppliers'][0]
            estimate = selected['estimate']
            require(estimate['quantity'] in answer and estimate['unit'] in answer,
                    stage, 'operator_read_ungrounded_text')
            if estimate.get('total_kg_co2e') is None:
                require(re.search(r'\b(?:total|combined)\b[^.;]{0,60}\b\d', answer, re.I) is None,
                        stage, 'operator_read_unknown_total')
        else:
            linked = next((row for row in dispatch['shipments']
                           if row.get('plan_id') == plan['plan_id']
                           and row.get('offer_id') == plan['selected_offer_id']), None)
            require(linked is not None
                    and linked['material_name'].casefold() in lowered
                    and linked['quantity'] in answer
                    and linked['quantity_unit'] in answer
                    and linked['expected_arrival'] in answer
                    and 'planning' in lowered and 'not live' in lowered,
                    stage, 'operator_read_ungrounded_text')
        after = client.json(stage, '/api/demo/session')
        require(_operator_action_state(after) == baseline,
                stage, 'operator_action_state_changed')
        observed.append({'message': message, 'tool': tool,
                         'assistant_text': answer, 'workspace_verified': True,
                         'action_state_unchanged': True})
    return observed


def prepare_company(base, profile, row, checkpoint, voice_audio=None):
    """Prepare one company's reviewed quote, authoritative plan and evidence."""
    from procure.demo.quote_service import fixture_bytes, fixture_review_values

    cid = identifier(profile['company_id'], 'company')
    stage = cid
    client = Client(base)
    state = client.json(stage, '/api/demo/session')
    csrf = state.get('csrf_token')
    context = state.get('context_version')
    require(isinstance(csrf, str) and csrf and type(context) is int and context >= 1,
            stage, 'invalid_session')
    if state['company']['company_id'] != cid:
        row['stage'] = 'company_switch_pending'; checkpoint()
        state = _post(client, stage, '/api/demo/company', csrf, context,
                      {'company_id': cid, 'idempotency_key': key()})
        context = state['context_version']
    require(state['company']['company_id'] == cid and state.get('offers') == []
            and state.get('plan') is None and state.get('rfq') is None,
            stage, 'not_fresh_company_session')
    scenario = profile['quote_coverage']['scenarios'][0]
    material = scenario['material_id']
    require(material in {item['material_id'] for item in profile['materials']}
            and len(scenario['fixture_ids']) == 3, stage, 'scenario_invalid')
    row.update(stage='quotes_pending', material_id=material); checkpoint()
    offers = []
    for fixture_id in scenario['fixture_ids']:
        filename, source = fixture_bytes(fixture_id)
        expected_hash = 'sha256:' + hashlib.sha256(source).hexdigest()
        review = fixture_review_values(fixture_id)
        extraction = _post(client, stage, '/api/demo/extract', csrf, context,
                           {'fixture_id': fixture_id, 'mode': 'rules',
                            'idempotency_key': key()})
        offer = extraction['offer']
        require(extraction.get('replayed') is False and offer['fixture_id'] == fixture_id
                and offer['document']['document_id'] == expected_hash
                and offer['document']['filename'] == filename
                and offer['extractor'] == 'rules' and offer['synthetic'] is True,
                stage, 'quote_source_mismatch')
        for field, value in review.items():
            offer = _post(client, stage,
                          '/api/demo/offers/' + identifier(offer['offer_id'], stage) + '/correct',
                          csrf, context, {'field': field, 'value': value,
                                          'expected_version': offer['version'],
                                          'idempotency_key': key()})['offer']
            checked = corrected_field(offer, field)
            require(checked.get('status') == 'corrected' and checked.get('value') == value
                    and isinstance(checked.get('original'), dict),
                    stage, 'quote_review_mismatch')
        offer = _post(client, stage,
                      '/api/demo/offers/' + identifier(offer['offer_id'], stage) + '/save',
                      csrf, context, {'expected_version': offer['version'],
                                      'idempotency_key': key()})['offer']
        require(offer['status'] == 'saved' and offer['blocking_reasons'] == []
                and offer['document']['document_id'] == expected_hash,
                stage, 'quote_save_mismatch')
        offers.append(offer)
        row['reviewed_quote_count'] = len(offers); checkpoint()
    defaults = {name: value for name, value in scenario['buyer_defaults'].items()
                if name not in ('offer_ids', 'expected_versions', 'quantity_kg',
                                'current_stock_kg', 'additional_cost_per_kg')}
    versions = {offer['offer_id']: offer['version'] for offer in offers}
    request = {**defaults, 'offer_ids': list(versions), 'expected_versions': versions}
    row['stage'] = 'plan_pending'; checkpoint()
    plan = _post(client, stage, '/api/demo/plan', csrf, context, request)
    row['plan'] = check_plan(plan, cid, defaults, versions)
    row['stage'] = 'carbon_pending'; checkpoint()
    carbon_input = carbon_request(plan)
    carbon = _post(client, stage, '/api/demo/carbon/estimate', csrf, context, carbon_input)
    row['carbon'] = check_carbon(carbon, plan, context, carbon_input)
    row['stage'] = 'assessment_pending'; checkpoint()
    assessment = client.json(stage, '/api/demo/carbon/assessment')
    row['assessment'] = check_assessment(assessment, plan, context, carbon_input, carbon)
    exported = client.json(stage, '/api/demo/export')
    require(exported.get('company', {}).get('company_id') == cid
            and exported.get('context_version') == context
            and exported.get('carbon') == assessment['assessment']['result'],
            stage, 'carbon_export_mismatch')
    row['stage'] = 'dispatch_pending'; checkpoint()
    dispatch = client.json(stage, '/api/demo/dispatch')
    linked = check_dispatch(dispatch, plan, context)
    row['shipment'] = {'linked': True, 'own_company': True,
                       'selected_offer_version': True, 'source_present': True}
    row['stage'] = 'shipment_detail_pending'; checkpoint()
    detail = client.json(stage, '/api/demo/dispatch/' + identifier(linked['shipment_id'], stage))
    require(detail.get('company_id') == cid
            and detail.get('context_version') == context
            and detail.get('shipment') == linked,
            stage, 'shipment_detail_mismatch')
    material_name = next(item['name'] for item in profile['materials']
                         if item['material_id'] == material)
    row['stage'] = 'typed_operator_reads_pending'; checkpoint()
    row['typed_operator_reads'] = check_typed_operator_reads(
        client, stage, profile['name'], material_name, csrf, context,
        plan, carbon, dispatch)
    checkpoint()
    if voice_audio is not None:
        row['stage'] = 'controlled_voice_pending'; checkpoint()
        row['voice_trace'] = {}
        row['voice'] = asyncio.run(voice_visit(
            base, client, csrf, context, profile['name'], plan, carbon, dispatch,
            voice_audio, trace=row['voice_trace'], checkpoint=checkpoint))
        checkpoint()
    row['stage'] = 'recovery_pending'; checkpoint()
    delay_days = 60
    recovery = _post(client, stage, '/api/demo/recovery', csrf, context,
                     {'baseline_plan_id': plan['plan_id'], 'expected_versions': versions,
                      'delay_days_by_offer': {linked['offer_id']: delay_days},
                      'shipment_id': linked['shipment_id'], 'idempotency_key': key()})
    row['recovery'] = check_recovery(recovery, plan, linked, delay_days)
    row['stage'] = 'post_recovery_restore_pending'; checkpoint()
    stale_assessment = client.json(stage, '/api/demo/carbon/assessment')
    stale_export = client.json(stage, '/api/demo/export')
    require(stale_assessment.get('company_id') == cid
            and stale_assessment.get('context_version') == context
            and stale_assessment.get('assessment') is None
            and stale_export.get('carbon') is None,
            stage, 'stale_carbon_visible')
    row['stage'] = 'rfq_pending'; checkpoint()
    row['rfq'] = create_reviewed_rfq(client, stage, csrf, context,
                                     recovery['scenario'], versions)
    row.update(stage='completed', ok=True, rfq_unsent=True,
               export_matches_current=True, shipment_detail_verified=True,
               stale_carbon_hidden=True)
    checkpoint()


def run(base, expected_revision, output, *, voice_audio=None, voice_path=None):
    receipt = {'started_at': now(), 'expected_revision': expected_revision,
               'deployed_revision': None, 'mode': 'rules', 'automatic_retries': 0,
               'controlled_speech': voice_audio is not None or voice_path is not None,
               'physical_microphone_test': False,
               'ok': False, 'stage': 'created', 'companies': []}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)

    def checkpoint():
        temporary = output.with_name(output.name + '.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)

    try:
        base = base_url(base)
        if voice_path is not None:
            require(voice_audio is None, 'wav', 'duplicate_audio_input')
            voice_audio = pcm(voice_path)
        catalogue = Client(base)
        receipt['stage'] = 'health_pending'; checkpoint()
        health = catalogue.json('health', '/-/health')
        receipt['deployed_revision'] = health.get('revision')
        checkpoint()
        require(health.get('ready') is True and health.get('revision') == expected_revision
                and health.get('persistence') == 'firestore', 'health', 'wrong_revision_or_storage')
        receipt['stage'] = 'catalogue_pending'; checkpoint()
        profiles = catalogue.json('catalogue', '/api/demo/companies').get('companies')
        require(isinstance(profiles, list) and len(profiles) == 10
                and len({p['company_id'] for p in profiles}) == 10,
                'catalogue', 'company_coverage_mismatch')
        for profile in profiles:
            row = {'company_id': profile['company_id'], 'stage': 'started', 'ok': False}
            receipt['companies'].append(row); checkpoint()
            try:
                if voice_audio is None:
                    prepare_company(base, profile, row, checkpoint)
                else:
                    prepare_company(base, profile, row, checkpoint, voice_audio)
            except AcceptanceError as exc:
                row.update(stage=exc.stage, error_code=exc.code, http_status=exc.status)
                checkpoint()
                raise
            except Exception as exc:
                row.update(error_type=type(exc).__name__)
                checkpoint()
                raise
        require(any(row.get('carbon', {}).get('calibration_8000kg_1000km_verified') is True
                    for row in receipt['companies']),
                'carbon', 'calibration_not_observed')
        receipt.update(ok=True, stage='completed', completed_at=now())
        checkpoint()
        return 0
    except AcceptanceError as exc:
        receipt.update(stage=exc.stage, error_code=exc.code, http_status=exc.status,
                       completed_at=now())
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__, completed_at=now())
    checkpoint()
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--voice-wav', type=Path,
                        help='Optional caller-owned 16 kHz mono PCM WAV asking for carbon and shipments')
    args = parser.parse_args()
    return run(args.base, args.expected_revision, args.output,
               voice_path=args.voice_wav)


if __name__ == '__main__':
    raise SystemExit(main())
