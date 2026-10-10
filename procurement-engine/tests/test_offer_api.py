"""Direct legacy API quote mount must fail closed; scoped route behavior uses generated context."""
from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

from fastapi.testclient import TestClient

from procure.api import app
from procure.offers.context import OfferContext

FIXTURES = Path(__file__).parent / 'fixtures' / 'offers'
QUOTES = {q['id']: q for q in json.loads((FIXTURES / 'manifest.json').read_text())['quotes']}


class InjectedContext:
    """Test-only ASGI context injection, never mounted in service runtime."""
    def __init__(self, backend, context):
        self.backend, self.context = backend, context

    async def __call__(self, scope, receive, send):
        await self.backend({**scope, 'offer_context': self.context}, receive, send)


def test_unsigned_legacy_api_mount_denies_all_quote_routes_without_lifespan():
    client = TestClient(app)  # No lifespan entry.
    assert client.get('/offers').status_code == 403
    assert client.get('/offers/sourcing-candidates').status_code == 403
    assert client.get('/offers/unknown').status_code == 403
    assert client.get('/offers/extractions/unknown').status_code == 403
    assert client.post('/offers/planning-input', json={}).status_code == 403
    assert client.post('/offers/extractions', content=b'{malformed', headers={'Content-Type': 'application/json'}).status_code == 403
    assert client.post('/offers/unknown/rfq-draft', json={}).status_code == 403


def test_test_injected_context_reviews_and_saves_synthetic_offer_without_actor_body():
    path = FIXTURES / QUOTES['Q01']['file']
    context = OfferContext('api-test-reviewer', 'api-test-workspace', 'editor')
    # TestClient without a context manager never enters the legacy API lifespan.
    client = TestClient(InjectedContext(app, context))
    upload = client.post('/offers/extractions', json={'filename': path.name,
        'content_base64': base64.b64encode(path.read_bytes()).decode(), 'mode': 'rules', 'synthetic': True})
    assert upload.status_code == 201
    offer = upload.json()['offer']
    assert offer['status'] == 'needs_review'
    correction = client.post(f"/offers/{offer['offer_id']}/corrections",
        headers={'Idempotency-Key': 'api-correct-key-001'},
        json={'field': 'settlement_currency', 'value': 'USD', 'expected_version': offer['version']})
    assert correction.status_code == 200
    assert correction.json()['fields']['settlement_currency']['source'] == 'user'
    save = client.post(f"/offers/{offer['offer_id']}/save", headers={'Idempotency-Key': 'api-save-key-001'},
        json={'expected_version': correction.json()['version']})
    assert save.status_code == 200 and save.json()['status'] == 'saved'
    detail = client.get(f"/offers/{offer['offer_id']}?include_events=true&as_of=2026-10-07")
    assert detail.status_code == 200
    assert [event['actor'] for event in detail.json()['events']] == ['api-test-reviewer'] * 3
    assert client.post('/offers/planning-input', json={}).status_code == 403
    assert client.post(f"/offers/{offer['offer_id']}/rfq-draft", json={}).status_code == 403
    assert client.post(f"/offers/{offer['offer_id']}/corrections", headers={'Idempotency-Key': 'api-key-unknown'},
        json={'field': 'settlement_currency', 'value': 'USD', 'expected_version': 2,
              'actor': 'spoofed'}).status_code == 422
