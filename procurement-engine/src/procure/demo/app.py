"""Public hosted demo boundary: synthetic inputs, scoped state, bounded provider work."""
from __future__ import annotations

import base64
import asyncio
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import logging
import os
from pathlib import Path
import re
import secrets
import threading
from time import monotonic
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.staticfiles import StaticFiles

from .sessions import SessionError, SessionStore
from .errors import DemoError
from .company import DEFAULT_COMPANY_ID, get_company, list_companies
from .carbon_api import carbon_export, current_carbon_assessment, register_carbon_routes
from .dispatch_view import build_dispatch_view, resolve_recovery_shipment
from .news_brief import build_news_brief, is_workspace_news_question
from .diagnostics import DependencyReadiness, news_freshness, request_outcome

COOKIE = 'demo_session'
MAX_BODY = 750_000
MAX_UPLOAD = 500_000
MAX_SPEECH_OUTPUT = 750_000
# A full visit can review 156 quotes across 52 material scenarios. Paid-model
# budgets remain separate and unchanged; these bound local synthetic work only.
MAX_REQUESTS = 4000
MAX_OFFERS = 48
MAX_IDEMPOTENCY = 2400
MAX_SESSION_CALLS = 36
MAX_SESSION_FAILURES = 6
MAX_OPERATOR_TURNS = 36
MAX_PROCESS_CALLS = 60
MAX_OPERATOR_PROCESS_CALLS_PER_HOUR = 90
MAX_CACHE = 64


def failure(code, message, status=422):
    return JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)


