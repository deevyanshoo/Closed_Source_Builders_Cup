"""Ten-company action boundary with injected model roles and no provider calls."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.company import get_company, list_companies
from test_demo_operator_smart import SmartProvider


COMPANY_IDS = [row['company_id'] for row in list_companies()]


@pytest.mark.parametrize('company_id', COMPANY_IDS)
def test_simulated_email_review_replay_history_and_company_isolation(company_id):
    profile = get_company(company_id)
    material_id = profile['quote_coverage']['scenarios'][0]['material_id']
    recipient = next(row['name'] for row in profile['suppliers']
                     if material_id in row['materials'])
    provider = SmartProvider('simulate_email', material_id)
    app = create_app(clock=lambda: datetime(2026, 10, 15, tzinfo=timezone.utc),
                     operator_provider=provider)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        if session['company_id'] != company_id:
            selected = client.post('/api/demo/company', headers=headers, json={
                'company_id': company_id, 'expected_context_version': session['context_version'],
                'idempotency_key': 'select-company'})
            assert selected.status_code == 200, selected.text
            session = selected.json()
        context = session['context_version']
        message = (f'Draft a simulated email to {recipient} about {material_id}. '
                   'Subject: Fictional quote inquiry. Body: Please send a fictional quotation.')
        response = client.post('/api/demo/operator', headers=headers, json={
            'message': message, 'expected_context_version': context,
            'idempotency_key': 'email-proposal'})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'completed'
        assert result['action_result']['tool'] == 'prepare_simulated_email'
        assert result['proposal']['kind'] == 'simulate_email'
        assert len(provider.prompts) == 3
        draft = client.get('/api/demo/session').json()['drafts'][-1]
        assert draft['company_id'] == company_id and draft['status'] == 'draft_unsent'
        assert not client.get('/api/demo/session').json()['simulated_outbox']
        body = {'proposal_id': result['proposal']['proposal_id'], 'reviewed': True,
                'expected_context_version': context, 'idempotency_key': 'review-email'}
        applied = client.post('/api/demo/operator/confirm', headers=headers, json=body)
        replay = client.post('/api/demo/operator/confirm', headers=headers, json=body)
        assert applied.status_code == replay.status_code == 200
        assert applied.json()['result'] == replay.json()['result']
        outbox = client.get('/api/demo/session').json()['simulated_outbox']
        assert len(outbox) == 1 and outbox[0]['company_id'] == company_id
        assert outbox[0]['simulated'] is True and outbox[0]['external_delivery'] is False
        assert outbox[0]['status'] == 'simulated_sent'
        history = client.get('/api/demo/operator/history').json()
        assert history['company_id'] == company_id and len(history['exchanges']) >= 1
        away = next(cid for cid in COMPANY_IDS if cid != company_id)
        selected = client.post('/api/demo/company', headers=headers, json={
            'company_id': away, 'expected_context_version': context,
            'idempotency_key': 'switch-away'})
        assert selected.status_code == 200
        foreign = selected.json()
        assert not foreign['simulated_outbox'] and not foreign['drafts']
        assert not client.get('/api/demo/operator/history').json()['exchanges']
        restored = client.post('/api/demo/company', headers=headers, json={
            'company_id': company_id, 'expected_context_version': foreign['context_version'],
            'idempotency_key': 'switch-back'})
        assert restored.status_code == 200
        assert restored.json()['simulated_outbox'] == outbox
        assert any(item['draft_id'] == draft['draft_id'] for item in restored.json()['drafts'])
