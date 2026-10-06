#!/usr/bin/env python3
"""Check the local gateway contract, optionally computing one legacy snapshot.

The connected check can create the preserved Python recommendation audit record.
No action/provider endpoint is exercised. Use a loopback gateway only.
"""
import argparse
import copy
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8816')
    parser.add_argument('--connected', action='store_true')
    args = parser.parse_args()
    spec = json.loads((ROOT / 'openapi.yaml').read_text())

    def validate(value, name):
        Draft202012Validator({'$ref': '#/components/schemas/' + name,
                              'components': spec['components']},
                             format_checker=FormatChecker()).validate(value)

    def call(path, body=None, status=200):
        request = Request(args.url.rstrip('/') + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json'})
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == status, (path, response.status, status)
            return json.load(response)

    mappings = {'request': 'PlanningRequest', 'precision': 'PlanningRequest',
                'success': 'PlanResponse', 'unsupported': 'PlanResponse',
                'offer': 'OfferExample', 'voice': 'VoiceExample', 'shipment': 'ShipmentExample'}
    for name, schema in mappings.items():
        actual = call('/v2/contracts/examples/' + name)
        expected = json.loads((ROOT / 'examples' / (name + '.json')).read_text())
        validate(actual, schema)
        assert actual == expected, name

    base = json.loads((ROOT / 'examples/request.json').read_text())
    unsupported = copy.deepcopy(base)
    unsupported['buyer']['destination_country'] = 'DE'
    unavailable = call('/v2/plans/recommendations', unsupported)
    validate(unavailable, 'PlanResponse')
    assert unavailable['status'] == 'unavailable' and unavailable['plan'] is None
    assert unavailable['availability']['code'] == 'unsupported_context'
    assert unavailable['request'] == unsupported
    for name, request, expected_code in [
        ('precision', json.loads((ROOT / 'examples/precision.json').read_text()), 'legacy_precision_unsupported'),
        ('units', dict(base, current_stock={'amount': '20000', 'unit': 'tonne'}), 'semantic_validation'),
        ('currency', dict(base, budget={'amount': '100', 'currency': 'USD'}), 'semantic_validation'),
    ]:
        error = call('/v2/plans/recommendations', request, 422)
        validate(error, 'ErrorResponse')
        assert error['error']['code'] == expected_code, name
    error = call('/v2/actions/prepare_rfq', {}, 501)
    validate(error, 'ErrorResponse')
    assert error['error']['code'] == 'not_implemented'
    print('7 exact HTTP fixtures, unsupported context, precision/unit/currency semantics and disabled actions passed')
    if args.connected:
        catalog = call('/v2/materials')
        validate(catalog, 'MaterialsResponse')
        assert catalog['availability']['code'] == 'available' and catalog['materials']
        plan = call('/v2/plans/recommendations', base)
        validate(plan, 'PlanResponse')
        assert plan['status'] == 'ready' and plan['request'] == base
        assert plan['plan']['computation_only'] is True
        assert any('Inventory feasibility' in item for item in plan['warnings'])
        print('Connected legacy catalog and ready snapshot passed OpenAPI validation; inventory feasibility remains unverified')


if __name__ == '__main__':
    main()
