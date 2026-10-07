"""Synthetic fixed-process bridge behavior."""
import json
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from procure.agents.voice_local_bridge import execute_local_request


EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts' / 'examples'


def test_bridge_returns_actual_hand_tool_outcome():
    fixture = json.loads((EXAMPLES / 'recovery_request.json').read_text())
    snapshot = fixture['snapshot']
    host = {'principal_id': 'p', 'workspace_id': snapshot['workspace_id'],
            'snapshot_id': snapshot['snapshot_id'], 'plan_version': snapshot['plan_version'],
            'scenario_id': fixture['scenario']['scenario_id'], 'session_id': 's', 'turn_id': 't',
            'snapshot': snapshot}
    call = {'name': 'compare_receipt_recovery',
            'arguments': {'arrival_replacements': fixture['scenario']['arrival_replacements'],
                          'assignments': fixture['recovery']['assignments']}}
    request = json.dumps({'bridge_version': '1', 'host_context_json': json.dumps(host),
                          'call_json': json.dumps(call)})
    response = execute_local_request(request, is_active=lambda *_: True)
    assert response['outcome']['status'] == 'compared'
    assert response['outcome']['result']['recovery']['orders'][0]['late_by_seconds'] == '3600'
    assert response['binding']['context_sha256'] == hashlib.sha256(
        json.dumps(host).encode()).hexdigest()
    assert response['binding']['principal_id'] == 'p'


def hand_request():
    fixture = json.loads((EXAMPLES / 'recovery_request.json').read_text())
    snapshot = fixture['snapshot']
    host = {'principal_id': 'p', 'workspace_id': snapshot['workspace_id'],
            'snapshot_id': snapshot['snapshot_id'], 'plan_version': snapshot['plan_version'],
            'scenario_id': fixture['scenario']['scenario_id'], 'session_id': 's', 'turn_id': 't',
            'snapshot': snapshot}
    call = {'name': 'compare_receipt_recovery',
            'arguments': {'arrival_replacements': fixture['scenario']['arrival_replacements'],
                          'assignments': fixture['recovery']['assignments']}}
    return {'bridge_version': '1', 'host_context_json': json.dumps(host),
            'call_json': json.dumps(call)}


def test_guard_suppresses_and_binds_without_comparison():
    request = hand_request()
    response = execute_local_request(json.dumps(request), is_active=lambda *_: False)
    assert response['outcome']['status'] == 'suppressed'
    assert 'result' not in response['outcome']
    assert response['binding']['session_id'] == 's'


def test_missing_critical_model_field_clarifies_without_numbers():
    request = hand_request()
    call = json.loads(request['call_json'])
    del call['arguments']['assignments'][0]['period_id']
    request['call_json'] = json.dumps(call)
    outcome = execute_local_request(json.dumps(request), is_active=lambda *_: True)['outcome']
    assert outcome['status'] == 'clarification_required'
    assert 'result' not in outcome


@pytest.mark.parametrize('mutation', [
    lambda r: r.update({'apply': True}),
    lambda r: r.update({'bridge_version': '2'}),
    lambda r: r.update({'host_context_json': '{"principal_id":"p","principal_id":"p"}'}),
    lambda r: r.update({'call_json': '{"name":"compare_receipt_recovery","name":"compare_receipt_recovery","arguments":{}}'}),
    lambda r: r.update({'call_json': '{"name":"compare_receipt_recovery","arguments":{}} trailing'}),
    lambda r: r.update({'call_json': '{"name":"compare_receipt_recovery","arguments":{"assignments":NaN}}'}),
    lambda r: r.update({'call_json': json.dumps({'name':'send_email','arguments':{}})}),
    lambda r: r.update({'call_json': json.dumps({'name':'compare_receipt_recovery',
                                                'arguments':{'workspace_id':'foreign'}})}),
    lambda r: r.update({'host_context_json': json.dumps({
        **json.loads(r['host_context_json']), 'workspace_id':'foreign'})}),
    lambda r: r.update({'host_context_json': json.dumps({
        **json.loads(r['host_context_json']), 'synthetic':False})}),
])
def test_protocol_and_identity_spoof_rejected_without_echo(mutation):
    request = hand_request()
    mutation(request)
    with pytest.raises(ValueError, match='invalid local request'):
        execute_local_request(json.dumps(request), is_active=lambda *_: True)


def test_bounds_and_exact_decimal_strings_are_preserved():
    fixture = json.loads((EXAMPLES / 'recovery_precision_request.json').read_text())
    request = hand_request()
    host = json.loads(request['host_context_json'])
    host['snapshot'] = fixture['snapshot']
    host['workspace_id'] = fixture['snapshot']['workspace_id']
    host['snapshot_id'] = fixture['snapshot']['snapshot_id']
    host['plan_version'] = fixture['snapshot']['plan_version']
    host['scenario_id'] = fixture['scenario']['scenario_id']
    request['host_context_json'] = json.dumps(host)
    request['call_json'] = json.dumps({'name':'compare_receipt_recovery',
        'arguments':{'arrival_replacements':fixture['scenario']['arrival_replacements'],
                     'assignments':fixture['recovery']['assignments']}})
    response = execute_local_request(json.dumps(request), is_active=lambda *_: True)
    assert response['outcome']['status'] == 'compared'
    assert isinstance(response['outcome']['result']['original_baseline']['orders'][0]['output_quantity'], str)
    with pytest.raises(ValueError, match='invalid local request'):
        execute_local_request('x' * ((3 << 20) + 1), is_active=lambda *_: True)
    request['call_json'] = 'x' * ((128 << 10) + 1)
    with pytest.raises(ValueError, match='invalid local request'):
        execute_local_request(json.dumps(request), is_active=lambda *_: True)


def test_cli_invalid_protocol_returns_generic_error_only():
    process = subprocess.run([sys.executable, '-m', 'procure.agents.voice_local_bridge'],
                             input=b'{"bridge_version":"1","host_context_json":"oops"}',
                             capture_output=True, check=False)
    assert process.returncode != 0
    assert process.stdout == b'{"bridge_version":"1","error":"invalid_request"}'
    assert process.stderr == b''
