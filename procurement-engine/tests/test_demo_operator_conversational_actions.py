"""Conversational approvals stay bound to the current reviewed workspace proposal."""
from fastapi.testclient import TestClient

from test_demo_operator_app import auth, demo, planned_demo, turn
from test_demo_operator import Provider
from test_demo_operator_smart import SmartProvider
from procure.demo.company import get_company
from procure.demo.live_voice import _spoken_confirmation
from datetime import timedelta
from test_demo_operator_app import NOW


def test_explicit_typed_review_applies_current_proposal_once():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        proposal = turn(client, headers).json()['proposal']
        applied = turn(client, headers, 'I approve this watch as shown', key='chat-approve')
        assert applied.status_code == 200, applied.text
        payload = applied.json()
        assert payload['status'] == 'completed'
        assert payload['action_result']['tool'] == 'watch'
        assert client.get('/api/demo/operator').json()['watch_items'][0]['material_id'] == 'copper'
        assert payload['proposal'] is None
        assert len(client.get('/api/demo/operator').json()['watch_items']) == 1
        replay = turn(client, headers, 'I approve this watch as shown', key='chat-approve')
        assert replay.status_code == 200
        assert len(client.get('/api/demo/operator').json()['watch_items']) == 1


def test_ambiguous_or_negated_approval_keeps_proposal_for_review():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        proposal = turn(client, headers).json()['proposal']
        for index, message in enumerate(('yes', "I don't approve this watch")):
            answer = turn(client, headers, message, key=f'not-approved-{index}')
            assert answer.status_code == 200, answer.text
            assert answer.json()['status'] == 'needs_clarification'
            assert answer.json()['proposal']['proposal_id'] == proposal['proposal_id']
            assert client.get('/api/demo/operator').json()['watch_items'] == []


def test_typed_approval_after_company_switch_does_not_apply_old_proposal():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        turn(client, headers)
        switched = client.post('/api/demo/company', headers=headers, json={
            'company_id': 'terrapack', 'expected_context_version': 1,
            'idempotency_key': 'switch-before-chat-approval'})
        assert switched.status_code == 200
        denied = turn(client, headers, 'I approve this watch as shown',
                      key='old-chat-approval', context=2)
        assert denied.status_code == 200
        assert denied.json()['status'] == 'needs_clarification'
        assert denied.json()['proposal'] is None
        assert client.get('/api/demo/operator').json()['watch_items'] == []


def test_typed_cancel_discards_current_proposal_without_applying():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        turn(client, headers)
        cancelled = turn(client, headers, 'Cancel this proposal', key='chat-cancel')
        assert cancelled.status_code == 200
        assert cancelled.json()['action_result']['tool'] == 'cancel_proposal'
        assert client.get('/api/demo/operator').json()['watch_items'] == []
        denied = turn(client, headers, 'I approve this watch as shown', key='after-cancel')
        assert denied.json()['status'] == 'needs_clarification'


def test_unrelated_read_keeps_current_review_available():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        proposal = turn(client, headers).json()['proposal']
        looked = turn(client, headers, 'Show current procurement inventory', key='read-between')
        assert looked.status_code == 200
        approved = turn(client, headers, 'I approve this watch as shown', key='approve-after-read')
        assert approved.status_code == 200
        assert approved.json()['action_result']['tool'] == 'watch'
        assert client.get('/api/demo/operator').json()['watch_items'][0]['material_id'] == 'copper'


def test_confirm_data_questions_route_normally_with_or_without_pending_review():
    for has_pending in (False, True):
        with demo(Provider(evidence=())) as client:
            headers = auth(client)
            proposal = turn(client, headers).json()['proposal'] if has_pending else None
            for index, question in enumerate(('Confirm our current shipment ETA',
                                              'Confirm latest inventory cover')):
                response = turn(client, headers, question,
                                key=f'data-confirm-{has_pending}-{index}')
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['assistant_text'] not in {
                    'Please review the current proposal, then say which action you approve.',
                    'There is no current proposal to approve.'}
                assert result.get('action_result', {}).get('tool') != 'watch'
                assert client.get('/api/demo/operator').json()['watch_items'] == []
            current = client.get('/api/demo/session').json()['pending_operator_proposal']
            assert (current is not None) == has_pending
            if has_pending:
                assert current['proposal_id'] == proposal['proposal_id']


