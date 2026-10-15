"""Durable anonymous sessions recover only their own bounded workspace state."""
from datetime import datetime, timedelta, timezone
import base64
from concurrent.futures import ThreadPoolExecutor
import secrets
import threading
import zlib

import pytest

from procure.demo.sessions import SessionError, SessionStore
from procure.demo.session_persistence import (FirestoreSessionPersistence, MemorySessionPersistence,
                                             StorageConflict, StorageInvalid, StorageUnavailable,
                                             decode_state, document_id)


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def stores(now=None):
    clock = now or [NOW]
    backend = MemorySessionPersistence()
    return backend, clock, lambda: SessionStore(clock=lambda: clock[0], persistence_backend=backend)


def pending(session, now=NOW):
    return {'proposal_id': 'reviewed-1', 'kind': 'research',
            'summary': 'Review public search',
            'parameters': {'query': 'public port news', 'destination': 'Google Search via Vertex AI',
                           'shared_data': 'Only this public query is sent to Google Search via Vertex AI.'},
            'plan_id': None, 'offer_versions': {}, 'context_version': session.context_version,
            'expires_at': (now + timedelta(minutes=5)).isoformat(),
            '_marker': 'snapshot-1', '_company_id': session.company_id,
            '_session_id': session.session_id}


def test_reviewed_proposal_survives_an_independent_store_until_expiry():
    _, clock, make = stores()
    first = make()
    session = first.create()
    session.pending_proposal = pending(session)
    first.save(session)
    assert make().get(session.session_id).pending_proposal == session.pending_proposal
    clock[0] += timedelta(minutes=6)
    assert make().get(session.session_id).pending_proposal is None


def test_proposal_prepared_near_session_expiry_does_not_invalidate_session():
    clock = [NOW]
    backend = MemorySessionPersistence()
    store = SessionStore(clock=lambda: clock[0], ttl_seconds=60, persistence_backend=backend)
    session = store.create()
    clock[0] += timedelta(seconds=40)
    session.pending_proposal = pending(session, clock[0])
    store.save(session)
    assert SessionStore(clock=lambda: clock[0], persistence_backend=backend).get(session.session_id).pending_proposal


def test_restored_proposal_with_wrong_company_or_context_is_discarded():
    backend, _, make = stores()
    store = make()
    session = store.create()
    session.pending_proposal = pending(session)
    store.save(session)
    state, version = backend.load(session.session_id)
    state['pending_proposal']['_company_id'] = 'another-company'
    backend.save(session.session_id, state, version)
    assert make().get(session.session_id).pending_proposal is None
    state, version = backend.load(session.session_id)
    state['pending_proposal']['_company_id'] = session.company_id
    state['pending_proposal']['context_version'] += 1
    backend.save(session.session_id, state, version)
    assert make().get(session.session_id).pending_proposal is None


def test_private_or_control_character_research_query_is_not_persisted_as_consent():
    _, _, make = stores()
    store = make()
    session = store.create()
    session.pending_proposal = pending(session)
    session.pending_proposal['parameters']['query'] = 'Search for alice@example.com\n'
    with pytest.raises(SessionError) as exc:
        store.save(session)
    assert exc.value.code == 'session_storage_unavailable'
    assert not session.active


def test_unchanged_save_does_not_write_and_shared_request_budget_remains_atomic():
    backend, _, make = stores()
    first = make()
    session = first.create()
    assert first.reserve_request(session, 2) == 1
    admitted_version = session.storage_version
    first.save(session)
    assert backend.load(session.session_id)[1] == admitted_version
    second = make()
    restored = second.get(session.session_id)
    assert second.reserve_request(restored, 2) == 2
    with pytest.raises(SessionError) as exc:
        first.reserve_request(session, 2)
    assert exc.value.code == 'session_conflict'
    refreshed = first.get(session.session_id, refresh=True)
    with pytest.raises(SessionError) as full:
        first.reserve_request(refreshed, 2)
    assert full.value.code == 'request_limit'
    assert backend.load(session.session_id)[1] == restored.storage_version


def test_request_count_survives_mutation_save_reload_and_next_admission():
    backend, _, make = stores()
    first = make()
    session = first.create()
    assert first.reserve_request(session, 3) == 1
    admitted_version = session.storage_version
    session.chat_history[session.company_id] = [{'text': 'durable mutation'}]
    first.save(session)
    assert session.storage_version > admitted_version
    second = make()
    recovered = second.get(session.session_id)
    assert recovered.requests == 1
    assert recovered.chat_history[session.company_id] == [{'text': 'durable mutation'}]
    assert second.reserve_request(recovered, 3) == 2
    assert make().get(session.session_id).requests == 2


