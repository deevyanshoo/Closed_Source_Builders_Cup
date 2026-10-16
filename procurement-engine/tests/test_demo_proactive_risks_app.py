from copy import deepcopy
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from fastapi.testclient import TestClient

from procure.demo.app import create_app

NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)


class News:
    def get(self, company, force=False):
        mid = company['materials'][0]['material_id']
        return {'checked_at': NOW.isoformat(), 'cache_state': 'fresh',
            'sources': [{'source_id': 'google_news_1', 'name': 'Google News', 'state': 'fresh',
                         'last_checked_at': NOW.isoformat(), 'reason': 'Metadata'}],
            'items': [{'item_id': company['company_id'] + '-news',
                'title': company['materials'][0]['name'] + ' supply shortage',
                'url': 'https://news.google.com/rss/articles/' + company['company_id'],
                'provider': 'Google News RSS', 'publisher': 'Public publisher',
                'source_id': 'google_news_1', 'source_reliability': 'unverified',
                'retrieved_at': NOW.isoformat(), 'published_at': NOW.isoformat(),
                'matched_materials': [mid], 'matched_routes': [], 'matched_currencies': [],
                'synthetic': False, 'matched_operations': ['supply'], 'relevance_kind': 'direct_material'}]}


def test_current_company_risk_scan_does_not_fetch_other_company_news():
    seen = []
    class RecordingNews(News):
        def get(self, company, force=False):
            seen.append(company['company_id'])
            return super().get(company, force=force)
    with TestClient(create_app(clock=lambda: NOW, news_service=RecordingNews()), base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        response = client.get('/api/demo/risks?scope=current')
        assert response.status_code == 200
        result = response.json()
        assert seen == [before['company_id']]
        assert [row['company_id'] for row in result['companies']] == [before['company_id']]
        assert all(alert['company_id'] == before['company_id'] for row in result['companies'] for alert in row['alerts'])
        assert client.get('/api/demo/session').json() == before


def test_risk_scan_is_owned_bounded_deduplicated_and_preserves_session_and_proposal():
    app = create_app(clock=lambda: NOW, news_service=News())
    with TestClient(app, base_url='https://demo.test') as client:
        assert client.get('/api/demo/risks').status_code == 401
        before = client.get('/api/demo/session').json()
        value = app.state.sessions.get(before['session_id'])
        value.pending_proposal = {'proposal_id': 'preserve', 'kind': 'watch'}
        proposal = deepcopy(value.pending_proposal)
        response = client.get('/api/demo/risks')
        assert response.status_code == 200
        first = response.json()
        second = client.get('/api/demo/risks').json()
        assert first['company_id'] == before['company_id'] and first['context_version'] == 1
        assert first['monitoring'] == 'while_workspace_open' and first['news_refresh_seconds'] == 900
        assert len(first['companies']) == 10
        assert sum(c['coverage']['materials_evaluated'] for c in first['companies']) == 52
        ids = [a['alert_id'] for c in first['companies'] for a in c['alerts']]
        assert len(ids) == len(set(ids)) and ids == [a['alert_id'] for c in second['companies'] for a in c['alerts']]
        assert all(any(a['category'] == 'news' for a in c['alerts']) for c in first['companies'])
        assert all(a['company_id'] == c['company_id'] for c in first['companies'] for a in c['alerts'])
        assert client.get('/api/demo/session').json() == before
        assert value.pending_proposal == proposal and value.operator_turns == 0 and value.live_calls == 0
        assert 'csrf_token' not in str(first) and 'session_id' not in str(first)


def test_news_failure_remains_explicit_without_dropping_inventory_or_order_scan():
    class Unavailable:
        def get(self, company, force=False):
            raise RuntimeError('private credential details must not leak')
    with TestClient(create_app(clock=lambda: NOW, news_service=Unavailable()), base_url='https://demo.test') as client:
        client.get('/api/demo/session')
        response = client.get('/api/demo/risks')
        assert response.status_code == 200
        rows = response.json()['companies']
        assert len(rows) == 10 and sum(c['coverage']['materials_evaluated'] for c in rows) == 52
        assert all(c['data_gaps'] for c in rows)
        assert any(a['category'] != 'news' for c in rows for a in c['alerts'])
        assert 'private credential' not in response.text


def test_company_switch_during_scan_rejects_old_response_and_has_no_side_effects():
    entered, release = Event(), Event()
    class Blocking(News):
        def get(self, company, force=False):
            if company['company_id'] == 'solara-cables':
                entered.set()
                assert release.wait(4)
            return super().get(company, force=force)
    app = create_app(clock=lambda: NOW, news_service=Blocking())
    with TestClient(app, base_url='https://demo.test') as client, ThreadPoolExecutor(max_workers=1) as pool:
        before = client.get('/api/demo/session').json()
        waiting = pool.submit(client.get, '/api/demo/risks')
        assert entered.wait(2)
        switched = client.post('/api/demo/company', headers={'Origin': 'https://demo.test', 'X-CSRF-Token': before['csrf_token']},
            json={'company_id': 'terrapack', 'expected_context_version': 1, 'idempotency_key': 'switch'}).json()
        release.set()
        response = waiting.result(timeout=5)
        assert response.status_code == 409 and response.json()['error']['code'] == 'stale_context'
        assert app.state.sessions.get(before['session_id']).risk_report is None
        assert client.get('/api/demo/session').json() == switched


def test_named_voice_risk_read_has_all_companies_and_no_session_credentials_or_mutation():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace as NS
    from test_demo_live_voice import Provider
    class RiskProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name='read_risks', args={'scope': 'all'}, id='risk-read')]), server_content=None))
    provider = RiskProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, news_service=News(), live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'origin': 'https://demo.test'}) as ws:
            ws.send_json({'type': 'start', 'csrf_token': before['csrf_token'], 'expected_context_version': 1})
            assert ws.receive_json()['type'] == 'ready'
            ws.send_json({'type': 'audio', 'data': 'AQACAA=='})
            while True:
                event = ws.receive_json()
                if event['type'] == 'tool_result':
                    result = event['result']
                    break
            assert result['read_only'] is True and result['status'] == 'completed'
            assert len(result['workspace']['risks']['companies']) == 10
            while ws.receive_json()['type'] != 'turn_complete':
                pass
            assert before['csrf_token'] not in str(provider.tool_response.response)
            assert before['session_id'] not in str(provider.tool_response.response)
            ws.send_json({'type': 'stop'})
        assert client.get('/api/demo/session').json() == before
        assert app.state.sessions.get(before['session_id']).operator_turns == 0


