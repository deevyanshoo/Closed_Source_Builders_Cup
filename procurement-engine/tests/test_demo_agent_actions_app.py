"""Agent actions operate the same owned synthetic workspace as the UI."""
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from fastapi.testclient import TestClient
import pytest

from procure.demo.app import create_app
from procure.demo import operator
from procure.demo.errors import DemoError
from test_demo_live_voice import Provider as LiveProvider
from test_demo_operator import Provider as OperatorProvider


class Available:
    available = True
    model = 'test-model'


@pytest.fixture
def client():
    with TestClient(create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                               operator_provider=Available()), base_url='https://demo.test') as value:
        yield value


def session_auth(client):
    session = client.get('/api/demo/session').json()
    return session, {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}


def outcome(**extra):
    return {'status': 'completed', 'assistant_text': 'Workspace tool ready.',
            'agent_steps': [], 'evidence_ids': [], 'candidate': None, **extra}


def test_canonical_buyer_draft_preserves_material_units_and_rejects_wrong_unit(client):
    session, headers = session_auth(client)
    profile = session['company']
    defaults = next(s['buyer_defaults'] for s in profile['quote_coverage']['scenarios']
                    if s['material_id'] == 'polymer')
    draft = {k: v for k, v in defaults.items() if k not in
             ('offer_ids', 'expected_versions', 'quantity_kg', 'current_stock_kg', 'additional_cost_per_kg')}
    body = {'buyer_draft': draft, 'expected_context_version': 1, 'idempotency_key': 'form'}
    response = client.post('/api/demo/buyer-draft', headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()['buyer_draft']['material'] == 'polymer'
    assert response.json()['selected_material_id'] == 'polymer'
    assert response.json()['context_version'] == 1
    wrong = {**body, 'buyer_draft': {**draft, 'quantity_unit': 'litre'}, 'idempotency_key': 'wrong'}
    assert client.post('/api/demo/buyer-draft', headers=headers, json=wrong).status_code == 422


def test_form_fill_restores_canonical_state_and_exact_retry_is_idempotent(client, monkeypatch):
    _, headers = session_auth(client)
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'fill_form', 'parameters': {'material_id': 'polymer', 'values': {'quantity': '12000'}}}))
    request = {'message': 'Fill the polymer form with quantity 12000 kg',
               'expected_context_version': 1, 'idempotency_key': 'utility'}
    first = client.post('/api/demo/operator', headers=headers, json=request)
    assert first.status_code == 200
    assert first.json()['read_only'] is False
    assert first.json()['action_result']['tool'] == 'fill_form'
    assert first.json()['session']['buyer_draft']['quantity'] == '12000'
    retry = client.post('/api/demo/operator', headers=headers, json=request)
    assert retry.status_code == 200
    assert retry.json()['turn_id'] == first.json()['turn_id']
    assert client.get('/api/demo/session').json()['buyer_draft']['quantity'] == '12000'


def test_simulated_email_requires_review_then_records_one_unsent_delivery(client, monkeypatch):
    session, headers = session_auth(client)
    recipient = session['company']['suppliers'][0]['name']
    parameters = {'material_id': 'copper', 'recipient_label': recipient,
                  'subject': 'Synthetic supplier follow-up', 'body': 'Please review this synthetic request.'}
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'simulate_email', 'parameters': parameters}))
    proposed = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Simulate a supplier follow-up email for copper',
        'expected_context_version': 1, 'idempotency_key': 'email'}).json()
    assert proposed['proposal']['kind'] == 'simulate_email'
    assert client.get('/api/demo/session').json()['simulated_outbox'] == []
    body = {'proposal_id': proposed['proposal']['proposal_id'], 'reviewed': True,
            'expected_context_version': 1, 'idempotency_key': 'review'}
    denied = client.post('/api/demo/operator/confirm', headers=headers, json={**body, 'reviewed': False})
    assert denied.status_code == 422
    applied = client.post('/api/demo/operator/confirm', headers=headers, json=body)
    assert applied.status_code == 200
    result = applied.json()['result']
    assert result['status'] == 'simulated_sent'
    assert result['simulated'] is True and result['external_delivery'] is False
    retry = client.post('/api/demo/operator/confirm', headers=headers, json=body)
    assert retry.status_code == 200 and retry.json()['result']['message_id'] == result['message_id']
    assert len(client.get('/api/demo/session').json()['simulated_outbox']) == 1


