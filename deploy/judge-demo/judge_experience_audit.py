#!/usr/bin/env python3
"""Exact-revision hosted judge read-path audit. Voice is explicit and chargeable.

Creates one private receipt exclusively. No retries, research, Hindi, recovery,
RFQ, mail, or approval calls. Run only after root approves a hosted revision.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlsplit
import wave

from company_intelligence_news_smoke import PooledClient
from smoke import AcceptanceError, MAX_RESPONSE_BYTES, TIMEOUT_SECONDS, base_url, key, require

PROTECTED = ('offers', 'plan', 'plan_request', 'recovery', 'rfq', 'buyer_draft',
             'drafts', 'simulated_outbox', 'dispatch', 'selected_material_id')
INVENTORY = 'Analyze our inventory and sourcing across all materials'
VOICE_LIMIT = 100
CAPTION_GRACE_SECONDS = 2
RESEARCH_TOOLS = {'prepare_research', 'confirm_research'}
SAFE_TOOL_LABEL = re.compile(r'[a-z][a-z0-9_]{0,59}\Z')
SAFE_STATUSES = {'proposal', 'completed', 'needs_input', 'ok', 'error',
                 'unavailable', 'applied', 'success', 'partial'}
PROPOSAL_KINDS = {'research', 'watch', 'recovery', 'rfq', 'simulate_email', 'review_quotes'}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def check(ok, stage, code):
    require(bool(ok), stage, code)


def short(value, limit=220):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def decimal(value, stage, code):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AcceptanceError(stage, code) from None
    check(result.is_finite(), stage, code)
    return result


class Receipt:
    def __init__(self, path, expected, base, voice):
        self.path = Path(path)
        self.data = {'started_at': stamp(), 'expected_revision': expected, 'base': base,
                     'controlled_speech': bool(voice), 'physical_microphone_test': False,
                     'ok': False, 'request_count': 0, 'companies': [], 'checks': {}, 'failure': None}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.flush()

    def flush(self):
        with self.path.open('w', encoding='utf-8') as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())


class Client(PooledClient):
    def __init__(self, base, receipt):
        super().__init__(base, max_transport_attempts=1)
        self.receipt = receipt

    def json(self, stage, path, *, body=None, csrf=None):
        check(self.receipt.data['request_count'] < 199, stage, 'request_bound_exceeded')
        self.receipt.data['request_count'] += 1
        self.receipt.flush()
        return super().json(stage, path, body=body, csrf=csrf)

    def route(self, stage, params, *, denied=False):
        """PooledClient's fixed-origin session and bounds, with encoded query."""
        check(self.receipt.data['request_count'] < 199, stage, 'request_bound_exceeded')
        self.receipt.data['request_count'] += 1
        self.receipt.flush()
        url = self.base + '/api/demo/judge/routes?' + urlencode(params)
        try:
            with self.session.get(url, headers={'Accept': 'application/json'},
                                  timeout=TIMEOUT_SECONDS, allow_redirects=False,
                                  stream=True, verify=True) as response:
                raw = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    raw.extend(chunk)
                    check(len(raw) <= MAX_RESPONSE_BYTES, stage, 'response_too_large')
                status = response.status_code
        except Exception as exc:
            # Network exception text can contain URLs or credentials.
            if exc.__class__.__module__.startswith('requests'):
                raise AcceptanceError(stage, 'transport_error') from None
            raise
        try:
            result = json.loads(raw)
        except (UnicodeError, ValueError):
            raise AcceptanceError(stage, 'invalid_json_response') from None
        check(isinstance(result, dict), stage, 'invalid_response')
        if denied:
            check(status == 422, stage, 'foreign_route_not_rejected_422')
            return result
        check(status == 200, stage, 'route_http_not_200')
        return result


def owned_route(profile):
    materials = {row['material_id'] for row in profile['materials']}
    for route in profile['routes']:
        for material in route['materials']:
            if material in materials:
                return route, material
    raise AcceptanceError(profile['company_id'], 'no_declared_route_material')