def test_legacy_document_request_count_seeds_new_atomic_field():
    backend, _, make = stores()
    store = make()
    session = store.create()
    state, version = backend.load(session.session_id)
    state['requests'] = 7
    backend.save(session.session_id, state, version)
    key = document_id(session.session_id)
    payload, updated_version, _ = backend._documents[key]
    backend._documents[key] = (payload, updated_version)
    recovered_store = make()
    recovered = recovered_store.get(session.session_id)
    assert recovered.requests == 7
    assert recovered_store.reserve_request(recovered, 8) == 8
    assert make().get(session.session_id).requests == 8


def test_concurrent_independent_request_admissions_do_not_exceed_limit():
    _, _, make = stores()
    first = make()
    session = first.create()
    second = make()
    restored = second.get(session.session_id)
    def admit(pair):
        store, value = pair
        try:
            return store.reserve_request(value, 1)
        except SessionError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(admit, ((first, session), (second, restored))))
    assert sorted(results, key=str) == [1, 'session_conflict']
    assert make().get(session.session_id).requests == 1


def test_cold_store_recovers_private_workspace_and_finalized_history():
    backend, now, make = stores()
    original = make()
    session = original.create()
    session.offers['quote-1'] = {'offer_id': 'quote-1', 'price': '100.00'}
    session.last_plan = {'plan_id': 'plan-1'}
    session.chat_history['atlas-petrochem'] = [{'speaker': 'user', 'text': 'Review copper', 'channel': 'text'}]
    session.research_attempts = 1
    session.research_records['claim-1'] = {'status': 'claimed', 'query': 'public port news'}
    session.pending_proposal = pending(session)
    original.save(session)

    restored_store = make()
    restored = restored_store.get(session.session_id)
    assert restored is not session
    assert restored.lock is not session.lock and isinstance(restored.lock, type(threading.RLock()))
    assert restored.csrf_token == session.csrf_token
    assert restored.offers['quote-1']['price'] == '100.00'
    assert restored.last_plan == {'plan_id': 'plan-1'}
    assert restored.chat_history['atlas-petrochem'][0]['text'] == 'Review copper'
    assert restored.research_records['claim-1']['status'] == 'claimed'
    assert restored.research_attempts == 1
    assert restored.pending_proposal == session.pending_proposal
    assert restored_store.persistence == 'firestore'


def test_company_switch_restores_only_that_company_history_and_workspace():
    _, _, make = stores()
    store = make()
    session = store.create()
    first_company = session.company_id
    session.offers['first'] = {'offer_id': 'first'}
    session.chat_history[first_company] = [{'text': 'First company'}]
    session.select_company('atlas-petrochem')
    session.offers['second'] = {'offer_id': 'second'}
    session.chat_history['atlas-petrochem'] = [{'text': 'Second company'}]
    store.save(session)

    restored = make().get(session.session_id)
    assert 'first' not in restored.offers
    assert restored.chat_history['atlas-petrochem'] == [{'text': 'Second company'}]
    restored.select_company(first_company)
    assert 'first' in restored.offers and 'second' not in restored.offers
    assert restored.chat_history[first_company] == [{'text': 'First company'}]


def test_unknown_session_does_not_disclose_other_session_history():
    _, _, make = stores()
    store = make()
    first, second = store.create(), store.create()
    first.chat_history[first.company_id] = [{'text': 'private'}]
    store.save(first)
    recovered = make()
    assert recovered.get(second.session_id).chat_history == {}
    with pytest.raises(SessionError) as exc:
        recovered.get(secrets.token_urlsafe(32))
    assert exc.value.code == 'session_expired'
    with pytest.raises(SessionError) as malformed:
        recovered.get('../other-session')
    assert malformed.value.code == 'session_expired'


def test_expired_durable_session_cannot_be_restored():
    _, now, make = stores()
    store = make()
    session = store.create()
    now[0] = NOW + timedelta(days=7, seconds=1)
    with pytest.raises(SessionError) as exc:
        make().get(session.session_id)
    assert exc.value.code == 'session_expired'


