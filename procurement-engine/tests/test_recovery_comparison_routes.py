"""Composition checks: exact route and existing service identity boundary, no providers."""
import json
import os
from pathlib import Path
from fastapi.testclient import TestClient
from procure import api, service_app
from test_service_auth import signed, AUD, CALLER

PATH = '/production/receipt-recovery/comparisons'
EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts/examples'


def test_legacy_api_contains_dedicated_read_only_router():
    # No context manager: do not invoke legacy ingestion lifespan.
    client = TestClient(api.app)
    response = client.post(PATH, json=json.loads((EXAMPLES / 'recovery_request.json').read_text()))
    assert response.status_code == 200
    assert response.json() == json.loads((EXAMPLES / 'recovery_response.json').read_text())


def test_restricted_service_exact_route_preserves_identity_and_allowlist(monkeypatch, signed):
    token, verifier, _ = signed
    calls = []
    async def backend(scope, receive, send):
        calls.append(scope['path'])
        await api.app(scope, receive, send)
    monkeypatch.setenv('PROCURE_DB_PATH', str(Path(os.environ['PROCURE_DATA_DIR']) / 'app.duckdb'))
    monkeypatch.setattr(service_app, 'prepare_cache', lambda _: {})
    app = service_app.create_app(mode='private_machine', audience=AUD, callers=(CALLER,),
                                  backend=backend, verifier=verifier)
    request = json.loads((EXAMPLES / 'recovery_request.json').read_text())
    with TestClient(app) as client:
        assert client.post(PATH, json=request).status_code == 401
        headers = {'Authorization': 'Bearer ' + token()}
        assert client.post(PATH, json=request, headers={**headers, 'X-Role': 'viewer'}).status_code == 403
        assert client.post(PATH, json=request, headers=[('Authorization', headers['Authorization']),
                                                       ('Authorization', headers['Authorization'])]).status_code == 401
        assert client.get(PATH, headers=headers).status_code == 403
        assert client.post(PATH + '/apply', headers=headers).status_code == 403
        assert client.post('/ingest/refresh', headers=headers).status_code == 403
        response = client.post(PATH, json=request, headers=headers)
        assert response.status_code == 200
        assert response.json() == json.loads((EXAMPLES / 'recovery_response.json').read_text())
    assert calls == [PATH]


def test_composed_route_multiple_receipts_same_instant_and_malformed_offset():
    from copy import deepcopy
    from procure.production.recovery import compare_receipt_recovery
    source = json.loads((EXAMPLES / 'recovery_request.json').read_text())
    client = TestClient(api.app)
    same = deepcopy(source)
    same['scenario']['arrival_replacements'][0]['arrival_at'] = same['snapshot']['receipts'][0]['arrival_at'].replace('Z', '+00:00')
    for request in [same]:
        response = client.post(PATH, json=request)
        assert response.status_code == 200
        assert response.json()['comparison'] == compare_receipt_recovery(request['snapshot'], request['scenario'], request['recovery'])
        assert response.json()['comparison']['receipt_changes'] == []
    multiple = deepcopy(source)
    receipt = deepcopy(multiple['snapshot']['receipts'][0])
    receipt['receipt_id'] = 'R2'
    multiple['snapshot']['receipts'].append(receipt)
    change = deepcopy(multiple['scenario']['arrival_replacements'][0])
    change['receipt_id'] = 'R2'
    multiple['scenario']['arrival_replacements'].append(change)
    response = client.post(PATH, json=multiple)
    assert response.status_code == 200
    assert response.json()['comparison'] == compare_receipt_recovery(multiple['snapshot'], multiple['scenario'], multiple['recovery'])
    assert {x['receipt_id'] for x in response.json()['comparison']['receipt_changes']} == {source['snapshot']['receipts'][0]['receipt_id'], 'R2'}
    bad = deepcopy(source)
    bad['scenario']['arrival_replacements'][0]['arrival_at'] = '2026-10-10T10:00:00+99:00'
    response = client.post(PATH, json=bad)
    assert response.status_code == 422
    assert response.json()['status'] == 'unsupported'
