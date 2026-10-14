"""Hosted API acceptance checks use real sessions/quotes; Gemini never called."""
import base64
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest
from procure.demo.app import create_app, MAX_REQUESTS, MAX_OFFERS


@pytest.fixture
def client(tmp_path):
    (tmp_path / 'index.html').write_text('<html>Public demo</html>')
    with TestClient(create_app(web_dir=tmp_path, clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc)), base_url='https://demo.test') as client:
        yield client


def auth(client):
    session = client.get('/api/demo/session').json()
    return {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}


def test_public_spa_and_secure_session_do_not_require_machine_auth(client):
    assert client.get('/demo').status_code == 200
    response = client.get('/api/demo/session')
    assert response.status_code == 200
    assert 'Secure' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=lax' in response.headers['set-cookie']
    assert response.json()['persistence'] == 'ephemeral'
    assert response.json()['offers'] == []
    assert client.get('/api/demo/session').json()['session_id'] == response.json()['session_id']


def test_root_redirects_to_public_demo_instead_of_private_planning(client):
    response = client.get('/', follow_redirects=False)
    assert response.status_code == 307
    assert response.headers['location'] == '/demo'


def test_cached_provenance_persists_in_session_and_export(client):
    headers = auth(client)
    first = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': 'express-copper', 'mode': 'rules', 'idempotency_key': 'original'})
    assert first.status_code == 200
    assert first.json()['offer']['served_from_cache'] is False
    cached = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': 'express-copper', 'mode': 'rules', 'idempotency_key': 'cached'})
    offer = cached.json()['offer']
    assert offer['served_from_cache'] is True
    saved = client.post(f"/api/demo/offers/{offer['offer_id']}/save", headers=headers,
                        json={'expected_version': 1, 'idempotency_key': 'cached-save'})
    assert saved.status_code == 200
    assert saved.json()['offer']['served_from_cache'] is True
    assert client.get('/api/demo/session').json()['offers'][-1]['served_from_cache'] is True
    assert client.get('/api/demo/export').json()['offers'][-1]['served_from_cache'] is True


@pytest.mark.parametrize('headers', [{}, {'Origin': 'https://evil.test'}, {'Origin': 'null'}])
def test_mutation_requires_csrf_and_same_origin(client, headers):
    auth(client)
    assert client.post('/api/demo/reset', headers=headers, json={}).status_code == 403


def test_wrong_origin_is_rejected_even_with_correct_csrf(client):
    headers = auth(client)
    headers['Origin'] = 'https://demo.test.evil.test'
    assert client.post('/api/demo/reset', headers=headers, json={}).status_code == 403


def test_reset_rotates_cookie_and_csrf_and_rejects_stale_request(client):
    headers = auth(client)
    old = client.get('/api/demo/session').json()
    response = client.post('/api/demo/reset', headers=headers, json={})
    assert response.status_code == 200
    assert response.json()['session_id'] != old['session_id']
    assert client.post('/api/demo/reset', headers=headers, json={}).status_code == 403


def test_body_bound_and_nonfinite_json_rejected(client):
    headers = auth(client)
    assert client.post('/api/demo/extract', headers=headers, content=b'x' * 800_000).status_code == 413
    headers['Content-Type'] = 'application/json'
    response = client.post('/api/demo/extract', headers=headers, content='{"fixture_id":NaN}')
    assert response.status_code == 422


def test_foreign_offer_is_not_visible_and_upload_allowlist_is_enforced(client):
    headers = auth(client)
    response = client.post('/api/demo/offers/not-owned/save', headers=headers,
                           json={'expected_version': 1, 'idempotency_key': 'one'})
    assert response.status_code == 404
    response = client.post('/api/demo/extract', headers=headers, json={
        'filename': '../../secret.pdf', 'document_base64': base64.b64encode(b'private').decode(),
        'mode': 'rules', 'idempotency_key': 'bad-file'})
    assert response.status_code == 422


def test_expiry_invalidates_mutation_and_old_state(tmp_path):
    now = [datetime(2026, 10, 14, tzinfo=timezone.utc)]
    with TestClient(create_app(clock=lambda: now[0]), base_url='https://demo.test') as client:
        headers = auth(client)
        old_id = client.get('/api/demo/session').json()['session_id']
        now[0] = datetime(2026, 10, 14, 2, tzinfo=timezone.utc)
        assert client.post('/api/demo/reset', headers=headers, json={}).status_code == 401
        assert client.get('/api/demo/session').json()['session_id'] != old_id