def test_cached_scan_news_is_available_to_typed_agent_and_reads_preserve_review_apply(monkeypatch):
    from procure.demo import operator
    class Available:
        available = True
    seen = []
    def answer(snapshot, *args, **kwargs):
        seen.append(snapshot)
        return {'status': 'proposal', 'assistant_text': 'Review this synthetic message.',
            'agent_steps': [], 'evidence_ids': [], 'candidate': {'kind': 'simulate_email',
                'parameters': {'material_id': 'copper', 'recipient_label': snapshot['company']['suppliers'][0]['name'],
                               'subject': 'Synthetic risk follow-up', 'body': 'Please review the fictional supply requirement.'}}}
    monkeypatch.setattr(operator, 'run_turn', answer)
    app = create_app(clock=lambda: NOW, news_service=News(), operator_provider=Available())
    with TestClient(app, base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        client.get('/api/demo/risks')
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': before['csrf_token']}
        response = client.post('/api/demo/operator', headers=headers, json={
            'message': 'Prepare a simulated risk email', 'expected_context_version': 1, 'idempotency_key': 'risk-email'})
        assert response.status_code == 200
        assert any(a['category'] == 'news' for a in seen[0]['risk_report']['alerts'])
        compact = operator._compact(seen[0])
        assert any(n['item_id'] == 'solara-cables-news' for n in compact['news']['items'])
        assert len(compact['portfolio_risks']) == 10
        client.get('/api/demo/risks')
        confirmed = client.post('/api/demo/operator/confirm', headers=headers, json={
            'proposal_id': response.json()['proposal']['proposal_id'], 'reviewed': True,
            'expected_context_version': 1, 'idempotency_key': 'confirm-risk-email'})
        assert confirmed.status_code == 200 and confirmed.json()['result']['external_delivery'] is False


def test_recorded_recovery_alert_survives_active_and_retained_company_scan(monkeypatch):
    from procure.demo import operator
    class Available:
        available = True
    def result(**kwargs):
        return {'status': 'completed', 'assistant_text': 'Ready.', 'agent_steps': [],
                'evidence_ids': [], 'candidate': None, **kwargs}
    app = create_app(clock=lambda: NOW, news_service=News(), operator_provider=Available())
    with TestClient(app, base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': before['csrf_token']}
        monkeypatch.setattr(operator, 'run_turn', lambda *a, **k: result(utility={'kind': 'load_quotes', 'parameters': {'material_id': 'copper'}}))
        assert client.post('/api/demo/operator', headers=headers, json={
            'message': 'Load copper quotes', 'expected_context_version': 1, 'idempotency_key': 'load'}).status_code == 200
        monkeypatch.setattr(operator, 'run_turn', lambda *a, **k: result(status='proposal',
            candidate={'kind': 'review_quotes', 'parameters': {'material_id': 'copper'}}))
        proposal = client.post('/api/demo/operator', headers=headers, json={
            'message': 'Review copper quotes', 'expected_context_version': 1, 'idempotency_key': 'review'}).json()['proposal']
        applied = client.post('/api/demo/operator/confirm', headers=headers, json={
            'proposal_id': proposal['proposal_id'], 'reviewed': True, 'expected_context_version': 1,
            'idempotency_key': 'apply'}).json()
        offers = applied['session']['offers']
        draft = before['company']['quote_coverage']['scenarios'][0]['buyer_defaults']
        plan_response = client.post('/api/demo/plan', headers=headers, json={**draft,
            'offer_ids': [o['offer_id'] for o in offers], 'expected_versions': {o['offer_id']: o['version'] for o in offers},
            'expected_context_version': 1})
        assert plan_response.status_code == 200
        plan = plan_response.json()
        assert plan['selected_offer_id']
        recovered = client.post('/api/demo/recovery', headers=headers, json={
            'baseline_plan_id': plan['plan_id'], 'expected_versions': plan['offer_versions'],
            'delay_days_by_offer': {plan['selected_offer_id']: 21}, 'expected_context_version': 1,
            'idempotency_key': 'delay'})
        assert recovered.status_code == 200
        first = client.get('/api/demo/risks').json()
        solara = next(c for c in first['companies'] if c['company_id'] == 'solara-cables')
        recorded = [a for a in solara['alerts'] if 'recorded synthetic recovery' in a['title']]
        assert len(recorded) == 1 and recorded[0]['category'] == 'supplier_delay'
        assert any(e['ref_id'] == plan['plan_id'] for e in recorded[0]['evidence'])
        switched = client.post('/api/demo/company', headers=headers, json={
            'company_id': 'terrapack', 'expected_context_version': 1, 'idempotency_key': 'to-terra'})
        assert switched.status_code == 200
        second = client.get('/api/demo/risks').json()
        retained = next(c for c in second['companies'] if c['company_id'] == 'solara-cables')
        assert [a for a in retained['alerts'] if a['alert_id'] == recorded[0]['alert_id']] == recorded