def route_check(view, profile, route, material, context, stage):
    check(view.get('schema') == 1 and view.get('company_id') == profile['company_id']
          and view.get('context_version') == context and view.get('route_id') == route['route_id']
          and view.get('material_id') == material and view.get('buyer_location_index') == 0,
          stage, 'route_context_or_schema')
    check(view.get('status') in ('partial', 'unavailable') and
          view.get('supplier_id') is None and view.get('offer_id') is None,
          stage, 'route_claims_unverified_binding')
    check(isinstance(view.get('limitations'), list) and view['limitations'] and
          isinstance(view.get('segments'), list) and view['segments'], stage, 'route_shape')
    for segment in view['segments']:
        check(isinstance(segment, dict) and segment.get('status') in ('approximate', 'unavailable')
              and isinstance(segment.get('label'), str) and segment['label']
              and isinstance(segment.get('source'), str) and segment['source'],
              stage, 'route_segment_shape')
        geometry = segment.get('geometry')
        check(geometry is None or isinstance(geometry, list), stage, 'route_geometry_shape')
        for point in geometry or []:
            check(isinstance(point, dict) and type(point.get('lat')) in (int, float)
                  and type(point.get('lng')) in (int, float)
                  and math.isfinite(point['lat']) and math.isfinite(point['lng'])
                  and -90 <= point['lat'] <= 90 and -180 <= point['lng'] <= 180,
                  stage, 'route_invalid_coordinate')
    forbidden = {'cost', 'cost_delta', 'total_cost', 'eta', 'emissions', 'carbon',
                 'selected_offer_id', 'shipment_id', 'plan_id', 'distance_km'}
    check(not forbidden.intersection(view), stage, 'route_derived_engine_field')
    check(not any(forbidden.intersection(s) for s in view['segments']),
          stage, 'segment_derived_engine_field')


def evidence_check(view, cid, context, stage):
    check(view.get('company_id') == cid and view.get('context_version') == context,
          stage, 'evidence_context')
    aggregate = view.get('aggregate')
    check(isinstance(aggregate, dict) and aggregate.get('schema') == 1,
          stage, 'evidence_schema')
    receipts = aggregate.get('receipts')
    check(isinstance(receipts, list) and len(receipts) <= 12 and
          type(aggregate.get('interpreted_articles')) is int and
          aggregate['interpreted_articles'] == len({r.get('input_source_id') for r in receipts}),
          stage, 'evidence_model_count')
    for receipt in receipts:
        check(receipt.get('status') == 'success' and
              str(receipt.get('model_id', '')).startswith('gemini-') and
              bool(receipt.get('model_version')) and bool(receipt.get('output')) and
              receipt.get('input_source_id') in receipt.get('evidence_refs', []),
              stage, 'ungrounded_model_receipt')
        url = receipt.get('source_url')
        try:
            parsed = urlsplit(url)
            good = (parsed.scheme in ('https', 'http') and bool(parsed.hostname) and
                    not parsed.username and not parsed.password and parsed.port in (None, 80, 443)
                    and len(url) <= 1200 and not any(ord(char) < 33 for char in url)
                    and '\\' not in url)
            host = parsed.hostname or ''
            if good:
                try:
                    good = ipaddress.ip_address(host).is_global
                except ValueError:
                    good = ('.' in host and not host.endswith(('.', '.local', '.localhost', '.internal'))
                            and all(re.fullmatch(r'[A-Za-z0-9-]+', part) for part in host.split('.')))
        except (TypeError, ValueError):
            good = False
        check(good, stage, 'invalid_evidence_source_url')
    return len(receipts)


def protected(session):
    return {field: session.get(field) for field in PROTECTED}


def last_tool_diagnostic(result, cid, context):
    value = result if isinstance(result, dict) else {}
    steps = value.get('agent_steps')
    steps = steps if isinstance(steps, list) else []
    labels = [step.get('tool') for step in steps[:12] if isinstance(step, dict)]
    proposal = value.get('proposal')
    proposal = proposal if isinstance(proposal, dict) else {}
    status = value.get('status')
    kind = proposal.get('kind')
    return {'tool_labels': [label if isinstance(label, str) and SAFE_TOOL_LABEL.fullmatch(label)
                            else 'invalid_label' for label in labels],
            'status': status if isinstance(status, str) and status in SAFE_STATUSES else 'other',
            'company_context_match': value.get('company_id') == cid and
            value.get('context_version') == context,
            'read_only': value.get('read_only') if type(value.get('read_only')) is bool else None,
            'has_proposal': value.get('proposal') is not None,
            'has_session': value.get('session') is not None,
            'has_action': value.get('action_result') is not None,
            'proposal_kind': kind if isinstance(kind, str) and kind in PROPOSAL_KINDS else None}


