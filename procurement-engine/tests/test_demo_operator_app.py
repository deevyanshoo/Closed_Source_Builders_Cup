"""Hosted operator/voice boundaries use fakes, never live providers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event
import base64
import hashlib
import logging

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app, MAX_OPERATOR_TURNS
from procure.demo.errors import DemoError
from procure.demo.operator import OperatorUnavailable, OperatorValidationError
from test_demo_operator import Provider


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def demo(provider=None, voice=None):
    return TestClient(create_app(clock=lambda: NOW, operator_provider=provider or Provider(),
                                 voice_service=voice), base_url='https://demo.test')


def auth(client):
    session = client.get('/api/demo/session').json()
    return {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}


def turn(client, headers, message='Watch copper', key='turn-one', context=1):
    return client.post('/api/demo/operator', headers=headers, json={
        'message': message, 'expected_context_version': context, 'idempotency_key': key})


def confirm(client, headers, proposal, key='confirm-one', context=1):
    return client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': True,
        'expected_context_version': context, 'idempotency_key': key})


def test_operator_overview_and_watch_requires_review_then_applies_once():
    provider = Provider(evidence=())
    with demo(provider) as client:
        headers = auth(client)
        overview = client.get('/api/demo/operator').json()
        assert overview['company_id'] == 'solara-cables' and overview['context_version'] == 1
        assert overview['available'] is True and len(overview['agents']) == 4
        assert overview['watch_items'] == []
        response = turn(client, headers)
        assert response.status_code == 200, response.text
        proposal = response.json()['proposal']
        assert response.json()['status'] == 'proposal' and response.json()['read_only'] is True
        assert proposal['kind'] == 'watch' and proposal['context_version'] == 1
        assert client.get('/api/demo/operator').json()['watch_items'] == []
        denied = client.post('/api/demo/operator/confirm', headers=headers, json={
            'proposal_id': proposal['proposal_id'], 'reviewed': False,
            'expected_context_version': 1, 'idempotency_key': 'no-review'})
        assert denied.status_code == 422
        result = confirm(client, headers, proposal)
        assert result.status_code == 200


def test_csp_allows_generated_mp3_blob_media_without_relaxing_scripts_or_connect():
    with demo(Provider(evidence=())) as client:
        policy = client.get('/api/demo/session').headers['content-security-policy']
        image_policy = next(part for part in policy.split(';') if part.strip().startswith('img-src ')).split()
        assert 'https://www.eia.gov' in image_policy and 'https://www.ecb.europa.eu' in image_policy
        assert 'https:' not in image_policy and '*' not in image_policy
        assert "media-src 'self' blob:" in policy
        assert "script-src 'self'" in policy
        assert "connect-src 'self'" in policy
        assert 'unsafe-inline' not in policy.split('script-src ')[1].split(';')[0]


def test_watch_confirm_replay_and_company_isolation():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        proposal = turn(client, headers).json()['proposal']
        body = {'proposal_id': proposal['proposal_id'], 'reviewed': True,
                'expected_context_version': 1, 'idempotency_key': 'apply-watch'}
        first = client.post('/api/demo/operator/confirm', headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert first.json()['status'] == 'applied'
        assert first.json()['kind'] == 'watch'
        assert first.json()['result']['material_id'] == 'copper'
        second = client.post('/api/demo/operator/confirm', headers=headers, json=body)
        assert second.status_code == 200 and second.json()['result'] == first.json()['result']
        assert len(client.get('/api/demo/operator').json()['watch_items']) == 1
        switched = client.post('/api/demo/company', headers=headers, json={
            'company_id': 'terrapack', 'expected_context_version': 1, 'idempotency_key': 'switch'})
        assert switched.status_code == 200
        assert client.get('/api/demo/operator').json()['watch_items'] == []
        assert confirm(client, headers, proposal, key='foreign', context=2).status_code in (404, 409)
        restored = client.post('/api/demo/company', headers=headers, json={
            'company_id': 'solara-cables', 'expected_context_version': 2, 'idempotency_key': 'return'})
        assert restored.status_code == 200
        assert len(client.get('/api/demo/operator').json()['watch_items']) == 1


def test_old_inflight_model_response_cannot_create_proposal_after_switch():
    entered, release = Event(), Event()
    provider = Provider(evidence=())
    original = provider.__call__

    class SlowProvider:
        def __call__(self, role, prompt):
            if role == 'evidence':
                entered.set()
                assert release.wait(5)
            return original(role, prompt)

    with demo(SlowProvider()) as client:
        headers = auth(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(turn, client, headers)
            assert entered.wait(5)
            switched = client.post('/api/demo/company', headers=headers, json={
                'company_id': 'loomworks', 'expected_context_version': 1,
                'idempotency_key': 'during-turn'})
            assert switched.status_code == 200
            release.set()
            assert future.result(timeout=5).status_code == 409
        assert client.get('/api/demo/operator').json()['watch_items'] == []


@pytest.mark.parametrize('first_finished', ['A', 'B'])
def test_newer_turn_supersedes_older_inflight_proposal_in_both_completion_orders(first_finished):
    entered = {'A': Event(), 'B': Event()}
    release = {'A': Event(), 'B': Event()}
    source = Provider(evidence=())

    class GatedProvider:
        def __call__(self, role, prompt):
            if role == 'evidence':
                label = 'A' if 'User message: "Watch copper A"' in prompt else 'B'
                entered[label].set()
                assert release[label].wait(5)
            return source(role, prompt)

    with demo(GatedProvider()) as client:
        headers = auth(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            older = pool.submit(turn, client, headers, 'Watch copper A', 'turn-A')
            assert entered['A'].wait(5)
            newer = pool.submit(turn, client, headers, 'Watch copper B', 'turn-B')
            assert entered['B'].wait(5)
            release[first_finished].set()
            if first_finished == 'A':
                assert older.result(timeout=5).status_code == 409
            else:
                newer_result = newer.result(timeout=5)
                assert newer_result.status_code == 200, newer_result.text
            release['B' if first_finished == 'A' else 'A'].set()
            older_result = older.result(timeout=5)
            newer_result = newer.result(timeout=5)
        assert older_result.status_code == 409
        assert newer_result.status_code == 200
        proposal = newer_result.json()['proposal']
        assert proposal['kind'] == 'watch'
        assert turn(client, headers, 'Watch copper A', 'turn-A').status_code == 409
        assert confirm(client, headers, proposal, key='apply-newer').status_code == 200


def test_failed_evidence_specialist_returns_unavailable_without_proposal():
    class FailedEvidence(Provider):
        def __call__(self, role, prompt):
            if role == 'evidence':
                raise RuntimeError('specialist unavailable')
            return super().__call__(role, prompt)

    with demo(FailedEvidence()) as client:
        headers = auth(client)
        result = turn(client, headers)
        assert result.status_code == 200
        assert result.json()['status'] == 'unavailable'
        assert result.json()['proposal'] is None
        assert client.get('/api/demo/operator').json()['watch_items'] == []


@pytest.mark.parametrize(('error_type', 'category'), [
    (OperatorUnavailable, 'provider'),
    (OperatorValidationError, 'validation'),
])
def test_operator_form_failure_logs_only_category_and_exception_fingerprint(
        monkeypatch, caplog, error_type, category):
    secret = 'private provider token and HarvestFoods buyer payload'

    def fail_turn(*args, **kwargs):
        raise error_type(secret)

    monkeypatch.setattr('procure.demo.operator.run_turn', fail_turn)
    with demo(Provider()) as client:
        headers = auth(client)
        switched = client.post('/api/demo/company', headers=headers, json={
            'company_id': 'harvest-foods', 'expected_context_version': 1,
            'idempotency_key': 'select-harvest'})
        assert switched.status_code == 200
        with caplog.at_level(logging.WARNING, logger='procure.demo'):
            response = turn(client, headers,
                            'Fill wheat quantity to 4020 tonne for a draft; do not run a plan.',
                            key='harvest-form', context=2)

    assert response.status_code == 200
    result = response.json()
    assert result['status'] == 'unavailable'
    assert result['assistant_text'] == 'The operator could not verify this response; no action was taken.'
    assert result['agent_steps'] == [] and result['proposal'] is None
    fingerprint = hashlib.sha256(secret.encode()).hexdigest()[:12]
    diagnostic = [record for record in caplog.records
                  if record.name == 'procure.demo' and
                  record.getMessage().startswith('operator_turn_unavailable ')]
    assert len(diagnostic) == 1
    assert diagnostic[0].getMessage() == (
        f'operator_turn_unavailable category={category} fingerprint={fingerprint}')
    assert diagnostic[0].exc_info is None
    assert secret not in caplog.text
    assert secret not in response.text


class Voice:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, mime_type, sample_rate_hertz):
        self.calls.append(('transcribe', audio, mime_type, sample_rate_hertz))
        return {'transcript': 'Watch copper', 'provider': 'test_stt',
                'model': 'fixture', 'call': 'mocked'}

    def speak(self, text):
        self.calls.append(('speak', text))
        return {'audio_base64': base64.b64encode(b'ID3fixture').decode(),
                'mime_type': 'audio/mpeg', 'provider': 'test_tts', 'call': 'mocked'}


def test_voice_endpoints_share_context_csrf_idempotency_and_no_auto_operator_turn():
    voice = Voice()
    with demo(Provider(evidence=()), voice) as client:
        headers = auth(client)
        payload = {'audio_base64': base64.b64encode(b'RIFFfixture').decode(),
                   'mime_type': 'audio/wav', 'sample_rate_hertz': 16000,
                   'expected_context_version': 1, 'idempotency_key': 'voice-one'}
        assert client.post('/api/demo/voice/transcribe', json=payload).status_code == 403
        transcribed = client.post('/api/demo/voice/transcribe', headers=headers, json=payload)
        assert transcribed.status_code == 200, transcribed.text
        assert transcribed.json()['transcript'] == 'Watch copper'
        assert transcribed.json()['call'] == 'mocked'
        assert client.post('/api/demo/voice/transcribe', headers=headers, json=payload).status_code == 200
        assert len(voice.calls) == 1
        assert client.get('/api/demo/operator').json()['watch_items'] == []
        spoken = client.post('/api/demo/voice/speak', headers=headers, json={
            'text': 'Watch copper', 'expected_context_version': 1, 'idempotency_key': 'speak-one'})
        assert spoken.status_code == 200 and spoken.json()['mime_type'] == 'audio/mpeg'
        assert len(voice.calls) == 2
        assert client.post('/api/demo/voice/speak', headers=headers, json={
            'text': 'x' * 1201, 'expected_context_version': 1,
            'idempotency_key': 'too-long'}).status_code == 422


def test_voice_adapter_preserves_reviewed_domain_error_but_sanitizes_unexpected_failure():
    class FailingVoice(Voice):
        def transcribe(self, audio, mime_type, sample_rate_hertz):
            raise DemoError('voice_invalid_audio', 'Unsupported recording structure.', 422)

        def speak(self, text):
            raise RuntimeError('private provider token and internal stack detail')

    with demo(Provider(evidence=()), FailingVoice()) as client:
        headers = auth(client)
        invalid = client.post('/api/demo/voice/transcribe', headers=headers, json={
            'audio_base64': base64.b64encode(b'RIFFfixture').decode(),
            'mime_type': 'audio/wav', 'sample_rate_hertz': 16000,
            'expected_context_version': 1, 'idempotency_key': 'domain-failure'})
        assert invalid.status_code == 422
        assert invalid.json() == {'error': {'code': 'voice_invalid_audio',
                                            'message': 'Unsupported recording structure.'}}
        unexpected = client.post('/api/demo/voice/speak', headers=headers, json={
            'text': 'Read this result', 'expected_context_version': 1,
            'idempotency_key': 'unexpected-failure'})
        assert unexpected.status_code == 503
        assert unexpected.json()['error']['code'] == 'voice_unavailable'
        assert 'private provider token' not in unexpected.text


def planned_demo(client, headers):
    offers = {}
    for fixture in ('express-copper', 'eu-copper'):
        extracted = client.post('/api/demo/extract', headers=headers, json={
            'fixture_id': fixture, 'mode': 'rules', 'idempotency_key': 'extract-' + fixture})
        assert extracted.status_code == 200, extracted.text
        offer = extracted.json()['offer']
        saved = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers,
                            json={'expected_version': 1, 'idempotency_key': 'save-' + fixture})
        assert saved.status_code == 200, saved.text
        offers[fixture] = saved.json()['offer']
    request = {'offer_ids': [o['offer_id'] for o in offers.values()],
               'expected_versions': {o['offer_id']: o['version'] for o in offers.values()},
               'material': 'copper', 'quantity_kg': '10000', 'current_stock_kg': '2000',
               'need_by': '2026-11-05', 'decision_date': '2026-10-14',
               'reporting_currency': 'USD', 'budget': '100000',
               'additional_cost_per_kg': '0.25'}
    planned = client.post('/api/demo/plan', headers=headers, json=request)
    assert planned.status_code == 200, planned.text
    return offers, request, planned.json()


def test_recovery_then_second_recovery_and_unsent_rfq_use_authoritative_tools():
    provider = Provider(intent='recovery', evidence=(), action='recovery')
    with demo(provider) as client:
        headers = auth(client)
        offers, _, baseline = planned_demo(client, headers)
        europa = offers['eu-copper']['offer_id']
        provider.offer_id, provider.delay_days = europa, 20
        first_turn = turn(client, headers, 'Compare the selected offer with a 20 day delay', key='delay-turn')
        assert first_turn.status_code == 200, first_turn.text
        first_proposal = first_turn.json()['proposal']
        assert first_proposal['parameters']['preview']['cost_delta'] == '4000'
        assert client.get('/api/demo/session').json()['recovery'] is None
        applied = confirm(client, headers, first_proposal, key='apply-delay')
        assert applied.status_code == 200, applied.text
        comparison = applied.json()['result']
        assert comparison['scenario']['total_cost'] == '86000'
        assert comparison['scenario']['selected_offer_id'] == offers['express-copper']['offer_id']
        provider.delay_days = 0
        next_turn = turn(client, headers, 'Restore Europa with 0 days delay', key='restore-turn')
        assert next_turn.status_code == 200, next_turn.text
        next_proposal = next_turn.json()['proposal']
        assert next_proposal['plan_id'] == baseline['plan_id']
        restored = confirm(client, headers, next_proposal, key='apply-restore')
        assert restored.status_code == 200, restored.text
        assert restored.json()['result']['cost_delta'] == '0'
        provider.intent, provider.action = 'rfq', 'rfq'
        provider.offer_id, provider.delay_days = None, None
        rfq_turn = turn(client, headers, 'Prepare an unsent RFQ', key='rfq-turn')
        assert rfq_turn.status_code == 200, rfq_turn.text
        prepared_rfq = client.get('/api/demo/session').json()['rfq']
        assert prepared_rfq['draft_id'] == rfq_turn.json()['proposal']['proposal_id']
        draft = confirm(client, headers, rfq_turn.json()['proposal'], key='apply-rfq')
        assert draft.status_code == 200, draft.text
        assert draft.json()['result']['status'] == 'draft_unsent'
        assert draft.json()['result'] == prepared_rfq
        assert draft.json()['result']['plan_id'] == restored.json()['result']['scenario']['plan_id']


def test_proposal_rejected_after_company_revisit_and_foreign_session():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        proposal = turn(client, headers).json()['proposal']
        with TestClient(client.app, base_url='https://demo.test') as other:
            foreign_headers = auth(other)
            assert confirm(other, foreign_headers, proposal, key='foreign').status_code == 404
        assert client.post('/api/demo/company', headers=headers, json={
            'company_id': 'terrapack', 'expected_context_version': 1,
            'idempotency_key': 'away'}).status_code == 200
        assert client.post('/api/demo/company', headers=headers, json={
            'company_id': 'solara-cables', 'expected_context_version': 2,
            'idempotency_key': 'back'}).status_code == 200
        assert confirm(client, headers, proposal, key='old-proposal', context=3).status_code == 404


def test_turn_and_confirmation_retries_reject_changed_workspace_state():
    with demo(Provider(evidence=())) as client:
        headers = auth(client)
        proposal = turn(client, headers, key='watch-turn').json()['proposal']
        first = confirm(client, headers, proposal, key='watch-confirm')
        assert first.status_code == 200
        extracted = client.post('/api/demo/extract', headers=headers, json={
            'fixture_id': 'express-copper', 'mode': 'rules',
            'idempotency_key': 'changed-workspace'})
        assert extracted.status_code == 200
        assert turn(client, headers, key='watch-turn').status_code == 409
        assert confirm(client, headers, proposal, key='watch-confirm').status_code == 409


def test_expired_proposal_cannot_be_confirmed():
    current = [NOW]
    with TestClient(create_app(clock=lambda: current[0], operator_provider=Provider(evidence=())),
                    base_url='https://demo.test') as client:
        headers = auth(client)
        proposal = turn(client, headers).json()['proposal']
        current[0] = NOW.replace(minute=6)
        response = confirm(client, headers, proposal)
        assert response.status_code == 409
        assert client.get('/api/demo/operator').json()['watch_items'] == []


def test_operator_turn_budget_and_stale_news_evidence():
    provider = Provider(intent='inspect', action='none', evidence=())
    current = [NOW]
    app = create_app(clock=lambda: current[0], operator_provider=provider)
    app.state.sessions.ttl_seconds = 7200
    with TestClient(app, base_url='https://demo.test') as client:
        headers = auth(client)
        for index in range(30):
            response = turn(client, headers, message='Inspect inventory', key=f'inspect-{index}')
            assert response.status_code == 200 and response.json()['status'] == 'completed'
        assert len(provider.calls) == 90
        hourly = turn(client, headers, message='Inspect inventory', key='hourly-over')
        assert hourly.status_code == 429
        assert 'within one hour' in hourly.json()['error']['message']
        current[0] += timedelta(hours=1, seconds=1)
        for index in range(30, MAX_OPERATOR_TURNS):
            response = turn(client, headers, message='Inspect inventory', key=f'inspect-{index}')
            assert response.status_code == 200 and response.json()['status'] == 'completed'
        assert len(provider.calls) == 3 * MAX_OPERATOR_TURNS
        session_cap = turn(client, headers, message='Inspect inventory', key='session-over')
        assert session_cap.status_code == 429
        assert '36 operator turns' in session_cap.json()['error']['message']


def test_voice_transcription_rejects_late_company_response():
    entered, release = Event(), Event()
    class SlowVoice(Voice):
        def transcribe(self, audio, mime_type, sample_rate_hertz):
            entered.set()
            assert release.wait(5)
            return super().transcribe(audio, mime_type, sample_rate_hertz)
    with demo(Provider(evidence=()), SlowVoice()) as client:
        headers = auth(client)
        payload = {'audio_base64': base64.b64encode(b'RIFFfixture').decode(),
                   'mime_type': 'audio/wav', 'sample_rate_hertz': 16000,
                   'expected_context_version': 1, 'idempotency_key': 'slow-audio'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, '/api/demo/voice/transcribe',
                                 headers=headers, json=payload)
            assert entered.wait(5)
            assert client.post('/api/demo/company', headers=headers, json={
                'company_id': 'terrapack', 'expected_context_version': 1,
                'idempotency_key': 'voice-switch'}).status_code == 200
            release.set()
            assert future.result(timeout=5).status_code == 409


def test_speech_output_within_service_cap_is_allowed_and_not_retained_in_session():
    class LargeVoice(Voice):
        def speak(self, text):
            self.calls.append(('speak', text))
            return {'audio_base64': base64.b64encode(b'ID3' + b'a' * 600_000).decode(),
                    'mime_type': 'audio/mpeg', 'provider': 'test_tts', 'call': 'mocked'}
    with demo(Provider(evidence=()), LargeVoice()) as client:
        headers = auth(client)
        payload = {'text': 'A bounded answer', 'expected_context_version': 1,
                   'idempotency_key': 'large-speech'}
        response = client.post('/api/demo/voice/speak', headers=headers, json=payload)
        assert response.status_code == 200, response.text[:300]
        session_id = client.get('/api/demo/session').json()['session_id']
        cached = client.app.state.sessions.get(session_id).idempotency['large-speech']['result']
        assert 'audio_base64' not in cached
        assert client.post('/api/demo/voice/speak', headers=headers, json=payload).status_code == 409