def test_simulated_outbox_is_owned_by_each_company_workspace(client, monkeypatch):
    session, headers = session_auth(client)
    recipient = session['company']['suppliers'][0]['name']
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'simulate_email', 'parameters': {
            'material_id': 'copper', 'recipient_label': recipient,
            'subject': 'Synthetic follow-up', 'body': 'Please review this synthetic request.'}}))
    proposal = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Simulate email for copper', 'expected_context_version': 1,
        'idempotency_key': 'company-email'}).json()['proposal']
    applied = client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': 1, 'idempotency_key': 'company-send'})
    assert applied.status_code == 200
    message_id = applied.json()['result']['message_id']
    other = client.post('/api/demo/company', headers=headers, json={
        'company_id': 'terrapack', 'expected_context_version': 1,
        'idempotency_key': 'company-away'})
    assert other.status_code == 200
    assert other.json()['simulated_outbox'] == []
    restored = client.post('/api/demo/company', headers=headers, json={
        'company_id': 'solara-cables', 'expected_context_version': 2,
        'idempotency_key': 'company-back'})
    assert restored.status_code == 200
    assert [item['message_id'] for item in restored.json()['simulated_outbox']] == [message_id]


def test_load_quotes_stages_only_owned_drafts_and_retries_without_duplicates(client, monkeypatch):
    _, headers = session_auth(client)
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': 'polymer'}}))
    request = {'message': 'Load polymer quotes', 'expected_context_version': 1,
               'idempotency_key': 'polymer-load'}
    first = client.post('/api/demo/operator', headers=headers, json=request)
    assert first.status_code == 200, first.text
    offers = first.json()['session']['offers']
    assert len(offers) == 3
    assert all(offer['status'] != 'saved' for offer in offers)
    assert all(offer['fixture_id'].startswith('solara-cables-polymer-') for offer in offers)
    assert client.post('/api/demo/operator', headers=headers, json=request).json()['turn_id'] == first.json()['turn_id']
    assert len(client.get('/api/demo/session').json()['offers']) == 3


def test_loading_another_material_aligns_selected_buyer_draft(client, monkeypatch):
    _, headers = session_auth(client)
    target = ['copper']
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': target[0]}}))
    first = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load copper quotes', 'expected_context_version': 1,
        'idempotency_key': 'material-copper'})
    assert first.status_code == 200, first.text
    assert first.json()['session']['buyer_draft']['material'] == 'copper'
    target[0] = 'polymer'
    second = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load polymer quotes', 'expected_context_version': 1,
        'idempotency_key': 'material-polymer'})
    assert second.status_code == 200, second.text
    assert second.json()['session']['selected_material_id'] == 'polymer'
    assert second.json()['session']['buyer_draft']['material'] == 'polymer'


def test_changed_buyer_draft_invalidates_proposal_and_preserves_company_version(client, monkeypatch):
    session, headers = session_auth(client)
    recipient = session['company']['suppliers'][0]['name']
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'simulate_email', 'parameters': {
            'material_id': 'copper', 'recipient_label': recipient,
            'subject': 'Synthetic follow-up', 'body': 'Please review this synthetic request.'}}))
    proposal = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Simulate email for copper', 'expected_context_version': 1,
        'idempotency_key': 'email-before-draft'}).json()['proposal']
    defaults = session['company']['quote_coverage']['scenarios'][0]['buyer_defaults']
    draft = {field: defaults[field] for field in ('material', 'quantity', 'current_stock',
        'quantity_unit', 'budget', 'additional_cost_per_unit', 'decision_date', 'need_by',
        'reporting_currency')}
    draft['quantity'] = '11000'
    changed = client.post('/api/demo/buyer-draft', headers=headers, json={
        'buyer_draft': draft, 'expected_context_version': 1, 'idempotency_key': 'new-draft'})
    assert changed.status_code == 200, changed.text
    assert changed.json()['context_version'] == 1
    assert client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': 1, 'idempotency_key': 'stale-email'}).status_code in (404, 409)
    assert client.get('/api/demo/session').json()['simulated_outbox'] == []