def steps_read_only(result, stage):
    check(result.get('proposal') is None and result.get('read_only') is True and
          result.get('action_result') is None and result.get('session') is None,
          stage, 'unexpected_action_or_proposal')
    steps = result.get('agent_steps')
    check(isinstance(steps, list) and steps, stage, 'missing_read_steps')
    for step in steps:
        check(step.get('tool') not in RESEARCH_TOOLS,
              stage, 'automatic_research_tool')
        check(step.get('provider_call') == 'none' and step.get('model') in ('none', 'deterministic'),
              stage, 'unexpected_model_or_provider_call')


def analytics_check(result, profile, stage):
    analytics = result.get('analytics') or (result.get('workspace') or {}).get('inventory_analytics')
    check(isinstance(analytics, dict) and analytics.get('company_id') == profile['company_id']
          and analytics.get('source') == 'fictional_company_profile', stage, 'canonical_analytics_missing')
    rows = analytics.get('materials')
    check(isinstance(rows, list) and len(rows) == len(profile['materials']), stage, 'analytics_material_count')
    by_id = {row.get('material_id'): row for row in rows}
    check(len(by_id) == len(rows) and set(by_id) == {m['material_id'] for m in profile['materials']},
          stage, 'analytics_material_ownership')
    for material in profile['materials']:
        row = by_id[material['material_id']]
        check(row.get('unit') == material['quantity_unit'] and row.get('stock') == material['stock']
              and row.get('synthetic') is True, stage, 'analytics_stock_or_unit')
        stock = decimal(material['stock'], stage, 'invalid_profile_stock')
        daily = decimal(material['consumption_per_day'], stage, 'invalid_profile_consumption')
        expected = stock / daily if daily > 0 else None
        actual = row.get('stock_cover_days')
        check((expected is None and actual is None) or
              (expected is not None and actual is not None and
               abs(decimal(actual, stage, 'invalid_cover') - expected) < Decimal('.000001')),
              stage, 'analytics_cover_mismatch')
    return len(rows)


def operator(client, session, message, stage):
    return client.json(stage, '/api/demo/operator', csrf=session['csrf_token'], body={
        'message': message, 'expected_context_version': session['context_version'],
        'idempotency_key': key()})