def test_stale_process_cannot_overwrite_accepted_history():
    _, _, make = stores()
    first_store = make()
    session = first_store.create()
    second_store = make()
    stale = second_store.get(session.session_id)
    session.chat_history[session.company_id] = [{'text': 'accepted'}]
    first_store.save(session)
    stale.chat_history[stale.company_id] = [{'text': 'stale'}]
    with pytest.raises(SessionError) as exc:
        second_store.save(stale)
    assert exc.value.code == 'session_conflict'
    assert make().get(session.session_id).chat_history[session.company_id] == [{'text': 'accepted'}]
    assert not stale.active
    assert second_store.get(session.session_id).chat_history[session.company_id] == [{'text': 'accepted'}]


def test_refresh_rejects_remote_revocation_of_cached_session():
    _, _, make = stores()
    first = make()
    session = first.create()
    session.chat_history[session.company_id] = [{'text': 'private'}]
    first.save(session)
    second = make()
    cached = second.get(session.session_id)
    first.invalidate(session)
    with pytest.raises(SessionError) as exc:
        second.get(cached.session_id, refresh=True)
    assert exc.value.code == 'session_expired'
    assert not cached.active and cached.chat_history == {}


def test_refresh_replaces_stale_cached_state_with_valid_proposal():
    _, _, make = stores()
    first = make()
    session = first.create()
    second = make()
    stale = second.get(session.session_id)
    session.chat_history[session.company_id] = [{'text': 'accepted'}]
    session.pending_proposal = pending(session)
    first.save(session)
    refreshed = second.get(session.session_id, refresh=True)
    assert refreshed is not stale and not stale.active
    assert refreshed.chat_history[session.company_id] == [{'text': 'accepted'}]
    assert refreshed.pending_proposal == session.pending_proposal
    assert second.get(session.session_id) is refreshed
    second.invalidate(stale, delete_remote=False)
    assert second.get(session.session_id) is refreshed


def test_refresh_storage_failure_never_returns_cached_history():
    backend, _, make = stores()
    first = make()
    session = first.create()
    second = make()
    cached = second.get(session.session_id)
    backend.unavailable = True
    with pytest.raises(SessionError) as exc:
        second.get(session.session_id, refresh=True)
    assert exc.value.code == 'session_storage_unavailable'
    assert not cached.active


def test_refresh_serializes_remote_read_with_local_save():
    class PausingPersistence(MemorySessionPersistence):
        pause = False
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
        def load(self, session_id):
            if self.pause:
                self.entered.set()
                assert self.release.wait(3)
            return super().load(session_id)

    backend = PausingPersistence()
    store = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    session = store.create()
    backend.pause = True
    saved = threading.Event()
    errors = []

    def refresh():
        try:
            store.get(session.session_id, refresh=True)
        except Exception as exc:
            errors.append(exc)

    def save():
        try:
            with session.lock:
                session.chat_history[session.company_id] = [{'text': 'durable'}]
                store.save(session)
            saved.set()
        except Exception as exc:
            errors.append(exc)

    reader = threading.Thread(target=refresh, daemon=True)
    writer = threading.Thread(target=save, daemon=True)
    reader.start()
    assert backend.entered.wait(2)
    writer.start()
    assert not saved.wait(0.2)
    backend.release.set()
    reader.join(timeout=3)
    writer.join(timeout=3)
    assert not errors and saved.is_set()
    backend.pause = False
    assert SessionStore(clock=lambda: NOW, persistence_backend=backend).get(session.session_id).chat_history[session.company_id] == [{'text': 'durable'}]


def test_remote_read_for_one_session_does_not_block_another_session_save():
    class PausingPersistence(MemorySessionPersistence):
        paused_id = None
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()
        def load(self, session_id):
            if session_id == self.paused_id:
                self.entered.set()
                assert self.release.wait(3)
            return super().load(session_id)

    backend = PausingPersistence()
    store = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    slow, fast = store.create(), store.create()
    backend.paused_id = slow.session_id
    reader = threading.Thread(target=lambda: store.get(slow.session_id, refresh=True), daemon=True)
    reader.start()
    assert backend.entered.wait(2)
    try:
        fast.chat_history[fast.company_id] = [{'text': 'unblocked'}]
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(store.save, fast).result(timeout=1)
    finally:
        backend.release.set()
        reader.join(timeout=3)
    assert backend.load(fast.session_id)[0]['chat_history'][fast.company_id] == [{'text': 'unblocked'}]


