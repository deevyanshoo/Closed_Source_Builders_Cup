"""Bounded exact replay preserves durable actions across a full demo visit."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.company import list_companies
from procure.demo.quote_service import (
    correct_offer, extract_fixture, fixture_review_values, new_offer, save_offer,
)
from procure.demo.research import DESTINATION, SHARED_DATA
from procure.demo.session_persistence import (
    MAX_ENCODED_BYTES, MAX_UNCOMPRESSED_BYTES, MemorySessionPersistence, document_id,
)
from procure.demo.sessions import SessionStore


TODAY = datetime(2026, 10, 15, tzinfo=timezone.utc)
RETIRED = {'_retry_result_expired': True}


def test_all_material_visit_keeps_business_state_and_bounded_recent_replay():
    backend = MemorySessionPersistence()
    store = SessionStore(clock=lambda: TODAY, persistence_backend=backend)
    session = store.create()
    operations = fixtures = 0
    for company in list_companies():
        session.select_company(company['company_id'])
        for scenario in company['quote_coverage']['scenarios']:
            for fixture_id in scenario['fixture_ids']:
                fixtures += 1
                offer = new_offer(extract_fixture(fixture_id, mode='rules', as_of=TODAY.date()),
                                  offer_id=f'fixture-{fixtures}', today=TODAY.date())
                offer['fixture_id'] = fixture_id
                steps = [('extract', None, None)] + [
                    ('correct', field, value)
                    for field, value in fixture_review_values(fixture_id).items()
                ] + [('save', None, None)]
                for operation, field, value in steps:
                    if operation == 'correct':
                        offer = correct_offer(offer, field=field, value=value, today=TODAY.date())
                    elif operation == 'save':
                        offer = save_offer(offer, today=TODAY.date())
                    operations += 1
                    session.idempotency[f'operation-{operations}'] = {
                        'digest': f'digest-{operations}', 'pending': False,
                        'result': {'offer': deepcopy(offer), 'replayed': False},
                    }
                session.offers[offer['offer_id']] = offer
    session.research_records['running-claim'] = {'state': 'running', 'company_id': session.company_id}
    session.pending_proposal = {
        'proposal_id': 'reviewed-public-research', 'kind': 'research',
        'summary': 'Review public search',
        'parameters': {'query': 'public port news', 'destination': DESTINATION,
                       'shared_data': SHARED_DATA},
        'plan_id': None, 'offer_versions': {}, 'context_version': session.context_version,
        'expires_at': (TODAY + timedelta(minutes=5)).isoformat(),
        '_marker': 'current-snapshot', '_company_id': session.company_id,
        '_session_id': session.session_id,
    }
    assert fixtures == 156 and operations < 2400
    assert len(json.dumps(store._snapshot(session), separators=(',', ':')).encode()) > MAX_UNCOMPRESSED_BYTES

    store.save(session)
    state = store._snapshot(session)
    assert len(json.dumps(state, separators=(',', ':')).encode()) < MAX_UNCOMPRESSED_BYTES
    assert len(backend._documents[document_id(session.session_id)][0]) < MAX_ENCODED_BYTES
    exact = [entry for entry in session.idempotency.values() if entry['result'] is not None
             and not entry.get('result_retired')]
    assert len(exact) <= 128
    assert sum(len(json.dumps(entry['result'], ensure_ascii=False,
                              separators=(',', ':')).encode()) for entry in exact) <= 2 * 1024 * 1024
    assert session.idempotency['operation-1']['result'] == RETIRED
    assert session.idempotency['operation-1']['result_retired'] is True
    assert session.idempotency['operation-1']['digest'] == 'digest-1'
    latest = deepcopy(session.idempotency[f'operation-{operations}']['result'])
    assert latest['offer'] == offer
    assert store.reserve_request(session, 4000) == 1

    restored = SessionStore(clock=lambda: TODAY, persistence_backend=backend).get(session.session_id)
    assert restored.requests == 1
    assert restored.research_records == session.research_records
    assert restored.pending_proposal == session.pending_proposal
    assert restored.idempotency['operation-1']['result'] == RETIRED
    assert restored.idempotency[f'operation-{operations}']['result'] == latest
    assert restored.workspaces['solara-cables']['offers']['fixture-1']
    assert restored.company_id == company['company_id']
    assert restored.context_version == session.context_version
    restored.offers[offer['offer_id']]['status'] = 'modified-after-restore'
    assert restored.idempotency[f'operation-{operations}']['result'] == latest


def test_pending_failed_and_oversized_single_result_keep_safe_metadata():
    backend = MemorySessionPersistence()
    store = SessionStore(clock=lambda: TODAY, persistence_backend=backend)
    session = store.create()
    session.idempotency = {
        'pending': {'digest': 'pending-digest', 'result': None, 'pending': True},
        'failed': {'digest': 'failed-digest', 'result': None, 'pending': False},
        'oversized': {'digest': 'oversized-digest', 'result': {'text': 'x' * (2 * 1024 * 1024 + 1)},
                      'pending': False, 'state_marker': 'reviewed-state', 'operator_generation': 3},
    }
    store.save(session)
    restored = SessionStore(clock=lambda: TODAY, persistence_backend=backend).get(session.session_id)
    assert restored.idempotency['pending'] == session.idempotency['pending']
    assert restored.idempotency['failed'] == session.idempotency['failed']
    assert restored.idempotency['oversized'] == {
        'digest': 'oversized-digest', 'result': RETIRED, 'pending': False,
        'state_marker': 'reviewed-state', 'operator_generation': 3, 'result_retired': True,
    }


def test_retired_http_retry_never_reexecutes_and_digest_conflict_takes_precedence():
    backend = MemorySessionPersistence()
    first_app = create_app(clock=lambda: TODAY, session_backend=backend)
    original_payload = {'fixture_id': 'express-copper', 'mode': 'rules', 'idempotency_key': 'original'}
    with TestClient(first_app, base_url='https://demo.test') as first:
        session_view = first.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session_view['csrf_token']}
        original = first.post('/api/demo/extract', headers=headers, json=original_payload)
        assert original.status_code == 200
        session = first_app.state.sessions.get(session_view['session_id'])
        for index in range(30):
            session.idempotency[f'historical-{index}'] = {
                'digest': f'digest-{index}', 'pending': False,
                'result': {'snapshot': 'repeatable-evidence' * 10_000, 'index': index},
            }
        first_app.state.sessions.save(session)
        assert session.idempotency['original']['result'] == RETIRED

        second_app = create_app(clock=lambda: TODAY, session_backend=backend)
        with TestClient(second_app, base_url='https://demo.test') as second:
            second.cookies.update(first.cookies)
            before = second.get('/api/demo/session').json()['offers']
            expired = second.post('/api/demo/extract', headers=headers, json=original_payload)
            assert expired.status_code == 409
            assert expired.json()['error']['code'] == 'retry_result_expired'
            mismatch = second.post('/api/demo/extract', headers=headers, json={
                **original_payload, 'fixture_id': 'eu-copper'})
            assert mismatch.status_code == 409
            assert mismatch.json()['error']['code'] == 'idempotency_conflict'
            assert second.get('/api/demo/session').json()['offers'] == before

            recent_payload = {'fixture_id': 'eu-copper', 'mode': 'rules', 'idempotency_key': 'recent'}
            recent = second.post('/api/demo/extract', headers=headers, json=recent_payload)
            assert recent.status_code == 200

        third_app = create_app(clock=lambda: TODAY, session_backend=backend)
        with TestClient(third_app, base_url='https://demo.test') as third:
            third.cookies.update(first.cookies)
            replay = third.post('/api/demo/extract', headers=headers, json=recent_payload)
            assert replay.status_code == 200
            assert replay.json() == {**recent.json(), 'replayed': True}