def test_session_restores_only_current_public_owned_proposal():
    for change in ('none', 'expired', 'marker', 'company', 'context', 'state'):
        with demo(Provider(evidence=())) as client:
            headers = auth(client)
            prepared = turn(client, headers).json()['proposal']
            value = client.app.state.sessions.get(client.get('/api/demo/session').json()['session_id'])
            before = client.get('/api/demo/session').json()['pending_operator_proposal']
            assert before == prepared and not any(key.startswith('_') for key in before)
            if change == 'expired':
                value.pending_proposal['expires_at'] = (NOW - timedelta(seconds=1)).isoformat()
            elif change == 'marker':
                value.pending_proposal['_marker'] = 'stale-marker'
            elif change == 'company':
                value.pending_proposal['_company_id'] = 'terrapack'
            elif change == 'context':
                value.pending_proposal['context_version'] += 1
            elif change == 'state':
                value.watch_items.append({'watch_id': 'another', 'material_id': 'polymer'})
            snapshot = dict(value.pending_proposal)
            current = client.get('/api/demo/session').json()['pending_operator_proposal']
            assert (current == prepared) if change == 'none' else (current is None)
            assert value.pending_proposal == snapshot


def test_unsent_rfq_preparation_completes_as_discoverable_draft():
    provider = Provider(intent='rfq', evidence=(), action='rfq')
    with demo(provider) as client:
        headers = auth(client)
        _, _, plan = planned_demo(client, headers)
        prepared = turn(client, headers, 'Create an unsent RFQ draft', key='draft-request')
        assert prepared.status_code == 200, prepared.text
        result = prepared.json()
        assert result['status'] == 'completed'
        assert result['action_result']['tool'] == 'prepare_rfq'
        ledger = result['session']['drafts'][-1]
        rfq = result['session']['rfq']
        assert ledger['status'] == rfq['status'] == 'draft_unsent'
        assert rfq['draft_id'] == ledger['draft_id'] == result['proposal']['proposal_id']
        assert rfq['text'] == ledger['body'] and rfq['plan_id'] == plan['plan_id']
        assert rfq['offer_versions'] == plan['offer_versions']
        download = client.get(f"/api/demo/rfq/{ledger['draft_id']}/download")
        assert download.status_code == 200 and download.text == rfq['text']
        assert client.get('/api/demo/session').json()['rfq'] == rfq
        with TestClient(client.app, base_url='https://demo.test') as foreign:
            foreign.get('/api/demo/session')
            denied = foreign.get(f"/api/demo/rfq/{ledger['draft_id']}/download")
            assert denied.status_code == 404 and denied.json()['error']['code'] == 'draft_not_found'
        replay = turn(client, headers, 'Create an unsent RFQ draft', key='draft-request')
        assert replay.status_code == 200 and replay.json()['session']['rfq'] == rfq
        assert len(client.get('/api/demo/session').json()['drafts']) == 1


def test_simulated_email_draft_is_saved_without_outbox_delivery():
    provider = SmartProvider('simulate_email', 'copper', action='simulate_email')
    with demo(provider) as client:
        headers = auth(client)
        supplier = get_company('solara-cables')['suppliers'][0]['name']
        prepared = turn(client, headers, f'Draft a simulated email to {supplier} about copper.',
                        key='draft-email')
        assert prepared.status_code == 200, prepared.text
        result = prepared.json()
        assert result['status'] == 'completed'
        assert result['action_result']['tool'] == 'prepare_simulated_email'
        assert result['session']['drafts'][-1]['kind'] == 'email'
        assert result['session']['simulated_outbox'] == []


def test_native_action_requires_full_audible_server_review_and_independent_words():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        turn(client, headers)
        assert _spoken_confirmation('confirm action', 'workspace_action')
        assert not _spoken_confirmation('yes', 'workspace_action')
        assert not _spoken_confirmation('do not confirm action', 'workspace_action')