def company_visit(base, profile, receipt, *, flagship=False):
    cid = profile['company_id']
    row = {'company_id': cid, 'ok': False, 'stage': 'session'}
    receipt.data['companies'].append(row)
    receipt.flush()
    client = Client(base, receipt)
    try:
        health = client.json(cid + ':health', '/-/health')
        check(health.get('ready') is True and health.get('persistence') == 'firestore' and
              health.get('revision') == receipt.data['expected_revision'],
              cid + ':health', 'revision_changed_during_audit')
        session = client.json(cid + ':session', '/api/demo/session')
        check(session.get('synthetic') is True and bool(session.get('csrf_token')),
              cid + ':session', 'invalid_session')
        if session.get('company_id') != cid:
            session = client.json(cid + ':select', '/api/demo/company', csrf=session['csrf_token'], body={
                'company_id': cid, 'expected_context_version': session['context_version'],
                'idempotency_key': key()})
        check(session.get('company_id') == cid, cid + ':select', 'wrong_company')
        context = session['context_version']
        row.update(stage='selected', context_version=context)
        receipt.flush()
        if flagship:
            body = {'expected_context_version': context, 'idempotency_key': key()}
            started = client.json(cid + ':judge_start', '/api/demo/judge/start',
                                  csrf=session['csrf_token'], body=body)
            replay = client.json(cid + ':judge_replay', '/api/demo/judge/start',
                                 csrf=session['csrf_token'], body=body)
            check(started.get('seed_version') == 'judge-baseline-v1' and started.get('seeded') is True
                  and replay.get('seed_version') == started['seed_version'] and
                  replay.get('session') == started.get('session') and replay.get('replayed') is True,
                  cid + ':judge_start', 'seed_or_replay_mismatch')
            session = started['session']
            check(session.get('plan') and session.get('offers'), cid + ':judge_start', 'baseline_missing')
            row['judge_seeded'] = True
            receipt.flush()
        before = protected(session)
        route, material = owned_route(profile)
        params = {'route_id': route['route_id'], 'material_id': material,
                  'buyer_location_index': 0}
        mapped = client.route(cid + ':route', params)
        route_check(mapped, profile, route, material, context, cid + ':route')
        config = client.json(cid + ':config', '/api/demo/judge/config')
        check(type(config.get('maps_enabled')) is bool, cid + ':config', 'maps_enabled_not_boolean')
        evidence = client.json(cid + ':evidence', '/api/demo/judge/evidence')
        receipt_count = evidence_check(evidence, cid, context, cid + ':evidence')
        row.update(stage='reads', route_status=mapped['status'], maps_enabled=config['maps_enabled'],
                   genuine_model_receipts=receipt_count)
        receipt.flush()
        answer = operator(client, session, INVENTORY, cid + ':inventory')
        assistant = answer.get('assistant_text')
        check(answer.get('company_id') == cid and answer.get('context_version') == context and
              answer.get('status') == 'completed' and
              isinstance(assistant, str) and 20 <= len(assistant.strip()) <= 4000,
              cid + ':inventory', 'inventory_answer_context_or_length')
        steps_read_only(answer, cid + ':inventory')
        count = analytics_check(answer, profile, cid + ':inventory')
        row.update(stage='inventory', material_count=count)
        receipt.flush()
        if flagship:
            question = 'What if our current shipment is delayed by 7 days?'
            preview_result = operator(client, session, question, cid + ':shipment_preview')
            preview = (preview_result.get('workspace') or {}).get('judge_preview')
            steps_read_only(preview_result, cid + ':shipment_preview')
            check(preview_result.get('company_id') == cid and
                  preview_result.get('context_version') == context and
                  isinstance(preview, dict) and preview.get('read_only') is True and
                  preview.get('scenario_plan_id', 'missing') is None and
                  preview.get('mode') != 'profile_stock_cover' and
                  preview.get('baseline_plan_id') == session['plan']['plan_id'] and
                  bool(preview.get('impact')), cid + ':shipment_preview', 'baseline_preview_missing')
            row['shipment_preview'] = 'read_only_judge_preview'
        else:
            first = profile['materials'][0]
            question = f"What if {first['name']} is delayed by 14 days?"
            profile_result = operator(client, session, question, cid + ':profile_preview')
            steps_read_only(profile_result, cid + ':profile_preview')
            preview = (profile_result.get('workspace') or {}).get('judge_preview')
            profile_answer = profile_result.get('assistant_text')
            graceful = (profile_result.get('status') == 'needs_clarification'
                        and isinstance(profile_answer, str)
                        and 1 <= len(profile_answer.strip()) <= 2000)
            valid_preview = (isinstance(preview, dict) and preview.get('read_only') is True
                             and preview.get('scenario_plan_id', 'missing') is None
                             and preview.get('mode') == 'profile_stock_cover')
            check(profile_result.get('company_id') == cid and
                  profile_result.get('context_version') == context and
                  (valid_preview or graceful and preview is None),
                  cid + ':profile_preview', 'profile_preview_or_graceful_input_missing')
            row['profile_preview'] = 'profile' if valid_preview else 'needs_input'
        after = client.json(cid + ':after', '/api/demo/session')
        check(after.get('company_id') == cid and after.get('context_version') == context and
              protected(after) == before, cid + ':after', 'protected_state_changed')
        row.update(stage='complete', ok=True)
        receipt.flush()
    finally:
        client.close()


def foreign_route(base, profiles, receipt):
    owner = profiles[0]
    owned_ids = {row['route_id'] for row in owner['routes']}
    choices = [(profile, route, material)
               for profile in profiles[1:]
               for route in profile['routes']
               for material in route['materials']
               if route['route_id'] not in owned_ids and
               material in {row['material_id'] for row in profile['materials']}]
    check(bool(choices), 'foreign:route', 'no_foreign_route_fixture')
    foreign, route, material = choices[0]
    client = Client(base, receipt)
    try:
        session = client.json('foreign:session', '/api/demo/session')
        if session['company_id'] != owner['company_id']:
            session = client.json('foreign:select', '/api/demo/company', csrf=session['csrf_token'], body={
                'company_id': owner['company_id'], 'expected_context_version': session['context_version'],
                'idempotency_key': key()})
        before = client.json('foreign:before', '/api/demo/session')
        client.route('foreign:route', {'route_id': route['route_id'], 'material_id': material,
                     'buyer_location_index': 0}, denied=True)
        after = client.json('foreign:after', '/api/demo/session')
        check(after['company_id'] == before['company_id'] and
              after['context_version'] == before['context_version'] and
              protected(after) == protected(before), 'foreign:after', 'foreign_route_changed_context')
        receipt.data['checks']['foreign_route_422'] = True
        receipt.flush()
    finally:
        client.close()