def test_local_test_cookie_mode_cannot_authorize_a_public_host():
    with TestClient(create_app(secure_cookies=False), base_url='http://public.example') as client:
        token = client.get('/api/demo/session').json()['csrf_token']
        response = client.post('/api/demo/reset', json={}, headers={
            'Origin': 'http://public.example', 'X-CSRF-Token': token})
        assert response.status_code == 403


def test_referer_fallback_and_port_mismatch(client):
    headers = auth(client)
    del headers['Origin']
    headers['Referer'] = 'https://demo.test:444/demo'
    assert client.post('/api/demo/reset', json={}, headers=headers).status_code == 403
    headers['Referer'] = 'https://demo.test/demo'
    assert client.post('/api/demo/reset', json={}, headers=headers).status_code == 200


def test_session_request_budget_rejects_further_work(client):
    auth(client)
    for _ in range(MAX_REQUESTS + 1):
        response = client.get('/api/demo/offers')
    assert response.status_code == 429


def test_malformed_correction_field_does_not_reach_domain_code(client):
    headers = auth(client)
    response = client.post('/api/demo/offers/unknown/correct', headers=headers, json={
        'field': {'unexpected': 'object'}, 'value': 'USD', 'expected_version': 1,
        'idempotency_key': 'malformed'})
    assert response.status_code in (404, 422)


def test_non_extraction_request_size_is_bounded(client):
    response = client.post('/api/demo/plan', headers=auth(client), json={'large': 'x' * 20000})
    assert response.status_code == 413


def test_nonfinite_exponent_json_is_rejected(client):
    headers = auth(client)
    headers['Content-Type'] = 'application/json'
    response = client.post('/api/demo/plan', headers=headers, content='{"budget":1e9999}')
    assert response.status_code == 422


def extract_rules(client, headers, fixture='late-copper', key='extract-one'):
    response = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': fixture, 'mode': 'rules', 'idempotency_key': key})
    assert response.status_code == 200, response.text
    return response.json()


def test_real_quote_retry_conflict_correction_save_stale_and_export(client):
    headers = auth(client)
    first = extract_rules(client, headers)
    offer_id = first['offer']['offer_id']
    assert first['offer']['fields']['settlement_currency']['value'] is None
    assert first['served_from_cache'] is False
    retry = extract_rules(client, headers)
    assert retry['offer']['offer_id'] == offer_id and retry['replayed'] is True
    assert len(client.get('/api/demo/offers').json()['offers']) == 1
    conflict = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': 'express-copper', 'mode': 'rules', 'idempotency_key': 'extract-one'})
    assert conflict.status_code == 409
    blocked = client.post(f'/api/demo/offers/{offer_id}/save', headers=headers, json={
        'expected_version': 1, 'idempotency_key': 'save-blocked'})
    assert blocked.status_code == 409
    correction = {'field': 'settlement_currency', 'value': 'USD',
                  'expected_version': 1, 'idempotency_key': 'correct-one'}
    changed = client.post(f'/api/demo/offers/{offer_id}/correct', headers=headers, json=correction)
    assert changed.status_code == 200, changed.text
    assert changed.json()['offer']['version'] == 2
    assert changed.json()['offer']['fields']['settlement_currency']['source'] == 'user'
    assert changed.json()['offer']['fields']['settlement_currency']['evidence'] == []
    repeated = client.post(f'/api/demo/offers/{offer_id}/correct', headers=headers, json=correction)
    assert repeated.json()['replayed'] is True
    stale = client.post(f'/api/demo/offers/{offer_id}/save', headers=headers, json={
        'expected_version': 1, 'idempotency_key': 'save-stale'})
    assert stale.status_code == 409
    saved = client.post(f'/api/demo/offers/{offer_id}/save', headers=headers, json={
        'expected_version': 2, 'idempotency_key': 'save-current'})
    assert saved.status_code == 200, saved.text
    assert saved.json()['offer']['version'] == 3
    exported = client.get('/api/demo/export')
    assert 'attachment' in exported.headers['content-disposition']
    assert exported.json()['offers'][0]['version'] == 3
    assert exported.json()['synthetic'] is True


