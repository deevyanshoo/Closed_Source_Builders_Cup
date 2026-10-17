"""Consent and history integration: public research never runs on a chat request."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app


class Research:
    def __init__(self):
        self.calls = []

    def research(self, query):
        self.calls.append(query)
        return {'status': 'completed', 'answer': 'The reviewed sources describe shipping risk; a complete closure is not established.',
                'sources': [{'source_id': 'source-1', 'title': 'Shipping advisory',
                             'url': 'https://www.imo.org/en/MediaCentre/Pages/Default.aspx'}],
                'search_suggestions_html': None,
                'checked_at': '2026-10-15T00:00:00+00:00'}


def setup(provider=None, clock=None, **kwargs):
    research = provider or Research()
    app = create_app(research_provider=research, clock=clock, **kwargs)
    client = TestClient(app, base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
    return client, headers, research, app


def propose(client, headers, key='research-turn'):
    return client.post('/api/demo/operator', headers=headers, json={
        'message': 'What is the latest Hormuz blockage impact on petrochemicals?',
        'expected_context_version': 1, 'idempotency_key': key})


def approve(client, headers, proposal, key='research-approval', reviewed=True, version=1):
    return client.post('/api/demo/operator/confirm', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'reviewed': reviewed,
        'expected_context_version': version, 'idempotency_key': key})


def test_public_question_prepares_exact_consent_before_search_and_replay_is_read_only():
    client, headers, provider, _ = setup()
    response = propose(client, headers)
    assert response.status_code == 200, response.text
    proposal = response.json()['proposal']
    assert proposal['kind'] == 'research'
    assert 'Hormuz' in proposal['parameters']['query']
    assert proposal['parameters']['destination'] == 'Google Search via Vertex AI'
    assert provider.calls == []
    assert approve(client, headers, proposal, reviewed=False).status_code == 422
    assert provider.calls == []
    accepted = approve(client, headers, proposal)
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()['result']
    assert result['status'] == 'completed' and result['sources'][0]['url'].startswith('https://')
    assert len(provider.calls) == 1
    replay = approve(client, headers, proposal)
    assert replay.status_code == 200 and replay.json()['result'] == result
    assert len(provider.calls) == 1
    assert approve(client, headers, proposal, key='second-approval').status_code in (404, 409)
    assert len(provider.calls) == 1


def test_cancelled_or_superseded_research_cannot_search():
    client, headers, provider, _ = setup()
    proposal = propose(client, headers).json()['proposal']
    cancelled = client.post('/api/demo/operator/cancel', headers=headers, json={
        'proposal_id': proposal['proposal_id'], 'expected_context_version': 1,
        'idempotency_key': 'cancel-research'})
    assert cancelled.status_code == 200
    assert approve(client, headers, proposal).status_code in (404, 409)
    newer = propose(client, headers, 'new-turn').json()['proposal']
    latest = propose(client, headers, 'latest-turn').json()['proposal']
    assert newer['proposal_id'] != latest['proposal_id']
    assert approve(client, headers, newer).status_code in (404, 409)
    assert provider.calls == []


def test_company_change_and_expiry_invalidate_research():
    now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
    client, headers, provider, _ = setup(clock=lambda: now[0])
    proposal = propose(client, headers).json()['proposal']
    now[0] += timedelta(minutes=6)
    assert approve(client, headers, proposal).status_code == 409
    assert provider.calls == []
    current = propose(client, headers, 'new-time').json()['proposal']
    switched = client.post('/api/demo/company', headers=headers, json={
        'company_id': 'terrapack', 'expected_context_version': 1,
        'idempotency_key': 'switch-research'})
    assert switched.status_code == 200
    assert approve(client, headers, current, version=2).status_code in (404, 409)
    assert provider.calls == []


def test_research_and_voice_history_are_scoped_and_idempotent():
    client, headers, _, _ = setup()
    proposal = propose(client, headers).json()['proposal']
    assert approve(client, headers, proposal).status_code == 200
    history = client.get('/api/demo/operator/history').json()
    assert history['company_id'] == 'solara-cables'
    assert any('Hormuz' in (item.get('question') or '') for item in history['exchanges'])
    assert any(item.get('research') or 'closure' in (item.get('answer') or '') for item in history['exchanges'])
    voice = {'text': 'Please explain this shipping risk.', 'speaker': 'input',
             'channel': 'voice', 'expected_context_version': 1, 'idempotency_key': 'voice-final-1'}
    first = client.post('/api/demo/operator/history', headers=headers, json=voice)
    second = client.post('/api/demo/operator/history', headers=headers, json=voice)
    assert first.status_code == second.status_code == 200
    history = client.get('/api/demo/operator/history').json()['exchanges']
    assert sum(item.get('question') == voice['text'] for item in history) == 1
    other = TestClient(create_app(), base_url='https://demo.test')
    assert other.get('/api/demo/operator/history').status_code == 401
    switched = client.post('/api/demo/company', headers=headers, json={
        'company_id': 'terrapack', 'expected_context_version': 1, 'idempotency_key': 'switch-history'})
    assert switched.status_code == 200
    assert client.get('/api/demo/operator/history').json()['exchanges'] == []


def test_provider_failure_is_truthful_and_approval_cannot_repeat_attempt():
    class Down(Research):
        def research(self, query):
            self.calls.append(query)
            raise RuntimeError('provider private diagnostic')

    client, headers, provider, _ = setup(provider=Down())
    proposal = propose(client, headers).json()['proposal']
    response = approve(client, headers, proposal)
    assert response.status_code == 200, response.text
    assert response.json()['result']['status'] == 'unavailable'
    assert 'private diagnostic' not in response.text
    assert approve(client, headers, proposal).status_code == 200
    assert len(provider.calls) == 1


def test_history_and_accepted_research_survive_a_new_application_instance():
    from procure.demo.session_persistence import MemorySessionPersistence
    backend = MemorySessionPersistence()
    client, headers, provider, _ = setup(session_backend=backend)
    proposal = propose(client, headers).json()['proposal']
    accepted = approve(client, headers, proposal).json()
    session_id = accepted['session']['session_id']
    restored = TestClient(create_app(session_backend=backend, research_provider=provider), base_url='https://demo.test')
    restored.cookies.set('demo_session', session_id)
    session = restored.get('/api/demo/session').json()
    assert session['session_id'] == session_id and session['persistence'] == 'firestore'
    assert session['csrf_token'] == headers['X-CSRF-Token']
    history = restored.get('/api/demo/operator/history').json()['exchanges']
    assert any(item.get('research', {}).get('research_id') == accepted['result']['research_id'] for item in history)
    replay = approve(restored, headers, proposal)
    assert replay.status_code == 200 and len(provider.calls) == 1


def test_restored_pending_consent_requires_the_same_reviewed_proposal_and_claims_once():
    from procure.demo.session_persistence import MemorySessionPersistence
    backend = MemorySessionPersistence()
    client, headers, provider, _ = setup(session_backend=backend)
    response = propose(client, headers)
    proposal = response.json()['proposal']
    session_id = client.get('/api/demo/session').json()['session_id']
    restored = TestClient(create_app(session_backend=backend, research_provider=provider), base_url='https://demo.test')
    restored.cookies.set('demo_session', session_id)
    accepted = approve(restored, headers, proposal)
    assert accepted.status_code == 200, accepted.text
    assert len(provider.calls) == 1
    assert approve(restored, headers, proposal).status_code == 200
    assert len(provider.calls) == 1


def test_storage_failure_before_claim_prevents_external_search():
    from procure.demo.session_persistence import MemorySessionPersistence
    backend = MemorySessionPersistence()
    client, headers, provider, _ = setup(session_backend=backend)
    proposal = propose(client, headers).json()['proposal']
    backend.unavailable = True
    failed = approve(client, headers, proposal)
    assert failed.status_code == 503, failed.text
    assert provider.calls == []


def test_search_suggestions_are_initial_display_only_and_never_stored_or_replayed():
    import json
    from procure.demo.session_persistence import MemorySessionPersistence

    class Suggestions(Research):
        def research(self, query):
            result = super().research(query)
            result['search_suggestions_html'] = '<div>owned-transient-suggestions-sentinel</div>'
            return result

    backend = MemorySessionPersistence()
    client, headers, provider, _ = setup(provider=Suggestions(), session_backend=backend)
    proposal = propose(client, headers).json()['proposal']
    initial = approve(client, headers, proposal).json()
    assert 'owned-transient-suggestions-sentinel' in initial['result']['search_suggestions_html']
    sid = initial['session']['session_id']
    stored = backend.load(sid)[0]
    assert 'owned-transient-suggestions-sentinel' not in json.dumps(stored)
    assert 'owned-transient-suggestions-sentinel' not in client.get('/api/demo/operator/history').text
    replay = approve(client, headers, proposal)
    assert replay.status_code == 200 and replay.json()['result']['search_suggestions_html'] is None
    assert replay.json()['result']['sources'] == initial['result']['sources']
    assert len(provider.calls) == 1


def test_a_completed_voice_caption_is_bounded_but_not_truncated():
    client, headers, _, _ = setup()
    text = 'spoken content ' * 1000
    body = {'text': text, 'speaker': 'output', 'channel': 'voice',
            'expected_context_version': 1, 'idempotency_key': 'long-voice'}
    assert client.post('/api/demo/operator/history', headers=headers, json=body).status_code == 200
    assert client.get('/api/demo/operator/history').json()['exchanges'][-1]['answer'] == text.strip()
    body.update(text='x' * 16001, idempotency_key='too-long-voice')
    assert client.post('/api/demo/operator/history', headers=headers, json=body).status_code == 422


def test_finalized_non_ascii_voice_caption_uses_the_text_limit_not_a_smaller_byte_limit():
    client, headers, _, _ = setup()
    text = 'हिंदी ' * 2000
    body = {'text': text, 'speaker': 'output', 'channel': 'voice',
            'expected_context_version': 1, 'idempotency_key': 'unicode-voice'}
    response = client.post('/api/demo/operator/history', headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert client.get('/api/demo/operator/history').json()['exchanges'][-1]['answer'] == text.strip()


def test_a_durable_inflight_claim_cannot_repeat_after_process_recovery():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from procure.demo.session_persistence import MemorySessionPersistence
    entered, release = Event(), Event()

    class Slow(Research):
        def research(self, query):
            self.calls.append(query)
            entered.set()
            assert release.wait(5)
            result = Research().research(query)
            return result

    backend, provider = MemorySessionPersistence(), Slow()
    client, headers, _, _ = setup(provider=provider, session_backend=backend)
    proposal = propose(client, headers).json()['proposal']
    sid = client.get('/api/demo/session').json()['session_id']
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(approve, client, headers, proposal)
        assert entered.wait(5)
        try:
            restored = TestClient(create_app(session_backend=backend, research_provider=provider), base_url='https://demo.test')
            restored.cookies.set('demo_session', sid)
            assert approve(restored, headers, proposal).status_code == 409
            assert approve(restored, headers, proposal, key='different-retry').status_code in (404, 409)
            assert len(provider.calls) == 1
        finally:
            release.set()
        # A concurrent recovering process changed the document: never overwrite it.
        assert pending.result(timeout=5).status_code == 409
    assert len(provider.calls) == 1


def test_another_approved_proposal_cannot_search_while_this_session_is_researching():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from procure.demo.session_persistence import MemorySessionPersistence
    entered, release = Event(), Event()

    class Slow(Research):
        def research(self, query):
            self.calls.append(query)
            entered.set()
            assert release.wait(5)
            return Research().research(query)

    provider = Slow()
    client, headers, _, _ = setup(provider=provider, session_backend=MemorySessionPersistence())
    proposal = propose(client, headers).json()['proposal']
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(approve, client, headers, proposal)
        assert entered.wait(5)
        try:
            second = propose(client, headers, key='second-public-question').json()['proposal']
            blocked = approve(client, headers, second, key='second-public-approval')
            assert blocked.status_code == 429, blocked.text
            assert blocked.json()['error']['code'] == 'research_busy'
            assert len(provider.calls) == 1
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200


@pytest.mark.parametrize('public', [True, False])
def test_voice_prepares_public_consent_and_reads_approved_sources_without_action_authority(monkeypatch, public):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace as NS
    from procure.demo import live_voice
    from test_demo_live_voice import Provider as LiveProvider
    monkeypatch.setattr(live_voice, '_PROCESS_STARTS', 0)

    class PublicVoice(LiveProvider):
        def __init__(self):
            super().__init__()
            self.stage = 0

        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                name = 'prepare_research' if self.stage == 0 else 'read_research'
                args = ({'message': 'What is the current Hormuz shipping situation?' if public else
                         'Fill the polymer buyer form with quantity 12000'} if self.stage == 0 else {})
                self.stage += 1
                await self.events.put(NS(tool_call=NS(function_calls=[NS(
                    name=name, args=args, id=f'voice-research-{self.stage}')]), server_content=None))

    voice, research = PublicVoice(), Research()

    @asynccontextmanager
    async def factory():
        yield voice

    app = create_app(research_provider=research, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'origin': 'https://demo.test'}) as socket:
            socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'], 'expected_context_version': 1})
            assert socket.receive_json()['type'] == 'ready'

            def next_event(kind):
                for _ in range(30):
                    event = socket.receive_json()
                    assert event['type'] != 'error', event
                    if event['type'] == kind:
                        return event
                raise AssertionError('Expected voice event was not returned')

            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            prepared = next_event('tool_result')['result']
            next_event('turn_complete')
            assert research.calls == [] and prepared['read_only'] is True
            if public:
                assert prepared['proposal']['kind'] == 'research'
                accepted = approve(client, headers, prepared['proposal'])
                assert accepted.status_code == 200, accepted.text
                socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
                read = next_event('tool_result')['result']
                next_event('turn_complete')
                assert read['workspace']['research']['sources'] == accepted.json()['result']['sources']
                assert read['workspace']['research']['research_id'] == accepted.json()['result']['research_id']
                assert len(research.calls) == 1
                assert session['csrf_token'] not in str(voice.tool_response.response)
            else:
                assert prepared['status'] == 'needs_clarification'
                assert prepared['proposal'] is None
                assert client.get('/api/demo/session').json()['buyer_draft'] is None
            socket.send_json({'type': 'stop'})