def read_wav(path):
    with wave.open(str(path), 'rb') as source:
        check((source.getnchannels(), source.getsampwidth(), source.getframerate(),
               source.getcomptype()) == (1, 2, 16000, 'NONE'), 'voice:input', 'wav_format')
        frames = source.getnframes()
        check(16000 <= frames <= 320000, 'voice:input', 'wav_duration')
        return source.readframes(frames)


async def send_audio(socket, data, started):
    for offset in range(0, len(data), 3200):
        await socket.send(json.dumps({'type': 'audio', 'data':
            base64.b64encode(data[offset:offset + 3200]).decode('ascii')}))
        started.set()
        await asyncio.sleep(.1)
    await socket.send(json.dumps({'type': 'audio_end'}))


def voice_intent(kind, caption):
    words = set(re.findall(r'[a-z]+|\d+', caption.casefold()))
    if kind == 'world-news':
        named_news = bool(words & {'news', 'headline', 'headlines'}) and bool(
            words & {'world', 'global', 'company', 'business', 'procurement', 'our', 'my'})
        world_business = bool(words & {'world', 'global'}) and bool(
            words & {'business', 'procurement', 'company', 'supply'}) and bool(
            words & {'happening', 'happens', 'affect', 'affects', 'impact', 'impacts'})
        return named_news or world_business
    return (bool(words & {'delay', 'delayed'}) and
            bool(words & {'shipment', 'current'}) and bool(words & {'seven', '7'}))


def caption_intent(kind, parts):
    return voice_intent(kind, ' '.join(parts)) or voice_intent(kind, ''.join(parts))


def grounded_output(kind, caption, grounding):
    words = re.findall(r'[a-z]+|\d+', caption.casefold())
    if len(words) < 8 or len(caption.strip()) < 35:
        return False
    source = set(re.findall(r'[a-z]{4,}', grounding.casefold())) - {
        'this', 'that', 'with', 'from', 'your', 'there', 'which', 'would',
        'could', 'about', 'current', 'company', 'days', 'source', 'sources',
        'news', 'world', 'business', 'procurement', 'supply', 'material',
        'materials', 'headline', 'headlines', 'delay', 'delayed', 'shipment',
        'plan', 'recovery'}
    overlap = set(words) & source
    if kind == 'world-news':
        return bool(set(words) & {'news', 'headline', 'headlines', 'world', 'global',
                                  'supply', 'procurement', 'business', 'material', 'materials'}) and bool(overlap)
    return bool(set(words) & {'seven', '7'}) and bool(set(words) & {'delay', 'delayed'}) and bool(
        set(words) & {'shipment', 'plan', 'recovery', 'cost', 'stock', 'arrival'}) and bool(overlap)