def test_cache_shares_only_immutable_extraction_not_mutable_offers(client):
    first_headers = auth(client)
    original = extract_rules(client, first_headers)['offer']
    with TestClient(client.app, base_url='https://demo.test') as other:
        other_headers = auth(other)
        assert other.get('/api/demo/offers').json()['offers'] == []
        assert other.get('/api/demo/export').json()['offers'] == []
        for action in ('save', 'correct'):
            payload = {'expected_version': 1, 'idempotency_key': f'foreign-{action}'}
            if action == 'correct':
                payload.update(field='settlement_currency', value='USD')
            assert other.post(f"/api/demo/offers/{original['offer_id']}/{action}", headers=other_headers,
                              json=payload).status_code == 404
        cached = extract_rules(other, other_headers)
        assert cached['served_from_cache'] is True
        assert cached['offer']['offer_id'] != original['offer_id']
        response = other.post(f"/api/demo/offers/{cached['offer']['offer_id']}/correct", headers=other_headers,
                              json={'field': 'settlement_currency', 'value': 'USD',
                                    'expected_version': 1, 'idempotency_key': 'my-correction'})
        assert response.status_code == 200
        assert client.get('/api/demo/offers').json()['offers'][0]['fields']['settlement_currency']['value'] is None
        assert client.post('/api/demo/reset', headers=first_headers, json={}).status_code == 200
        assert len(other.get('/api/demo/offers').json()['offers']) == 1


def test_upload_exact_fixture_allowed_but_one_byte_change_refused(client):
    from procure.demo.quote_service import fixture_bytes
    headers = auth(client)
    filename, raw = fixture_bytes('eu-copper')
    payload = {'filename': filename, 'document_base64': base64.b64encode(raw).decode(),
               'mode': 'rules', 'idempotency_key': 'upload-one'}
    response = client.post('/api/demo/extract', headers=headers, json=payload)
    assert response.status_code == 200, response.text
    payload.update(document_base64=base64.b64encode(raw + b' ').decode(), idempotency_key='tampered')
    assert client.post('/api/demo/extract', headers=headers, json=payload).status_code == 422


def plan_payload(offer_id, version):
    return {'offer_ids': [offer_id], 'expected_versions': {offer_id: version}, 'material': 'copper',
            'quantity_kg': '8000', 'current_stock_kg': '0', 'need_by': '2026-11-05',
            'decision_date': '2026-10-14', 'reporting_currency': 'USD', 'budget': '100000',
            'additional_cost_per_kg': '0'}


def test_plan_ownership_exact_versions_and_plan_invalidation(client):
    headers = auth(client)
    offer = extract_rules(client, headers, fixture='express-copper')['offer']
    offer_id = offer['offer_id']
    saved = client.post(f'/api/demo/offers/{offer_id}/save', headers=headers,
                        json={'expected_version': 1, 'idempotency_key': 'save-one'})
    assert saved.status_code == 200, saved.text
    assert client.post('/api/demo/plan', headers=headers, json=plan_payload(offer_id, 1)).status_code == 409
    with TestClient(client.app, base_url='https://demo.test') as other:
        assert other.post('/api/demo/plan', headers=auth(other), json=plan_payload(offer_id, 2)).status_code == 404
    plan = client.post('/api/demo/plan', headers=headers, json=plan_payload(offer_id, 2))
    assert plan.status_code == 200, plan.text
    assert plan.json()['selected_offer_id'] == offer_id
    assert client.get('/api/demo/export').json()['plan']['plan_id'] == plan.json()['plan_id']
    # A correction to another offer changes the session's decision evidence and clears its plan.
    draft = extract_rules(client, headers, fixture='late-copper', key='another-extract')['offer']
    assert client.post(f"/api/demo/offers/{draft['offer_id']}/correct", headers=headers, json={
        'field': 'settlement_currency', 'value': 'USD', 'expected_version': 1,
        'idempotency_key': 'another-correct'}).status_code == 200
    assert client.get('/api/demo/export').json()['plan'] is None


def test_offer_limit_rejects_without_unbounded_session_growth(client):
    headers = auth(client)
    for i in range(MAX_OFFERS):
        extract_rules(client, headers, fixture='eu-copper', key=f'offer-{i}')
    response = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': 'eu-copper', 'mode': 'rules', 'idempotency_key': 'over-capacity'})
    assert response.status_code == 429
    assert len(client.get('/api/demo/offers').json()['offers']) == MAX_OFFERS


def test_provider_exception_is_redacted_and_failed_call_budget_is_bounded(client, monkeypatch):
    from procure.offers import extract_gemini
    monkeypatch.setenv('OFFLINE', '0')
    def failed_provider(*args, **kwargs):
        raise RuntimeError('secret-token /private/credentials.json')
    monkeypatch.setattr(extract_gemini, 'extract', failed_provider)
    headers = auth(client)
    for i in range(6):
        response = client.post('/api/demo/extract', headers=headers, json={
            'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': f'failed-{i}'})
        assert response.status_code == 503
        assert 'secret-token' not in response.text and '/private/' not in response.text
    seventh = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': 'over-budget'})
    assert seventh.status_code == 429
    assert client.get('/api/demo/offers').json()['offers'] == []
    # A failed provider call must release its capacity slot for explicitly selected rules.
    assert extract_rules(client, headers, fixture='eu-copper')['offer']['extractor'] == 'rules'


