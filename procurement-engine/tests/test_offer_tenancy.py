"""Generated tenant-boundary witnesses; no original document or provider access."""
from __future__ import annotations

import pytest

from procure.offers import store
from procure.offers.context import OfferContext
from procure.offers.lifecycle import OfferError


def test_store_requires_explicit_immutable_workspace_context():
    with pytest.raises(OfferError) as exc:
        store.list_offers()
    assert exc.value.code == 'identity_required'
    with pytest.raises(OfferError) as exc:
        store.get_offer("missing")
    assert exc.value.code == 'identity_required'
    with pytest.raises(OfferError) as exc:
        store.sourcing_candidates()
    assert exc.value.code == 'identity_required'
    with pytest.raises(OfferError) as exc:
        store.draft_rfq("missing", buyer={}, idempotency_key="test-key-001")
    assert exc.value.code == 'identity_required'
    with pytest.raises(OfferError) as exc:
        store.list_offers(context=OfferContext("", "workspace-a", "viewer"))
    assert exc.value.code == 'identity_required'


def test_same_synthetic_quote_is_independent_per_workspace_and_foreign_ids_are_hidden():
    import datetime as dt
    import json
    from pathlib import Path
    fixture = Path(__file__).parent / 'fixtures' / 'offers'
    record = next(q for q in json.loads((fixture / 'manifest.json').read_text())['quotes'] if q['id'] == 'Q01')
    path = fixture / record['file']
    a = OfferContext('reviewer-a', 'tenant-a', 'editor')
    b = OfferContext('reviewer-b', 'tenant-b', 'editor')
    date = dt.date(2026, 10, 7)
    first = store.create_from_upload(path.read_bytes(), path.name, mode='rules', as_of=date, context=a)
    second = store.create_from_upload(path.read_bytes(), path.name, mode='rules', as_of=date, context=b)
    assert first['offer']['offer_id'] != second['offer']['offer_id']
    assert not second['deduplicated']
    assert len(store.list_offers(context=a, today=date)) == 1
    assert len(store.list_offers(context=b, today=date)) == 1
    for action in (lambda: store.get_offer(first['offer']['offer_id'], context=b),
                   lambda: store.get_extraction(first['extraction_id'], context=b),
                   lambda: store.save_offer(first['offer']['offer_id'], context=b,
                                            expected_version=1, idempotency_key='foreign-key-0001', today=date)):
        with pytest.raises(OfferError) as exc:
            action()
        assert exc.value.code == 'not_found'


def test_receipt_replay_and_event_actor_come_only_from_context():
    import datetime as dt
    import json
    from pathlib import Path
    fixture = Path(__file__).parent / 'fixtures' / 'offers'
    record = next(q for q in json.loads((fixture / 'manifest.json').read_text())['quotes'] if q['id'] == 'Q01')
    path = fixture / record['file']
    editor = OfferContext('reviewer-a', 'tenant-receipt', 'editor')
    another = OfferContext('reviewer-b', 'tenant-receipt', 'editor')
    date = dt.date(2026, 10, 7)
    offer = store.create_from_upload(path.read_bytes(), path.name, mode='rules', as_of=date, context=editor)['offer']
    args = dict(field='settlement_currency', value='USD', evidence=None,
                expected_version=offer['version'], idempotency_key='receipt-key-0001', today=date)
    first = store.correct_field(offer['offer_id'], context=editor, **args)
    replay = store.correct_field(offer['offer_id'], context=editor, **args)
    assert replay['replayed'] is True and first['version'] == replay['version']
    with pytest.raises(OfferError) as exc:
        store.correct_field(offer['offer_id'], context=another, **args)
    assert exc.value.code == 'idempotency_conflict'
    events = store.get_offer(offer['offer_id'], include_events=True, context=editor, today=date)['events']
    assert [e['actor'] for e in events] == ['reviewer-a', 'reviewer-a']
    assert [e['action'] for e in events] == ['created', 'correct']


def test_null_legacy_offer_and_receipt_are_inaccessible():
    import datetime as dt
    import json
    from pathlib import Path
    from procure import db
    context = OfferContext('reviewer', 'tenant-null', 'editor')
    store.ensure_schema()
    db.insert_row('offers', {'id': 'legacy-null-offer', 'status': 'saved', 'version': 1,
                            'payload_json': '{}', 'created_at': '2026-01-01', 'updated_at': '2026-01-01'})
    db.insert_row('offer_requests', {'idempotency_key': 'legacy-key-0001', 'action': 'correct',
                                    'request_hash': 'legacy', 'response_json': '{"forged":true}',
                                    'created_at': '2026-01-01'})
    assert store.list_offers(context=context, today=dt.date(2026, 10, 7)) == []
    with pytest.raises(OfferError) as exc:
        store.get_offer('legacy-null-offer', context=context)
    assert exc.value.code == 'not_found'
    fixture = Path(__file__).parent / 'fixtures' / 'offers'
    record = next(q for q in json.loads((fixture / 'manifest.json').read_text())['quotes'] if q['id'] == 'Q01')
    path = fixture / record['file']
    offer = store.create_from_upload(path.read_bytes(), path.name, mode='rules',
                                     as_of=dt.date(2026, 10, 7), context=context)['offer']
    corrected = store.correct_field(offer['offer_id'], context=context, field='settlement_currency',
                                    value='USD', evidence=None, expected_version=1,
                                    idempotency_key='legacy-key-0001', today=dt.date(2026, 10, 7))
    assert corrected['replayed'] is False and corrected['status'] == 'corrected'


def test_generated_hop_rejects_tamper_expiry_duplicate_json_and_header():
    import base64
    import hashlib
    import hmac
    import json
    from procure.offers.context import HopRejected, QuoteHopVerifier
    key = b't' * 32
    now = 1_000_000
    scope = {'method': 'GET', 'raw_path': b'/offers', 'query_string': b'', 'headers': []}
    value = {'v': 1, 'method': 'GET', 'raw_path': '/offers', 'raw_query': '',
             'body_sha256': hashlib.sha256(b'').hexdigest(), 'issued_at_unix': now,
             'nonce': 'c' * 32, 'principal_id': 'p', 'workspace_id': 'w', 'role': 'viewer',
             'idempotency_key': None}
    def signed(obj, *, wire=None):
        wire = wire if wire is not None else json.dumps(obj).encode()
        return [(b'x-quote-context', base64.urlsafe_b64encode(wire).rstrip(b'=')),
                (b'x-quote-signature', base64.urlsafe_b64encode(hmac.new(key, wire, hashlib.sha256).digest()).rstrip(b'='))]
    verifier = QuoteHopVerifier(key, clock=lambda: now)
    with pytest.raises(HopRejected):
        verifier.verify({**scope, 'headers': signed(value)}, b'tampered-body', idempotent=False)
    with pytest.raises(HopRejected):
        verifier.verify({**scope, 'headers': signed({**value, 'issued_at_unix': now - 16})}, b'', idempotent=False)
    duplicated = json.dumps(value)[:-1] + ',"role":"editor"}'
    with pytest.raises(HopRejected):
        verifier.verify({**scope, 'headers': signed(value, wire=duplicated.encode())}, b'', idempotent=False)
    with pytest.raises(HopRejected):
        verifier.verify({**scope, 'headers': signed(value) + signed(value)[:1]}, b'', idempotent=False)
    assert verifier.verify({**scope, 'headers': signed(value)}, b'', idempotent=False).workspace_id == 'w'
    with pytest.raises(HopRejected):
        verifier.verify({**scope, 'headers': signed(value)}, b'', idempotent=False)