async def voice_turn(socket, data, kind, cid, context, baseline_plan_id, row, receipt):
    stream_started = asyncio.Event()
    sender = asyncio.create_task(send_audio(socket, data, stream_started))
    started = time.monotonic()
    counts = Counter()
    captions = []
    tools = []
    input_parts, output_events = [], []
    first_input_event = None
    validated_tool = False
    grounding = ''
    tool_before_input_caption = False
    first_audio = None
    tool_time = None
    audio_bytes = 0
    completion_deadline = None
    answer_completion_seen = False
    pre_answer_completions = 0
    research_phases = []

    def trace():
        saw_input = caption_intent(kind, input_parts)
        output_parts = [part for _, part in output_events]
        spoke_answer = (grounded_output(kind, ' '.join(output_parts), grounding) or
                        grounded_output(kind, ''.join(output_parts), grounding))
        row['voice_trace'] = {'events': dict(counts), 'tools': tools[-20:],
                              'captions': captions, 'output_audio_bytes': audio_bytes,
                              'recognized_intent': saw_input, 'qualifying_tool': validated_tool,
                              'grounded_output_caption': spoke_answer,
                              'pre_answer_completions': pre_answer_completions,
                              'tool_before_input_caption': tool_before_input_caption,
                              'research_phases': research_phases,
                              'tool_latency_ms': tool_time, 'first_audio_ms': first_audio}
        return saw_input, spoke_answer

    try:
        async with asyncio.timeout(VOICE_LIMIT):
            while True:
                if completion_deadline is None:
                    raw = await socket.recv()
                else:
                    remaining = completion_deadline - time.monotonic()
                    if remaining <= 0:
                        saw_input, spoke_answer = trace()
                        if saw_input and spoke_answer and validated_tool and audio_bytes >= 10000:
                            break
                        completion_deadline = None
                        continue  # An incomplete caption needs a later answer completion.
                    try:
                        raw = await asyncio.wait_for(socket.recv(), remaining)
                    except TimeoutError:
                        saw_input, spoke_answer = trace()
                        if saw_input and spoke_answer and validated_tool and audio_bytes >= 10000:
                            break
                        completion_deadline = None
                        continue
                event = json.loads(raw)
                event_type = event.get('type')
                counts[event_type] += 1
                index = sum(counts.values())
                if event_type == 'error':
                    code = event.get('code')
                    raise AcceptanceError('voice:stream', code if isinstance(code, str) and
                                          re.fullmatch(r'[a-z0-9_]{1,60}', code) else 'voice_error')
                if event_type == 'research_progress':
                    phase = event.get('phase')
                    research_phases.append(phase if phase in {
                        'checking_consent', 'awaiting_consent', 'started',
                        'completed', 'unavailable'} else 'unknown')
                    trace()
                    receipt.flush()
                    raise AcceptanceError('voice:research', 'automatic_research_progress')
                if event_type == 'transcript':
                    direction = event.get('direction')
                    caption = short(event.get('text'), 240)
                    captions.append({'direction': direction, 'text': caption})
                    captions[:] = captions[-12:]
                    if direction in ('input', 'user'):
                        if first_input_event is None:
                            first_input_event = index
                        input_parts.append(caption)
                        input_parts[:] = input_parts[-16:]
                    elif direction == 'output' and stream_started.is_set():
                        output_events.append((index, caption))
                        output_events[:] = output_events[-16:]
                elif event_type == 'tool_result':
                    result = event.get('result') or {}
                    row['last_tool_diagnostic'] = last_tool_diagnostic(result, cid, context)
                    receipt.flush()  # Keep the failing tool's safe shape before strict checks.
                    labels = [step.get('tool') for step in result.get('agent_steps', [])]
                    tools.extend(label if isinstance(label, str) and SAFE_TOOL_LABEL.fullmatch(label)
                                 else 'invalid_label' for label in labels if label)
                    if any(isinstance(label, str) and label in RESEARCH_TOOLS for label in labels):
                        trace()
                        receipt.flush()
                        raise AcceptanceError('voice:research', 'automatic_research_tool')
                    check(result.get('company_id') == cid and result.get('context_version') == context,
                          'voice:tool', 'tool_context_mismatch')
                    check(result.get('proposal') is None and result.get('action_result') is None and
                          result.get('session') is None and result.get('read_only') is True,
                          'voice:tool', 'voice_mutation_or_proposal')
                    valid = False
                    if kind == 'what-if':
                        preview = (result.get('workspace') or {}).get('judge_preview')
                        valid = (isinstance(preview, dict) and
                                 preview.get('read_only') is True and
                                 preview.get('scenario_plan_id', 'missing') is None and
                                 preview.get('mode') != 'profile_stock_cover' and
                                 preview.get('baseline_plan_id') == baseline_plan_id and
                                 bool(preview.get('impact')) and
                                 bool(re.search(r'\b(?:7|seven)[- ]?day', str(preview.get('summary', '')), re.I)))
                        evidence_text = str(preview.get('summary', '')) if valid else ''
                    else:
                        brief = (result.get('workspace') or {}).get('news_brief')
                        answer = result.get('assistant_text')
                        headlines = brief.get('headlines') if isinstance(brief, dict) else None
                        headlines = headlines if isinstance(headlines, list) else []
                        valid = (bool({'read_news', 'canonical_news'} & set(labels))
                                 and isinstance(brief, dict) and
                                 brief.get('company_id') == cid and
                                 bool(result.get('evidence_ids') or headlines)
                                 and isinstance(answer, str) and
                                 1 <= len(answer.strip()) <= 2000)
                        evidence_text = (answer + ' ' + ' '.join(
                            str(item.get('title', '')) for item in headlines[:3]
                            if isinstance(item, dict))) if valid else ''
                    if valid and stream_started.is_set() and not validated_tool:
                        validated_tool = True
                        grounding = evidence_text
                        tool_before_input_caption = not caption_intent(kind, input_parts)
                        tool_time = round((time.monotonic() - started) * 1000)
                elif event_type == 'audio' and validated_tool:
                    check(event.get('sample_rate_hertz') == 24000, 'voice:audio', 'output_sample_rate')
                    audio_bytes += len(base64.b64decode(event.get('data', ''), validate=True))
                    answer_completion_seen = False
                    completion_deadline = None  # More answer audio requires a later completion.
                    if first_audio is None:
                        first_audio = round((time.monotonic() - started) * 1000)
                trace()
                if event_type in ('tool_result', 'transcript', 'turn_complete'):
                    receipt.flush()
                if event_type == 'turn_complete':
                    if first_input_event is None and not validated_tool:
                        output_events.clear()
                        continue  # An ungrounded greeting cannot finish the question.
                    if not validated_tool or audio_bytes < 10000:
                        answer_completion_seen = False
                        pre_answer_completions += 1
                        trace()
                        receipt.flush()
                        continue  # A tool-transition completion precedes the spoken answer.
                    await sender
                    answer_completion_seen = True
                    completion_deadline = time.monotonic() + CAPTION_GRACE_SECONDS
                if answer_completion_seen and completion_deadline is None:
                    saw_input, spoke_answer = trace()
                    if saw_input and spoke_answer and validated_tool and audio_bytes >= 10000:
                        break  # Late caption after grace belongs to the completed answer.
            saw_input, spoke_answer = trace()
            check(answer_completion_seen, 'voice:turn', 'turn_not_complete')
            check(saw_input, 'voice:turn', 'input_intent_missing')
            check(validated_tool and spoke_answer and audio_bytes >= 10000,
                  'voice:turn', 'ungrounded_or_silent_turn')
            row['voice_trace']['total_ms'] = round((time.monotonic() - started) * 1000)
            row['voice_trace']['caption_grace_ms'] = round(CAPTION_GRACE_SECONDS * 1000)
            receipt.flush()
            return
    finally:
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