def test_gemini_fallback_is_visible_and_never_cached_as_live(client, monkeypatch):
    from procure.offers import extract_gemini
    monkeypatch.setenv('OFFLINE', '0')
    def unavailable(*args, **kwargs):
        raise extract_gemini.GeminiUnavailable('offline')
    monkeypatch.setattr(extract_gemini, 'extract', unavailable)
    headers = auth(client)
    response = client.post('/api/demo/extract', headers=headers, json={
        'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': 'fallback'})
    assert response.status_code == 503
    assert client.get('/api/demo/offers').json()['offers'] == []
    assert extract_rules(client, headers, fixture='eu-copper')['served_from_cache'] is False


def test_reset_during_provider_call_refuses_stale_result_and_keeps_event_loop_responsive(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from procure.offers import extract_gemini, extract_rules as rules
    monkeypatch.setenv('OFFLINE', '0')
    entered, release = Event(), Event()
    def simulated_provider(doc, client=None):
        entered.set()
        if not release.wait(5):
            raise RuntimeError('test provider timed out')
        # Deliberately simulates the SDK real-call receipt; this test is not live-provider evidence.
        return rules.extract(doc), {'provider': 'test-simulation', 'model': 'mocked', 'call': 'real'}
    monkeypatch.setattr(extract_gemini, 'extract', simulated_provider)
    headers = auth(client)
    payload = {'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': 'inflight'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, '/api/demo/extract', headers=headers, json=payload)
        try:
            assert entered.wait(3)
            assert client.get('/-/health').status_code == 200
            assert client.post('/api/demo/extract', headers=headers, json=payload).status_code == 409
            assert client.post('/api/demo/reset', headers=headers, json={}).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    assert client.get('/api/demo/offers').json()['offers'] == []


def test_simultaneous_provider_work_has_a_hard_concurrency_bound(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    from procure.offers import extract_gemini
    monkeypatch.setenv('OFFLINE', '0')
    entered, release, lock = Event(), Event(), Lock()
    started = 0
    def simulated_provider(*args, **kwargs):
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                entered.set()
        release.wait(5)
        raise extract_gemini.GeminiUnavailable('offline')
    monkeypatch.setattr(extract_gemini, 'extract', simulated_provider)
    headers = auth(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(client.post, '/api/demo/extract', headers=headers, json={
            'fixture_id': fixture, 'mode': 'gemini', 'idempotency_key': fixture})
                   for fixture in ('late-copper', 'express-copper')]
        try:
            assert entered.wait(3)
            refused = client.post('/api/demo/extract', headers=headers, json={
                'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': 'third'})
            assert refused.status_code == 429
        finally:
            release.set()
        assert all(result.result(timeout=5).status_code == 503 for result in pending)


def test_failed_operation_keeps_retry_key_bound_to_action_and_payload(client):
    headers = auth(client)
    offer_id = extract_rules(client, headers)['offer']['offer_id']
    failed = client.post(f'/api/demo/offers/{offer_id}/save', headers=headers, json={
        'expected_version': 1, 'idempotency_key': 'bound-key'})
    assert failed.status_code == 409
    conflicting = client.post(f'/api/demo/offers/{offer_id}/correct', headers=headers, json={
        'field': 'settlement_currency', 'value': 'USD', 'expected_version': 1,
        'idempotency_key': 'bound-key'})
    assert conflicting.status_code == 409
    assert conflicting.json()['error']['code'] == 'idempotency_conflict'


def test_provider_status_uses_actual_configured_model(client, monkeypatch):
    monkeypatch.setenv('GEMINI_MODEL', 'unused-environment-override')
    from procure.settings import assumptions
    assert client.get('/api/demo/session').json()['provider']['model'] == assumptions()['news']['gemini_model']


def test_reset_cannot_bypass_process_provider_budget(client, monkeypatch):
    from procure.offers import extract_gemini
    monkeypatch.setenv('OFFLINE', '0')
    def unavailable(*args, **kwargs):
        raise extract_gemini.GeminiUnavailable('offline')
    monkeypatch.setattr(extract_gemini, 'extract', unavailable)
    for session_number in range(10):
        headers = auth(client)
        for call in range(6):
            response = client.post('/api/demo/extract', headers=headers, json={
                'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': f'call-{call}'})
            assert response.status_code == 503
        assert client.post('/api/demo/reset', headers=headers, json={}).status_code == 200
    response = client.post('/api/demo/extract', headers=auth(client), json={
        'fixture_id': 'eu-copper', 'mode': 'gemini', 'idempotency_key': 'process-limit'})
    assert response.status_code == 429
