"""Behavioral tests for the dedicated numerical HTTP boundary."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from procure.production.http_comparison import router
from procure.production import http_comparison as adapter
from procure.production.recovery import compare_receipt_recovery


EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts' / 'examples'


def example(name):
    return json.loads((EXAMPLES / name).read_text())


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_matches_actual_public_numerical_comparison():
    request = example('recovery_request.json')
    with client() as c:
        response = c.post('/production/receipt-recovery/comparisons', json=request)
    assert response.status_code == 200
    assert response.json() == {
        'contract_version': '0.2.0',
        'comparison': compare_receipt_recovery(request['snapshot'], request['scenario'],
                                               request['recovery']),
    }


def test_noop_and_precision_examples_match_public_comparison():
    with client() as c:
        for name in ('recovery_noop_request.json', 'recovery_precision_request.json'):
            request = example(name)
            response = c.post('/production/receipt-recovery/comparisons', json=request)
            assert response.status_code == 200
            assert response.json()['comparison'] == compare_receipt_recovery(
                request['snapshot'], request['scenario'], request['recovery'])


def test_valid_numerically_unsupported_is_distinct_from_malformed_json():
    request = example('recovery_unsupported_request.json')
    with client() as c:
        response = c.post('/production/receipt-recovery/comparisons', json=request)
        assert response.status_code == 422
        assert response.json() == example('recovery_unsupported_response.json')
        assert c.post('/production/receipt-recovery/comparisons',
                      content=b'{').json()['error']['code'] == 'invalid_request'


def test_strict_wire_parsing_rejects_duplicate_unknown_nonfinite_trailing_and_depth():
    raw = (EXAMPLES / 'recovery_request.json').read_text()
    with client() as c:
        bodies = (
            raw.replace('"contract_version": "0.2.0",',
                        '"contract_version": "0.2.0", "contract_version": "0.2.0",', 1),
            raw.replace('"contract_version": "0.2.0",',
                        '"contract_version": "0.2.0", "unexpected": true,', 1),
            raw.replace('"contract_version": "0.2.0",',
                        '"contract_version": "0.2.0", "unexpected": NaN,', 1),
            raw + '{}',
            '[' * 17 + '0' + ']' * 17,
        )
        for body in bodies:
            response = c.post('/production/receipt-recovery/comparisons',
                              content=body, headers={'content-type': 'application/json'})
            assert response.status_code == 422
            assert response.json()['error']['code'] == 'invalid_request'


def test_request_size_content_type_and_missing_fields_fail_closed():
    request = example('recovery_request.json')
    with client() as c:
        no_type = c.post('/production/receipt-recovery/comparisons', content=b'{}',
                         headers={'content-type': 'text/plain'})
        assert no_type.status_code == 422
        oversized = c.post('/production/receipt-recovery/comparisons',
                           content=b' ' * (1048576 + 1),
                           headers={'content-type': 'application/json'})
        assert oversized.status_code == 413
        del request['scenario']['arrival_replacements']
        missing = c.post('/production/receipt-recovery/comparisons', json=request)
        assert missing.status_code == 422
        assert missing.json()['error']['code'] == 'invalid_request'


def test_numerical_service_exception_is_generic(monkeypatch):
    def unavailable(*_):
        raise RuntimeError('private internal detail')

    monkeypatch.setattr(adapter, 'compare_receipt_recovery', unavailable)
    with client() as c:
        response = c.post('/production/receipt-recovery/comparisons',
                          json=example('recovery_request.json'))
    assert response.status_code == 503
    assert response.json()['error']['code'] == 'service_unavailable'
    assert 'private internal detail' not in response.text