def test_concurrent_cold_restores_of_same_session_use_one_capacity_slot():
    backend = MemorySessionPersistence()
    creator = SessionStore(clock=lambda: NOW, persistence_backend=backend)
    session = creator.create()
    gate = threading.Barrier(2)
    original_load = backend.load
    def aligned_load(session_id):
        result = original_load(session_id)
        gate.wait(timeout=3)
        return result
    backend.load = aligned_load
    cold = SessionStore(clock=lambda: NOW, max_sessions=1, persistence_backend=backend)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cold.get, (session.session_id, session.session_id)))
    assert results[0] is results[1]


def test_cold_restore_respects_capacity_and_prunes_expired_cache():
    backend, now, make = stores()
    creator = make()
    first = creator.create()
    limited = SessionStore(clock=lambda: now[0], max_sessions=1, persistence_backend=backend)
    assert limited.get(first.session_id).session_id == first.session_id
    second = creator.create()
    with pytest.raises(SessionError) as full:
        limited.get(second.session_id)
    assert full.value.code == 'session_capacity'
    now[0] += timedelta(days=7, seconds=1)
    third = creator.create()
    assert limited.get(third.session_id).session_id == third.session_id
    assert first.session_id not in limited._sessions


def test_failed_save_evicts_local_session_without_memory_fallback():
    backend, _, make = stores()
    store = make()
    session = store.create()
    backend.unavailable = True
    session.chat_history[session.company_id] = [{'text': 'not durable'}]
    with pytest.raises(SessionError) as exc:
        store.save(session)
    assert exc.value.code == 'session_storage_unavailable'
    assert not session.active
    with pytest.raises(SessionError) as read_error:
        store.get(session.session_id)
    assert read_error.value.code == 'session_storage_unavailable'


def test_oversized_state_is_rejected_and_not_left_active():
    _, _, make = stores()
    store = make()
    session = store.create()
    session.offers['large'] = {'random': secrets.token_hex(600_000)}
    with pytest.raises(SessionError) as exc:
        store.save(session)
    assert exc.value.code == 'session_too_large'
    assert not session.active
    assert store.get(session.session_id).offers == {}


def test_sixty_final_voice_captions_round_trip_above_old_raw_limit():
    _, _, make = stores()
    store = make()
    session = store.create()
    caption = 'A reviewed quote comparison with source evidence. ' * 330
    session.chat_history[session.company_id] = [
        {'id': f'voice-{i}', 'speaker': 'assistant', 'channel': 'voice', 'text': caption[:16_000]}
        for i in range(60)]
    store.save(session)
    recovered = make().get(session.session_id)
    assert len(recovered.chat_history[session.company_id]) == 60
    assert recovered.chat_history[session.company_id][-1]['text'] == caption[:16_000]


def test_save_does_not_wait_on_store_lock_while_holding_session_lock():
    _, _, make = stores()
    store = make()
    session = store.create()
    session_locked = threading.Event()
    store_locked = threading.Event()
    finished = threading.Event()

    def competing_expiry():
        with store._lock:
            store_locked.set()
            with session.lock:
                pass

    def save_under_session_lock():
        with session.lock:
            session_locked.set()
            assert store_locked.wait(2)
            store.save(session)
            finished.set()

    saver = threading.Thread(target=save_under_session_lock, daemon=True)
    competing = threading.Thread(target=competing_expiry, daemon=True)
    saver.start()
    assert session_locked.wait(2)
    competing.start()
    assert store_locked.wait(2)
    assert finished.wait(2), 'Save deadlocked against an expiry check'
    saver.join(timeout=2)
    competing.join(timeout=2)


def test_repeated_save_does_not_duplicate_history():
    _, _, make = stores()
    store = make()
    session = store.create()
    session.chat_history[session.company_id] = [{'id': 'turn-1', 'text': 'hello'}]
    store.save(session)
    store.save(session)
    assert make().get(session.session_id).chat_history[session.company_id] == [{'id': 'turn-1', 'text': 'hello'}]


def test_shared_daily_budget_is_atomic_across_stores():
    _, _, make = stores()
    first, second = make(), make()
    assert first.reserve_research_budget('2026-10-15', 2)
    assert second.reserve_research_budget('2026-10-15', 2)
    assert not first.reserve_research_budget('2026-10-15', 2)
    assert second.reserve_research_budget('2026-10-16', 2)


