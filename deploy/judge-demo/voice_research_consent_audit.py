#!/usr/bin/env python3
"""One controlled-speech, two-turn public research consent audit.

Explicitly chargeable only with --execute-public-research. No HTTP approval,
retry, business action, or simulated consent. Root runs this after deployment.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit, urlunsplit

from judge_experience_audit import (AcceptanceError, PooledClient, base_url, check,
                                    protected, read_wav, send_audio, short, stamp)

COMPANY = 'solara-cables'
QUESTION = "Please do fresh external research on today's London Metal Exchange copper market news."
QUERY = QUESTION + ' Relevance to the electrical cable manufacturing industry.'
DESTINATION = 'Google Search via Vertex AI'
SHARED_DATA = 'Only this public query is sent to Google Search via Vertex AI.'
TOTAL_SECONDS = 200
CAPTION_GRACE_SECONDS = 2
MONTH = r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b|\b' + MONTH + r'\s+\d{1,2}(?:st|nd|rd|th)?\b|\b\d{1,2}(?:st|nd|rd|th)?\s+' + MONTH + r'\b', re.I)


def words(value):
    return re.findall(r'[a-z]+|\d+', value.casefold())


def compact(value):
    return ''.join(words(value))


def captions_match(parts, predicate):
    return predicate(' '.join(parts)) or predicate(''.join(parts))


def question_heard(value):
    tokens = set(words(value))
    return {'research', 'copper', 'market', 'news', 'london', 'metal', 'exchange'} <= tokens and bool(tokens & {'today', 'todays'})


def consent_heard(value):
    tokens = set(words(value))
    return {'yes', 'confirm', 'start', 'research'} <= tokens and bool(tokens & {'online', 'public'})


def same_public_query(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 400:
        return False
    normalized = value.casefold().replace('’', "'").replace("today's", 'today')
    if not re.fullmatch(r"[a-z .,?!()':-]+", normalized):
        return False
    tokens = words(normalized)
    allowed = (set(words(QUERY)) - {'s'}) | {
        'find', 'look', 'up', 'search', 'for', 'about', 'provide', 'give', 'me',
        'a', 'an', 'get', 'latest', 'current', 'todays', 'lme', 'headlines', 'updates',
        'developments', 'reports', 'relevant', 'regarding', 'related', 'and'}
    token_set = set(tokens)
    joined = ' '.join(tokens)
    return (bool(tokens) and token_set <= allowed and
            ('london metal exchange' in joined or 'lme' in token_set) and
            'copper' in token_set and 'market' in token_set and
            bool(token_set & {'news', 'headlines', 'updates', 'developments', 'reports'}) and
            bool(token_set & {'today', 'todays', 'current', 'latest'}) and
            'electrical cable manufacturing industry' in joined)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:16] if isinstance(value, str) else None


def tool_diagnostic(result, context):
    candidate = result.get('proposal')
    candidate = candidate if isinstance(candidate, dict) else {}
    params = candidate.get('parameters')
    params = params if isinstance(params, dict) else {}
    proposal_id = candidate.get('proposal_id')
    query = params.get('query')
    return {
        'query_digest': digest(query), 'query_length': len(query) if isinstance(query, str) else None,
        'assistant_text_length': len(result.get('assistant_text'))
        if isinstance(result.get('assistant_text'), str) else None,
        'proposal_id_digest': digest(proposal_id),
        'scope': {'status_proposal': result.get('status') == 'proposal',
                  'company_matches': result.get('company_id') == COMPANY,
                  'context_matches': result.get('context_version') == context,
                  'read_only': result.get('read_only') is True,
                  'research_kind': candidate.get('kind') == 'research',
                  'proposal_context_matches': candidate.get('context_version') == context,
                  'public_query': same_public_query(query),
                  'parameter_keys_exact': set(params) == {'query', 'destination', 'shared_data'},
                  'destination_matches': params.get('destination') == DESTINATION,
                  'shared_data_matches': params.get('shared_data') == SHARED_DATA}}


def proposal_spoken(value, query):
    normalized = compact(value)
    return compact(query) in normalized and 'confirmresearch' in normalized


def dated(value):
    return isinstance(value, str) and bool(DATE.search(value))


def public_https(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 1200 or any(ord(c) < 33 for c in value):
        return False
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').lower()
        if (parts.scheme != 'https' or not host or parts.username or parts.password or
                parts.port not in (None, 443) or '\\' in value or host.endswith('.') or
                host.endswith(('.local', '.localhost', '.internal'))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return len(host.split('.')) >= 2 and all(
                re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                for part in host.split('.'))
    except ValueError:
        return False


def public_text(value, limit):
    """Return bounded plain public text, or reject before any raw text is recorded."""
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        return None
    if (any(ord(char) < 32 and char not in '\n\r\t' for char in value) or
            '<' in value or '>' in value or
            re.search(r'\b[^\s@]+@[^\s@]+\.[a-z]{2,}\b', value, re.I) or
            re.search(r'\b(?:api[_ -]?key|csrf|bearer|authorization|password|secret|'
                      r'session[_ -]?token|access[_ -]?token)\b', value, re.I)):
        return None
    for match in re.finditer(r'https?://[^\s)\]]+', value, re.I):
        if not public_https(match.group().rstrip('.,;')):
            return None
    # Inline links can have tracking or private query parameters. The separately
    # validated source list supplies public URLs without query strings.
    plain = re.sub(r'https?://[^\s)\]]+', '[public source URL]', value)
    plain = re.sub(r'\[([^\]]{1,200})\]\(\[public source URL\]\)', r'\1', plain)
    plain = re.sub(r'[`*_#~]', '', plain)
    plain = re.sub(r'\s+', ' ', plain).strip()
    return plain if plain else None


def public_result(research):
    return {
        'answer': public_text(research['answer'], 8000)[:2000],
        'sources': [{'title': public_text(source['title'], 200),
                     'url': urlunsplit((*urlsplit(source['url'])[:3], '', ''))}
                    for source in research['sources'][:24]],
        'checked_at': research['checked_at'],
        'has_explicit_date': dated(research['answer']),
        'source_count': len(research['sources']),
    }


def completed_research(result, context, query):
    check(isinstance(result, dict) and result.get('company_id') == COMPANY and
          result.get('context_version') == context and result.get('status') == 'completed' and
          result.get('proposal') is None and result.get('read_only') is False and
          (result.get('action_result') or {}).get('tool') == 'confirm_research' and
          (result.get('session') or {}).get('company_id') == COMPANY and
          (result.get('session') or {}).get('context_version') == context,
          'consent:tool', 'confirmation_not_applied_to_current_context')
    research = (result.get('workspace') or {}).get('research')
    check(isinstance(research, dict) and research.get('status') == 'completed' and
          research.get('query') == query and isinstance(research.get('research_id'), str) and
          bool(research['research_id']) and research.get('search_suggestions_html') is None,
          'consent:research', 'completed_research_missing')
    answer = research.get('answer')
    check(isinstance(answer, str) and 30 <= len(answer.strip()) <= 8000 and
          public_text(answer, 8000) is not None,
          'consent:research', 'invalid_or_private_answer')
    sources = research.get('sources')
    check(isinstance(sources, list) and 1 <= len(sources) <= 24,
          'consent:research', 'no_public_sources')
    ids = set()
    for source in sources:
        check(isinstance(source, dict) and isinstance(source.get('source_id'), str) and
              source['source_id'] not in ids and public_https(source.get('url')) and
              public_text(source.get('title'), 200) is not None,
              'consent:research', 'invalid_public_source')
        ids.add(source['source_id'])
    checked = research.get('checked_at')
    try:
        checked_time = datetime.fromisoformat(checked.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        raise AcceptanceError('consent:research', 'invalid_checked_at') from None
    check(checked_time.tzinfo is not None and
          datetime.now(timezone.utc) - timedelta(minutes=5) <= checked_time <=
          datetime.now(timezone.utc) + timedelta(minutes=5),
          'consent:research', 'invalid_checked_at')
    return research


def spoken_answer(value, research):
    if not isinstance(value, str) or not 40 <= len(value.strip()) <= 2000 or not public_text(value, 2000):
        return False
    grounded = set(words(research['answer'] + ' ' + ' '.join(
        source['title'] for source in research['sources']))) - {
        'the', 'and', 'this', 'that', 'with', 'from', 'your', 'there', 'today',
        'copper', 'market', 'news', 'source', 'sources', 'research', 'public'}
    return len(set(words(value)) & grounded) >= 2


class Receipt:
    def __init__(self, path, base, revision):
        self.path = Path(path)
        self.data = {'started_at': stamp(), 'base': base, 'expected_revision': revision,
                     'fixed_question': QUESTION, 'expected_public_query': QUERY,
                     'controlled_speech': True, 'physical_microphone_test': False,
                     'execute_public_research': True, 'automatic_retries': 0,
                     'ok': False, 'stage': 'created', 'failure': None,
                     'turns': [{'kind': 'research_request'}, {'kind': 'spoken_consent'}]}
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        self.flush()

    def flush(self):
        with self.path.open('w', encoding='utf-8') as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())


def trace(row, counts, tools, progress, captions, audio_bytes, start, query_validated):
    visible_tools = [item['name'] if query_validated and item['validated_at_capture'] and
                     re.fullmatch(r'[a-z_]{1,60}', item['name']) else
                     {'digest': digest(item['name']), 'length': len(item['name'])}
                     for item in tools[-16:]]
    visible_captions = []
    for item in captions[-16:]:
        direction = item['direction'] if isinstance(item['direction'], str) and item['direction'] in {
            'input', 'output'} else 'other'
        visible_captions.append(
            {'direction': direction, 'text': public_text(item['text'], 240)}
            if query_validated and item['validated_at_capture'] and
            public_text(item['text'], 240) is not None else
            {'direction': direction, 'digest': digest(item['text']), 'length': len(item['text'])})
    row.update(events=dict(counts), tools=visible_tools, progress=progress[-12:],
               captions=visible_captions, output_audio_bytes=audio_bytes,
               elapsed_ms=round((time.monotonic() - start) * 1000))


async def receive_turn(socket, data, kind, context, receipt, proposal=None):
    row = receipt.data['turns'][0 if kind == 'request' else 1]
    started = time.monotonic()
    streaming = asyncio.Event()
    sender = asyncio.create_task(send_audio(socket, data, streaming))
    counts, tools, progress, captions = Counter(), [], [], []
    input_parts, output_parts = [], []
    audio_bytes = 0
    current_proposal = proposal
    research = None
    progress_started = progress_completed = consent_checked = consent_armed = False
    deadline = None
    answer_completion_seen = False
    pre_answer_completions = 0

    def turn_ready():
        heard = captions_match(input_parts, question_heard if kind == 'request' else consent_heard)
        if kind == 'request':
            return (current_proposal is not None and heard and consent_armed and
                    captions_match(output_parts, lambda text: proposal_spoken(
                        text, current_proposal['parameters']['query'])) and audio_bytes >= 10000 and
                    not progress_started and not progress_completed)
        return (heard and progress_started and progress_completed and research is not None and
                spoken_answer(' '.join(output_parts), research) and audio_bytes >= 10000)

    try:
        while True:
            if deadline is None:
                raw = await socket.recv()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if turn_ready():
                        break
                    deadline = None
                    continue  # Wait for a later answer completion within the total cap.
                try:
                    raw = await asyncio.wait_for(socket.recv(), remaining)
                except TimeoutError:
                    if turn_ready():
                        break
                    deadline = None
                    continue
            frame = json.loads(raw)
            event = frame.get('type')
            counts[event if event in {'error', 'transcript', 'tool_result',
                                      'research_progress', 'audio', 'turn_complete'} else 'other'] += 1
            if event == 'error':
                code = frame.get('code')
                raise AcceptanceError('voice:stream', code if isinstance(code, str) and
                                      re.fullmatch(r'[a-z0-9_]{1,60}', code) else 'voice_error')
            if event == 'tool_result':
                result = frame.get('result')
                result = result if isinstance(result, dict) else {}
                row.setdefault('tool_diagnostics', []).append(tool_diagnostic(result, context))
                row['tool_diagnostics'] = row['tool_diagnostics'][-8:]
                receipt.flush()  # Preserve safe scope evidence before any tool assertion.
            if event == 'transcript':
                direction = frame.get('direction')
                text = short(frame.get('text'), 240)
                captions.append({'direction': direction, 'text': text,
                                 'validated_at_capture': current_proposal is not None and
                                 (kind == 'request' or direction == 'input' or research is not None)})
                captions[:] = captions[-16:]
                if direction == 'input':
                    input_parts.append(text)
                    input_parts[:] = input_parts[-16:]
                elif direction == 'output' and streaming.is_set():
                    output_parts.append(text)
                    output_parts[:] = output_parts[-16:]
            elif event == 'tool_result':
                if kind == 'request':
                    check((result.get('action_result') or {}).get('tool') != 'confirm_research' and
                          (result.get('workspace') or {}).get('research') is None and
                          result.get('read_only') is not False,
                          'request:tool', 'auto_confirm_before_spoken_consent')
                labels = [step.get('tool') for step in result.get('agent_steps', [])
                          if isinstance(step, dict)]
                tools.extend({'name': short(label, 60),
                              'validated_at_capture': kind == 'consent' or current_proposal is not None}
                             for label in labels if isinstance(label, str))
                if kind == 'request':
                    candidate = result.get('proposal')
                    if isinstance(candidate, dict) and candidate.get('kind') == 'research':
                        params = candidate.get('parameters')
                        params = params if isinstance(params, dict) else {}
                        check(result.get('company_id') == COMPANY and
                              result.get('context_version') == context and
                              result.get('status') == 'proposal' and
                              result.get('read_only') is True and
                              bool(result.get('agent_steps')) and
                              result.get('action_result') is None and
                              result.get('session') is None and
                              candidate.get('context_version') == context and
                              set(params) == {'query', 'destination', 'shared_data'} and
                              same_public_query(params.get('query')) and
                              params.get('destination') == DESTINATION and
                              params.get('shared_data') == SHARED_DATA and
                              isinstance(candidate.get('proposal_id'), str) and
                              bool(candidate['proposal_id']),
                              'request:proposal', 'wrong_or_stale_proposal')
                        try:
                            expires = datetime.fromisoformat(candidate['expires_at'].replace('Z', '+00:00'))
                        except (KeyError, AttributeError, ValueError):
                            raise AcceptanceError('request:proposal', 'invalid_proposal_expiry') from None
                        check(expires.tzinfo is not None and
                              expires > datetime.now(timezone.utc),
                              'request:proposal', 'invalid_proposal_expiry')
                        check(current_proposal is None or
                              (candidate['proposal_id'] == current_proposal['proposal_id'] and
                               params['query'] == current_proposal['parameters']['query']),
                              'request:proposal', 'proposal_changed')
                        current_proposal = candidate
                        row['proposal_digest'] = hashlib.sha256(candidate['proposal_id'].encode()).hexdigest()[:12]
                        row['actual_reviewed_query'] = params['query']
                        receipt.data['actual_reviewed_public_query'] = params['query']
                        receipt.flush()
                else:
                    check(not (result.get('status') == 'proposal' and
                               isinstance(result.get('proposal'), dict)),
                          'consent:tool', 'consent_not_verified')
                    maybe = (result.get('workspace') or {}).get('research')
                    if maybe is not None:
                        research = completed_research(result, context,
                                                      current_proposal['parameters']['query'])
                        row['research_id_digest'] = hashlib.sha256(research['research_id'].encode()).hexdigest()[:12]
                        receipt.data['public_result'] = public_result(research)
                        receipt.flush()
            elif event == 'research_progress':
                phase = frame.get('phase')
                check(phase in {'checking_consent', 'awaiting_consent', 'started',
                                'completed', 'unavailable'} and
                      frame.get('company_id') == COMPANY and
                      frame.get('context_version') == context and
                      current_proposal is not None and
                      frame.get('proposal_id') == current_proposal['proposal_id'],
                      'voice:progress', 'foreign_or_invalid_research_progress')
                progress.append({'phase': phase, 'elapsed_ms': round((time.monotonic() - started) * 1000)})
                if kind == 'request':
                    check(phase == 'awaiting_consent',
                          'request:progress', 'research_started_without_consent')
                    consent_armed = True
                else:
                    check(streaming.is_set(), 'consent:progress', 'research_started_before_consent_audio')
                    if phase == 'checking_consent':
                        consent_checked = True
                    if phase == 'started':
                        check(consent_checked and not progress_started,
                              'consent:progress', 'start_without_consent_verification')
                        progress_started = True
                    if phase == 'completed':
                        check(progress_started and not progress_completed,
                              'consent:progress', 'completion_without_start')
                        progress_completed = True
                    check(phase != 'unavailable', 'consent:progress', 'research_unavailable')
            elif event == 'audio':
                if (kind == 'request' and current_proposal is not None or
                        kind == 'consent' and progress_completed):
                    check(frame.get('sample_rate_hertz') == 24000,
                          'voice:audio', 'wrong_output_rate')
                    audio_bytes += len(base64.b64decode(frame.get('data', ''), validate=True))
                    answer_completion_seen = False
                    deadline = None  # Further prompt or answer audio needs a later completion.
            trace(row, counts, tools, progress, captions, audio_bytes, started,
                  kind == 'consent' or current_proposal is not None)
            if event in {'tool_result', 'research_progress', 'transcript', 'turn_complete'}:
                receipt.flush()
            if event == 'turn_complete':
                answer_associated = (current_proposal is not None and consent_armed and
                                     audio_bytes >= 10000 if kind == 'request' else
                                     progress_completed and research is not None and
                                     audio_bytes >= 10000)
                if not answer_associated:
                    answer_completion_seen = False
                    pre_answer_completions += 1
                    row['pre_answer_completions'] = pre_answer_completions
                    receipt.flush()
                    continue  # Tool transition or slow-provider cue precedes answer audio.
                await sender
                answer_completion_seen = True
                deadline = time.monotonic() + CAPTION_GRACE_SECONDS
            if answer_completion_seen and deadline is None and turn_ready():
                break  # A final caption may arrive after grace without another completion.
        await sender
        check(answer_completion_seen, f'{kind}:turn', 'answer_completion_missing')
        heard = captions_match(input_parts, question_heard if kind == 'request' else consent_heard)
        spoken = ' '.join(output_parts)
        if kind == 'request':
            check(current_proposal is not None and heard and captions_match(
                  output_parts, lambda text: proposal_spoken(text, current_proposal['parameters']['query']))
                  and audio_bytes >= 10000 and consent_armed and
                  not progress_started and not progress_completed,
                  'request:turn', 'proposal_not_spoken_or_ungrounded')
        else:
            check(heard, 'consent:turn', 'spoken_consent_not_recognized')
            check(progress_started and progress_completed and research is not None and
                  spoken_answer(spoken, research) and audio_bytes >= 10000,
                  'consent:turn', 'completed_sourced_spoken_answer_missing')
            row['spoken_answer_has_explicit_date'] = dated(spoken)
        row.update(ok=True, caption_grace_ms=round(CAPTION_GRACE_SECONDS * 1000),
                   elapsed_ms=round((time.monotonic() - started) * 1000))
        receipt.flush()
        return current_proposal, research
    finally:
        if not sender.done():
            sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


def final_quality(receipt):
    check(receipt.data['public_result']['has_explicit_date'],
          'consent:research', 'answer_missing_or_undated')
    check(receipt.data['turns'][1].get('spoken_answer_has_explicit_date') is True,
          'consent:turn', 'spoken_answer_undated')


async def run(base, revision, question_pcm, consent_pcm, receipt):
    from websockets.asyncio.client import connect
    client = PooledClient(base, max_transport_attempts=1)
    try:
        health = await asyncio.to_thread(client.json, 'health', '/-/health')
        check(health.get('ready') is True and health.get('persistence') == 'firestore' and
              health.get('revision') == revision, 'health', 'wrong_revision_or_storage')
        session = await asyncio.to_thread(client.json, 'session', '/api/demo/session')
        check(session.get('synthetic') is True and session.get('company_id') == COMPANY and
              type(session.get('context_version')) is int and
              bool(session.get('csrf_token')) and session.get('plan') is None and
              session.get('recovery') is None and session.get('rfq') is None,
              'session', 'fresh_solara_session_required')
        context = session['context_version']
        before = protected(session)
        receipt.data['stage'] = 'voice_start'
        receipt.flush()
        cookie = '; '.join(c.name + '=' + c.value for c in client.session.cookies)
        async with connect(base.replace('https:', 'wss:') + '/api/demo/voice/live',
                           origin=base, additional_headers={'Cookie': cookie},
                           max_size=1_024_000, open_timeout=20, close_timeout=5) as socket:
            await socket.send(json.dumps({'type': 'start', 'csrf_token': session['csrf_token'],
                                          'expected_context_version': context}))
            ready = json.loads(await asyncio.wait_for(socket.recv(), 25))
            check(ready.get('type') == 'ready', 'voice:ready', 'not_ready')
            receipt.data['stage'] = 'research_question'
            receipt.flush()
            try:
                proposal, _ = await receive_turn(socket, question_pcm, 'request', context, receipt)
                receipt.data['stage'] = 'spoken_confirmation'
                receipt.flush()
                _, research = await receive_turn(socket, consent_pcm, 'consent', context,
                                                 receipt, proposal=proposal)
            finally:
                try:
                    await socket.send(json.dumps({'type': 'stop'}))
                except Exception:
                    pass  # Preserve the original voice failure if the socket already closed.
        receipt.data['stage'] = 'durable_read'
        receipt.flush()
        history = await asyncio.to_thread(client.json, 'history', '/api/demo/operator/history')
        check(history.get('company_id') == COMPANY and history.get('context_version') == context and
              isinstance(history.get('exchanges'), list),
              'history', 'wrong_history_context')
        matches = [entry.get('research') for entry in history['exchanges']
                   if isinstance(entry, dict) and isinstance(entry.get('research'), dict)
                   and entry['research'].get('research_id') == research['research_id']]
        check(len(matches) == 1 and matches[0].get('status') == 'completed' and
              matches[0].get('query') == proposal['parameters']['query'] and
              matches[0].get('answer') == research['answer'] and
              matches[0].get('sources') == research['sources'],
              'history', 'completed_research_not_durable')
        receipt.data['public_result']['durable_history_read'] = True
        receipt.flush()
        after = await asyncio.to_thread(client.json, 'after', '/api/demo/session')
        check(after.get('company_id') == COMPANY and after.get('context_version') == context and
              protected(after) == before, 'session:after', 'protected_business_state_changed')
        receipt.data['public_result']['protected_business_state_unchanged'] = True
        receipt.flush()
        final_health = await asyncio.to_thread(client.json, 'health_final', '/-/health')
        check(final_health.get('revision') == revision and final_health.get('ready') is True and
              final_health.get('persistence') == 'firestore',
              'health_final', 'revision_changed')
        final_quality(receipt)
        receipt.data['stage'] = 'complete'
        receipt.data['ok'] = True
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--execute-public-research', action='store_true',
                        help='Explicitly authorize this one hosted, chargeable two-turn probe')
    parser.add_argument('--research-wav', type=Path, required=True)
    parser.add_argument('--consent-wav', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute_public_research:
        parser.error('--execute-public-research is required to run the hosted probe')
    if not args.expected_revision.strip() or args.expected_revision == 'local':
        parser.error('a deployed exact --expected-revision is required')
    base = base_url(args.base)
    question_pcm, consent_pcm = read_wav(args.research_wav), read_wav(args.consent_wav)
    receipt = Receipt(args.output, base, args.expected_revision)
    try:
        asyncio.run(asyncio.wait_for(run(base, args.expected_revision, question_pcm,
                                         consent_pcm, receipt), TOTAL_SECONDS))
    except AcceptanceError as exc:
        code = exc.code if isinstance(exc.code, str) and re.fullmatch(r'[a-z0-9_]{1,80}', exc.code) else 'invalid_error_code'
        receipt.data['failure'] = {'stage': exc.stage, 'code': code, 'status': exc.status}
    except TimeoutError:
        receipt.data['failure'] = {'stage': receipt.data['stage'], 'code': 'total_timeout_200s'}
    except Exception:
        receipt.data['failure'] = {'stage': receipt.data['stage'], 'code': 'unexpected_error'}
    finally:
        receipt.data['finished_at'] = stamp()
        receipt.flush()
    print(json.dumps({'ok': receipt.data['ok'], 'stage': receipt.data['stage'],
                      'failure': receipt.data['failure'], 'output': str(receipt.path)}))
    return 0 if receipt.data['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