def test_same_default_buyer_draft_keeps_reviewable_proposal(client, monkeypatch):
    session, headers = session_auth(client)
    recipient = session['company']['suppliers'][0]['name']
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'simulate_email', 'parameters': {
            'material_id': 'copper', 'recipient_label': recipient,
            'subject': 'Synthetic follow-up', 'body': 'Please review this synthetic request.'}}))
    proposal = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Simulate email for copper', 'expected_context_version': 1,
        'idempotency_key': 'same-email'}).json()['proposal']
    defaults = session['company']['quote_coverage']['scenarios'][0]['buyer_defaults']
    draft = {field: defaults[field] for field in ('material', 'quantity', 'current_stock',
        'quantity_unit', 'budget', 'additional_cost_per_unit', 'decision_date', 'need_by',
        'reporting_currency')}
    assert client.post('/api/demo/buyer-draft', headers=headers, json={
        'buyer_draft': draft, 'expected_context_version': 1,
        'idempotency_key': 'same-before-confirm'}).status_code == 200
    applied = client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': 1, 'idempotency_key': 'same-confirm'})
    assert applied.status_code == 200, applied.text


def test_quote_review_uses_verified_fixture_values_and_saves_atomically(client, monkeypatch):
    _, headers = session_auth(client)
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': 'polymer'}}))
    loaded = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load polymer quotes', 'expected_context_version': 1,
        'idempotency_key': 'load-to-review'}).json()
    ids = [offer['offer_id'] for offer in loaded['session']['offers']]
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'review_quotes', 'parameters': {
            'material_id': 'polymer', 'offer_ids': ids}}))
    proposal_response = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Review polymer quote values', 'expected_context_version': 1,
        'idempotency_key': 'review-turn'})
    assert proposal_response.status_code == 200, proposal_response.text
    proposal = proposal_response.json()['proposal']
    assert len(proposal['parameters']['preview']) == 3
    applied = client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': 1, 'idempotency_key': 'review-apply'})
    assert applied.status_code == 200, applied.text
    assert all(offer['status'] == 'saved' for offer in applied.json()['result']['offers'])
    assert all(offer['document']['filename'].startswith('SYN-') for offer in applied.json()['result']['offers'])


def test_failed_quote_review_leaves_every_owned_offer_unmodified(client, monkeypatch):
    from procure.demo import quote_service
    _, headers = session_auth(client)
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': 'polymer'}}))
    loaded = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load polymer quotes', 'expected_context_version': 1,
        'idempotency_key': 'atomic-load'}).json()
    ids = [offer['offer_id'] for offer in loaded['session']['offers']]
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'review_quotes', 'parameters': {
            'material_id': 'polymer', 'offer_ids': ids}}))
    proposal = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Review polymer quote values', 'expected_context_version': 1,
        'idempotency_key': 'atomic-propose'}).json()['proposal']
    original = quote_service.save_offer
    attempts = [0]

    def fail_second(*args, **kwargs):
        attempts[0] += 1
        if attempts[0] == 2:
            raise DemoError('fixture_unavailable', 'A fixture could not be reviewed.', 503)
        return original(*args, **kwargs)

    monkeypatch.setattr(quote_service, 'save_offer', fail_second)
    denied = client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': 1, 'idempotency_key': 'atomic-apply'})
    assert denied.status_code == 503
    current = client.get('/api/demo/session').json()['offers']
    assert [(o['offer_id'], o['version'], o['status']) for o in current] == [
        (o['offer_id'], o['version'], o['status']) for o in loaded['session']['offers']]