def test_firestore_writes_use_hashed_ids_and_expected_update_time():
    class Response:
        status_code = 200
        def __init__(self, version):
            self.version = version
        def json(self):
            return {'updateTime': self.version}

    class Client:
        def __init__(self):
            self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response('2026-10-15T00:00:01Z' if method == 'POST' else '2026-10-15T00:00:02Z')

    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    session_id = secrets.token_urlsafe(32)
    state = {'expires_at': '2026-10-22T00:00:00+00:00', 'session_id': session_id}
    version = backend.save(session_id, state, None)
    assert version == '2026-10-15T00:00:01Z'
    backend.save(session_id, state, version)
    create, update = backend._client.calls
    assert session_id not in str(create) + str(update)
    assert create[2]['params']['documentId'] == document_id(session_id)
    assert update[2]['params']['currentDocument.updateTime'] == version
    assert update[2]['params']['updateMask.fieldPaths'] == ['payload', 'expiresAt']
    assert decode_state(create[2]['json']['fields']['payload']['stringValue']) == state


def test_firestore_request_admission_updates_only_counter_with_document_precondition():
    class Response:
        status_code = 200
        def json(self):
            return {'updateTime': 'v2'}
    class Client:
        def __init__(self):
            self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response()
    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    session_id = secrets.token_urlsafe(32)
    assert backend.reserve_session_request(session_id, 2, 1, 'v1') == (2, 'v2')
    assert backend.reserve_session_request(session_id, 2, 2, 'v2') is None
    assert len(backend._client.calls) == 1
    method, url, kwargs = backend._client.calls[0]
    assert method == 'PATCH' and url.endswith('/demo_sessions/' + document_id(session_id))
    assert kwargs['params'] == {'currentDocument.updateTime': 'v1',
                                'updateMask.fieldPaths': ['requestCount']}
    assert kwargs['json'] == {'fields': {'requestCount': {'integerValue': '2'}}}


def test_firestore_readiness_probes_one_masked_document_without_writes():
    expected = ('projects/public-demo-project/databases/(default)/documents/'
                'demo_sessions/' + document_id('0' * 43))

    class Response:
        status_code = 200
        def json(self):
            return {'missing': expected, 'readTime': '2026-10-15T00:00:00Z'}

    class Client:
        def __init__(self):
            self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response()

    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    assert backend.probe_readiness() is True
    method, url, kwargs = backend._client.calls[0]
    assert len(backend._client.calls) == 1
    assert method == 'POST' and url.endswith('/documents:batchGet')
    assert kwargs['json'] == {'documents': [expected], 'mask': {'fieldPaths': ['expiresAt']}}


@pytest.mark.parametrize('body', [
    {}, [], [{'missing': 'wrong-name'}],
    {'found': {'name': 'wrong-name'}},
    [{'missing': 'wrong-name'}, {'missing': 'wrong-name'}],
    {'missing': 'wrong-name', 'found': {'name': 'wrong-name'}},
])
def test_firestore_readiness_rejects_malformed_or_wrong_resource(body):
    class Response:
        status_code = 200
        def json(self):
            return body
    class Client:
        def request(self, *_args, **_kwargs):
            return Response()
    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    with pytest.raises(StorageInvalid):
        backend.probe_readiness()


def test_firestore_readiness_accepts_one_found_probe_document():
    expected = ('projects/public-demo-project/databases/(default)/documents/'
                'demo_sessions/' + document_id('0' * 43))
    class Response:
        status_code = 200
        def json(self):
            return {'found': {'name': expected, 'fields': {}}}
    class Client:
        def request(self, *_args, **_kwargs):
            return Response()
    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    assert backend.probe_readiness() is True


def test_firestore_readiness_rejects_extra_or_duplicate_results():
    expected = ('projects/public-demo-project/databases/(default)/documents/'
                'demo_sessions/' + document_id('0' * 43))
    class Response:
        status_code = 200
        def __init__(self, body):
            self.body = body
        def json(self):
            return self.body
    class Client:
        def __init__(self, body):
            self.body = body
        def request(self, *_args, **_kwargs):
            return Response(self.body)
    backend = FirestoreSessionPersistence('public-demo-project')
    for body in ({'missing': expected, 'error': {'code': 404}},
                 [{'missing': expected}, {'missing': expected}]):
        backend._client = Client(body)
        with pytest.raises(StorageInvalid):
            backend.probe_readiness()


def test_memory_readiness_observes_availability_without_writing():
    backend = MemorySessionPersistence()
    assert backend.probe_readiness() is True
    assert backend._documents == {}
    backend.unavailable = True
    with pytest.raises(StorageUnavailable):
        backend.probe_readiness()
    assert backend._documents == {}