async def failed_voice_state(client, row, receipt, cid, context, before):
    try:
        after = await asyncio.to_thread(client.json, 'voice:after_failure', '/api/demo/session')
        row['failed_voice_state'] = {
            'protected_state_unchanged': protected(after) == before,
            'owned_context': after.get('company_id') == cid and
            after.get('context_version') == context}
        receipt.flush()
    except Exception:
        pass  # A diagnostic read must not replace the original voice failure.


async def voice_visit(base, profile, wav, kind, receipt):
    from websockets.asyncio.client import connect
    cid = profile['company_id']
    row = {'company_id': cid, 'kind': kind, 'ok': False, 'stage': 'session'}
    receipt.data['voice'] = row
    receipt.flush()
    data = read_wav(wav)
    client = Client(base, receipt)
    try:
        health = await asyncio.to_thread(client.json, 'voice:health', '/-/health')
        check(health.get('ready') is True and health.get('persistence') == 'firestore' and
              health.get('revision') == receipt.data['expected_revision'],
              'voice:health', 'revision_changed_during_audit')
        session = await asyncio.to_thread(client.json, 'voice:session', '/api/demo/session')
        if session['company_id'] != cid:
            session = await asyncio.to_thread(client.json, 'voice:select', '/api/demo/company',
                csrf=session['csrf_token'], body={'company_id': cid,
                'expected_context_version': session['context_version'], 'idempotency_key': key()})
        context = session['context_version']
        started = await asyncio.to_thread(client.json, 'voice:judge_start', '/api/demo/judge/start',
            csrf=session['csrf_token'], body={'expected_context_version': context, 'idempotency_key': key()})
        session = started['session']
        check(bool(session.get('plan')), 'voice:judge_start', 'baseline_missing')
        before = protected(session)
        await asyncio.to_thread(client.json, 'voice:news', '/api/demo/news')
        row['stage'] = 'socket'
        receipt.flush()
        cookie = '; '.join(c.name + '=' + c.value for c in client.session.cookies)
        try:
            async with connect(base.replace('https:', 'wss:') + '/api/demo/voice/live',
                               origin=base, additional_headers={'Cookie': cookie},
                               max_size=1_024_000, open_timeout=20, close_timeout=5) as socket:
                await socket.send(json.dumps({'type': 'start', 'csrf_token': session['csrf_token'],
                                              'expected_context_version': context}))
                ready = json.loads(await asyncio.wait_for(socket.recv(), 25))
                check(ready.get('type') == 'ready', 'voice:ready', 'not_ready')
                row['stage'] = 'turn'
                receipt.flush()
                voice_failed = False
                try:
                    await voice_turn(socket, data, kind, cid, context,
                                     session['plan']['plan_id'], row, receipt)
                except Exception:
                    voice_failed = True
                    raise
                finally:
                    try:
                        await socket.send(json.dumps({'type': 'stop'}))
                    except Exception:
                        if not voice_failed:
                            raise
        except Exception:
            await failed_voice_state(client, row, receipt, cid, context, before)
            raise
        after = await asyncio.to_thread(client.json, 'voice:after', '/api/demo/session')
        check(after.get('company_id') == cid and after.get('context_version') == context and
              protected(after) == before, 'voice:after', 'protected_state_changed')
        row.update(ok=True, stage='complete')
        receipt.flush()
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--company', action='append', help='Company ID; repeat to select several')
    parser.add_argument('--voice-wav', type=Path)
    parser.add_argument('--voice-kind', choices=('world-news', 'what-if'))
    parser.add_argument('--voice-company', default='solara-cables')
    args = parser.parse_args()
    if bool(args.voice_kind) != bool(args.voice_wav):
        parser.error('--voice-kind and --voice-wav must be supplied together')
    if not args.expected_revision.strip() or args.expected_revision.strip() == 'local':
        parser.error('expected-revision must name a deployed revision')
    base = base_url(args.base)
    receipt = Receipt(args.output, args.expected_revision, base, args.voice_wav)
    client = Client(base, receipt)
    try:
        health = client.json('health', '/-/health')
        check(health.get('ready') is True and health.get('persistence') == 'firestore' and
              health.get('revision') == args.expected_revision,
              'health', 'not_ready_firestore_or_exact_revision')
        receipt.data['checks']['health_exact_revision_firestore'] = True
        profiles = client.json('catalogue', '/api/demo/companies').get('companies')
        check(isinstance(profiles, list) and len(profiles) == 10 and
              len({p.get('company_id') for p in profiles}) == 10, 'catalogue', 'not_ten_unique_profiles')
        selected = set(args.company or [p['company_id'] for p in profiles])
        check(selected <= {p['company_id'] for p in profiles}, 'args', 'unknown_company')
        check(args.voice_company == 'solara-cables', 'args', 'voice_company_requires_owned_solara_baseline')
        receipt.data['checks']['catalogue_ten'] = True
        receipt.flush()
        for profile in profiles:
            if profile['company_id'] in selected:
                company_visit(base, profile, receipt, flagship=profile['company_id'] == 'solara-cables')
        foreign_route(base, profiles, receipt)
        if args.voice_wav:
            solara = next(p for p in profiles if p['company_id'] == args.voice_company)
            asyncio.run(voice_visit(base, solara, args.voice_wav, args.voice_kind, receipt))
        final_health = client.json('health_final', '/-/health')
        check(final_health.get('ready') is True and
              final_health.get('persistence') == 'firestore' and
              final_health.get('revision') == args.expected_revision,
              'health_final', 'revision_changed_during_audit')
        check(receipt.data['request_count'] < 200, 'requests', 'request_bound_exceeded')
        receipt.data['ok'] = True
    except AcceptanceError as exc:
        code = exc.code if isinstance(exc.code, str) and re.fullmatch(r'[a-z0-9_]{1,80}', exc.code) else 'invalid_error_code'
        receipt.data['failure'] = {'stage': exc.stage, 'code': code, 'status': exc.status}
    except (KeyError, TypeError, ValueError, IndexError, AssertionError):
        receipt.data['failure'] = {'stage': 'response', 'code': 'invalid_response_shape'}
    except (TimeoutError, asyncio.TimeoutError):
        receipt.data['failure'] = {'stage': 'voice', 'code': 'turn_timeout'}
    except Exception:
        receipt.data['failure'] = {'stage': 'runtime', 'code': 'unexpected_error'}
    finally:
        client.close()
        receipt.data['finished_at'] = stamp()
        receipt.flush()
    print(json.dumps({'ok': receipt.data['ok'], 'request_count': receipt.data['request_count'],
                      'failure': receipt.data['failure'], 'output': str(receipt.path)}))
    return 0 if receipt.data['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