def test_quote_review_rejects_an_offer_from_another_material(client, monkeypatch):
    _, headers = session_auth(client)
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': 'polymer'}}))
    polymer = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load polymer quotes', 'expected_context_version': 1,
        'idempotency_key': 'foreign-polymer'}).json()['session']['offers']
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': 'copper'}}))
    copper = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load copper quotes', 'expected_context_version': 1,
        'idempotency_key': 'foreign-copper'}).json()['session']['offers']
    copper_id = next(offer['offer_id'] for offer in copper if offer['fixture_id'] == 'express-copper')
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'review_quotes', 'parameters': {
            'material_id': 'polymer', 'offer_ids': [polymer[0]['offer_id'], copper_id]}}))
    response = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Review polymer quote values', 'expected_context_version': 1,
        'idempotency_key': 'foreign-review'})
    assert response.status_code == 422
    assert all(offer['status'] != 'saved' for offer in client.get('/api/demo/session').json()['offers'])


def test_compare_uses_saved_owned_quotes_and_same_buyer_draft_keeps_plan(client, monkeypatch):
    session, headers = session_auth(client)
    defaults = next(row['buyer_defaults'] for row in session['company']['quote_coverage']['scenarios']
                    if row['material_id'] == 'polymer')
    draft = {field: defaults[field] for field in ('material', 'quantity', 'current_stock',
        'quantity_unit', 'budget', 'additional_cost_per_unit', 'decision_date', 'need_by',
        'reporting_currency')}
    assert client.post('/api/demo/buyer-draft', headers=headers, json={
        'buyer_draft': draft, 'expected_context_version': 1,
        'idempotency_key': 'compare-draft'}).status_code == 200
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'compare', 'parameters': {'material_id': 'polymer'}}))
    premature = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Compare polymer suppliers', 'expected_context_version': 1,
        'idempotency_key': 'premature'})
    assert premature.status_code == 409
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'load_quotes', 'parameters': {'material_id': 'polymer'}}))
    loaded = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Load polymer quotes', 'expected_context_version': 1,
        'idempotency_key': 'compare-load'}).json()
    ids = [offer['offer_id'] for offer in loaded['session']['offers']]
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(
        status='proposal', candidate={'kind': 'review_quotes', 'parameters': {
            'material_id': 'polymer', 'offer_ids': ids}}))
    proposal = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Review polymer quote values', 'expected_context_version': 1,
        'idempotency_key': 'compare-review'}).json()['proposal']
    applied = client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': 1, 'idempotency_key': 'compare-save'})
    assert applied.status_code == 200, applied.text
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome(utility={
        'kind': 'compare', 'parameters': {'material_id': 'polymer'}}))
    compared = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Compare polymer suppliers', 'expected_context_version': 1,
        'idempotency_key': 'compare-now'})
    assert compared.status_code == 200, compared.text
    plan_id = compared.json()['session']['plan']['plan_id']
    same = client.post('/api/demo/buyer-draft', headers=headers, json={
        'buyer_draft': draft, 'expected_context_version': 1,
        'idempotency_key': 'same-draft'})
    assert same.status_code == 200
    assert same.json()['plan']['plan_id'] == plan_id


def test_operator_hourly_cap_is_bounded_instead_of_lifetime_exhaustion(client, monkeypatch):
    _, headers = session_auth(client)
    monkeypatch.setattr(operator, 'run_turn', lambda *args, **kwargs: outcome())
    for index in range(30):
        response = client.post('/api/demo/operator', headers=headers, json={
            'message': 'Inspect inventory', 'expected_context_version': 1,
            'idempotency_key': f'hourly-{index}'})
        assert response.status_code == 200, response.text
    capped = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Inspect inventory', 'expected_context_version': 1,
        'idempotency_key': 'hourly-over'})
    assert capped.status_code == 429
    assert 'within one hour' in capped.json()['error']['message']


