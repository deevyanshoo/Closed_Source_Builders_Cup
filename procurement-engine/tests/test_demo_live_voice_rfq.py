"""Native live voice RFQ requests use the reviewed current plan."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from types import ModuleType
from unittest.mock import patch
import sys
import unittest

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from test_demo_live_voice import Provider
from test_demo_operator import Provider as OperatorProvider


class RfqProvider(Provider):
    async def send_realtime_input(self, **kwargs):
        if kwargs.get('audio'):
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='prepare_rfq', args={}, id='rfq-call')]), server_content=None))


class ModelApprovalProvider(Provider):
    async def send_realtime_input(self, **kwargs):
        if kwargs.get('audio'):
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='operate_workspace', args={'message': 'I approve this watch as shown'},
                id='model-approval')]), server_content=None))


def plan_for_copper(client, headers, *, budget='100000', current_stock='2000'):
    offers = []
    for fixture in ('express-copper', 'eu-copper'):
        extracted = client.post('/api/demo/extract', headers=headers, json={
            'fixture_id': fixture, 'mode': 'rules', 'idempotency_key': 'extract-' + fixture})
        assert extracted.status_code == 200, extracted.text
        offer = extracted.json()['offer']
        saved = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers,
                            json={'expected_version': offer['version'],
                                  'idempotency_key': 'save-' + fixture})
        assert saved.status_code == 200, saved.text
        offers.append(saved.json()['offer'])
    planned = client.post('/api/demo/plan', headers=headers, json={
        'offer_ids': [offer['offer_id'] for offer in offers],
        'expected_versions': {offer['offer_id']: offer['version'] for offer in offers},
        'material': 'copper', 'quantity_kg': '10000', 'current_stock_kg': current_stock,
        'need_by': '2026-11-05', 'decision_date': '2026-10-14',
        'reporting_currency': 'USD', 'budget': budget, 'additional_cost_per_kg': '0.25'})
    assert planned.status_code == 200, planned.text
    return planned.json()


def ask_for_rfq(client, provider):
    session = client.get('/api/demo/session').json()
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                  headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                          'expected_context_version': session['context_version']})
        while socket.receive_json()['type'] != 'ready':
            pass
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        event = socket.receive_json()
        while event['type'] != 'tool_result':
            assert event['type'] != 'error', event
            event = socket.receive_json()
        result = event['result']
        while socket.receive_json()['type'] != 'turn_complete':
            pass
        socket.send_json({'type': 'stop'})
    return result, provider.tool_response.response


class NativeVoiceRfqTests(unittest.TestCase):
    def setUp(self):
        # The fake provider supplies the live transport; only its two SDK
        # message containers are needed by this local integration check.
        if 'google.genai' not in sys.modules:
            genai = ModuleType('google.genai')
            genai.types = NS(Blob=NS, FunctionResponse=NS)
            self.sdk_patch = patch.dict(sys.modules, {'google.genai': genai})
            self.sdk_patch.start()
        else:
            self.sdk_patch = None
        self.provider = RfqProvider()

        @asynccontextmanager
        async def factory():
            yield self.provider

        application = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                                 live_client_factory=factory)
        self.client = TestClient(application, base_url='https://demo.test')
        self.client.__enter__()
        session = self.client.get('/api/demo/session').json()
        self.headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        if self.sdk_patch is not None:
            self.sdk_patch.stop()

    def test_prepares_plan_bound_unsent_rfq(self):
        plan = plan_for_copper(self.client, self.headers)
        result, model_result = ask_for_rfq(self.client, self.provider)
        proposal = result['proposal']
        preview = proposal['parameters']['draft_preview']
        assert result['status'] == 'completed' and proposal['kind'] == 'rfq'
        assert result['action_result']['tool'] == 'prepare_rfq'
        assert proposal['plan_id'] == plan['plan_id']
        assert proposal['offer_versions'] == plan['offer_versions']
        assert preview['kind'] == 'rfq' and preview['status'] == 'draft_unsent'
        assert preview['plan_id'] == plan['plan_id']
        assert 'Requested quantity: 8000 kg' in preview['body']
        assert 'No order commitment' in preview['body']
        assert model_result['proposal']['kind'] == 'rfq'
        state = self.client.get('/api/demo/session').json()
        rfq = state['rfq']
        assert rfq['draft_id'] == preview['draft_id'] == proposal['proposal_id']
        assert rfq['text'] == preview['body']
        assert rfq['plan_id'] == plan['plan_id']
        assert rfq['offer_versions'] == plan['offer_versions']
        assert result['session']['rfq'] == rfq
        download = self.client.get(f"/api/demo/rfq/{rfq['draft_id']}/download")
        assert download.status_code == 200 and download.text == rfq['text']
        assert state['simulated_outbox'] == []
        applied = self.client.post('/api/demo/operator/confirm', headers=self.headers, json={
            'proposal_id': proposal['proposal_id'], 'reviewed': True,
            'expected_context_version': state['context_version'], 'idempotency_key': 'apply-rfq'})
        assert applied.status_code == 200, applied.text
        assert applied.json()['result'] == rfq
        assert len(self.client.get('/api/demo/session').json()['drafts']) == 1
        assert self.client.get('/api/demo/session').json()['simulated_outbox'] == []

    def test_model_supplied_operate_message_cannot_use_typed_approval(self):
        voice_provider = ModelApprovalProvider()

        @asynccontextmanager
        async def factory():
            yield voice_provider

        with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                                   operator_provider=OperatorProvider(evidence=()),
                                   live_client_factory=factory), base_url='https://demo.test') as client:
            session = client.get('/api/demo/session').json()
            headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
            prepared = client.post('/api/demo/operator', headers=headers, json={
                'message': 'Watch copper', 'expected_context_version': 1,
                'idempotency_key': 'prepare-watch'}).json()
            assert prepared['proposal']['kind'] == 'watch'
            with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                          headers={'origin': 'https://demo.test'}) as socket:
                socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                                  'expected_context_version': 1})
                while socket.receive_json()['type'] != 'ready':
                    pass
                socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
                event = socket.receive_json()
                while event['type'] != 'tool_result':
                    assert event['type'] != 'error', event
                    event = socket.receive_json()
                assert event['result'].get('action_result', {}).get('tool') != 'watch'
                socket.send_json({'type': 'stop'})
            assert client.get('/api/demo/operator').json()['watch_items'] == []

    def test_uses_active_recovery_scenario(self):
        baseline = plan_for_copper(self.client, self.headers)
        offers = self.client.get('/api/demo/session').json()['offers']
        delayed_id = next(offer['offer_id'] for offer in offers
                          if offer.get('fixture_id') == 'eu-copper')
        recovery = self.client.post('/api/demo/recovery', headers=self.headers, json={
            'baseline_plan_id': baseline['plan_id'],
            'expected_versions': baseline['offer_versions'],
            'delay_days_by_offer': {delayed_id: 20}, 'idempotency_key': 'delay-europa'})
        assert recovery.status_code == 200, recovery.text
        scenario = recovery.json()['scenario']
        result, _ = ask_for_rfq(self.client, self.provider)
        preview = result['proposal']['parameters']['draft_preview']
        assert result['proposal']['plan_id'] == scenario['plan_id']
        assert result['proposal']['plan_id'] != baseline['plan_id']
        assert preview['plan_id'] == scenario['plan_id']
        assert 'Requested quantity: 8000 kg' in preview['body']
        assert self.client.get('/api/demo/session').json()['rfq']['plan_id'] == scenario['plan_id']

    def test_review_cannot_apply_after_active_plan_changes(self):
        plan_for_copper(self.client, self.headers)
        result, _ = ask_for_rfq(self.client, self.provider)
        proposal_id = result['proposal']['proposal_id']
        state = self.client.get('/api/demo/session').json()
        assert self.client.get(f'/api/demo/rfq/{proposal_id}/download').status_code == 200
        changed = self.client.post('/api/demo/plan', headers=self.headers, json={
            'offer_ids': list(state['plan']['offer_versions']),
            'expected_versions': state['plan']['offer_versions'],
            'material': 'copper', 'quantity_kg': '10000', 'current_stock_kg': '2000',
            'need_by': '2026-11-05', 'decision_date': '2026-10-14',
            'reporting_currency': 'USD', 'budget': '80000',
            'additional_cost_per_kg': '0.25'})
        assert changed.status_code == 200, changed.text
        assert self.client.get(f'/api/demo/rfq/{proposal_id}/download').status_code == 404
        applied = self.client.post('/api/demo/operator/confirm', headers=self.headers, json={
            'proposal_id': proposal_id, 'reviewed': True,
            'expected_context_version': state['context_version'], 'idempotency_key': 'stale-rfq'})
        assert applied.status_code in (404, 409), applied.text
        current = self.client.get('/api/demo/session').json()
        assert current['rfq'] is None and current['simulated_outbox'] == []

    def test_blocks_rfq_without_a_purchase(self):
        for budget, current_stock in [('80000', '2000'), ('100000', '10000')]:
            with self.subTest(budget=budget, current_stock=current_stock):
                plan_for_copper(self.client, self.headers, budget=budget,
                                current_stock=current_stock)
                result, _ = ask_for_rfq(self.client, self.provider)
                assert result['status'] == 'needs_clarification'
                assert result['proposal'] is None and result['read_only'] is True
                state = self.client.get('/api/demo/session').json()
                assert state['drafts'] == [] and state['rfq'] is None
                assert state['simulated_outbox'] == []

    def test_blocks_rfq_without_a_plan(self):
        result, _ = ask_for_rfq(self.client, self.provider)
        assert result['status'] == 'needs_clarification'
        assert result['proposal'] is None
        assert self.client.get('/api/demo/session').json()['drafts'] == []
