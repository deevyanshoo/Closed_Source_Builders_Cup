"""Synthetic behavioral tests for a read-only recovery function-call adapter."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from procure.agents import voice_tools

from procure.agents.voice_tools import (TOOL_NAME, TrustedRecoveryContext,
                                        execute_recovery_tool, function_declaration)
from procure.production.recovery import compare_receipt_recovery


EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts' / 'examples'


def example(name):
    return json.loads((EXAMPLES / name).read_text())


def setup(name='recovery_request.json'):
    fixture = example(name)
    snapshot = fixture['snapshot']
    context = TrustedRecoveryContext(
        workspace_id=snapshot['workspace_id'], snapshot_id=snapshot['snapshot_id'],
        plan_version=snapshot['plan_version'], scenario_id=fixture['scenario']['scenario_id'],
        session_id='session-1', turn_id='turn-1', snapshot=snapshot)
    arguments = {'arrival_replacements': fixture['scenario']['arrival_replacements'],
                 'assignments': fixture['recovery']['assignments']}
    return fixture, context, {'name': TOOL_NAME, 'arguments': arguments}


def call(tool_call, context, active=lambda _session, _turn: True):
    return execute_recovery_tool(json.dumps(tool_call), context, is_active=active)


def test_hand_recovery_tool_returns_actual_public_result_unchanged():
    fixture, context, tool_call = setup()
    result = call(tool_call, context)
    expected = compare_receipt_recovery(fixture['snapshot'], fixture['scenario'],
                                        fixture['recovery'])
    assert result['status'] == 'compared'
    assert result['read_only'] is True and result['tool_name'] == TOOL_NAME
    assert result['ownership'] == {'session_id': 'session-1', 'turn_id': 'turn-1'}
    assert result['result'] == expected
    assert result['result']['disrupted_baseline']['orders'][0]['demands'][0]['shortfall'] == '4'
    assert result['result']['recovery']['orders'][0]['late_by_seconds'] == '3600'


@pytest.mark.parametrize('name', [
    'recovery_noop_request.json', 'recovery_precision_request.json',
    'recovery_unsupported_request.json',
])
def test_public_numerical_result_is_preserved_for_other_synthetic_cases(name):
    fixture, context, tool_call = setup(name)
    result = call(tool_call, context)
    assert result['result'] == compare_receipt_recovery(
        fixture['snapshot'], fixture['scenario'], fixture['recovery'])
    assert result['status'] == result['result']['status']


def test_declaration_is_fresh_inline_and_exposes_only_two_arrays():
    declaration = function_declaration()
    assert declaration['name'] == TOOL_NAME
    parameters = declaration['parameters']
    assert set(parameters['properties']) == {'arrival_replacements', 'assignments'}
    assert set(parameters['required']) == set(parameters['properties'])
    assert parameters['additionalProperties'] is False
    assert '$ref' not in json.dumps(declaration)
    assert 'workspace_id' not in json.dumps(declaration)
    assert 'apply' not in json.dumps(parameters)
    declaration['parameters']['properties']['assignments']['items']['required'].clear()
    assert function_declaration()['parameters']['properties']['assignments']['items']['required']


@pytest.mark.parametrize('mutation,expected', [
    (lambda c: c.update({'workspace_id': 'other'}), 'rejected'),
    (lambda c: c.update({'apply': True}), 'rejected'),
    (lambda c: c.update({'result': {}}), 'rejected'),
    (lambda c: c.update({'name': 'send_email'}), 'rejected'),
    (lambda c: c.pop('name'), 'rejected'),
    (lambda c: c['arguments'].update({'workspace_id': 'other'}), 'rejected'),
    (lambda c: c['arguments']['assignments'].append(
        deepcopy(c['arguments']['assignments'][0])), 'rejected'),
    (lambda c: c['arguments']['assignments'].pop(), 'clarification_required'),
    (lambda c: c['arguments']['arrival_replacements'].append(
        deepcopy(c['arguments']['arrival_replacements'][0])), 'rejected'),
    (lambda c: c['arguments']['arrival_replacements'][0].update(
        {'receipt_id': 'foreign'}), 'rejected'),
    (lambda c: c['arguments']['assignments'][0].update(
        {'order_id': 'foreign'}), 'rejected'),
    (lambda c: c['arguments']['assignments'][0].pop('period_id'),
     'clarification_required'),
])
def test_untrusted_tool_data_cannot_spoof_or_omit_authorized_inputs(mutation, expected):
    _, context, tool_call = setup()
    mutation(tool_call)
    result = call(tool_call, context)
    assert result['status'] == expected
    assert 'result' not in result


def test_missing_arrays_clarifies_without_guessing():
    _, context, tool_call = setup()
    del tool_call['arguments']['arrival_replacements']
    result = call(tool_call, context)
    assert result['status'] == 'clarification_required'
    assert result['missing_fields'] == ['arguments.arrival_replacements']
    assert 'result' not in result


def test_empty_recovery_assignment_needs_clarification():
    _, context, tool_call = setup()
    tool_call['arguments']['assignments'] = []
    result = call(tool_call, context)
    assert result['status'] == 'clarification_required'
    assert result['missing_fields'] == ['arguments.assignments']
    assert 'result' not in result


def test_unknown_fields_are_rejected_even_alongside_missing_fields():
    _, context, tool_call = setup()
    del tool_call['arguments']['assignments']
    tool_call['arguments']['apply'] = True
    assert call(tool_call, context)['status'] == 'rejected'


@pytest.mark.parametrize('raw', [
    '{"name":"compare_receipt_recovery","name":"compare_receipt_recovery","arguments":{}}',
    '{"name":"compare_receipt_recovery","arguments":{}} trailing',
    '{"name":"compare_receipt_recovery","arguments":{"assignments":NaN}}',
    b'\xff',
    'x' * (128 * 1024 + 1),
    json.dumps({'name': TOOL_NAME, 'arguments': {'assignments': [[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]}}),
])
def test_malformed_call_fails_closed_without_echo(raw):
    _, context, _ = setup()
    result = execute_recovery_tool(raw, context, is_active=lambda *_: True)
    assert result['status'] == 'rejected'
    assert 'result' not in result
    assert (repr(raw) if isinstance(raw, bytes) else raw) not in str(result)


def test_array_bound_and_unknown_nested_field_reject():
    _, context, tool_call = setup()
    tool_call['arguments']['arrival_replacements'] = []
    tool_call['arguments']['assignments'] = [{}] * 257
    assert call(tool_call, context)['status'] == 'rejected'
    _, context, tool_call = setup()
    tool_call['arguments']['assignments'][0]['apply'] = True
    assert call(tool_call, context)['status'] == 'rejected'


def test_context_metadata_and_snapshot_are_trusted_and_validated():
    _, context, tool_call = setup()
    bad = TrustedRecoveryContext('foreign', context.snapshot_id,
                                 context.plan_version, context.scenario_id,
                                 context.session_id, context.turn_id, context.snapshot)
    assert call(tool_call, bad)['reason'] == 'context_mismatch'
    bad = TrustedRecoveryContext(context.workspace_id, context.snapshot_id,
                                 context.plan_version, context.scenario_id,
                                 context.session_id, context.turn_id,
                                 {**context.snapshot, 'inventory': [{'bad': 1}]})
    assert call(tool_call, bad)['status'] == 'rejected'


def test_public_authority_decides_stale_receipt_and_invalid_timestamp():
    _, context, tool_call = setup()
    tool_call['arguments']['arrival_replacements'][0]['expected_arrival_at'] = (
        '2026-10-10T08:00:00Z')
    result = call(tool_call, context)
    assert result['status'] == 'unsupported'
    assert result['result']['reason'] == 'stale_receipt'
    _, context, tool_call = setup()
    tool_call['arguments']['arrival_replacements'][0]['arrival_at'] = 'yesterday'
    invalid = call(tool_call, context)
    assert invalid['status'] == 'unsupported'
    assert invalid['result']['reason'] == 'invalid_timestamp'


def test_guard_before_invocation_and_after_computation_suppresses_result(monkeypatch):
    _, context, tool_call = setup()
    invoked = []
    real = voice_tools.compare_receipt_recovery

    def counting(*args):
        invoked.append(True)
        return real(*args)

    monkeypatch.setattr(voice_tools, 'compare_receipt_recovery', counting)
    assert call(tool_call, context, lambda *_: False)['status'] == 'suppressed'
    assert invoked == []
    count = [0]

    def second_false(*_):
        count[0] += 1
        return count[0] < 2

    assert call(tool_call, context, second_false)['status'] == 'suppressed'
    assert invoked == []
    active = [True]

    def cancel_during(*args):
        outcome = real(*args)
        active[0] = False
        return outcome

    monkeypatch.setattr(voice_tools, 'compare_receipt_recovery', cancel_during)
    result = call(tool_call, context, lambda *_: active[0])
    assert result['status'] == 'suppressed' and 'result' not in result
    assert execute_recovery_tool(json.dumps(tool_call), context,
                                 is_active=lambda *_: 1)['status'] == 'suppressed'


def test_execution_owns_snapshot_and_model_arrays_during_computation(monkeypatch):
    fixture, context, tool_call = setup()
    original = deepcopy(fixture)
    real = voice_tools.compare_receipt_recovery

    def mutate_caller_after_capture(snapshot, scenario, recovery):
        context.snapshot['orders'][0]['output_quantity'] = '999'
        tool_call['arguments']['assignments'][0]['period_id'] = 'foreign'
        return real(snapshot, scenario, recovery)

    monkeypatch.setattr(voice_tools, 'compare_receipt_recovery', mutate_caller_after_capture)
    result = call(tool_call, context)
    assert result['status'] == 'compared'
    assert result['result'] == real(original['snapshot'], original['scenario'],
                                    original['recovery'])