def test_firestore_transport_allows_another_session_while_one_request_waits():
    class Response:
        status_code = 404
    class Client:
        def __init__(self, blocked_key):
            self.blocked_key = blocked_key
            self.entered = threading.Event()
            self.release = threading.Event()
        def request(self, _method, url, **_kwargs):
            if url.endswith(self.blocked_key):
                self.entered.set()
                assert self.release.wait(3)
            return Response()
    backend = FirestoreSessionPersistence('public-demo-project')
    first_id, second_id = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    backend._client = Client(document_id(first_id))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(backend.load, first_id)
        assert backend._client.entered.wait(2)
        try:
            assert pool.submit(backend.load, second_id).result(timeout=1) is None
        finally:
            backend._client.release.set()
        assert first.result(timeout=3) is None


def test_firestore_failed_precondition_400_is_conflict_but_other_400_is_unavailable():
    class Response:
        status_code = 400
        def __init__(self, status):
            self.status = status
        def json(self):
            return {'error': {'code': 400, 'message': 'redacted', 'status': self.status}}

    class Client:
        def __init__(self, status):
            self.status = status
        def request(self, *_args, **_kwargs):
            return Response(self.status)

    backend = FirestoreSessionPersistence('public-demo-project')
    session_id = secrets.token_urlsafe(32)
    state = {'expires_at': '2026-10-22T00:00:00+00:00', 'session_id': session_id}
    backend._client = Client('FAILED_PRECONDITION')
    with pytest.raises(StorageConflict):
        backend.save(session_id, state, '2026-10-15T00:00:01Z')
    backend._client = Client('INVALID_ARGUMENT')
    with pytest.raises(StorageUnavailable):
        backend.save(session_id, state, '2026-10-15T00:00:01Z')
    backend._client = Client('FAILED_PRECONDITION')
    with pytest.raises(StorageConflict):
        backend.delete(session_id, '2026-10-15T00:00:01Z')


def test_firestore_budget_retries_only_failed_precondition_collision():
    class Response:
        def __init__(self, code, body):
            self.status_code, self.body = code, body
        def json(self):
            return self.body

    class Client:
        def __init__(self):
            self.responses = [
                Response(200, {'updateTime': 'v1', 'fields': {'count': {'integerValue': '1'}}}),
                Response(400, {'error': {'code': 400, 'status': 'FAILED_PRECONDITION'}}),
                Response(200, {'updateTime': 'v2', 'fields': {'count': {'integerValue': '2'}}}),
                Response(200, {'updateTime': 'v3', 'fields': {'count': {'integerValue': '3'}}}),
            ]
            self.calls = []
        def request(self, method, _url, **kwargs):
            self.calls.append((method, kwargs))
            return self.responses.pop(0)

    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client()
    assert backend.reserve_research_budget('2026-10-15', 30)
    assert [method for method, _ in backend._client.calls] == ['GET', 'PATCH', 'GET', 'PATCH']
    assert backend._client.calls[-1][1]['json']['fields']['count']['integerValue'] == '3'


def test_invalid_or_bomb_payload_is_rejected_before_restore():
    with pytest.raises(StorageInvalid):
        document_id('attacker-supplied-path/../other')
    with pytest.raises(StorageInvalid):
        decode_state('not base64')
    bomb = base64.b64encode(zlib.compress(b'x' * 8_500_000)).decode()
    with pytest.raises(StorageInvalid):
        decode_state(bomb)


def test_firestore_cleanup_deletes_only_expired_documents_with_versions():
    class Response:
        status_code = 200
        def __init__(self, body):
            self.body = body
        def json(self):
            return self.body

    class Client:
        def __init__(self, resource):
            self.resource = resource
            self.calls = []
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if method == 'POST':
                return Response([{'document': {'name': self.resource + '/demo_sessions/' + 'a' * 64,
                                                'updateTime': '2026-10-15T00:00:00Z'}}])
            return Response({})

    backend = FirestoreSessionPersistence('public-demo-project')
    backend._client = Client('projects/public-demo-project/databases/(default)/documents')
    assert backend.cleanup(NOW, limit=8) == 1
    query, deletion = backend._client.calls
    assert query[0] == 'POST' and query[1].endswith('/documents:runQuery')
    assert query[2]['json']['structuredQuery']['select'] == {'fields': [{'fieldPath': 'expiresAt'}]}
    assert deletion[0] == 'DELETE'
    assert deletion[2]['params']['currentDocument.updateTime'] == '2026-10-15T00:00:00Z'