class BodyLimit:
    """Limit streamed bodies as well as declared content lengths before JSON parsing."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/demo'):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            limit = MAX_BODY if scope['path'] in ('/api/demo/extract', '/api/demo/voice/transcribe') else 16_384
            if scope['path'] == '/api/demo/operator/history':
                limit = 128_000  # Completed multilingual captions may use four bytes per character.
            if len(body) > limit:
                return await failure('body_too_large', 'Request is too large.', 413)(scope, receive, send)
            if not message.get('more_body', False):
                break
        sent = False
        async def bounded_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()
        await self.app(scope, bounded_receive, send)


def create_app(*, web_dir: Path | None = None, secure_cookies=True, clock=None,
               news_service=None, operator_provider=None, voice_service=None, live_client_factory=None,
               research_provider=None, session_backend=None, voice_consent_verifier=None, judge_provider=None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = SessionStore(clock=clock, persistence_backend=session_backend)
    app.state.sessions = store
    readiness = DependencyReadiness(store.backend)
    app.state.dependency_readiness = readiness
    app.router.add_event_handler('shutdown', readiness.close)
    from .context_cache import BoundedContextCache
    context_cache = BoundedContextCache()
    app.state.operator_context_cache = context_cache
    # Enable only reviewed demo diagnostics; never turn on SDK request/body logging.
    diagnostics = logging.getLogger('procure.demo')
    if not diagnostics.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        diagnostics.addHandler(handler)
    diagnostics.setLevel(logging.INFO)
    cache: OrderedDict = OrderedDict()
    cache_lock = threading.RLock()
    workers = threading.BoundedSemaphore(2)
    process_calls = 0
    pending_cache = set()
    news_lock = threading.RLock()
    risk_news_slots = asyncio.Semaphore(3)
    operator_injected = operator_provider is not None
    voice_injected = voice_service is not None
    operator_lock = threading.RLock()
    operator_workers = threading.BoundedSemaphore(2)
    operator_process_calls = 0
    operator_call_times = []
    voice_lock = threading.RLock()
    voice_workers = threading.BoundedSemaphore(2)
    voice_process_calls = 0
    research_workers = threading.BoundedSemaphore(2)
    research_lock = threading.RLock()
    app.add_middleware(BodyLimit)

    @app.exception_handler(SessionError)
    async def session_error(request, exc):
        return failure(exc.code, exc.message, exc.status_code)

    @app.middleware('http')
    async def public_errors(request, call_next):
        started = monotonic()
        try:
            response = await call_next(request)
            value = getattr(request.state, 'demo_session', None)
            if value is not None and value.active and store.persistence != 'ephemeral':
                # A response is never acknowledged as durable until its state is saved.
                await run_in_threadpool(store.save, value)
        except Exception as exc:
            # Only domain errors contain reviewed public messages. Never reflect provider exceptions.
            if isinstance(exc, (DemoError, SessionError)):
                response = failure(exc.code, exc.message, exc.status_code)
            else:
                response = failure('internal_error', 'Demo could not complete this request.', 500)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' https://maps.googleapis.com https://maps.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://maps.googleapis.com https://maps.gstatic.com https://*.googleapis.com https://*.gstatic.com https://*.google.com https://*.googleusercontent.com https://www.gstatic.com https://www.eia.gov https://www.ecb.europa.eu; media-src 'self' blob:; connect-src 'self' https://maps.googleapis.com https://maps.gstatic.com https://*.googleapis.com https://*.gstatic.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        request_outcome(diagnostics, request.method, request.url.path,
                        response.status_code, (monotonic() - started) * 1000)
        return response

    def session(request):
        value = store.get(request.cookies.get(COOKIE), refresh=True)
        request.state.demo_session = value
        store.reserve_request(value, MAX_REQUESTS)
        return value

    def origin(value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return None
            return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
        except ValueError:
            return None

    def mutate(request):
        value = session(request)
        source = request.headers.get('origin') or request.headers.get('referer')
        expected = origin(str(request.url))
        if not secure_cookies and (expected is None or expected[1] not in ('localhost', '127.0.0.1', '::1')):
            raise SessionError('insecure_host', 'Local test mode requires localhost.', 403)
        if source is None or origin(source) != expected:
            raise SessionError('invalid_origin', 'A same-origin browser request is required.', 403)
        token = request.headers.get('x-csrf-token', '')
        if not secrets.compare_digest(token, value.csrf_token):
            raise SessionError('invalid_csrf', 'Refresh the demo session and try again.', 403)
        return value

    async def body(request):
        def finite_float(raw):
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError()
            return value

        try:
            if request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
                raise ValueError()
            result = json.loads(await request.body(), parse_float=finite_float, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise SessionError('invalid_json', 'Provide a valid JSON object with finite values.', 422)

    def shape(data, required, optional=()):
        if not set(required) <= data.keys() or not data.keys() <= set(required) | set(optional):
            raise SessionError('invalid_request', 'Request fields do not match this operation.', 422)

    def active(value):
        if not value.active or value.expires_at <= store.now():
            raise SessionError('session_expired', 'Session changed or expired. Refresh the demo.', 401)

    def context(value, data, *, explicit=False):
        active(value)
        expected = data.get('expected_context_version')
        if expected is None and not explicit and value.company_id == DEFAULT_COMPANY_ID and value.context_version == 1:
            return
        if type(expected) is not int or expected < 1:
            raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)
        if expected != value.context_version:
            raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)

    def quote_coverage(value, *, fixture_id=None, material=None):
        coverage = get_company(value.company_id)['quote_coverage']
        if (fixture_id is not None and fixture_id not in coverage['fixture_ids']) or (material is not None and material not in coverage['materials']):
            raise SessionError('quote_unavailable', 'No reviewed synthetic quote coverage for this company and material.', 422)

    def news_adapter():
        nonlocal news_service
        with news_lock:
            if news_service is None:
                from .news import NewsService
                live = NewsService(clock=store.now)
                bucket = os.environ.get('NEWS_SNAPSHOT_BUCKET', '').strip()
                if bucket and not live.offline:
                    from .news_snapshots import GcsSnapshotStore, SnapshotNewsService
                    news_service = SnapshotNewsService(live=live,
                        store=GcsSnapshotStore(bucket), clock=store.now)
                else:
                    news_service = live
            return news_service

    def operator_adapter():
        nonlocal operator_provider
        with operator_lock:
            if operator_provider is None:
                from .operator import VertexOperatorProvider
                operator_provider = VertexOperatorProvider()
            return operator_provider

    def research_adapter():
        nonlocal research_provider
        with research_lock:
            if research_provider is None:
                from .research import VertexResearchProvider
                research_provider = VertexResearchProvider(clock=store.now)
            return research_provider

    def voice_adapter():
        nonlocal voice_service
        with voice_lock:
            if voice_service is None:
                from .voice import VoiceService
                voice_service = VoiceService()
            return voice_service

    def dispatch_view(value):
        plan = active_plan(value)
        if plan is not None:
            try:
                validate_plan_current(value, plan)
            except SessionError:
                plan = None
        result = build_dispatch_view(get_company(value.company_id),
            list(value.offers.values()), plan, value.recovery if plan is not None else None)
        result['context_version'] = value.context_version
        return result

    def bound_shipment(value, shipment_id, baseline, delays):
        if not isinstance(baseline, dict) or not isinstance(delays, dict):
            raise SessionError('stale_plan', 'Current linked shipment is unavailable.', 409)
        selected_id = baseline.get('selected_offer_id')
        version = (baseline.get('offer_versions') or {}).get(selected_id)
        row = resolve_recovery_shipment(dispatch_view(value), shipment_id,
                                        baseline.get('plan_id'), version)
        if row is None:
            raise SessionError('shipment_not_found', 'Current linked shipment is unavailable.', 404)
        if set(delays) != {selected_id}:
            raise SessionError('invalid_delay', 'Delay must match the selected shipment offer.', 422)
        return row

    def operator_snapshot(value, *, include_risks=True):
        snapshot = {'company_id': value.company_id, 'context_version': value.context_version,
                'company': get_company(value.company_id), 'news': deepcopy(value.news),
                'offers': deepcopy(list(value.offers.values())),
                'plan': deepcopy(active_plan(value)), 'baseline_plan': deepcopy(value.last_plan),
                'last_plan': deepcopy(value.last_plan),
                'plan_request': deepcopy(value.plan_request), 'recovery': deepcopy(value.recovery),
                'watch_items': deepcopy(value.watch_items), 'buyer_draft': deepcopy(value.buyer_draft),
                'selected_material_id': value.selected_material_id,
                'simulated_outbox': deepcopy(value.simulated_outbox)}
        snapshot['carbon_assessment'] = current_carbon_assessment(
            value, active_plan, validate_plan_current)
        snapshot['shipments'] = dispatch_view(value)
        from .experience_service import completed_research
        snapshot['completed_research'] = completed_research(value)
        if include_risks:
            from .proactive_risks import assess_company
            snapshot['risk_report'] = assess_company(snapshot, value.news, store.now())
            cached = value.risk_report
            if (cached and cached['company_id'] == value.company_id and
                    cached['context_version'] == value.context_version and
                    cached.get('_state_marker') == snapshot_marker(snapshot)):
                snapshot['portfolio_risks'] = risk_projection(cached)['companies']
                snapshot['risk_report'] = deepcopy(next(row for row in cached['companies']
                                                       if row['company_id'] == value.company_id))
                snapshot['risk_news'] = deepcopy(cached.get('_current_news', {}).get('items', []))
        return snapshot

    def risk_projection(report, *, scope='all'):
        """Bounded provider facts; no session, credentials, document text or hidden state."""
        companies = report['companies']
        if scope == 'current':
            companies = [row for row in companies if row['company_id'] == report['company_id']]
        rows = []
        for company in companies:
            alerts = company['alerts']
            ranked = sorted(alerts, key=lambda alert: {'high': 0, 'medium': 1, 'low': 2}[alert['severity']])
            selected = []
            for category in ('inventory', 'supplier_delay', 'news'):
                match = next((a for a in ranked if a['category'] == category), None)
                if match:
                    selected.append(match)
            selected.extend(a for a in ranked if a not in selected)
            rows.append({'company_id': company['company_id'], 'company_name': company['company_name'],
                'counts': {severity: sum(a['severity'] == severity for a in alerts)
                           for severity in ('high', 'medium', 'low')},
                'data_gaps': company['data_gaps'],
                'alerts': [{key: deepcopy(alert[key]) for key in
                    ('alert_id', 'category', 'severity', 'title', 'impact', 'material_ids',
                     'provenance', 'confidence', 'recovery_actions', 'metrics')} |
                    {'evidence': [{key: e[key] for key in ('kind', 'ref_id', 'label', 'observed_at')}
                                  for e in alert['evidence'][:1]]} for alert in selected[:3]]})
        return {'checked_at': report['checked_at'], 'companies': rows,
                'limitations': report['limitations']}

    async def scan_risks(value, expected_version=None, scan_scope='all'):
        """Read trusted catalogues without switching company or changing proposals."""
        from .proactive_risks import assess_company
        with value.lock:
            active(value)
            company_id, version = value.company_id, value.context_version
            if expected_version is not None:
                context(value, {'expected_context_version': expected_version}, explicit=True)
        profiles = [row for row in list_companies()
                    if scan_scope == 'all' or row['company_id'] == company_id]
        news = {}
        async def fetch_news(company):
            try:
                async with risk_news_slots:
                    payload = await run_in_threadpool(news_adapter().get, company)
                news[company['company_id']] = payload if isinstance(payload, dict) else None
            except Exception:
                news[company['company_id']] = None
        try:
            async with asyncio.timeout(35):
                await asyncio.gather(*(fetch_news(company) for company in profiles))
        except TimeoutError:
            pass  # Completed sources remain usable; queued failures become explicit gaps.
        with value.lock:
            active(value)
            if value.company_id != company_id or value.context_version != version:
                raise SessionError('stale_context', 'Company context changed during the risk scan.', 409)
            current = operator_snapshot(value, include_risks=False)
            reports = []
            now = store.now()
            for company in profiles:
                cid = company['company_id']
                if cid == company_id:
                    snapshot = current
                else:
                    saved = value.workspaces.get(cid, {})
                    recovery = saved.get('recovery')
                    snapshot = {'company_id': cid, 'company': company,
                        'offers': deepcopy(list(saved.get('offers', {}).values())),
                        'plan': deepcopy((recovery or {}).get('scenario') or saved.get('last_plan')),
                        'baseline_plan': deepcopy(saved.get('last_plan')),
                        'last_plan': deepcopy(saved.get('last_plan')),
                        'plan_request': deepcopy(saved.get('plan_request')),
                        'recovery': deepcopy(recovery), 'buyer_draft': deepcopy(saved.get('buyer_draft')),
                        'selected_material_id': saved.get('selected_material_id')}
                reports.append(assess_company(snapshot, news.get(cid), now))
            from .news_snapshots import SnapshotNewsService
            scheduled_news = isinstance(news_adapter(), SnapshotNewsService)
            alerts = [alert for company in reports for alert in company['alerts']]
            result = {'company_id': company_id, 'context_version': version,
                'checked_at': now.isoformat(), 'monitoring': 'while_workspace_open',
                'news_refresh_seconds': 3600 if scheduled_news else 900,
                'companies': reports,
                'summary': {**{severity: sum(a['severity'] == severity for a in alerts)
                              for severity in ('high', 'medium', 'low')},
                            'companies_with_alerts': sum(bool(row['alerts']) for row in reports)},
                'limitations': [('Risk monitoring runs while the workspace is open; hourly news snapshots are configured. Check recorded collection times and source health in the news workspace.'
                    if scheduled_news else
                    'Monitoring runs while the workspace is open; public news uses a 15-minute cache.'),
                    'Company inventory, supplier leads and delay scenarios are synthetic.',
                    'Headlines indicate conditional exposure, not confirmed shipment delays or forecasts.',
                    'Recovery suggestions require current inputs and review; no supplier message or order is sent.']}
            current_ids = {e['ref_id'] for row in reports if row['company_id'] == company_id
                           for a in row['alerts'] for e in a['evidence'] if e['kind'] == 'news'}
            current_news = news.get(company_id) or {}
            value.risk_report = {**deepcopy(result), '_state_marker': snapshot_marker(current),
                '_current_news': {'items': [deepcopy(item) for item in current_news.get('items', [])
                                           if item.get('item_id') in current_ids]}}
            return result

    def snapshot_marker(snapshot):
        # Bind asynchronous work and proposals to authoritative state, including observed evidence.
        fields = {key: snapshot[key] for key in ('company_id', 'context_version', 'news',
                  'offers', 'plan', 'baseline_plan', 'plan_request', 'recovery', 'watch_items',
                  'buyer_draft', 'selected_material_id', 'simulated_outbox')}
        fields['carbon_assessment'] = snapshot.get('carbon_assessment')
        fields['shipments'] = snapshot.get('shipments')
        fields['completed_research'] = snapshot.get('completed_research', [])
        return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(',', ':'),
                                         default=str).encode()).hexdigest()

    def public_proposal(proposal):
        return {key: deepcopy(proposal[key]) for key in ('proposal_id', 'kind', 'summary',
                'parameters', 'plan_id', 'offer_versions', 'context_version', 'expires_at')}

    def current_public_proposal(value):
        """Project a current owned review card without exposing its private binding."""
        proposal = value.pending_proposal
        if proposal is None:
            return None
        try:
            if (not store._valid_proposal(proposal, value) or
                    datetime.fromisoformat(proposal['expires_at']).astimezone(timezone.utc)
                        <= store.now().astimezone(timezone.utc) or
                    proposal['_marker'] != snapshot_marker(operator_snapshot(value, include_risks=False))):
                return None
            return public_proposal(proposal)
        except (TypeError, ValueError, KeyError, SessionError):
            return None

    def news_view(value, payload, company_id, version):
        if not isinstance(payload, dict):
            raise SessionError('news_unavailable', 'News is unavailable right now.', 503)
        return {**deepcopy(payload), 'company_id': company_id,
                'context_version': version}

    def start(value, data, action):
        active(value)
        key = data.get('idempotency_key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key):
            raise SessionError('invalid_idempotency', 'A short alphanumeric retry key is required.', 422)
        digest = hashlib.sha256(json.dumps([action, data], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        previous = value.idempotency.get(key)
        if previous:
            if previous['digest'] != digest:
                raise SessionError('idempotency_conflict', 'Retry key was already used for another request.', 409)
            if previous.get('result_retired') or previous.get('result') == {'_retry_result_expired': True}:
                raise SessionError('retry_result_expired', 'Exact retry response is no longer retained. Start a new action with a new key.', 409)
            if previous['result'] is None:
                if previous['pending']:
                    raise SessionError('request_in_progress', 'This request is still processing.', 409)
                previous['pending'] = True
                return key, None
            result = deepcopy(previous['result'])
            result['replayed'] = True
            return key, result
        if len(value.idempotency) >= MAX_IDEMPOTENCY:
            raise SessionError('operation_limit', 'Session operation limit reached.', 429)
        value.idempotency[key] = {'digest': digest, 'result': None, 'pending': True}
        return key, None

    def failed(value, key):
        # Keep failed retry keys bound to their original action and payload.
        if key in value.idempotency:
            value.idempotency[key]['pending'] = False

    def complete(value, key, result):
        active(value)
        value.idempotency[key]['result'] = deepcopy(result)
        value.idempotency[key]['pending'] = False
        return result

    def remember(value, entry, *, company_id=None):
        """Only accepted, bounded exchanges; no credentials or audio enter history."""
        company_id = company_id or value.company_id
        entries = value.chat_history.setdefault(company_id, [])
        if any(item['id'] == entry['id'] for item in entries):
            return
        entry = deepcopy(entry)
        entry.setdefault('created_at', store.now().isoformat())
        if isinstance(entry.get('turn'), dict):
            entry['turn'].pop('session', None)
        entries.append(entry)
        del entries[:-60]
        # Keep recent complete exchanges; retire oldest entries before a long
        # voice session can monopolise the bounded session document.
        while sum(len(json.dumps(items, ensure_ascii=False)) for items in value.chat_history.values()) > 240_000:
            candidates = [items for items in value.chat_history.values() if len(items) > 1]
            if not candidates:
                break
            oldest = min(candidates, key=lambda items: items[0].get('created_at', ''))
            del oldest[0]

    def remember_turn(value, message, result, *, channel='text'):
        remember(value, {'id': result['turn_id'], 'channel': channel,
                         'question': message, 'turn': result})

    def owned(value, offer_id):
        result = value.offers.get(offer_id)
        if result is None:
            raise SessionError('offer_not_found', 'Offer not found in this session.', 404)
        return result

    def version(offer, expected):
        if type(expected) is not int or expected < 1:
            raise SessionError('invalid_version', 'An integer offer version is required.', 422)
        if offer['version'] != expected:
            raise SessionError('stale_version', 'Offer changed. Refresh it before continuing.', 409)

    def provider():
        # Availability means configured, not successful-call evidence. Extraction reports actual outcome.
        from procure.settings import assumptions
        return {'model': assumptions()['news']['gemini_model'],
                'available': os.getenv('OFFLINE', '').lower() not in ('1', 'true')}

    def clear_derived(value):
        value.last_plan = None
        value.plan_request = None
        value.recovery = None
        value.rfq = None
        value.pending_proposal = None
        value.carbon_assessment = None
        value.pending_carbon = None

    def active_plan(value):
        return value.recovery['scenario'] if value.recovery is not None else value.last_plan

    def current_offers(value, plan, expected_versions, *, today):
        from .quote_service import blocking_reasons
        if not isinstance(expected_versions, dict) or expected_versions != plan.get('offer_versions'):
            raise SessionError('stale_version', 'Offer versions changed. Compare again.', 409)
        ids = list(plan['offer_versions'])
        selected = [owned(value, offer_id) for offer_id in ids]
        for offer in selected:
            version(offer, expected_versions[offer['offer_id']])
            if offer.get('status') != 'saved' or blocking_reasons(offer, today):
                raise SessionError('stale_plan', 'A reviewed quote is no longer current.', 409)
        return selected

    def validate_plan_current(value, plan):
        """One currentness gate for emissions, shipments and their agent tools."""
        if not isinstance(plan, dict) or plan.get('company_id') != value.company_id:
            raise SessionError('stale_plan', 'Compare reviewed quotes for the current company first.', 409)
        current_offers(value, plan, plan.get('offer_versions'), today=store.now().date())
        if value.buyer_draft is not None:
            from .agent_tools import BUYER_FIELDS
            request = value.plan_request or {}
            aliases = {'quantity': 'quantity_kg', 'current_stock': 'current_stock_kg',
                       'additional_cost_per_unit': 'additional_cost_per_kg'}
            expected = {key: request.get(key, request.get(aliases.get(key))) for key in BUYER_FIELDS}
            expected['quantity_unit'] = expected['quantity_unit'] or plan.get('quantity_unit', 'kg')
            try:
                matching = canonical_buyer(value, value.buyer_draft) == canonical_buyer(value, expected)
            except SessionError:
                matching = False
            if not matching:
                raise SessionError('stale_buyer', 'Buyer inputs changed. Run the comparison again.', 409)

    def canonical_buyer(value, draft):
        from .agent_tools import ToolValidationError, validate_buyer_draft
        try:
            return validate_buyer_draft(get_company(value.company_id), draft)
        except ToolValidationError as exc:
            raise SessionError('invalid_buyer_draft', str(exc), 422) from None

    def current_buyer(value, material_id):
        from .agent_tools import BUYER_FIELDS
        company = get_company(value.company_id)
        if value.buyer_draft and value.buyer_draft.get('material') == material_id:
            return canonical_buyer(value, value.buyer_draft)
        if value.plan_request and value.plan_request.get('material') == material_id:
            source = value.plan_request
            aliases = {'quantity': 'quantity_kg', 'current_stock': 'current_stock_kg',
                       'additional_cost_per_unit': 'additional_cost_per_kg'}
            unit = next((m['quantity_unit'] for m in company['materials']
                         if m['material_id'] == material_id), None)
            candidate = {field: source.get(field, source.get(aliases.get(field)))
                         for field in BUYER_FIELDS}
            candidate['quantity_unit'] = candidate['quantity_unit'] or unit
            return canonical_buyer(value, candidate)
        scenario = next((row for row in company['quote_coverage']['scenarios']
                         if row['material_id'] == material_id), None)
        if scenario is None:
            raise SessionError('quote_unavailable', 'Material is outside this workspace.', 422)
        return canonical_buyer(value, {field: scenario['buyer_defaults'][field]
                                       for field in BUYER_FIELDS})

    def set_buyer(value, draft):
        canonical = canonical_buyer(value, draft)
        material_id = canonical['material']
        changed = canonical != current_buyer(value, material_id) or (
            value.selected_material_id is not None and value.selected_material_id != material_id) or (
            value.buyer_draft is not None and value.buyer_draft.get('material') != material_id) or (
            value.plan_request is not None and value.plan_request.get('material') != material_id)
        previous_marker = snapshot_marker(operator_snapshot(value)) if not changed else None
        if changed:
            clear_derived(value)
        value.buyer_draft = deepcopy(canonical)
        value.selected_material_id = material_id
        if not changed:
            current_marker = snapshot_marker(operator_snapshot(value))
            if value.pending_proposal and value.pending_proposal.get('_marker') == previous_marker:
                value.pending_proposal['_marker'] = current_marker
            for entry in value.idempotency.values():
                if entry.get('state_marker') == previous_marker:
                    entry['state_marker'] = current_marker
        return changed

    def scenario(value, material_id):
        company = get_company(value.company_id)
        row = next((item for item in company['quote_coverage']['scenarios']
                    if item['material_id'] == material_id), None)
        if row is None:
            raise SessionError('quote_unavailable', 'No reviewed quotes for this material.', 422)
        return row

    def staged_quotes(value, material_id):
        from .quote_service import extract_fixture, new_offer, company_for_offer
        row = scenario(value, material_id)
        existing = {offer.get('fixture_id'): offer for offer in value.offers.values()
                    if offer.get('fixture_id') in row['fixture_ids']}
        missing = [fixture_id for fixture_id in row['fixture_ids'] if fixture_id not in existing]
        if len(value.offers) + len(missing) > MAX_OFFERS:
            raise SessionError('offer_limit', 'Session offer limit reached.', 429)
        fresh = []
        for fixture_id in missing:
            payload = extract_fixture(fixture_id, mode='rules', as_of=store.now().date())
            offer = new_offer(payload, offer_id=secrets.token_urlsafe(18), today=store.now().date())
            if company_for_offer(offer) != value.company_id:
                raise SessionError('quote_unavailable', 'Fixture is outside this workspace.', 422)
            offer['fixture_id'] = fixture_id
            offer['served_from_cache'] = False
            fresh.append(offer)
        # Commit only after every immutable source has passed parsing and ownership checks.
        set_buyer(value, current_buyer(value, material_id))
        if fresh:
            clear_derived(value)
            for offer in fresh:
                value.offers[offer['offer_id']] = offer
        return [deepcopy(existing[fixture_id]) if fixture_id in existing else
                deepcopy(next(offer for offer in fresh if offer['fixture_id'] == fixture_id))
                for fixture_id in row['fixture_ids']]

    def compare_quotes(value, material_id):
        from .planner import build_plan
        from .quote_service import company_for_offer
        from .agent_tools import validate_buyer_draft
        row = scenario(value, material_id)
        selected = [deepcopy(offer) for offer in value.offers.values()
                    if offer.get('status') == 'saved' and offer.get('fixture_id') in row['fixture_ids']
                    and company_for_offer(offer) == value.company_id]
        if not selected:
            raise SessionError('quotes_unavailable', 'Review and save this material’s quotes first.', 409)
        draft = current_buyer(value, material_id)
        request = {**draft, 'offer_ids': [offer['offer_id'] for offer in selected],
                   'expected_versions': {offer['offer_id']: offer['version'] for offer in selected}}
        plan = build_plan(selected, request, today=store.now().date())
        plan['plan_id'] = secrets.token_urlsafe(18)
        set_buyer(value, draft)
        value.plan_request = deepcopy(request)
        value.last_plan = deepcopy(plan)
        value.recovery = None
        value.rfq = None
        value.pending_proposal = None
        value.pending_carbon = None
        return plan

    def execute_utility(value, utility, message):
        from .agent_tools import ToolValidationError, prepare_form
        if not isinstance(utility, dict) or set(utility) != {'kind', 'parameters'} or not isinstance(utility['parameters'], dict):
            raise SessionError('invalid_utility', 'Unsupported workspace action.', 422)
        kind, parameters = utility['kind'], utility['parameters']
        if kind == 'navigate':
            shape(parameters, ('view',))
            if parameters['view'] not in ('company', 'overview', 'inventory', 'routes', 'shipments', 'news', 'procurement', 'outbox'):
                raise SessionError('invalid_utility', 'Unknown workspace view.', 422)
            return {'tool': kind, 'summary': f"Open {parameters['view']} view.", 'view': parameters['view']}, True
        shape(parameters, ('material_id', 'values') if kind == 'fill_form' else ('material_id',))
        material_id = parameters['material_id']
        scenario(value, material_id)
        if kind == 'fill_form':
            try:
                draft = prepare_form(get_company(value.company_id), material_id, parameters['values'], message)
            except ToolValidationError as exc:
                raise SessionError('invalid_utility', str(exc), 422) from None
            changed = set_buyer(value, draft)
            return {'tool': kind, 'summary': f'Buyer form prepared for {material_id}.',
                    'fields_changed': list(parameters['values']) if changed else []}, True
        if kind == 'load_quotes':
            before = len(value.offers)
            offers = staged_quotes(value, material_id)
            return {'tool': kind, 'summary': f'{len(offers)} synthetic quotes are ready for review.',
                    'fields_changed': ['offers'] if len(value.offers) != before else []}, True
        if kind == 'compare':
            plan = compare_quotes(value, material_id)
            return {'tool': kind, 'summary': f"Compared {len(plan['offer_versions'])} reviewed quotes for {material_id}.",
                    'fields_changed': ['plan']}, True
        raise SessionError('invalid_utility', 'Unsupported workspace action.', 422)

    def validate_simulated_email(value, parameters):
        shape(parameters, ('material_id', 'recipient_label', 'subject', 'body'))
        material_id = parameters['material_id']
        scenario(value, material_id)
        suppliers = {supplier['name'] for supplier in get_company(value.company_id)['suppliers']
                     if material_id in supplier.get('materials', [])}
        if parameters['recipient_label'] not in suppliers:
            raise SessionError('invalid_recipient', 'Select a supplier in this material workspace.', 422)
        for field, limit in (('subject', 120), ('body', 1000)):
            raw = parameters[field]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > limit or re.search(r'\b[^\s@]+@[^\s@]+\.[^\s@]+\b', raw):
                raise SessionError('invalid_message', 'Use a short simulated message without an email address.', 422)

    def reviewed_quote_preview(value, parameters):
        from .quote_service import fixture_review_values, company_for_offer
        shape(parameters, ('material_id',), ('offer_ids',))
        row = scenario(value, parameters['material_id'])
        ids = parameters.get('offer_ids')
        if ids is None:
            ids = [offer['offer_id'] for offer in value.offers.values()
                   if offer.get('fixture_id') in row['fixture_ids'] and offer.get('status') != 'saved'
                   and company_for_offer(offer) == value.company_id]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 3 or
                any(not isinstance(offer_id, str) for offer_id in ids) or len(set(ids)) != len(ids)):
            raise SessionError('invalid_offers', 'Select one to three owned quotes.', 422)
        previews = []
        for offer_id in ids:
            offer = owned(value, offer_id)
            fixture_id = offer.get('fixture_id')
            if (fixture_id not in row['fixture_ids'] or
                    company_for_offer(offer) != value.company_id or
                    offer.get('status') in ('saved', 'corrected')):
                raise SessionError('invalid_offers', 'Select unmodified synthetic quote drafts for review.', 422)
            previews.append({'offer_id': offer_id, 'fixture_id': fixture_id,
                             'source_filename': offer.get('document', {}).get('filename'),
                             'expected_version': offer['version'],
                             'review_values': fixture_review_values(fixture_id)})
        return {'material_id': parameters['material_id'], 'offer_ids': ids,
                'preview': previews}

    def apply_reviewed_quotes(value, parameters):
        from .quote_service import correct_offer, save_offer
        preview = reviewed_quote_preview(value, {key: parameters[key] for key in ('material_id', 'offer_ids')})
        expected = {item['offer_id']: item for item in parameters['preview']}
        if len(expected) != len(parameters['preview']) or set(expected) != set(parameters['offer_ids']):
            raise SessionError('stale_proposal', 'Reviewed quote selection changed.', 409)
        staged = {}
        for item in preview['preview']:
            original = expected[item['offer_id']]
            if item != original:
                raise SessionError('stale_proposal', 'Reviewed quote source or version changed.', 409)
            offer = deepcopy(owned(value, item['offer_id']))
            for field, field_value in item['review_values'].items():
                offer = correct_offer(offer, field=field, value=field_value, today=store.now().date())
            staged[item['offer_id']] = save_offer(offer, today=store.now().date())
        for offer_id, offer in staged.items():
            value.offers[offer_id] = offer
        clear_derived(value)
        value.selected_material_id = parameters['material_id']
        return {'material_id': parameters['material_id'],
                'offers': deepcopy(list(staged.values())), 'status': 'reviewed_saved'}

    def simulated_email_parameters(value, args):
        from .presentation import supplier_display_name
        shape(args, ('material_id',), ('recipient_label', 'subject', 'body'))
        material_id = args['material_id']
        scenario(value, material_id)
        company = get_company(value.company_id)
        suppliers = [supplier['name'] for supplier in company['suppliers']
                     if material_id in supplier.get('materials', [])]
        if not suppliers:
            raise SessionError('invalid_recipient', 'No supplier is listed for this material.', 422)
        draft = current_buyer(value, material_id)
        parameters = {'material_id': material_id,
                      'recipient_label': suppliers[0],
                      'subject': f'Quotation request for {material_id}',
                      'body': (f'Please provide a quotation for {material_id}. '
                               f'Our draft requirement is {draft["quantity"]} {draft["quantity_unit"]} '
                               f'by {draft["need_by"]}. Please confirm capacity, delivery lead time and payment terms.')}
        selected_offer_id = (active_plan(value) or {}).get('selected_offer_id')
        if selected_offer_id in value.offers:
            selected_name = (value.offers[selected_offer_id].get('fields', {}).get('supplier_name') or {}).get('value')
            if selected_name in suppliers:
                parameters['recipient_label'] = selected_name
        parameters.update({key: args[key] for key in ('recipient_label', 'subject', 'body') if key in args})
        label = parameters['recipient_label']
        if isinstance(label, str):
            normalized = label.strip().casefold()
            if normalized in ('first', 'first supplier'):
                parameters['recipient_label'] = suppliers[0]
            else:
                matches = [supplier for supplier in suppliers if supplier.casefold() == normalized
                           or supplier_display_name(supplier).casefold() == normalized]
                if len(matches) == 1:
                    parameters['recipient_label'] = matches[0]
        validate_simulated_email(value, parameters)
        return parameters

    def remember_prepared_draft(value, proposal, *, rfq_preview=None):
        """Save readable artifacts, never an approval capability or delivery effect."""
        parameters = proposal['parameters']
        is_email = proposal['kind'] == 'simulate_email'
        if not is_email and proposal['kind'] != 'rfq':
            return
        artifact = {'draft_id': proposal['proposal_id'], 'kind': 'email' if is_email else 'rfq',
            'status': 'draft_unsent', 'company_id': value.company_id,
            'material_id': parameters['material_id'] if is_email else rfq_preview['material'],
            'recipient_label': parameters['recipient_label'] if is_email else rfq_preview['supplier'],
            'subject': parameters['subject'] if is_email else f"Quotation request for {rfq_preview['material']}",
            'body': parameters['body'] if is_email else rfq_preview['text'],
            'created_at': store.now().isoformat(), 'plan_id': proposal['plan_id'],
            'offer_versions': deepcopy(proposal['offer_versions'])}
        value.drafts.append(artifact)
        del value.drafts[:-24]
        parameters['draft_preview'] = deepcopy(artifact)

    @app.post('/api/demo/buyer-draft')
    async def buyer_draft(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('buyer_draft', 'expected_context_version', 'idempotency_key'))
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'buyer_draft')
            if replay is not None:
                if value.idempotency[key].get('state_marker') != snapshot_marker(operator_snapshot(value)):
                    raise SessionError('stale_context', 'Workspace changed since this form update.', 409)
                replay.pop('replayed', None)
                return replay
            try:
                set_buyer(value, data['buyer_draft'])
                result = view(value)
                complete(value, key, result)
                value.idempotency[key]['state_marker'] = snapshot_marker(operator_snapshot(value))
                return result
            except Exception:
                failed(value, key)
                raise

    def view(value):
        with value.lock:
            return {'session_id': value.session_id, 'csrf_token': value.csrf_token,
                    'expires_at': value.expires_at.isoformat(), 'synthetic': True,
                    'persistence': store.persistence, 'offers': deepcopy(list(value.offers.values())),
                    'provider': provider(), 'plan': deepcopy(active_plan(value)),
                    'plan_request': deepcopy(value.plan_request), 'recovery': deepcopy(value.recovery),
                    'carbon_assessment': current_carbon_assessment(value, active_plan, validate_plan_current),
                    'dispatch': dispatch_view(value),
                    'rfq': deepcopy(value.rfq), 'company': get_company(value.company_id),
                    'company_id': value.company_id,
                    'buyer_draft': deepcopy(value.buyer_draft),
                    'selected_material_id': value.selected_material_id,
                    'simulated_outbox': deepcopy(value.simulated_outbox),
                    'drafts': deepcopy(value.drafts),
                    'pending_operator_proposal': current_public_proposal(value),
                    'context_version': value.context_version}

    def session_response(value):
        response = JSONResponse(view(value))
        response.set_cookie(COOKIE, value.session_id, max_age=store.ttl_seconds,
                            httponly=True, secure=secure_cookies, samesite='lax', path='/api/demo')
        return response

    @app.get('/-/health')
    async def health():
        return {'ready': True, 'revision': os.getenv('K_REVISION', 'local'), 'persistence': store.persistence}

    @app.get('/-/ready')
    async def dependency_ready():
        ready = await run_in_threadpool(readiness.check)
        return JSONResponse({'ready': ready, 'persistence': store.persistence},
                            status_code=200 if ready else 503)

    @app.get('/api/demo/session')
    async def get_session(request: Request):
        try:
            value = session(request)
        except SessionError as exc:
            if exc.status_code != 401:
                raise
            value = store.create()
        request.state.demo_session = value
        return session_response(value)

    @app.get('/api/demo/companies')
    async def companies():
        return {'companies': list_companies()}

    @app.get('/api/demo/judge/config')
    async def judge_config(request: Request):
        value = session(request)
        with value.lock:
            key = os.environ.get('MAPS_BROWSER_KEY', '').strip()
            return {'maps_browser_key': key or None, 'maps_enabled': bool(key),
                    'roles': {'gemini': 'Source interpretation', 'vertex_ai': 'Model runtime', 'cloud_run': 'Workflow hosting'}}

    @app.get('/api/demo/judge/routes')
    async def judge_routes(request: Request, route_id: str, material_id: str, buyer_location_index: int = 0):
        from .route_display import build_route_display
        value = session(request)
        with value.lock:
            active(value)
            try:
                result = build_route_display(get_company(value.company_id), route_id, material_id, buyer_location_index)
            except ValueError:
                raise SessionError('route_unavailable', 'Choose an owned profile route, material and buyer location.', 422)
            return {**result, 'context_version': value.context_version}

    @app.get('/api/demo/judge/evidence')
    async def judge_evidence(request: Request):
        from .experience_service import evidence
        value = session(request)
        with value.lock:
            return evidence(value)

    @app.post('/api/demo/judge/start')
    async def judge_start(request: Request):
        from .agent_tools import inventory_analytics
        value = mutate(request)
        data = await body(request)
        shape(data, ('expected_context_version','idempotency_key'))
        with value.lock:
            context(value,data,explicit=True)
            key,replay = start(value,data,'judge_start')
            if replay is not None:
                if value.idempotency[key].get('state_marker') != snapshot_marker(operator_snapshot(value)):
                    raise SessionError('stale_context','Workspace changed since startup.',409)
                return replay
            try:
                seeded = False
                # Only the untouched flagship session can be seeded. Never replace a restored workspace.
                if (value.company_id == DEFAULT_COMPANY_ID and value.context_version == 1 and not value.workspaces
                        and not value.offers and value.last_plan is None and value.buyer_draft is None
                        and not value.drafts and not value.simulated_outbox and not value.watch_items
                        and value.pending_proposal is None and value.recovery is None and value.rfq is None):
                    material = get_company(value.company_id)['buyer_defaults']['material']
                    staged_quotes(value,material)
                    parameters = reviewed_quote_preview(value, {'material_id':material,
                        'offer_ids':[o['offer_id'] for o in value.offers.values()]})
                    apply_reviewed_quotes(value,parameters)
                    compare_quotes(value,material)
                    seeded = True
                rows = inventory_analytics(operator_snapshot(value))['materials']
                selected = next((r for r in rows if r['material_id']==value.selected_material_id),rows[0])
                facts = [{'label':'Profile stock cover','value':str(selected['stock_cover_days'])+' days',
                          'basis':'Fictional profile stock / daily consumption'},
                         {'label':'Shortest profile lead','value':str(selected['shortest_supplier_lead_days'])+' days',
                          'basis':'Matching material supplier profile; not a shipment ETA'}]
                if value.last_plan:
                    facts.append({'label':'Reviewed baseline spend', 'value':str(value.last_plan['total_cost'])+' '+value.last_plan['reporting_currency'],
                                  'basis':'Deterministic bundled quote comparison; not realized loss or savings'})
                example = None
                if seeded and value.last_plan.get('selected_offer_id'):
                    from .recovery import compare
                    paired = compare(value.last_plan,value.plan_request,list(value.offers.values()),
                        {value.last_plan['selected_offer_id']:7},today=store.now().date())
                    example = {'assumed_delay_days':7,'baseline_spend':paired['baseline']['total_cost'],
                               'scenario_spend':paired['scenario']['total_cost'],'additional_recovery_cost':paired['cost_delta'],
                               'currency':paired['baseline']['reporting_currency'],'outcome':paired['outcome'],
                               'scenario_plan_id':None,'read_only':True}
                result = complete(value,key,{'seed_version':'judge-baseline-v1','seeded':seeded,
                    'session':view(value),'facts':facts,'before_after':example})
                value.idempotency[key]['state_marker'] = snapshot_marker(operator_snapshot(value))
                store.save(value)
                return result
            except BaseException:
                failed(value,key)
                raise

    @app.post('/api/demo/judge/interpret-hindi')
    async def interpret_hindi(request: Request):
        from .experience_service import VertexHindiProvider, hindi_payload, sanitize_receipt, evidence
        value = mutate(request)
        data = await body(request)
        shape(data, ('expected_context_version','idempotency_key'))
        acquired = False
        with value.lock:
            context(value,data,explicit=True)
            if value.company_id != 'loomworks':
                raise SessionError('case_unavailable','This public cotton case belongs to LoomWorks.',422)
            key,replay = start(value,data,'judge_hindi')
            if replay is not None:
                return replay
            try:
                if value.idempotency[key].get('judge_call_claimed'):
                    raise SessionError('judge_claimed','This interpretation was already attempted. Start a new explicit request.',409)
                verified = evidence(value)
                if verified['hindi_case'] and verified['hindi_case']['status'] == 'success':
                    result = complete(value,key,verified)
                    store.save(value)
                    return result
                if value.judge_attempts >= 3:
                    raise SessionError('judge_limit','This session reached its interpretation limit.',429)
                if not research_workers.acquire(blocking=False):
                    raise SessionError('judge_busy','Interpretation capacity is busy.',429)
                acquired = True
                if not store.reserve_research_budget(store.now().date().isoformat(), 60):
                    raise SessionError('judge_limit','Public interpretation budget is exhausted.',429)
                value.idempotency[key]['judge_call_claimed'] = True
                value.judge_attempts += 1
                value.operator_generation += 1
                generation = value.operator_generation
                marker = snapshot_marker(operator_snapshot(value))
                store.save(value)
            except BaseException:
                if acquired: research_workers.release()
                failed(value,key)
                raise
        try:
            raw = await asyncio.wait_for(run_in_threadpool(judge_provider or VertexHindiProvider(), hindi_payload()),timeout=20)
            with value.lock:
                context(value,data,explicit=True)
                if generation != value.operator_generation or marker != snapshot_marker(operator_snapshot(value)):
                    raise SessionError('stale_context','Workspace changed during interpretation.',409)
                record = sanitize_receipt(raw,store.now())
                if record is None:
                    raise SessionError('interpretation_unavailable','No verifiable model version and interpretation returned.',503)
                value.judge_receipts.setdefault(value.company_id,[]).append(record)
                result = complete(value,key,evidence(value))
                store.save(value)
                return result
        except BaseException as exc:
            with value.lock:
                failed(value,key)
                store.save(value)
            if isinstance(exc,(SessionError,asyncio.CancelledError)): raise
            raise SessionError('interpretation_unavailable','Interpretation unavailable; no verified translation is claimed.',503) from None
        finally:
            research_workers.release()

    @app.get('/api/demo/dispatch')
    async def get_dispatch(request: Request):
        value = session(request)
        with value.lock:
            return dispatch_view(value)

    @app.get('/api/demo/dispatch/{shipment_id}')
    async def get_dispatch_shipment(request: Request, shipment_id: str):
        value = session(request)
        with value.lock:
            current = dispatch_view(value)
            rows = [row for row in current['shipments'] if row['shipment_id'] == shipment_id]
            if len(rows) != 1:
                raise SessionError('shipment_not_found', 'Shipment is unavailable in this workspace.', 404)
            return {'company_id': value.company_id, 'context_version': value.context_version,
                    'shipment': rows[0]}

    @app.post('/api/demo/company')
    async def select_company(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('company_id', 'expected_context_version', 'idempotency_key'))
        if not isinstance(data['company_id'], str) or get_company(data['company_id']) is None:
            raise SessionError('company_not_found', 'Select a listed fictional company.', 404)
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'company')
            if replay is not None:
                return session_response(value)
            try:
                value.select_company(data['company_id'])
                complete(value, key, {'company_id': value.company_id,
                                      'context_version': value.context_version})
                return session_response(value)
            except Exception:
                failed(value, key)
                raise

    async def current_news(value, expected_version):
        """Read the same cached feed as News Workspace, bound to the current company."""
        with value.lock:
            context(value, {'expected_context_version': expected_version}, explicit=True)
            company_id = value.company_id
            company = get_company(company_id)
        try:
            payload = await run_in_threadpool(news_adapter().get, company)
        except Exception:
            # Retain dated company-owned facts, while exposing failed freshness.
            with value.lock:
                context(value, {'expected_context_version': expected_version}, explicit=True)
                previous = deepcopy(value.news)
                if not isinstance(previous, dict) or previous.get('company_id') != company_id:
                    return None
                previous['cache_state'] = 'stale'
                value.news = deepcopy(previous)
                return previous
        with value.lock:
            context(value, {'expected_context_version': expected_version}, explicit=True)
            if value.company_id != company_id:
                raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)
            result = news_view(value, payload, company_id, expected_version)
            value.news = deepcopy(result)
            return result

    async def voice_news(value, expected_version):
        view = await current_news(value, expected_version)
        with value.lock:
            context(value, {'expected_context_version': expected_version}, explicit=True)
            brief = build_news_brief(get_company(value.company_id), view, now=store.now())
            items = brief['headlines'][:3]
            if items:
                lines = []
                for row in items:
                    refs = []
                    for name, label in (('matched_materials','material'),('matched_routes','route'),('matched_currencies','currency')):
                        refs.extend(f'{label} {entry}' for entry in row.get('exposure',{}).get(name,[])[:2])
                    exposure = f" Profile match: {', '.join(refs)}; actual impact needs verification." if refs else ' Sector context; verify company relevance.'
                    lines.append(f"- [{row['title']}]({row['url']}) — {row['publisher'] or row['provider'] or 'Source unverified'}, "
                                 f"{row['published_at'][:10] if row['published_at'] else 'date unavailable'}.{exposure}")
                summary = (f"For {brief['company_name']}, the connected News view shows:\n" + '\n'.join(lines) +
                    '\nNext: check the named supplier and current shipment or route evidence, then review the quote or plan before changing it. These are possible exposures, not confirmed delays or costs.')
            elif brief['global_watch']['events']:
                summary = (f"No company-matched headline is available for {brief['company_name']}. "
                    'Global Watch has public events, but company exposure needs verification against its recorded materials and routes.')
            else:
                summary = 'Connected news coverage is unavailable for this company right now; no current business impact can be verified.'
            if brief['availability'] != 'available':
                summary += '\nCoverage is partial or stale; check the source dates.'
            return {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                'context_version': expected_version, 'status': 'completed',
                'assistant_text': summary,
                'agent_steps': [{'agent': 'news_reader', 'role': 'Read company-owned public news.',
                    'status': 'completed', 'model': 'none', 'provider_call': 'none',
                    'tool': 'read_news', 'summary': 'Read bounded connected news with source dates and conditional exposure.'}],
                'evidence_ids': [row['item_id'] for row in brief['headlines']],
                'proposal': None, 'read_only': True, 'workspace': {'news_brief': brief}}

    def combine_local_news(news_result, local_resolution):
        if local_resolution is None:
            return news_result
        from .experience_service import judge_outcome
        local = judge_outcome(local_resolution)
        result = {**news_result,
            'assistant_text': local['assistant_text'] + '\n' + news_result['assistant_text'],
            'agent_steps': local['agent_steps'] + news_result['agent_steps'],
            'evidence_ids': list(dict.fromkeys(local['evidence_ids'] + news_result['evidence_ids']))}
        if local.get('analytics') is not None:
            result['analytics'] = local['analytics']
        return result

    @app.get('/api/demo/news')
    async def get_news(request: Request):
        value = session(request)
        with value.lock:
            active(value)
            company_id, version = value.company_id, value.context_version
            company = get_company(company_id)
        payload = await run_in_threadpool(news_adapter().get, company)
        with value.lock:
            active(value)
            if value.company_id != company_id or value.context_version != version:
                raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)
            result = news_view(value, payload, company_id, version)
            value.news = deepcopy(result)
            news_freshness(diagnostics, result.get('cache_state'))
            return result

    @app.post('/api/demo/news/refresh')
    async def refresh_news(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('expected_context_version', 'idempotency_key'))
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'news_refresh')
            if replay is not None:
                return replay
            company_id, version = value.company_id, value.context_version
            company = get_company(company_id)
        try:
            payload = await run_in_threadpool(news_adapter().get, company, force=True)
            with value.lock:
                active(value)
                if value.company_id != company_id or value.context_version != version:
                    raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)
                result = news_view(value, payload, company_id, version)
                value.news = deepcopy(result)
                news_freshness(diagnostics, result.get('cache_state'))
                return complete(value, key, result)
        except Exception:
            with value.lock:
                failed(value, key)
            raise

    @app.get('/api/demo/risks')
    async def get_risks(request: Request):
        scan_scope = request.query_params.get('scope', 'all')
        if scan_scope not in ('all', 'current'):
            raise SessionError('invalid_scope', 'Choose current company or portfolio risks.', 400)
        return await scan_risks(session(request), scan_scope=scan_scope)

    @app.get('/api/demo/operator')
    async def operator_overview(request: Request):
        from .operator import ROLES, attention
        value = session(request)
        with value.lock:
            adapter = operator_adapter()
            available = bool(getattr(adapter, 'available', True))
            return {'company_id': value.company_id, 'context_version': value.context_version,
                    'available': available, 'agents': deepcopy(list(ROLES)),
                    'watch_items': deepcopy(value.watch_items),
                    'attention': attention(get_company(value.company_id)),
                    'limitations': ['Company inventory and plans are synthetic.',
                                    'Public news is observed metadata, not a confirmed delay or forecast.',
                                    'No supplier message or order can be sent.']}

    @app.post('/api/demo/operator')
    async def operator_turn(request: Request):
        value = mutate(request)
        data = await body(request)
        return await execute_operator_turn(value, data, trusted_typed_user=True)

    @app.get('/api/demo/operator/history')
    async def operator_history(request: Request):
        value = session(request)
        with value.lock:
            return {'company_id': value.company_id, 'context_version': value.context_version,
                    'exchanges': deepcopy(value.chat_history.get(value.company_id, []))}

    @app.post('/api/demo/operator/history')
    async def voice_history(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('text', 'speaker', 'channel', 'expected_context_version', 'idempotency_key'))
        if (data['channel'] != 'voice' or data['speaker'] not in ('input', 'output')
                or not isinstance(data['text'], str) or not data['text'].strip()
                or len(data['text']) > 16000
                or any(ord(char) < 32 and char not in '\n\t' for char in data['text'])):
            raise SessionError('invalid_history', 'Provide one completed voice caption.', 422)
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'voice_history')
            if replay is not None:
                replay.pop('replayed', None)
                return replay
            entry = {'id': secrets.token_urlsafe(18), 'channel': 'voice',
                     'speaker': data['speaker'], 'question': data['text'].strip() if data['speaker'] == 'input' else None,
                     'answer': data['text'].strip() if data['speaker'] == 'output' else None}
            remember(value, entry)
            return complete(value, key, {'status': 'saved', 'company_id': value.company_id,
                                        'context_version': value.context_version, 'id': entry['id']})

    @app.post('/api/demo/operator/cancel')
    async def cancel_operator(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('proposal_id', 'expected_context_version', 'idempotency_key'))
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'operator_cancel')
            if replay is not None:
                replay.pop('replayed', None)
                return replay
            if (not isinstance(data['proposal_id'], str) or value.pending_proposal is None
                    or value.pending_proposal['proposal_id'] != data['proposal_id']):
                failed(value, key)
                raise SessionError('proposal_not_found', 'Proposal is no longer current.', 404)
            value.pending_proposal = None
            value.operator_generation += 1
            return complete(value, key, {'status': 'cancelled', 'company_id': value.company_id,
                                        'context_version': value.context_version})

    def typed_proposal_approval(message, proposal):
        """Only an exact human typed instruction can select the current reviewed card."""
        words = re.sub(r'[^a-z0-9 ]+', ' ', message.casefold()).split()
        phrase = ' '.join(words)
        if not words or len(words) > 14 or any(word in words for word in
                ('no', 'not', 'never', 'cancel', 'stop', 'dont', 'withdraw')):
            return False
        kind = proposal['kind'] if proposal else None
        noun = {'watch': 'watch', 'recovery': 'recovery', 'review_quotes': 'quotes',
                'simulate_email': 'simulated email', 'rfq': 'rfq draft'}.get(kind)
        if noun is None:
            return False
        phrases = {f'confirm this {noun}', f'approve this {noun}',
                          f'i approve this {noun} as shown',
                          f'i approve the {noun} as shown',
                          'i approve this proposal as shown',
                          'i approve the current proposal as shown',
                          'confirm this proposal',
                          'i approve the draft as shown' if kind in {'rfq', 'simulate_email'} else ''}
        if kind == 'review_quotes':
            phrases.update({'confirm these quotes', 'approve these quotes',
                            'i approve these quotes as shown',
                            'i approve the reviewed quotes as shown'})
        return phrase in phrases

    async def execute_operator_turn(value, data, *, channel='text', trusted_typed_user=False):
        nonlocal operator_process_calls
        from .operator import (DEFAULT_MODEL, OperatorUnavailable, OperatorValidationError,
                               _broad_inventory_scope, run_turn)
        shape(data, ('message', 'expected_context_version', 'idempotency_key'))
        message = data['message']
        if not isinstance(message, str) or not message.strip() or len(message) > 1200:
            raise SessionError('invalid_message', 'Provide a message of at most 1200 characters.', 422)
        from .experience_service import baseline_snapshot, judge_outcome, resolve_local_turn, has_local_evidence
        from .carbon_operator import is_estimate_request, is_pending_reply, estimate_turn
        with value.lock:
            context(value, data, explicit=True)
            proposal = value.pending_proposal
            approval_phrase = ' '.join(re.sub(r'[^a-z0-9 ]+', ' ', message.casefold()).split())
            approval_language = (typed_proposal_approval(message, proposal) or
                approval_phrase in {'yes', 'no', 'approve', 'confirm', 'i approve', 'i confirm',
                                    'approve it', 'confirm it', 'i approve it', 'i confirm it'} or
                bool(re.fullmatch(
                    r'(?:i )?(?:(?:don t|do not|never|no) )?(?:approve|confirm) '
                    r'(?:this|the|the current|current|these) '
                    r'(?:proposal|draft|rfq draft|watch|recovery|quotes|reviewed quotes|simulated email)'
                    r'(?: as shown)?', approval_phrase)))
            cancelling = message.strip().casefold().rstrip('.!') in {
                'cancel this proposal', 'discard this proposal', 'cancel the proposal'}
            if trusted_typed_user and (approval_language or cancelling):
                key, replay = start(value, data, 'operator_turn')
                if replay is not None:
                    entry = value.idempotency[key]
                    if (entry.get('state_marker') != snapshot_marker(operator_snapshot(value)) or
                            entry.get('operator_generation') != value.operator_generation):
                        raise SessionError('stale_context', 'Workspace changed since this approval.', 409)
                    replay.pop('replayed', None)
                    return replay
                if cancelling and proposal is not None:
                    value.pending_proposal = None
                    value.operator_generation += 1
                    result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                        'context_version': value.context_version, 'status': 'completed',
                        'assistant_text': 'Cancelled the current proposal. No reviewed change was applied.',
                        'agent_steps': [], 'evidence_ids': [], 'proposal': None,
                        'read_only': False, 'action_result': {'tool': 'cancel_proposal',
                            'summary': 'Cancelled the current proposal.'}, 'session': view(value)}
                elif not typed_proposal_approval(message, proposal):
                    result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                        'context_version': value.context_version, 'status': 'needs_clarification',
                        'assistant_text': ('Please review the current proposal, then say which action you approve.'
                                           if proposal else 'There is no current proposal to approve.'),
                        'agent_steps': [], 'evidence_ids': [],
                        'proposal': public_proposal(proposal) if proposal else None,
                        'read_only': True}
                else:
                    apply_key = 'chat-' + hashlib.sha256((value.session_id + ':' + key).encode()).hexdigest()[:40]
                    try:
                        applied = apply_pending_proposal(value, proposal['proposal_id'],
                                                         value.context_version, apply_key)
                    except Exception:
                        failed(value, key)
                        raise
                    place = {'watch': 'Watch', 'recovery': 'Recovery',
                             'review_quotes': 'Procurement quotes',
                             'simulate_email': 'Simulated outbox', 'rfq': 'Drafts'}[applied['kind']]
                    result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                        'context_version': value.context_version, 'status': 'completed',
                        'assistant_text': f"Applied the reviewed {applied['kind'].replace('_', ' ')}. You can find it in {place}.",
                        'agent_steps': [], 'evidence_ids': [], 'proposal': None,
                        'read_only': False, 'action_result': {'tool': applied['kind'],
                            'summary': f'Applied reviewed {applied["kind"]}.'},
                        'session': applied['session']}
                complete(value, key, result)
                value.idempotency[key]['state_marker'] = snapshot_marker(operator_snapshot(value))
                value.idempotency[key]['operator_generation'] = value.operator_generation
                remember_turn(value, message, result, channel=channel)
                return result
        # Load the canonical cached News view for the utterance itself. On a
        # first turn the resolver can only see news=None and may propose public
        # research before these company-owned facts have been read.
        briefing = is_workspace_news_question(message)
        news_outcome = await voice_news(value,data['expected_context_version']) if briefing else None
        with value.lock:
            context(value, data, explicit=True)
            from .research import prepare_research, ResearchValidationError
            current_snapshot = operator_snapshot(value)
            carbon_turn = is_estimate_request(message, has_assessment=current_snapshot.get('carbon_assessment') is not None) or (
                value.pending_carbon is not None and
                is_pending_reply(message, value.pending_carbon, get_company(value.company_id)))
            resolved = None if carbon_turn else resolve_local_turn(current_snapshot, message, now=store.now())
            local_news_resolution = None
            if news_outcome is not None:
                local_snapshot = {**current_snapshot, 'news': None, 'completed_research': []}
                local = resolve_local_turn(local_snapshot, message, now=store.now())
                if has_local_evidence(local, local_snapshot):
                    local_news_resolution = local
            try:
                research_parameters = (None if carbon_turn or briefing or resolved is not None else
                    prepare_research(message, get_company(value.company_id), store.now()))
                research_invalid = None
            except ResearchValidationError as exc:
                research_parameters, research_invalid = None, str(exc)
            prepared = carbon_turn or news_outcome is not None or resolved is not None or research_parameters is not None or research_invalid is not None
            key, replay = start(value, data, 'operator_turn')
            if replay is not None:
                if (value.idempotency[key].get('operator_generation') != value.operator_generation or
                        value.idempotency[key].get('state_marker') != snapshot_marker(operator_snapshot(value))):
                    raise SessionError('stale_context', 'Workspace changed since this operator turn.', 409)
                replay.pop('replayed', None)
                return replay
            previous_generation = value.idempotency[key].get('operator_generation')
            if previous_generation is not None and previous_generation != value.operator_generation:
                failed(value, key)
                raise SessionError('stale_context', 'A newer operator turn superseded this request.', 409)
            snapshot = operator_snapshot(value)
            marker = snapshot_marker(snapshot)
            # Canonical reads do not call a model. Keep the normal request,
            # session and concurrency limits, without reserving AI capacity.
            from .integration_tools import integration_request, navigation_request
            prepared = (prepared or integration_request(snapshot, message) is not None
                        or navigation_request(message) is not None
                        or _broad_inventory_scope(snapshot, message) is not None)
            adapter = operator_adapter()
            model = getattr(adapter, 'model', None) or os.getenv('GEMINI_OPERATOR_MODEL', DEFAULT_MODEL)
            if not prepared and not bool(getattr(adapter, 'available', True)):
                value.operator_generation += 1
                value.idempotency[key]['operator_generation'] = value.operator_generation
                result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                          'context_version': value.context_version, 'status': 'unavailable',
                          'assistant_text': 'The Vertex operator is unavailable; no action was taken.',
                          'agent_steps': [], 'evidence_ids': [], 'proposal': None, 'read_only': True}
                complete(value, key, result)
                remember_turn(value, message, result, channel=channel)
                value.idempotency[key]['state_marker'] = marker
                return result
            if value.operator_turns >= MAX_OPERATOR_TURNS:
                failed(value, key)
                raise SessionError('operator_limit', 'This session reached 36 operator turns. Start a new session to continue.', 429)
            if not operator_workers.acquire(blocking=False):
                failed(value, key)
                raise SessionError('operator_busy', 'Operator is busy. Try again shortly.', 429)
            with operator_lock:
                cutoff = store.now() - timedelta(hours=1)
                operator_call_times[:] = [time for time in operator_call_times if time > cutoff]
                if not prepared and len(operator_call_times) + 3 > MAX_OPERATOR_PROCESS_CALLS_PER_HOUR:
                    operator_workers.release()
                    failed(value, key)
                    raise SessionError('operator_limit', 'Operator call limit reached. Capacity resets within one hour.', 429)
                if not prepared:
                    operator_call_times.extend([store.now()] * 3)
            value.operator_turns += 1
            value.operator_generation += 1
            generation = value.operator_generation
            value.idempotency[key]['operator_generation'] = generation
        try:
            try:
                if carbon_turn:
                    outcome = {'status': 'completed', 'assistant_text': '', 'agent_steps': [],
                               'evidence_ids': [], 'candidate': None, 'carbon_turn': True}
                elif news_outcome is not None:
                    outcome = {**combine_local_news(news_outcome, local_news_resolution), 'candidate': None}
                else:
                    outcome = (judge_outcome(resolved) if resolved is not None else await run_in_threadpool(run_turn, snapshot, message, adapter if not prepared else None,
                                                  model=model,
                                                  provider_call='mocked' if operator_injected else 'real',
                                                  context_cache=context_cache,
                                                  cache_scope=hashlib.sha256(value.session_id.encode()).hexdigest()))
            except (OperatorUnavailable, OperatorValidationError) as exc:
                category = 'provider' if isinstance(exc, OperatorUnavailable) else 'validation'
                fingerprint = hashlib.sha256(str(exc).encode()).hexdigest()[:12]
                diagnostics.warning('operator_turn_unavailable category=%s fingerprint=%s',
                                    category, fingerprint)
                outcome = {'status': 'unavailable',
                           'assistant_text': 'The operator could not verify this response; no action was taken.',
                           'agent_steps': [], 'evidence_ids': [], 'candidate': None}
            with value.lock:
                active(value)
                if value.operator_generation != generation:
                    raise SessionError('stale_context', 'A newer operator turn superseded this request.', 409)
                if snapshot_marker(operator_snapshot(value)) != marker:
                    raise SessionError('stale_context', 'Workspace changed during the operator turn.', 409)
                if outcome.get('judge_resolution', {}).get('kind') == 'read_only_preview':
                    outcome = execute_judge_preview(value, outcome)
                proposal = None
                utility = outcome.get('utility')
                action_result = None
                changed = False
                if outcome.get('carbon_turn'):
                    carbon = estimate_turn(value, message, active_plan=active_plan,
                        validate_plan_current=validate_plan_current, compare_quotes=compare_quotes,
                        company=get_company(value.company_id), now=store.now())
                    outcome.update(status=carbon['status'], assistant_text=carbon['assistant_text'],
                        agent_steps=[{'agent': 'workspace', 'role': 'deterministic',
                                      'status': 'completed', 'model': 'none', 'provider_call': 'none',
                                      'tool': 'estimate_carbon',
                                      'summary': 'Calculated against current owned plan and cited factors.'}])
                    if carbon['comparison'] is not None:
                        outcome['workspace'] = {'carbon': carbon['comparison']}
                    if carbon['changed']:
                        action_result = {'tool': 'estimate_carbon',
                                         'summary': ('Saved current plan emissions assessment.'
                                                     if carbon['comparison'] is not None else carbon['assistant_text'])}
                        changed = True
                if utility is not None:
                    action_result, changed = execute_utility(value, utility, message)
                candidate = outcome.get('candidate')
                if candidate is not None:
                    kind = candidate['kind']
                    parameters = deepcopy(candidate['parameters'])
                    rfq_preview = None
                    plan = active_plan(value)
                    if kind == 'recovery':
                        from .recovery import compare
                        baseline = value.last_plan
                        if 'shipment_id' in parameters:
                            bound_shipment(value, parameters['shipment_id'], baseline,
                                           parameters['delay_days_by_offer'])
                        selected = current_offers(value, baseline, baseline['offer_versions'],
                                                  today=store.now().date())
                        preview = compare(baseline, value.plan_request, selected,
                                          parameters['delay_days_by_offer'], today=store.now().date())
                        parameters['preview'] = {'outcome': preview['outcome'],
                                                 'cost_delta': preview['cost_delta'],
                                                 'selected_offer_id': preview['scenario']['selected_offer_id'],
                                                 'total_cost': preview['scenario']['total_cost'],
                                                 'reporting_currency': preview['scenario']['reporting_currency']}
                    elif kind == 'rfq':
                        from .rfq import build_draft
                        selected = current_offers(value, plan, plan['offer_versions'],
                                                  today=store.now().date())
                        chosen = next((o for o in selected if o['offer_id'] == plan['selected_offer_id']), None)
                        if chosen is None:
                            raise SessionError('draft_unavailable', 'Selected quote is unavailable.', 409)
                        rfq_preview = build_draft(deepcopy(plan), deepcopy(chosen),
                                                  all_offers=deepcopy(selected), created_at=store.now())
                    elif kind == 'simulate_email':
                        validate_simulated_email(value, parameters)
                    elif kind == 'review_quotes':
                        parameters = reviewed_quote_preview(value, parameters)
                    bound_plan = value.last_plan if kind == 'recovery' else plan
                    proposal = {'proposal_id': secrets.token_urlsafe(18), 'kind': kind,
                                'summary': outcome['assistant_text'], 'parameters': parameters,
                                'plan_id': bound_plan['plan_id'] if bound_plan else None,
                                'offer_versions': ({item['offer_id']: item['expected_version'] for item in parameters['preview']}
                                                   if kind == 'review_quotes' else
                                                   deepcopy(bound_plan['offer_versions']) if bound_plan else {}),
                                'context_version': value.context_version,
                                'expires_at': (store.now() + timedelta(minutes=5)).isoformat(),
                                '_marker': marker, '_company_id': value.company_id,
                                '_session_id': value.session_id}
                    remember_prepared_draft(value, proposal, rfq_preview=rfq_preview)
                    value.pending_proposal = proposal
                    if kind == 'rfq':
                        outcome['status'] = 'completed'
                        outcome['assistant_text'] = 'Created an unsent RFQ draft. You can review and download it in Drafts.'
                        action_result = {'tool': 'prepare_rfq', 'summary': 'Created an unsent RFQ draft in Drafts.'}
                        changed = True
                    elif (kind == 'simulate_email' and
                          re.search(r'\b(draft|prepare|write|compose)\b', message, re.I) and
                          not re.search(r'\b(send|sent|dispatch)\b',
                                        re.split(r'\b(?:subject|body)\s*:', message, maxsplit=1,
                                                 flags=re.I)[0], re.I)):
                        outcome['status'] = 'completed'
                        outcome['assistant_text'] = 'Created a simulated email draft in Drafts. No external message was sent.'
                        action_result = {'tool': 'prepare_simulated_email',
                                         'summary': 'Created a simulated email draft in Drafts.'}
                        changed = True
                result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                          'context_version': value.context_version, 'status': outcome['status'],
                          'assistant_text': outcome['assistant_text'],
                          'agent_steps': deepcopy(outcome['agent_steps']),
                          'evidence_ids': deepcopy(outcome['evidence_ids']),
                          'proposal': public_proposal(proposal) if proposal else None,
                          'read_only': not changed}
                if action_result is not None:
                    result['action_result'] = action_result
                    result['session'] = view(value)
                if outcome.get('analytics') is not None:
                    result['analytics'] = deepcopy(outcome['analytics'])
                if outcome.get('workspace') is not None:
                    result['workspace'] = deepcopy(outcome['workspace'])
                if proposal is not None and proposal['kind'] in ('rfq', 'simulate_email'):
                    result['workspace'] = {'drafts': deepcopy(value.drafts)}
                complete(value, key, result)
                remember_turn(value, message, result, channel=channel)
                value.idempotency[key]['state_marker'] = snapshot_marker(operator_snapshot(value))
                store.save(value)
                return result
        except Exception:
            with value.lock:
                failed(value, key)
            raise
        finally:
            operator_workers.release()

    def execute_judge_preview(value, outcome):
        from .experience_service import baseline_snapshot
        from .judge_impact import build_judge_impact
        from .judge_resolver import resolve_judge_turn
        from .recovery import compare
        resolved = outcome['judge_resolution']
        request = resolved['preview_request']
        snapshot = baseline_snapshot(operator_snapshot(value))
        if (resolved['company_id'] != value.company_id or resolved['context_version'] != value.context_version
                or resolved.get('baseline_plan_id') != (snapshot.get('plan') or {}).get('plan_id')
                or resolved.get('offer_versions') != (snapshot.get('plan') or {}).get('offer_versions', {})):
            raise SessionError('stale_context', 'Preview context changed.', 409)
        preview = {'preview_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                   'context_version': value.context_version, 'baseline_plan_id': resolved.get('baseline_plan_id'),
                   'scenario_plan_id': None, 'read_only': True, 'mode': request.get('mode','reviewed_plan')}
        if request.get('mode') != 'profile_stock_cover':
            baseline = snapshot['plan']
            validate_plan_current(value, baseline)
            if request.get('material_id') != baseline.get('material'):
                raise SessionError('stale_plan', 'Preview material changed.', 409)
            delays = request['delay_days_by_offer']
            row = resolve_recovery_shipment(snapshot['shipments'], request['shipment_id'],
                baseline['plan_id'], baseline['offer_versions'].get(baseline['selected_offer_id']))
            if row is None or set(delays) != {baseline['selected_offer_id']}:
                raise SessionError('shipment_not_found', 'Owned baseline shipment unavailable.', 409)
            offers = current_offers(value, baseline, baseline['offer_versions'], today=store.now().date())
            compared = compare(baseline, value.plan_request, offers, delays, today=store.now().date())
            compared['scenario']['company_id'] = value.company_id
            compared['scenario']['plan_id'] = None
            impact = build_judge_impact(snapshot, compared, [row['order_id']] if row.get('order_id') else [])
            preview.update(impact=impact, outcome=compared['outcome'])
            delta = impact['additional_recovery_cost']
            cost = f"{delta['value']} {delta['currency']}" if delta['status']=='available' else 'unavailable for this scenario'
            outcome['assistant_text'] += f" Baseline {baseline['total_cost']} {baseline['reporting_currency']}; assumed-delay outcome {compared['outcome']}; additional recovery cost {cost}. This preview leaves the accepted plan and RFQ unchanged."
        preview['summary'] = outcome['assistant_text']
        outcome['workspace'] = {'judge_preview': preview}
        outcome['candidate'] = None
        return outcome

    @app.websocket('/api/demo/voice/live')
    async def live_voice(websocket: WebSocket):
        # Same trusted runtime and operator boundary as typed chat; no browser token.
        source = websocket.headers.get('origin')
        expected_url = str(websocket.url).replace('wss://', 'https://', 1).replace('ws://', 'http://', 1)
        expected = origin(expected_url)
        if (source is None or origin(source) != expected or
                (not secure_cookies and (expected is None or expected[1] not in ('localhost', '127.0.0.1', '::1')))):
            await websocket.close(code=1008)
            return
        try:
            value = session(websocket)
        except SessionError as exc:
            # Only same-origin clients reach here. Accept long enough to deliver
            # a reviewed recovery reason; no provider connection is opened.
            await websocket.accept()
            await websocket.send_json({'type': 'error', 'code': exc.code,
                                       'message': exc.message})
            await websocket.close(code=1008)
            return
        from .live_voice import run_live_voice

        async def operate(message, context_version, key):
            return await execute_operator_turn(value, {'message': message,
                'expected_context_version': context_version, 'idempotency_key': key}, channel='voice')

        async def workspace_tools(name, args, context_version, key, *, on_research_progress=None):
            if name == 'confirm_workspace_action':
                shape(args, ('proposal_id',))
                applied = apply_pending_proposal(value, args['proposal_id'], context_version, key)
                place = 'Watch' if applied['kind'] == 'watch' else 'Recovery'
                return {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                    'context_version': context_version, 'status': 'completed',
                    'assistant_text': f"Applied the reviewed {applied['kind']}. You can find it in {place}.",
                    'agent_steps': [], 'evidence_ids': [], 'proposal': None,
                    'read_only': False, 'action_result': {'tool': applied['kind'],
                        'summary': f"Applied reviewed {applied['kind']}."}, 'session': applied['session']}
            if name == 'read_news':
                shape(args, ())
                return await voice_news(value, context_version)
            if name == 'confirm_research':
                shape(args, ('proposal_id',))
                if not isinstance(args['proposal_id'], str) or not args['proposal_id']:
                    raise SessionError('invalid_proposal', 'Choose the current research request.', 422)
                # The live bridge admits this callback only for a fresh, explicit
                # user transcript after reading the exact query. Reuse the same
                # durable claim and replay checks as the visual confirmation.
                confirmed = await confirm_research(value, {
                    'proposal_id': args['proposal_id'], 'reviewed': True,
                    'expected_context_version': context_version, 'idempotency_key': key},
                    on_progress=on_research_progress)
                research = deepcopy(confirmed['result'])
                # Voice needs the grounded answer/citations, not transient
                # provider markup in either model context or replay caches.
                research['search_suggestions_html'] = None
                summary = ('Public research completed.' if research['status'] == 'completed'
                           else 'Public research is unavailable; no business action was taken.')
                return {'turn_id': research['research_id'], 'company_id': value.company_id,
                    'context_version': context_version, 'status': 'completed', 'assistant_text': research['answer'],
                    'agent_steps': [], 'evidence_ids': [], 'proposal': None, 'read_only': False,
                    'session': confirmed['session'], 'action_result': {'tool': 'confirm_research', 'summary': summary},
                    'workspace': {'research': research}}
            if name == 'prepare_research':
                if not isinstance(args, dict):
                    raise SessionError('invalid_research', 'Provide a public research question.', 422)
                shape(args, ('message',))
                from .experience_service import resolve_local_turn, has_local_evidence
                # Share the typed path's canonical News read and local facts.
                if is_workspace_news_question(args['message']):
                    news_result = await voice_news(value, context_version)
                    with value.lock:
                        context(value, {'expected_context_version': context_version}, explicit=True)
                        local_snapshot = {**operator_snapshot(value), 'news': None,
                                          'completed_research': []}
                        local = resolve_local_turn(local_snapshot, args['message'], now=store.now())
                        if not has_local_evidence(local, local_snapshot):
                            local = None
                        return combine_local_news(news_result, local)
                from .research import prepare_research, ResearchValidationError
                with value.lock:
                    context(value, {'expected_context_version': context_version}, explicit=True)
                    local = resolve_local_turn(operator_snapshot(value), args['message'], now=store.now())
                    if local is None:
                        try:
                            public = prepare_research(args['message'], get_company(value.company_id), store.now())
                        except ResearchValidationError:
                            public = None
                        if public is None:
                            raise SessionError('invalid_research','Provide a public question without private workspace details.',422)
                return await execute_operator_turn(value, {'message': args['message'],
                    'expected_context_version': context_version, 'idempotency_key': key}, channel='voice')
            if name == 'read_research':
                if not isinstance(args, dict):
                    raise SessionError('invalid_research', 'Choose an approved research result.', 422)
                shape(args, (), ('research_id',))
                with value.lock:
                    context(value, {'expected_context_version': context_version}, explicit=True)
                    records = [item for item in value.research_records.values()
                        if item.get('company_id') == value.company_id
                        and item.get('context_version') == value.context_version
                        and item.get('state') == 'completed'
                        and item.get('result', {}).get('status') == 'completed']
                    requested = args.get('research_id')
                    if requested is not None:
                        records = [item for item in records if item['result']['research_id'] == requested]
                    result = deepcopy(records[-1]['result']) if records else None
                    if result is None:
                        raise SessionError('research_not_found',
                            'No completed approved research is available for this company. Prepare a search and review its consent card first.', 404)
                    return {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                        'context_version': value.context_version, 'status': 'completed',
                        'assistant_text': result['answer'], 'agent_steps': [],
                        'evidence_ids': [], 'proposal': None, 'read_only': True,
                        'workspace': {'research': result}}
            if name == 'read_risks':
                if not isinstance(args, dict):
                    raise SessionError('invalid_utility', 'Risk scope must be an object.', 422)
                shape(args, (), ('scope',))
                scope = args.get('scope', 'current')
                if scope not in ('current', 'all'):
                    raise SessionError('invalid_utility', 'Choose current or all company risks.', 422)
                report = await scan_risks(value, context_version)
                projection = risk_projection(report, scope=scope)
                count = sum(sum(row['counts'].values()) for row in projection['companies'])
                return {'turn_id': secrets.token_urlsafe(18), 'company_id': report['company_id'],
                    'context_version': report['context_version'], 'status': 'completed',
                    'assistant_text': f"Scanned {len(projection['companies'])} companies; {count} risk and watch signals. Review the evidence and conditional impacts before taking action.",
                    'agent_steps': [{'agent': 'risk_monitor', 'role': 'Assess evidence-bound company risks.',
                        'status': 'completed', 'model': 'none', 'provider_call': 'none',
                        'tool': 'read_risks', 'summary': 'Read current company-owned risk signals.'}],
                    'evidence_ids': [], 'proposal': None, 'read_only': True,
                    'workspace': {'risks': projection}}
            from .agent_tools import inventory_analytics
            from .quote_service import company_for_offer
            mapping = {'read_workspace', 'analyze_inventory', 'fill_buyer_form',
                       'load_sample_quotes', 'review_sample_quotes', 'compare_suppliers',
                       'prepare_rfq', 'prepare_simulated_email', 'navigate_workspace',
                       'read_carbon', 'read_routes', 'read_shipments', 'preview_supplier_delay',
                       'estimate_carbon',
                       'prepare_shipment_recovery'}
            if name not in mapping or not isinstance(args, dict):
                raise SessionError('invalid_utility', 'Unsupported voice workspace tool.', 422)
            with value.lock:
                context(value, {'expected_context_version': context_version}, explicit=True)
                # Gemini may return a catalogue name or a case variant of its ID.
                # Resolve only exact owned-company matches before binding the retry.
                args = deepcopy(args)
                if 'material_id' in args:
                    supplied = args['material_id']
                    if not isinstance(supplied, str) or not supplied.strip():
                        raise SessionError('invalid_material',
                            f'{name} made no change: choose an exact material ID or name from this company.', 422)
                    needle = supplied.strip().casefold()
                    matches = {row['material_id'] for row in get_company(value.company_id)['materials']
                               if needle in (row['material_id'].casefold(), row['name'].casefold())}
                    if len(matches) != 1:
                        raise SessionError('invalid_material',
                            f'{name} made no change: choose an exact material ID or name from this company.', 422)
                    args['material_id'] = matches.pop()
                data = {'name': name, 'args': args, 'expected_context_version': context_version,
                        'idempotency_key': key}
                token, replay = start(value, data, 'voice_workspace_tool')
                if replay is not None:
                    if value.idempotency[token].get('state_marker') != snapshot_marker(operator_snapshot(value)):
                        raise SessionError('stale_context', 'Workspace changed since this voice action.', 409)
                    replay.pop('replayed', None)
                    return replay
                marker = snapshot_marker(operator_snapshot(value))
                try:
                    result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                              'context_version': value.context_version, 'status': 'completed',
                              'assistant_text': '', 'agent_steps': [{'agent': 'workspace', 'role': 'deterministic',
                                  'status': 'completed', 'model': 'none', 'provider_call': 'none',
                                  'tool': name, 'summary': 'Validated owned workspace tool.'}],
                              'evidence_ids': [], 'proposal': None, 'read_only': True}
                    if name == 'estimate_carbon':
                        shape(args, ('message',))
                        if not isinstance(args['message'], str) or not 1 <= len(args['message'].strip()) <= 1200:
                            raise SessionError('invalid_message', 'State the carbon request or freight details.', 422)
                        from .carbon_operator import estimate_turn
                        carbon = estimate_turn(value, args['message'], active_plan=active_plan,
                            validate_plan_current=validate_plan_current, compare_quotes=compare_quotes,
                            company=get_company(value.company_id), now=store.now())
                        result.update(status=carbon['status'], assistant_text=carbon['assistant_text'])
                        if carbon['comparison'] is not None:
                            result['workspace'] = {'carbon': carbon['comparison']}
                        if carbon['changed']:
                            result['action_result'] = {'tool': 'estimate_carbon',
                                'summary': ('Saved current plan emissions assessment.'
                                            if carbon['comparison'] is not None else carbon['assistant_text'])}
                            result['session'] = view(value)
                            result['read_only'] = False
                    elif name == 'preview_supplier_delay':
                        shape(args, ('message',))
                        message = args['message']
                        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 1200:
                            raise SessionError('invalid_message', 'State one supplier delay question.', 422)
                        from .experience_service import baseline_snapshot, resolve_local_turn, judge_outcome
                        local = resolve_local_turn(baseline_snapshot(operator_snapshot(value)),
                                                   message, now=store.now())
                        if local is None or local['kind'] not in {
                                'read_only_preview', 'needs_input', 'unavailable'}:
                            result.update(status='needs_clarification',
                                assistant_text='Ask a what-if question with an explicit supplier delay in whole days. No recovery was prepared.')
                        else:
                            outcome = judge_outcome(local)
                            if local['kind'] == 'read_only_preview':
                                outcome = execute_judge_preview(value, outcome)
                            result.update(status=outcome['status'],
                                assistant_text=outcome['assistant_text'],
                                agent_steps=outcome['agent_steps'],
                                evidence_ids=outcome['evidence_ids'])
                            if outcome.get('workspace') is not None:
                                result['workspace'] = outcome['workspace']
                    elif name in ('read_carbon', 'read_shipments', 'read_routes'):
                        shape(args, ('message',) if name == 'read_routes' else ())
                        from .integration_tools import integration_read
                        payload = integration_read(operator_snapshot(value), name,
                                                   args['message'] if name == 'read_routes' else '')
                        result.update(payload)
                    elif name == 'prepare_shipment_recovery':
                        shape(args, ('shipment_id', 'delay_days'))
                        days = args['delay_days']
                        if type(days) is not int or not 0 <= days <= 3650:
                            raise SessionError('invalid_delay', 'Use explicit whole delay days.', 422)
                        baseline = value.last_plan
                        if baseline is None:
                            raise SessionError('stale_plan', 'Compare reviewed quotes first.', 409)
                        selected_id = baseline.get('selected_offer_id')
                        delays = {selected_id: days}
                        row = bound_shipment(value, args['shipment_id'], baseline, delays)
                        from .recovery import compare
                        selected = current_offers(value, baseline, baseline['offer_versions'],
                                                  today=store.now().date())
                        preview = compare(baseline, value.plan_request, selected, delays,
                                          today=store.now().date())
                        parameters = {'shipment_id': row['shipment_id'],
                                      'delay_days_by_offer': delays,
                                      'preview': {'outcome': preview['outcome'],
                                          'cost_delta': preview['cost_delta'],
                                          'selected_offer_id': preview['scenario']['selected_offer_id'],
                                          'total_cost': preview['scenario']['total_cost'],
                                          'reporting_currency': preview['scenario']['reporting_currency']}}
                        proposal = {'proposal_id': secrets.token_urlsafe(18), 'kind': 'recovery',
                                    'summary': f"Review recovery for {row['material_name']} with an assumed {days}-day delay. No plan change has run.",
                                    'parameters': parameters, 'plan_id': baseline['plan_id'],
                                    'offer_versions': deepcopy(baseline['offer_versions']),
                                    'context_version': value.context_version,
                                    'expires_at': (store.now() + timedelta(minutes=5)).isoformat(),
                                    '_marker': marker, '_company_id': value.company_id,
                                    '_session_id': value.session_id}
                        value.pending_proposal = proposal
                        result.update(status='proposal', proposal=public_proposal(proposal),
                                      assistant_text=proposal['summary'])
                    elif name in ('read_workspace', 'analyze_inventory'):
                        shape(args, (), ('material_id',))
                        material_id = args.get('material_id')
                        if material_id is not None:
                            scenario(value, material_id)
                        snapshot = operator_snapshot(value)
                        analytics = inventory_analytics(snapshot)
                        if material_id:
                            analytics['materials'] = [item for item in analytics['materials'] if item['material_id'] == material_id]
                            analytics['materials_by_cover'] = [item for item in analytics['materials_by_cover'] if item['material_id'] == material_id]
                        result['analytics'] = analytics
                        if name == 'read_workspace':
                            offer_material = (material_id or value.selected_material_id or
                                              (active_plan(value) or {}).get('material') or
                                              snapshot['company']['materials'][0]['material_id'])
                            quote_ids = scenario(value, offer_material)['fixture_ids']
                            offers = [offer for offer in value.offers.values()
                                      if company_for_offer(offer) == value.company_id and
                                      offer.get('fixture_id') in quote_ids]
                            result['workspace'] = {'company': deepcopy(snapshot['company']),
                                'buyer_draft': deepcopy(value.buyer_draft),
                                'selected_material_id': value.selected_material_id,
                                'drafts': [{key: item[key] for key in
                                            ('draft_id', 'kind', 'status', 'material_id', 'recipient_label', 'subject')}
                                           for item in value.drafts[-12:]],
                                'news_brief': build_news_brief(
                                    snapshot['company'], value.news, now=store.now()),
                                'plan': deepcopy(active_plan(value)),
                                'risks': deepcopy(snapshot.get('risk_report')),
                                'offers': [{'offer_id': offer['offer_id'], 'version': offer['version'],
                                            'status': offer['status'], 'fixture_id': offer.get('fixture_id'),
                                            'source_filename': offer.get('document', {}).get('filename'),
                                            'supplier_name': (offer.get('fields', {}).get('supplier_name') or {}).get('value'),
                                            'lead_time_days': (offer.get('fields', {}).get('lead_time_days') or {}).get('value'),
                                            'pricing': ((offer.get('items') or [{}])[0].get('pricing', {}).get('value')
                                                        if offer['status'] == 'saved' else None)}
                                           for offer in offers[:MAX_OFFERS]],
                                'simulated_outbox': [{'message_id': item['message_id'], 'material_id': item['material_id'],
                                                      'recipient_label': item['recipient_label'], 'status': item['status']}
                                                     for item in value.simulated_outbox]}
                        result['assistant_text'] = 'Current workspace facts are ready.'
                    elif name == 'prepare_rfq':
                        shape(args, ())
                        plan = active_plan(value)
                        if plan is None:
                            raise SessionError('draft_unavailable',
                                               'Compare reviewed supplier quotes to create a current sourcing plan before drafting an RFQ.', 409)
                        selected_id = plan.get('selected_offer_id')
                        if selected_id is None or plan.get('required_purchase', plan.get('required_purchase_kg')) == '0':
                            raise SessionError('draft_unavailable',
                                               'The current plan has no eligible purchase to request. Review the plan before drafting an RFQ.', 409)
                        from .rfq import build_draft
                        selected = current_offers(value, plan, plan['offer_versions'],
                                                  today=store.now().date())
                        chosen = next((offer for offer in selected if offer['offer_id'] == selected_id), None)
                        if chosen is None:
                            raise SessionError('stale_plan', 'Selected quote is no longer available.', 409)
                        preview = build_draft(deepcopy(plan), deepcopy(chosen),
                                              all_offers=deepcopy(selected), created_at=store.now())
                        proposal = {'proposal_id': secrets.token_urlsafe(18), 'kind': 'rfq',
                                    'summary': 'Review the unsent RFQ draft for the current sourcing plan.',
                                    'parameters': {}, 'plan_id': plan['plan_id'],
                                    'offer_versions': deepcopy(plan['offer_versions']),
                                    'context_version': value.context_version,
                                    'expires_at': (store.now() + timedelta(minutes=5)).isoformat(),
                                    '_marker': marker, '_company_id': value.company_id,
                                    '_session_id': value.session_id}
                        remember_prepared_draft(value, proposal, rfq_preview=preview)
                        value.pending_proposal = proposal
                        result['status'] = 'completed'
                        result['proposal'] = public_proposal(proposal)
                        result['assistant_text'] = 'Created an unsent RFQ draft. You can review and download it in Drafts.'
                        result['workspace'] = {'drafts': deepcopy(value.drafts)}
                        result['read_only'] = False
                        result['action_result'] = {'tool': 'prepare_rfq',
                                                   'summary': 'Created an unsent RFQ draft in Drafts.'}
                        result['session'] = view(value)
                    elif name == 'prepare_simulated_email':
                        parameters = simulated_email_parameters(value, args)
                        proposal = {'proposal_id': secrets.token_urlsafe(18), 'kind': 'simulate_email',
                                    'summary': f"Review simulated message to {parameters['recipient_label']}.",
                                    'parameters': deepcopy(parameters), 'plan_id': None,
                                    'offer_versions': {}, 'context_version': value.context_version,
                                    'expires_at': (store.now() + timedelta(minutes=5)).isoformat(),
                                    '_marker': marker, '_company_id': value.company_id,
                                    '_session_id': value.session_id}
                        remember_prepared_draft(value, proposal)
                        value.pending_proposal = proposal
                        result['status'] = 'completed'
                        result['proposal'] = public_proposal(proposal)
                        result['assistant_text'] = 'Created a simulated email draft in Drafts. No external message was sent.'
                        result['workspace'] = {'drafts': deepcopy(value.drafts)}
                        result['read_only'] = False
                        result['action_result'] = {'tool': 'prepare_simulated_email',
                                                   'summary': 'Created a simulated email draft in Drafts.'}
                        result['session'] = view(value)
                    elif name == 'review_sample_quotes':
                        parameters = reviewed_quote_preview(value, args)
                        proposal = {'proposal_id': secrets.token_urlsafe(18), 'kind': 'review_quotes',
                                    'summary': 'Review exact synthetic quote values before saving.',
                                    'parameters': parameters, 'plan_id': None,
                                    'offer_versions': {item['offer_id']: item['expected_version'] for item in parameters['preview']},
                                    'context_version': value.context_version,
                                    'expires_at': (store.now() + timedelta(minutes=5)).isoformat(),
                                    '_marker': marker, '_company_id': value.company_id,
                                    '_session_id': value.session_id}
                        value.pending_proposal = proposal
                        result['status'] = 'proposal'
                        result['proposal'] = public_proposal(proposal)
                        result['assistant_text'] = proposal['summary']
                    else:
                        if name == 'fill_buyer_form':
                            shape(args, ('material_id', 'values'))
                            if not isinstance(args['values'], dict):
                                raise SessionError('invalid_utility', 'Buyer values must be an object.', 422)
                            scenario(value, args['material_id'])
                            material = next(m for m in get_company(value.company_id)['materials']
                                            if m['material_id'] == args['material_id'])
                            phrases = []
                            for field, field_value in args['values'].items():
                                suffix = (' ' + material['quantity_unit'] if field in ('quantity', 'current_stock')
                                          else ' ' + get_company(value.company_id)['reporting_currency']
                                          if field == 'budget' else ' ' + get_company(value.company_id)['reporting_currency']
                                          + ' per ' + material['quantity_unit'] if field == 'additional_cost_per_unit' else '')
                                phrases.append(f'{field.replace("_", " ")} to {field_value}{suffix}')
                            utility = {'kind': 'fill_form', 'parameters': args}
                            message = '; '.join(phrases)
                        elif name == 'load_sample_quotes':
                            utility, message = {'kind': 'load_quotes', 'parameters': args}, ''
                        elif name == 'compare_suppliers':
                            utility, message = {'kind': 'compare', 'parameters': args}, ''
                        else:
                            utility, message = {'kind': 'navigate', 'parameters': args}, ''
                        action_result, changed = execute_utility(value, utility, message)
                        result['action_result'] = action_result
                        if name == 'load_sample_quotes':
                            fixture_ids = scenario(value, args['material_id'])['fixture_ids']
                            owned_offers = {offer.get('fixture_id'): offer for offer in value.offers.values()
                                            if offer.get('fixture_id') in fixture_ids and
                                            company_for_offer(offer) == value.company_id}
                            result['workspace'] = {'material_id': args['material_id'],
                                'offers': [{'offer_id': offer['offer_id'],
                                            'fixture_id': fixture_id,
                                            'version': offer['version'],
                                            'status': offer['status'],
                                            'source_filename': offer.get('document', {}).get('filename'),
                                            'supplier_name': (offer.get('fields', {}).get('supplier_name') or {}).get('value')}
                                           for fixture_id in fixture_ids
                                           if (offer := owned_offers.get(fixture_id)) is not None]}
                        result['read_only'] = not changed
                        result['session'] = view(value)
                        result['assistant_text'] = action_result['summary']
                    complete(value, token, result)
                    value.idempotency[token]['state_marker'] = snapshot_marker(operator_snapshot(value))
                    return result
                except (SessionError, DemoError) as exc:
                    result = {'turn_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                              'context_version': value.context_version,
                              'status': 'needs_clarification' if exc.status_code < 500 else 'unavailable',
                              'assistant_text': (f'{name} made no change: {exc.message}' if exc.status_code < 500
                                                 else f'{name} made no change: workspace tool is unavailable right now.'),
                              'agent_steps': [], 'evidence_ids': [], 'proposal': None,
                              'read_only': True}
                    complete(value, token, result)
                    value.idempotency[token]['state_marker'] = snapshot_marker(operator_snapshot(value))
                    return result
                except Exception:
                    failed(value, token)
                    raise

        async def saved_workspace_tools(name, args, context_version, key):
            result = await workspace_tools(name, args, context_version, key)
            with value.lock:
                store.save(value)
            return result

        async def research_tools(args, context_version, key, on_progress):
            # confirm_research persists the claim and terminal record itself.
            return await workspace_tools('confirm_research', args, context_version, key,
                                         on_research_progress=on_progress)

        from .voice_consent import verify_spoken_consent
        await run_live_voice(websocket, session=value, store=store,
                             operate_workspace=operate, workspace_tools=saved_workspace_tools,
                             research_tools=research_tools,
                             client_factory=live_client_factory,
                             verify_spoken_consent=voice_consent_verifier or verify_spoken_consent)

    async def confirm_research(value, data, *, on_progress=None):
        started_at = monotonic()
        logger = logging.getLogger(__name__)
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'operator_confirm')
            if replay is not None:
                if replay.get('kind') != 'research':
                    raise SessionError('stale_proposal', 'This approval belongs to another operation.', 409)
                replay.pop('replayed', None)
                replay['session'] = view(value)
                return replay
            proposal = value.pending_proposal
            if (proposal is None or proposal['kind'] != 'research'
                    or proposal['proposal_id'] != data['proposal_id']
                    or proposal['_session_id'] != value.session_id
                    or proposal['_company_id'] != value.company_id):
                failed(value, key)
                raise SessionError('proposal_not_found', 'Research consent is no longer current.', 404)
            marker = snapshot_marker(operator_snapshot(value))
            if (proposal['context_version'] != value.context_version or proposal['_marker'] != marker
                    or store.now() >= datetime.fromisoformat(proposal['expires_at'])):
                value.pending_proposal = None
                failed(value, key)
                raise SessionError('stale_proposal', 'Research consent expired or the workspace changed.', 409)
            proposal_id = proposal['proposal_id']
            if proposal_id in value.research_records:
                failed(value, key)
                raise SessionError('research_claimed', 'This approved search was already attempted. Read its result or prepare a new request.', 409)
            for record in value.research_records.values():
                if record.get('state') != 'running':
                    continue
                try:
                    started = datetime.fromisoformat(record['started_at'])
                    fresh = store.now() < started + timedelta(minutes=5)
                except (KeyError, TypeError, ValueError):
                    fresh = True
                if fresh:
                    failed(value, key)
                    raise SessionError('research_busy',
                        'This session already has an approved search in progress. Wait for its result; after an interrupted connection, allow five minutes before approving a new request.', 429)
                # Completion is unknown after a restart, not permission to retry
                # the consumed approval. Only a newly reviewed query can proceed.
                record['state'] = 'interrupted'
            if value.research_attempts >= 6:
                failed(value, key)
                raise SessionError('research_limit', 'This session reached six public research attempts.', 429)
            if not research_workers.acquire(blocking=False):
                failed(value, key)
                raise SessionError('research_busy', 'Public research is busy. Try again shortly.', 429)
            query = proposal['parameters']['query']
            company_id, version = value.company_id, value.context_version
            # This durable claim precedes the paid provider call. Unknown completion
            # after a crash is never automatically retried with the same consent.
            value.research_attempts += 1
            value.research_records[proposal_id] = {'state': 'running', 'query': query,
                'company_id': company_id, 'context_version': version,
                'idempotency_key': key, 'started_at': store.now().isoformat()}
            value.pending_proposal = None
            try:
                store.save(value)
            except Exception:
                research_workers.release()
                raise
        logger.info('research_stage stage=claimed elapsed_ms=%d', int((monotonic() - started_at) * 1000))
        try:
            allowed = await run_in_threadpool(store.reserve_research_budget, store.now().date().isoformat(), 30)
            if not allowed:
                raise SessionError('research_limit', 'The shared daily research allowance is used. Try again tomorrow.', 429)
            with value.lock:
                context(value, data, explicit=True)
                if snapshot_marker(operator_snapshot(value)) != marker:
                    raise SessionError('stale_context', 'Workspace changed before researching. Prepare a new request.', 409)
            if on_progress is not None:
                await on_progress('started')
            logger.info('research_stage stage=provider_started elapsed_ms=%d', int((monotonic() - started_at) * 1000))
            try:
                adapter = research_adapter()
                result = await run_in_threadpool(adapter.research, query)
            except Exception:
                result = None
            if not isinstance(result, dict) or result.get('status') not in ('completed', 'unavailable'):
                result = {'status': 'unavailable', 'answer': '', 'sources': [],
                          'search_suggestions_html': None, 'checked_at': store.now().isoformat()}
            result = {**deepcopy(result), 'research_id': secrets.token_urlsafe(18), 'query': query}
            # Google permits the grounded answer in the requesting user's chat
            # history. Attribution markup stays request-local, never in history,
            # research records or idempotency copies.
            suggestions_html = result.pop('search_suggestions_html', None)
            result['search_suggestions_html'] = None
            if result['status'] == 'unavailable':
                result['answer'] = 'Public research could not verify a sourced answer. No business action was taken. Prepare a new request to try again.'
                result['sources'] = []
                result['search_suggestions_html'] = None
                suggestions_html = None
            with value.lock:
                active(value)
                record = value.research_records[proposal_id]
                if (value.company_id != company_id or value.context_version != version
                        or snapshot_marker(operator_snapshot(value)) != marker):
                    record.update(state='discarded', result=deepcopy(result))
                    failed(value, key)
                    store.save(value)
                    raise SessionError('stale_context', 'Workspace changed while researching. The result was not applied.', 409)
                record.update(state='completed', result=deepcopy(result))
                remember(value, {'id': result['research_id'], 'channel': 'text', 'question': None,
                                  'answer': result['answer'], 'research': result})
                response = {'status': 'applied', 'company_id': company_id,
                    'context_version': version, 'kind': 'research', 'result': result, 'session': view(value)}
                complete(value, key, response)
                store.save(value)
                initial_response = deepcopy(response)
                initial_response['result']['search_suggestions_html'] = suggestions_html
                logger.info('research_stage stage=completed status=%s elapsed_ms=%d', result['status'], int((monotonic() - started_at) * 1000))
                return initial_response
        except Exception:
            with value.lock:
                failed(value, key)
                record = value.research_records.get(proposal_id)
                if record and record['state'] == 'running':
                    record['state'] = 'interrupted'
                    store.save(value)
            raise
        finally:
            research_workers.release()

    def apply_pending_proposal(value, proposal_id, expected_context_version, idempotency_key):
        """The single durable apply boundary for UI and verified conversations."""
        data = {'proposal_id': proposal_id, 'reviewed': True,
                'expected_context_version': expected_context_version,
                'idempotency_key': idempotency_key}
        if not isinstance(proposal_id, str) or not proposal_id:
            raise SessionError('invalid_proposal', 'A proposal ID is required.', 422)
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', idempotency_key):
            raise SessionError('invalid_idempotency', 'A short alphanumeric retry key is required.', 422)
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, 'operator_confirm')
            if replay is not None:
                if value.idempotency[key].get('state_marker') != snapshot_marker(operator_snapshot(value)):
                    raise SessionError('stale_proposal', 'Workspace changed since this proposal was applied.', 409)
                replay.pop('replayed', None)
                kind, result = replay.get('kind'), replay.get('result', {})
                current = (kind == 'watch' and any(w['watch_id'] == result.get('watch_id') for w in value.watch_items)
                           or kind == 'recovery' and value.recovery is not None and value.recovery.get('comparison_id') == result.get('comparison_id')
                           or kind == 'rfq' and value.rfq is not None and value.rfq.get('draft_id') == result.get('draft_id')
                           or kind == 'simulate_email' and any(item['message_id'] == result.get('message_id') for item in value.simulated_outbox)
                           or kind == 'review_quotes' and all(value.offers.get(item['offer_id'], {}).get('version') == item['version']
                                                                for item in result.get('offers', [])))
                if not current:
                    raise SessionError('stale_proposal', 'Applied result is no longer current.', 409)
                return replay
            proposal = value.pending_proposal
            if (proposal is None or proposal['proposal_id'] != data['proposal_id']
                    or proposal['_session_id'] != value.session_id
                    or proposal['_company_id'] != value.company_id):
                failed(value, key)
                raise SessionError('proposal_not_found', 'Proposal is not current in this workspace.', 404)
            if (proposal['context_version'] != value.context_version
                    or store.now() >= datetime.fromisoformat(proposal['expires_at'])
                    or proposal['_marker'] != snapshot_marker(operator_snapshot(value))):
                value.pending_proposal = None
                failed(value, key)
                raise SessionError('stale_proposal', 'Proposal expired or workspace evidence changed.', 409)
            try:
                kind = proposal['kind']
                if kind == 'watch':
                    material_id = proposal['parameters']['material_id']
                    if material_id not in {m['material_id'] for m in get_company(value.company_id)['materials']}:
                        raise SessionError('stale_proposal', 'Material is no longer current.', 409)
                    existing = next((w for w in value.watch_items if w['material_id'] == material_id), None)
                    result = deepcopy(existing) if existing else {
                        'watch_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                        'material_id': material_id, 'evidence_ids': deepcopy(proposal['parameters']['evidence_ids']),
                        'created_at': store.now().isoformat(), 'synthetic': True}
                    if existing is None:
                        value.watch_items.append(deepcopy(result))
                elif kind == 'recovery':
                    from .recovery import compare
                    baseline = value.last_plan
                    if baseline is None or proposal['plan_id'] != baseline.get('plan_id'):
                        raise SessionError('stale_plan', 'Baseline plan changed.', 409)
                    if 'shipment_id' in proposal['parameters']:
                        bound_shipment(value, proposal['parameters']['shipment_id'], baseline,
                                       proposal['parameters']['delay_days_by_offer'])
                    selected = current_offers(value, baseline, proposal['offer_versions'],
                                              today=store.now().date())
                    result = compare(baseline, value.plan_request, selected,
                                     proposal['parameters']['delay_days_by_offer'],
                                     today=store.now().date())
                    result['comparison_id'] = secrets.token_urlsafe(18)
                    result['scenario']['plan_id'] = secrets.token_urlsafe(18)
                    value.recovery = deepcopy(result)
                    value.rfq = None
                elif kind == 'simulate_email':
                    parameters = {k: v for k, v in proposal['parameters'].items() if k != 'draft_preview'}
                    validate_simulated_email(value, parameters)
                    if len(value.simulated_outbox) >= 48:
                        raise SessionError('outbox_limit', 'Simulated outbox is full.', 429)
                    result = {'message_id': secrets.token_urlsafe(18), 'company_id': value.company_id,
                              **deepcopy(parameters), 'status': 'simulated_sent',
                              'simulated': True, 'external_delivery': False,
                              'created_at': store.now().isoformat()}
                    value.simulated_outbox.append(deepcopy(result))
                elif kind == 'review_quotes':
                    result = apply_reviewed_quotes(value, proposal['parameters'])
                else:
                    from .rfq import build_draft
                    plan = active_plan(value)
                    if plan is None or proposal['plan_id'] != plan.get('plan_id'):
                        raise SessionError('stale_plan', 'Active plan changed.', 409)
                    selected = current_offers(value, plan, proposal['offer_versions'],
                                              today=store.now().date())
                    chosen = next((o for o in selected if o['offer_id'] == plan.get('selected_offer_id')), None)
                    if chosen is None or plan.get('required_purchase', plan.get('required_purchase_kg')) == '0':
                        raise SessionError('draft_unavailable', 'Current plan has no purchase to draft.', 409)
                    result = build_draft(deepcopy(plan), deepcopy(chosen),
                                         all_offers=deepcopy(selected), created_at=store.now())
                    result['draft_id'] = proposal['proposal_id']
                    value.rfq = deepcopy(result)
                value.pending_proposal = None
                response = {'status': 'applied', 'company_id': value.company_id,
                            'context_version': value.context_version, 'kind': kind,
                            'result': deepcopy(result), 'session': view(value)}
                complete(value, key, response)
                value.idempotency[key]['state_marker'] = snapshot_marker(operator_snapshot(value))
                return response
            except Exception:
                failed(value, key)
                raise

    @app.post('/api/demo/operator/confirm')
    async def operator_confirm(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('proposal_id', 'reviewed', 'expected_context_version', 'idempotency_key'))
        if data['reviewed'] is not True:
            raise SessionError('review_required', 'Review the exact proposal before applying it.', 422)
        if not isinstance(data['proposal_id'], str) or not data['proposal_id']:
            raise SessionError('invalid_proposal', 'A proposal ID is required.', 422)
        if not isinstance(data['idempotency_key'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', data['idempotency_key']):
            raise SessionError('invalid_idempotency', 'A short alphanumeric retry key is required.', 422)
        with value.lock:
            previous = value.idempotency.get(data['idempotency_key'], {}).get('result')
            is_research = (value.pending_proposal is not None and value.pending_proposal['kind'] == 'research'
                           and value.pending_proposal['proposal_id'] == data['proposal_id']
                           or data['proposal_id'] in value.research_records
                           or isinstance(previous, dict) and previous.get('kind') == 'research')
        if is_research:
            return await confirm_research(value, data)
        return apply_pending_proposal(value, data['proposal_id'], data['expected_context_version'],
                                      data['idempotency_key'])

    async def voice_call(request, operation):
        nonlocal voice_process_calls
        value = mutate(request)
        data = await body(request)
        if operation == 'transcribe':
            shape(data, ('audio_base64', 'mime_type', 'sample_rate_hertz',
                         'expected_context_version', 'idempotency_key'))
            mime = data['mime_type']
            rate = data['sample_rate_hertz']
            if mime not in ('audio/webm', 'audio/wav') or type(rate) is not int or not 8000 <= rate <= 48000:
                raise SessionError('invalid_audio', 'Use supported WebM or WAV audio and a valid sample rate.', 422)
            encoded = data['audio_base64']
            if not isinstance(encoded, str):
                raise SessionError('invalid_audio', 'Audio must be base64 encoded.', 422)
            try:
                audio = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise SessionError('invalid_audio', 'Audio must be base64 encoded.', 422) from None
            if not 1 <= len(audio) <= MAX_UPLOAD or (
                    mime == 'audio/wav' and not audio.startswith(b'RIFF') or
                    mime == 'audio/webm' and not audio.startswith(b'\x1a\x45\xdf\xa3')):
                raise SessionError('invalid_audio', 'Use a bounded recording with the selected audio header.', 422)
        else:
            shape(data, ('text', 'expected_context_version', 'idempotency_key'))
            spoken_text = data['text']
            if not isinstance(spoken_text, str) or not spoken_text.strip() or len(spoken_text) > 1200:
                raise SessionError('invalid_text', 'Speech text must be at most 1200 characters.', 422)
        with value.lock:
            context(value, data, explicit=True)
            key, replay = start(value, data, f'voice_{operation}')
            if replay is not None:
                if operation == 'speak':
                    # Generated audio is transient and is never retained in session idempotency state.
                    raise SessionError('voice_replay_unavailable', 'Speech was already generated; request a new turn.', 409)
                replay.pop('replayed', None)
                return replay
            if value.voice_calls >= 8:
                failed(value, key)
                raise SessionError('voice_limit', 'Speech call limit reached for this session.', 429)
            if not voice_workers.acquire(blocking=False):
                failed(value, key)
                raise SessionError('voice_busy', 'Speech service is busy. Try again shortly.', 429)
            with voice_lock:
                if voice_process_calls >= 40:
                    voice_workers.release()
                    failed(value, key)
                    raise SessionError('voice_limit', 'Speech process call limit reached.', 429)
                voice_process_calls += 1
            value.voice_calls += 1
            company_id, context_version = value.company_id, value.context_version
        try:
            adapter = voice_adapter()
            try:
                if operation == 'transcribe':
                    output = await run_in_threadpool(adapter.transcribe, audio, mime, rate)
                else:
                    output = await run_in_threadpool(adapter.speak, spoken_text)
            except DemoError as exc:
                raise SessionError(exc.code, exc.message, exc.status_code) from None
            except Exception:
                raise SessionError('voice_unavailable', 'Google speech service did not complete this request.', 503) from None
            if not isinstance(output, dict):
                raise SessionError('voice_unavailable', 'Speech service returned an invalid response.', 503)
            if operation == 'transcribe':
                transcript = output.get('transcript')
                if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 1200:
                    raise SessionError('voice_unavailable', 'Speech service returned no usable transcript.', 503)
                result = {'transcript': transcript, 'provider': output.get('provider'),
                          'model': output.get('model'),
                          'call': 'mocked' if voice_injected else output.get('call'),
                          'company_id': company_id, 'context_version': context_version}
            else:
                encoded_output = output.get('audio_base64')
                try:
                    spoken_audio = base64.b64decode(encoded_output, validate=True)
                except (ValueError, TypeError):
                    raise SessionError('voice_unavailable', 'Speech service returned invalid audio.', 503) from None
                if (not 1 <= len(spoken_audio) <= MAX_SPEECH_OUTPUT or output.get('mime_type') != 'audio/mpeg'
                        or not (spoken_audio.startswith(b'ID3') or spoken_audio[:1] == b'\xff')):
                    raise SessionError('voice_unavailable', 'Speech service returned invalid MP3 audio.', 503)
                result = {'audio_base64': encoded_output, 'mime_type': 'audio/mpeg',
                          'provider': output.get('provider'),
                          'call': 'mocked' if voice_injected else output.get('call'),
                          'company_id': company_id, 'context_version': context_version}
            if (not isinstance(result['provider'], str) or not result['provider']
                    or result['call'] not in ('real', 'mocked')
                    or (not voice_injected and result['call'] != 'real')
                    or (operation == 'transcribe' and
                        (not isinstance(result['model'], str) or not result['model']))):
                raise SessionError('voice_unavailable', 'Speech provider receipt was not verified.', 503)
            with value.lock:
                active(value)
                if value.company_id != company_id or value.context_version != context_version:
                    raise SessionError('stale_context', 'Company changed during speech processing.', 409)
                if operation == 'speak':
                    complete(value, key, {'voice_completed': True, 'company_id': company_id,
                                          'context_version': context_version})
                    return result
                return complete(value, key, result)
        except Exception:
            with value.lock:
                failed(value, key)
            raise
        finally:
            voice_workers.release()

    @app.post('/api/demo/voice/transcribe')
    async def transcribe(request: Request):
        return await voice_call(request, 'transcribe')

    @app.post('/api/demo/voice/speak')
    async def speak(request: Request):
        return await voice_call(request, 'speak')

    @app.get('/api/demo/fixtures')
    async def fixtures():
        from .quote_service import list_fixtures
        return {'fixtures': list_fixtures(), 'synthetic': True}

    @app.get('/api/demo/fixtures/{fixture_id}')
    async def download(fixture_id: str):
        from .quote_service import fixture_bytes
        filename, data = fixture_bytes(fixture_id)
        safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', filename)
        return Response(data, media_type='application/pdf' if filename.endswith('.pdf') else 'text/csv',
                        headers={'Content-Disposition': f'attachment; filename="{safe_name}"'})

    @app.get('/api/demo/offers')
    async def offers(request: Request):
        value = session(request)
        with value.lock:
            return {'offers': deepcopy(list(value.offers.values()))}

    @app.post('/api/demo/extract')
    async def extract(request: Request):
        nonlocal process_calls
        value = mutate(request)
        data = await body(request)
        from .quote_service import fixture_bytes, match_fixture, extract_fixture, new_offer
        if 'fixture_id' in data:
            shape(data, ('fixture_id', 'mode', 'idempotency_key'), ('expected_context_version',))
            fixture_id = data['fixture_id']
            if not isinstance(fixture_id, str) or len(fixture_id) > 100:
                raise SessionError('invalid_fixture', 'Select a synthetic demo fixture.', 422)
            _, raw = fixture_bytes(fixture_id)
        else:
            shape(data, ('filename', 'document_base64', 'mode', 'idempotency_key'), ('expected_context_version',))
            filename, encoded = data['filename'], data['document_base64']
            if not isinstance(filename, str) or len(filename) > 150 or '/' in filename or '\\' in filename:
                raise SessionError('invalid_upload', 'Upload an unchanged synthetic demo fixture.', 422)
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                raise SessionError('invalid_upload', 'Upload an unchanged synthetic demo fixture.', 422)
            if len(raw) > MAX_UPLOAD:
                raise SessionError('upload_too_large', 'Synthetic document is too large.', 413)
            fixture_id = match_fixture(filename, raw)
        if data['mode'] not in ('rules', 'gemini'):
            raise SessionError('invalid_mode', 'Choose rules or gemini extraction.', 422)
        with value.lock:
            context(value, data)
            quote_coverage(value, fixture_id=fixture_id)
            company_id, context_version = value.company_id, value.context_version
            key, replay = start(value, data, 'extract')
            if replay:
                return replay
            if len(value.offers) >= MAX_OFFERS:
                failed(value, key)
                raise SessionError('offer_limit', 'Session offer limit reached.', 429)
        today = store.now().date()
        cache_key = (hashlib.sha256(raw).hexdigest(), data['mode'], today.isoformat())
        acquired = False
        marked = False
        provider_completed = False
        try:
            with cache_lock:
                payload = deepcopy(cache.get(cache_key))
                cached = payload is not None
                if not cached:
                    if cache_key in pending_cache:
                        raise SessionError('extraction_busy', 'This document is being extracted. Try again shortly.', 409)
                    if not workers.acquire(blocking=False):
                        raise SessionError('provider_busy', 'Extraction is busy. Try again shortly.', 429)
                    acquired = True
                    if data['mode'] == 'gemini':
                        if not provider()['available'] or value.live_calls >= MAX_SESSION_CALLS or value.live_failed_calls >= MAX_SESSION_FAILURES or process_calls >= MAX_PROCESS_CALLS:
                            raise SessionError('provider_limit', 'Live extraction is unavailable or its demo limit was reached.', 429)
                        value.live_calls += 1
                        process_calls += 1
                    pending_cache.add(cache_key)
                    marked = True
            if payload is None:
                payload = await run_in_threadpool(extract_fixture, fixture_id, mode=data['mode'], as_of=today)
                if data['mode'] == 'gemini' and (payload.get('extractor') != 'gemini' or payload.get('extractor_detail', {}).get('call') != 'real'):
                    raise SessionError('provider_unavailable', 'Gemini did not return a verified live extraction. Try again or select rules.', 503)
                provider_completed = True
                with cache_lock:
                    cache[cache_key] = deepcopy(payload)
                    while len(cache) > MAX_CACHE:
                        cache.popitem(last=False)
            with value.lock:
                active(value)
                if value.company_id != company_id or value.context_version != context_version:
                    raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)
                if len(value.offers) >= MAX_OFFERS:
                    raise SessionError('offer_limit', 'Session offer limit reached.', 429)
                offer = new_offer(deepcopy(payload), offer_id=secrets.token_urlsafe(18), today=today)
                offer['fixture_id'] = fixture_id
                offer['served_from_cache'] = cached
                value.offers[offer['offer_id']] = offer
                clear_derived(value)
                result = {'offer': deepcopy(offer), 'provider': deepcopy(offer.get('extractor_detail', {})),
                          'replayed': False, 'served_from_cache': cached}
                return complete(value, key, result)
        except Exception:
            with value.lock:
                if marked and data['mode'] == 'gemini' and not provider_completed:
                    value.live_failed_calls += 1
                failed(value, key)
            raise
        finally:
            with cache_lock:
                if marked:
                    pending_cache.discard(cache_key)
                if acquired:
                    workers.release()

    async def change(request, offer_id, operation):
        value = mutate(request)
        data = await body(request)
        shape(data, ('expected_version', 'idempotency_key', 'field', 'value') if operation == 'correct' else ('expected_version', 'idempotency_key'), ('expected_context_version',))
        with value.lock:
            context(value, data)
            original = owned(value, offer_id)
            key, replay = start(value, data, f'{operation}:{offer_id}')
            if replay:
                return replay
            try:
                version(original, data['expected_version'])
                from .quote_service import correct_offer, save_offer
                if operation == 'correct':
                    if not isinstance(data['field'], str) or len(data['field']) > 100 or len(json.dumps(data['value'])) > 4096:
                        raise SessionError('invalid_correction', 'Provide a supported field and a short typed value.', 422)
                    result = correct_offer(deepcopy(original), field=data['field'], value=data['value'], today=store.now().date())
                else:
                    result = save_offer(deepcopy(original), today=store.now().date())
                value.offers[offer_id] = result
                clear_derived(value)
                return complete(value, key, {'offer': deepcopy(result), 'replayed': False})
            except Exception:
                failed(value, key)
                raise

    @app.post('/api/demo/offers/{offer_id}/correct')
    async def correct(request: Request, offer_id: str):
        return await change(request, offer_id, 'correct')

    @app.post('/api/demo/offers/{offer_id}/save')
    async def save(request: Request, offer_id: str):
        return await change(request, offer_id, 'save')

    @app.post('/api/demo/plan')
    async def plan(request: Request):
        value = mutate(request)
        data = await body(request)
        from .planner import build_plan
        generic = any(key in data for key in ('quantity', 'current_stock', 'additional_cost_per_unit'))
        quantities = ('quantity', 'current_stock', 'additional_cost_per_unit', 'quantity_unit') if generic else (
            'quantity_kg', 'current_stock_kg', 'additional_cost_per_kg')
        aliases = ('quantity_kg', 'current_stock_kg', 'additional_cost_per_kg') if generic else ('quantity_unit',)
        shape(data, ('offer_ids', 'expected_versions', 'material', 'need_by', 'decision_date',
                     'reporting_currency', 'budget', *quantities),
              ('delay_days_by_offer', 'expected_context_version', *aliases))
        ids, versions = data['offer_ids'], data['expected_versions']
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_OFFERS or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or not isinstance(versions, dict) or set(versions) != set(ids):
            raise SessionError('invalid_offers', 'Provide unique offer IDs and their exact versions.', 422)
        with value.lock:
            context(value, data)
            quote_coverage(value, material=data['material'])
            selected = [owned(value, i) for i in ids]
            for offer in selected:
                quote_coverage(value, fixture_id=offer.get('fixture_id'))
                version(offer, versions[offer['offer_id']])
            plan_data = {key: item for key, item in data.items() if key != 'expected_context_version'}
            result = build_plan(deepcopy(selected), plan_data, today=store.now().date())
            result['plan_id'] = secrets.token_urlsafe(18)
            value.last_plan = deepcopy(result)
            value.plan_request = deepcopy(plan_data)
            value.recovery = None
            value.rfq = None
            value.pending_proposal = None
            return result

    @app.post('/api/demo/recovery')
    async def recovery(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('baseline_plan_id', 'expected_versions', 'delay_days_by_offer', 'idempotency_key'),
              ('expected_context_version', 'shipment_id'))
        with value.lock:
            context(value, data)
            baseline, original_request = value.last_plan, value.plan_request
            if (baseline is None or original_request is None
                    or not isinstance(data['baseline_plan_id'], str)
                    or data['baseline_plan_id'] != baseline.get('plan_id')):
                raise SessionError('stale_plan', 'Original comparison changed. Compare again.', 409)
            selected = current_offers(value, baseline, data['expected_versions'], today=store.now().date())
            delays = data['delay_days_by_offer']
            if (not isinstance(delays, dict) or not set(delays) <= set(baseline['offer_versions'])
                    or any(type(days) is not int or not 0 <= days <= 3650 for days in delays.values())):
                raise SessionError('invalid_delay', 'Use whole nonnegative days for accepted offers.', 422)
            key, replay = start(value, data, 'recovery')
            if replay is not None:
                if value.recovery is None or replay.get('comparison_id') != value.recovery.get('comparison_id'):
                    raise SessionError('stale_plan', 'This comparison has been replaced.', 409)
                return replay
            try:
                if 'shipment_id' in data:
                    bound_shipment(value, data['shipment_id'], baseline, delays)
                from .recovery import compare
                comparison = compare(baseline, original_request, selected, delays, today=store.now().date())
                comparison['comparison_id'] = secrets.token_urlsafe(18)
                comparison['scenario']['plan_id'] = secrets.token_urlsafe(18)
                result = complete(value, key, comparison)
                value.recovery = deepcopy(result)
                value.rfq = None
                value.pending_proposal = None
                return result
            except Exception:
                failed(value, key)
                raise

    @app.post('/api/demo/rfq')
    async def rfq(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, ('plan_id', 'expected_versions', 'reviewed', 'idempotency_key'), ('expected_context_version',))
        with value.lock:
            context(value, data)
            plan = active_plan(value)
            if plan is None or not isinstance(data['plan_id'], str) or data['plan_id'] != plan.get('plan_id'):
                raise SessionError('stale_plan', 'Active comparison changed. Review the current plan.', 409)
            selected = current_offers(value, plan, data['expected_versions'], today=store.now().date())
            if data['reviewed'] is not True:
                raise SessionError('review_required', 'Review the selected plan before drafting.', 422)
            selected_id = plan.get('selected_offer_id')
            if selected_id is None or plan.get('required_purchase', plan.get('required_purchase_kg')) == '0':
                raise SessionError('draft_unavailable', 'The current plan has no purchase to request.', 409)
            chosen = next((offer for offer in selected if offer['offer_id'] == selected_id), None)
            if chosen is None:
                raise SessionError('stale_plan', 'Selected quote is no longer available.', 409)
            key, replay = start(value, data, 'rfq')
            if replay is not None:
                if value.rfq is None or replay.get('draft_id') != value.rfq.get('draft_id'):
                    raise SessionError('stale_plan', 'This RFQ draft has been replaced.', 409)
                replay.pop('replayed', None)
                return replay
            try:
                from .rfq import build_draft
                draft = build_draft(deepcopy(plan), deepcopy(chosen),
                                    all_offers=deepcopy(selected), created_at=store.now())
                draft['draft_id'] = secrets.token_urlsafe(18)
                remember_prepared_draft(value, {
                    'proposal_id': draft['draft_id'], 'kind': 'rfq', 'parameters': {},
                    'plan_id': plan['plan_id'], 'offer_versions': plan['offer_versions']}, rfq_preview=draft)
                result = complete(value, key, draft)
                value.rfq = deepcopy(result)
                value.pending_proposal = None
                return result
            except Exception:
                failed(value, key)
                raise

    @app.get('/api/demo/rfq/{draft_id}/download')
    async def rfq_download(request: Request, draft_id: str):
        try:
            value = session(request)
        except SessionError as exc:
            if exc.status_code != 401:
                raise
            raise SessionError('draft_not_found', 'RFQ draft is unavailable.', 404) from None
        with value.lock:
            draft, plan = value.rfq, active_plan(value)
            if (draft is None or plan is None or draft.get('draft_id') != draft_id
                    or draft.get('plan_id') != plan.get('plan_id')):
                raise SessionError('draft_not_found', 'RFQ draft is unavailable.', 404)
            try:
                selected = current_offers(value, plan, draft.get('offer_versions'), today=store.now().date())
                if (plan.get('selected_offer_id') not in {offer['offer_id'] for offer in selected}
                        or plan.get('required_purchase', plan.get('required_purchase_kg')) == '0'):
                    raise SessionError('draft_not_found', 'RFQ draft is unavailable.', 404)
            except SessionError:
                raise SessionError('draft_not_found', 'RFQ draft is unavailable.', 404) from None
            return Response(draft['text'], media_type='text/plain',
                            headers={'Content-Disposition': 'attachment; filename="synthetic-rfq-draft.txt"'})

    @app.get('/api/demo/export')
    async def export(request: Request):
        value = session(request)
        with value.lock:
            return JSONResponse({'synthetic': True, 'persistence': store.persistence,
                                 'exported_at': store.now().isoformat(),
                                 'company': get_company(value.company_id),
                                 'context_version': value.context_version,
                                 'news': deepcopy(value.news),
                                 'offers': deepcopy(list(value.offers.values())),
                                 'plan': deepcopy(active_plan(value)),
                                 'baseline_plan': deepcopy(value.last_plan),
                                 'plan_request': deepcopy(value.plan_request),
                                 'buyer_draft': deepcopy(value.buyer_draft),
                                 'selected_material_id': value.selected_material_id,
                                 'simulated_outbox': deepcopy(value.simulated_outbox),
                                 'drafts': deepcopy(value.drafts),
                                 'recovery': deepcopy(value.recovery), 'rfq': deepcopy(value.rfq),
                                 'carbon': carbon_export(value, active_plan, validate_plan_current)},
                                headers={'Content-Disposition': 'attachment; filename="synthetic-demo-report.json"'})

    @app.post('/api/demo/reset')
    async def reset(request: Request):
        value = mutate(request)
        data = await body(request)
        shape(data, (), ('expected_context_version',))
        with value.lock:
            context(value, data)
        store.invalidate(value)
        return session_response(store.create())

    register_carbon_routes(app, session=session, mutate=mutate, body=body, shape=shape,
                           context=context, active_plan=active_plan,
                           validate_plan_current=validate_plan_current, now=store.now)

    @app.get('/dispatch')
    async def legacy_dispatch():
        return RedirectResponse('/demo?view=inventory&supply=shipments', status_code=307)

    if web_dir is not None:
        web_dir = Path(web_dir).resolve()
        if (web_dir / 'assets').is_dir():
            app.mount('/assets', StaticFiles(directory=web_dir / 'assets'), name='assets')
        @app.get('/demo')
        @app.get('/demo/{path:path}')
        async def spa(path: str = ''):
            index = web_dir / 'index.html'
            if not index.is_file():
                return failure('assets_unavailable', 'Demo assets are not available.', 503)
            return FileResponse(index)
        @app.get('/')
        async def demo_root():
            return RedirectResponse('/demo', status_code=307)
    return app


app = create_app(web_dir=Path(os.environ['DEMO_WEB_DIR']) if os.getenv('DEMO_WEB_DIR') else None)
