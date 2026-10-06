#!/usr/bin/env python3
"""Exercise the real loopback gateway/Python/PostgreSQL action vertical.

Creates synthetic development records. Tokens are read privately from a file.
Run `create` before a task gateway/database restart and `verify` afterward.
The output checkpoint contains record identifiers, never credentials.
"""
import argparse
import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['create', 'verify'])
    parser.add_argument('--url', default='http://127.0.0.1:8817')
    parser.add_argument('--identities-file', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    assert urlparse(args.url).hostname in {'127.0.0.1', 'localhost', '::1'}, 'loopback required'
    identities = json.loads(args.identities_file.read_text())['identities']
    editor = next(i for i in identities if i['role'] == 'editor' and i['workspace_id'] == 'local-workspace')
    viewer = next(i for i in identities if i['role'] == 'viewer' and i['workspace_id'] == editor['workspace_id'])
    other = next(i for i in identities if i['workspace_id'] != editor['workspace_id'])
    spec = json.loads((ROOT / 'openapi.yaml').read_text())

    def validate(value, schema):
        Draft202012Validator({'$ref': '#/components/schemas/' + schema, 'components': spec['components']},
                             format_checker=FormatChecker()).validate(value)

    def call(method, path, body=None, identity=editor, status=200, schema=None):
        headers = {'Content-Type': 'application/json'}
        if identity is not None:
            headers['Authorization'] = 'Bearer ' + identity['token']
        request = Request(args.url.rstrip('/') + path, method=method, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            assert response.status == status, (method, path, response.status, status)
            result = json.load(response)
        if schema:
            validate(result, schema)
        return result

    call('GET', '/ready', schema='ReadinessResponse')
    call('GET', '/v2/session', schema='SessionResponse')
    execute = lambda body: call('POST', '/v2/actions/prepare_rfq', body, schema='ActionReceiptResponse')
    if args.phase == 'verify':
        checkpoint = json.loads(args.checkpoint.read_text())
        path = '/v2/plans/' + checkpoint['saved_plan_id']
        current = call('GET', path, schema='SavedPlanResponse')['plan']
        assert current['revision'] == checkpoint['revision']
        assert current['snapshot'] == checkpoint['snapshot']
        assert execute(checkpoint['execute']) == checkpoint['receipt']
        history = call('GET', path + '/receipts', schema='ActionReceiptsResponse')['receipts']
        assert len(history) == 1 and history[0] == checkpoint['receipt']['receipt']
        print('Post-restart snapshot, revision, receipt and identical replay verified')
        return

    request = json.loads((ROOT / 'examples/request.json').read_text())
    workspace = '/v2/workspaces/' + editor['workspace_id'] + '/plans'
    create = {'contract_version': '0.2.0', 'planning_request': request}
    for identity, status in [(None, 401), (viewer, 403), (other, 403)]:
        call('POST', workspace, create, identity=identity, status=status, schema='StateErrorResponse')
    for null_value in [False, True]:
        malformed = copy.deepcopy(create)
        if null_value:
            malformed['planning_request']['lead_time_months'] = None
        else:
            del malformed['planning_request']['lead_time_months']
        call('POST', workspace, malformed, status=422, schema='StateErrorResponse')
    pasted = dict(create, snapshot=json.loads((ROOT / 'examples/success.json').read_text()))
    call('POST', workspace, pasted, status=422, schema='StateErrorResponse')
    saved = call('POST', workspace, create, status=201, schema='SavedPlanResponse')['plan']
    assert saved['snapshot']['request'] == request
    assert saved['snapshot']['status'] == 'ready'
    path = '/v2/plans/' + saved['saved_plan_id']
    for null_value in [False, True]:
        malformed = copy.deepcopy(create)
        malformed['expected_revision'] = 1
        if null_value:
            malformed['planning_request']['lead_time_months'] = None
        else:
            del malformed['planning_request']['lead_time_months']
        call('PUT', path, malformed, status=422, schema='StateErrorResponse')
    assert call('GET', path, schema='SavedPlanResponse')['plan'] == saved
    rfq_args = {key: request[key] for key in ['material', 'quantity', 'quote_currency', 'settlement_currency', 'reporting_currency']}
    rfq_args.update(buyer_id=request['buyer']['id'], destination_country=request['buyer']['destination_country'])
    confirm = {'contract_version': '0.2.0', 'expected_revision': 1,
               'expected_plan_version': saved['snapshot']['plan']['plan_version'],
               'action_kind': 'prepare_rfq', 'arguments': rfq_args}
    approval = call('POST', path + '/approvals', confirm, status=201, schema='ApprovalResponse')['approval']
    execution = dict(confirm, workspace_id=editor['workspace_id'], saved_plan_id=saved['saved_plan_id'],
                     approval_id=approval['approval_id'], idempotency_key='integration-' + uuid.uuid4().hex)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(execute, [execution] * 8))
    assert all(receipt == receipts[0] for receipt in receipts)
    receipt = receipts[0]
    assert receipt['receipt']['arguments'] == rfq_args
    assert receipt['receipt']['supplier_communication'] is False
    changed = copy.deepcopy(execution)
    changed['arguments']['quantity']['amount'] = '50001'
    call('POST', '/v2/actions/prepare_rfq', changed, status=409, schema='StateErrorResponse')
    second_key = dict(execution, idempotency_key='integration-' + uuid.uuid4().hex)
    call('POST', '/v2/actions/prepare_rfq', second_key, status=409, schema='StateErrorResponse')
    unused = call('POST', path + '/approvals', confirm, status=201, schema='ApprovalResponse')['approval']
    revised = call('PUT', path, dict(create, expected_revision=1), schema='SavedPlanResponse')['plan']
    stale = dict(execution, approval_id=unused['approval_id'], idempotency_key='integration-' + uuid.uuid4().hex)
    call('POST', '/v2/actions/prepare_rfq', stale, status=409, schema='StateErrorResponse')
    assert execute(execution) == receipt, 'committed retry must survive a later plan revision'
    for identity in [viewer, other]:
        call('POST', '/v2/actions/prepare_rfq', execution, identity=identity, status=403, schema='StateErrorResponse')
    history = call('GET', path + '/receipts', schema='ActionReceiptsResponse')['receipts']
    assert len(history) == 1
    audit = call('GET', path + '/audit', schema='AuditResponse')['events']
    assert sum(event['event_type'] == 'rfq_prepared' for event in audit) == 1
    checkpoint = {'saved_plan_id': saved['saved_plan_id'], 'revision': revised['revision'],
                  'snapshot': revised['snapshot'], 'execute': execution, 'receipt': receipt}
    args.checkpoint.write_text(json.dumps(checkpoint, indent=2) + '\n')
    print('Real HTTP save/revise/confirmation, eight concurrent retries, conflict/stale/denied and one-effect audit verified')


if __name__ == '__main__':
    main()
