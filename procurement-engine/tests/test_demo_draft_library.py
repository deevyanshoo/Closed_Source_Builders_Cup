"""Prepared draft artifacts survive reload without restoring action approval."""
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from procure.demo.app import create_app
from procure.demo import operator
from procure.demo.session_persistence import MemorySessionPersistence
from procure.demo.sessions import SessionStore
from test_demo_rfq import demo

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


class Available:
    available = True
    model = 'test-model'


def test_prepared_email_is_visible_durable_scoped_and_never_sent(monkeypatch):
    backend = MemorySessionPersistence()
    app = create_app(clock=lambda: NOW, session_backend=backend, operator_provider=Available())
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        parameters = {'material_id': 'copper', 'recipient_label': session['company']['suppliers'][0]['name'],
                      'subject': 'Quotation request', 'body': 'Please confirm copper delivery availability.'}
        monkeypatch.setattr(operator, 'run_turn', lambda *a, **k: {
            'status': 'proposal', 'assistant_text': 'Draft ready to review.', 'agent_steps': [],
            'evidence_ids': [], 'candidate': {'kind': 'simulate_email', 'parameters': parameters}})
        body = {'message': 'Draft a copper supplier email.', 'expected_context_version': 1,
                'idempotency_key': 'prepare-draft'}
        response = client.post('/api/demo/operator', headers=headers, json=body)
        assert response.status_code == 200
        turn = response.json()
        drafts = client.get('/api/demo/session').json()['drafts']
        assert len(drafts) == 1 and drafts[0]['kind'] == 'email'
        assert drafts[0]['status'] == 'draft_unsent' and drafts[0]['body'] == parameters['body']
        assert turn['proposal']['parameters']['draft_preview'] == drafts[0]
        assert turn['workspace']['drafts'] == drafts
        assert client.get('/api/demo/session').json()['simulated_outbox'] == []
        assert client.post('/api/demo/operator', headers=headers, json=body).status_code == 200
        assert len(client.get('/api/demo/session').json()['drafts']) == 1
        restored_app = create_app(clock=lambda: NOW, session_backend=backend, operator_provider=Available())
        with TestClient(restored_app, base_url='https://demo.test') as restored:
            restored.cookies.update(client.cookies)
            state = restored.get('/api/demo/session').json()
            assert state['drafts'] == drafts and state['simulated_outbox'] == []
            denied = restored.post('/api/demo/operator/confirm', headers=headers, json={
                'proposal_id': turn['proposal']['proposal_id'], 'reviewed': True,
                'expected_context_version': 1, 'idempotency_key': 'old-consent'})
            assert denied.status_code in (404, 409)
            switched = restored.post('/api/demo/company', headers=headers, json={
                'company_id': 'terrapack', 'expected_context_version': 1, 'idempotency_key': 'switch'})
            assert switched.status_code == 200 and switched.json()['drafts'] == []
            back = restored.post('/api/demo/company', headers=headers, json={
                'company_id': 'solara-cables', 'expected_context_version': 2, 'idempotency_key': 'return'})
            assert back.status_code == 200 and back.json()['drafts'] == drafts


def test_old_persisted_session_without_drafts_restores_with_empty_library():
    store = SessionStore(clock=lambda: NOW, persistence_backend=MemorySessionPersistence())
    session = store.create()
    snapshot = store._snapshot(session)
    snapshot.pop('drafts', None)
    restored = store._restore(session.session_id, snapshot, 'v1')
    assert restored.drafts == []


def test_rfq_proposal_exposes_unsent_content_before_apply_without_changing_plan(demo, monkeypatch):
    client, headers, offers, request, plan = demo
    monkeypatch.setattr(operator.VertexOperatorProvider, 'available', property(lambda self: True))
    monkeypatch.setattr(operator, 'run_turn', lambda *a, **k: {
        'status': 'proposal', 'assistant_text': 'Unsent RFQ ready to review.', 'agent_steps': [],
        'evidence_ids': [], 'candidate': {'kind': 'rfq', 'parameters': {'material_id': 'copper'}}})
    result = client.post('/api/demo/operator', headers=headers, json={
        'message': 'Draft an RFQ for copper.', 'expected_context_version': 1, 'idempotency_key': 'rfq-preview'})
    assert result.status_code == 200
    preview = result.json()['proposal']['parameters']['draft_preview']
    assert preview['kind'] == 'rfq' and preview['status'] == 'draft_unsent'
    assert 'UNSENT REQUEST FOR QUOTATION DRAFT' in preview['body']
    assert preview['plan_id'] == plan['plan_id'] and preview['offer_versions'] == plan['offer_versions']
    state = client.get('/api/demo/session').json()
    assert state['plan']['plan_id'] == plan['plan_id'] and state['rfq'] is None
    assert state['drafts'] == result.json()['workspace']['drafts'] == [preview]


def test_direct_rfq_survives_a_new_comparison_and_session_reload(demo):
    client, headers, offers, request, plan = demo
    # Reuse the accepted quote fixture, then persist every subsequent mutation.
    backend = MemorySessionPersistence()
    original_store = client.app.state.sessions
    session_id = client.get('/api/demo/session').json()['session_id']
    existing = original_store.get(session_id)
    original_store.backend = backend
    original_store.persistence = 'firestore'
    original_store.save(existing)
    response = client.post('/api/demo/rfq', headers=headers, json={
        'plan_id': plan['plan_id'], 'expected_versions': plan['offer_versions'],
        'reviewed': True, 'idempotency_key': 'direct-rfq'})
    assert response.status_code == 200
    rfq = response.json()
    drafts = client.get('/api/demo/session').json()['drafts']
    assert len(drafts) == 1 and drafts[0]['draft_id'] == rfq['draft_id']
    assert drafts[0]['body'] == rfq['text'] and drafts[0]['kind'] == 'rfq'
    assert client.post('/api/demo/plan', headers=headers, json=request).status_code == 200
    restored = client.get('/api/demo/session').json()
    assert restored['rfq'] is None and restored['drafts'] == drafts
    fresh_app = create_app(clock=original_store.clock, session_backend=backend)
    with TestClient(fresh_app, base_url='https://demo.test') as cold:
        cold.cookies.update(client.cookies)
        state = cold.get('/api/demo/session').json()
        assert state['session_id'] == session_id
        assert state['rfq'] is None and state['drafts'] == drafts
        assert state['drafts'][0]['status'] == 'draft_unsent'
        assert state['simulated_outbox'] == []