def test_native_voice_form_tool_updates_owned_session_without_sending_tokens_to_model(monkeypatch):
    from procure.demo import live_voice
    monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)
    class FormProvider(LiveProvider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name='fill_buyer_form', args={'material_id': 'polymer',
                    'values': {'quantity': '12000'}}, id='voice-form-one')]), server_content=None))

    provider = FormProvider()

    @asynccontextmanager
    async def factory():
        yield provider

    application = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                             operator_provider=Available(), live_client_factory=factory)
    with TestClient(application, base_url='https://demo.test') as voice_client:
        session = voice_client.get('/api/demo/session').json()
        with voice_client.websocket_connect('wss://demo.test/api/demo/voice/live',
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
            result = event['result']
            assert result['read_only'] is False
            assert result['session']['buyer_draft']['quantity'] == '12000'
            assert result['action_result']['tool'] == 'fill_form'
            while socket.receive_json()['type'] != 'turn_complete':
                pass
            assert 'session' not in provider.tool_response.response
            assert session['csrf_token'] not in str(provider.tool_response.response)
            socket.send_json({'type': 'stop'})
        assert voice_client.get('/api/demo/session').json()['buyer_draft']['quantity'] == '12000'


def test_native_voice_repeat_call_id_cannot_restore_stale_form(monkeypatch):
    from procure.demo import live_voice
    monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)

    class RepeatedFormProvider(LiveProvider):
        calls = (('form-A', '12000'), ('form-B', '13000'), ('form-A', '12000'))

        def __init__(self):
            super().__init__()
            self.index = 0

        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                call_id, quantity = self.calls[self.index]
                self.index += 1
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name='fill_buyer_form', args={'material_id': 'polymer',
                    'values': {'quantity': quantity}}, id=call_id)]), server_content=None))

    provider = RepeatedFormProvider()

    @asynccontextmanager
    async def factory():
        yield provider

    application = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                             operator_provider=Available(), live_client_factory=factory)
    with TestClient(application, base_url='https://demo.test') as voice_client:
        session = voice_client.get('/api/demo/session').json()
        with voice_client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                            headers={'origin': 'https://demo.test'}) as socket:
            socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                              'expected_context_version': 1})
            while socket.receive_json()['type'] != 'ready':
                pass
            results = []
            for _ in range(3):
                socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
                event = socket.receive_json()
                while event['type'] != 'tool_result':
                    assert event['type'] != 'error', event
                    event = socket.receive_json()
                results.append(event['result'])
                while socket.receive_json()['type'] != 'turn_complete':
                    pass
            assert [result['session']['buyer_draft']['quantity'] for result in results[:2]] == ['12000', '13000']
            assert results[2]['status'] == 'needs_clarification'
            assert results[2]['read_only'] is True and 'session' not in results[2]
            socket.send_json({'type': 'stop'})
        assert voice_client.get('/api/demo/session').json()['buyer_draft']['quantity'] == '13000'


def test_native_voice_repeat_generic_turn_reuses_paid_operator_result(monkeypatch):
    from procure.demo import live_voice
    monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)

    class DuplicateProvider(LiveProvider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                event = NS(tool_call=NS(function_calls=[NS(
                    name='operate_workspace', args={'message': 'Inspect copper inventory'},
                    id='same-generic-call')]), server_content=None)
                await self.events.put(event)
                await self.events.put(event)

    provider, operator_provider = DuplicateProvider(), OperatorProvider(intent='inspect', action='none', evidence=())

    @asynccontextmanager
    async def factory():
        yield provider

    application = create_app(clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc),
                             operator_provider=operator_provider, live_client_factory=factory)
    with TestClient(application, base_url='https://demo.test') as voice_client:
        session = voice_client.get('/api/demo/session').json()
        with voice_client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                            headers={'origin': 'https://demo.test'}) as socket:
            socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                              'expected_context_version': 1})
            while socket.receive_json()['type'] != 'ready':
                pass
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            results = []
            while len(results) < 2:
                event = socket.receive_json()
                assert event['type'] != 'error', event
                if event['type'] == 'tool_result':
                    results.append(event['result'])
            assert results[0]['turn_id'] == results[1]['turn_id']
            assert len(operator_provider.calls) == 3
            socket.send_json({'type': 'stop'})
