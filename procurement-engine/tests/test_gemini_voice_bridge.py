"""Offline tests for the pinned SDK proposal boundary; no provider client."""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
from pathlib import Path
import socket

from google import genai
from google.genai import client as genai_client
from google.genai import types
import pytest

from procure.agents import voice_tools
from procure.agents.voice_tools import TrustedRecoveryContext, execute_recovery_tool, function_declaration
from procure.production.recovery import compare_receipt_recovery


EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts' / 'examples'


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('provider or credential access attempted')
    monkeypatch.setattr(genai, 'Client', forbidden)
    monkeypatch.setattr(genai_client.Client, '__init__', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket.socket, 'connect_ex', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)
    try:
        import google.auth
    except ImportError:
        pass
    else:
        monkeypatch.setattr(google.auth, 'default', forbidden)
        monkeypatch.setattr(google.auth._default, 'default', forbidden)
    try:
        import dotenv
    except ImportError:
        pass
    else:
        monkeypatch.setattr(dotenv, 'load_dotenv', forbidden)
        monkeypatch.setattr(dotenv, 'dotenv_values', forbidden)


def bridge():
    return importlib.import_module('procure.agents.gemini_voice_bridge')


def response(arguments=None):
    if arguments is None:
        arguments = {'arrival_replacements': [], 'assignments': [{'order_id': 'o', 'period_id': 'p'}]}
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=types.FinishReason.STOP,
        content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
            name='compare_receipt_recovery', args=arguments))]))])


def fixture_case(name):
    fixture = json.loads((EXAMPLES / name).read_text())
    snapshot = fixture['snapshot']
    context = TrustedRecoveryContext(
        workspace_id=snapshot['workspace_id'], snapshot_id=snapshot['snapshot_id'],
        plan_version=snapshot['plan_version'], scenario_id=fixture['scenario']['scenario_id'],
        session_id='session-1', turn_id='turn-1', snapshot=snapshot)
    arguments = {'arrival_replacements': fixture['scenario']['arrival_replacements'],
                 'assignments': fixture['recovery']['assignments']}
    return fixture, context, arguments


def test_declaration_is_exact_fresh_sdk_json_schema():
    module = bridge()
    expected = function_declaration()
    declaration = module.sdk_function_declaration()
    assert type(declaration) is types.FunctionDeclaration
    assert declaration.name == expected['name']
    assert declaration.description == expected['description']
    assert declaration.parameters is None
    assert declaration.parameters_json_schema == expected['parameters']
    declaration.parameters_json_schema['properties']['assignments']['items']['required'].clear()
    assert module.sdk_function_declaration().parameters_json_schema == expected['parameters']


def test_canonical_call_is_owned_and_preserves_strings():
    module = bridge()
    args = {'assignments': [{'period_id': '2026-10-12T00:00:00Z', 'order_id': 'o'}],
            'arrival_replacements': [{'receipt_id': 'r', 'expected_arrival_at': '2026-10-10T08:00:00Z',
                                      'arrival_at': '2026-10-10T09:00:00Z'}]}
    proposal = response(args)
    raw = module.proposal_call_bytes(proposal)
    assert type(raw) is bytes
    assert raw == json.dumps({'name': 'compare_receipt_recovery', 'arguments': args},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    args['assignments'][0]['order_id'] = 'changed'
    assert json.loads(raw)['arguments']['assignments'][0]['order_id'] == 'o'


@pytest.mark.parametrize('name', [
    'recovery_request.json', 'recovery_noop_request.json',
    'recovery_precision_request.json', 'recovery_unsupported_request.json',
])
def test_actual_sdk_proposal_executes_existing_public_synthetic_comparison(name):
    module = bridge()
    fixture, context, args = fixture_case(name)
    raw = module.proposal_call_bytes(response(args))
    outcome = execute_recovery_tool(raw, context, is_active=lambda *_: True)
    expected = compare_receipt_recovery(fixture['snapshot'], fixture['scenario'], fixture['recovery'])
    assert outcome['status'] == expected['status']
    assert outcome['read_only'] is True
    assert outcome['result'] == expected
    if name == 'recovery_request.json':
        assert outcome['result']['disrupted_baseline']['orders'][0]['demands'][0]['shortfall'] == '4'
        assert outcome['result']['recovery']['orders'][0]['late_by_seconds'] == '3600'


def test_missing_known_fields_pass_to_tool_clarification():
    module = bridge()
    _, context, args = fixture_case('recovery_request.json')
    del args['assignments'][0]['period_id']
    outcome = execute_recovery_tool(module.proposal_call_bytes(response(args)), context,
                                    is_active=lambda *_: True)
    assert outcome['status'] == 'clarification_required'
    assert outcome['missing_fields'] == ['arguments.assignments[].period_id']


def test_inactive_and_mismatched_context_do_not_invoke_numerical_tool(monkeypatch):
    module = bridge()
    _, context, args = fixture_case('recovery_request.json')
    raw = module.proposal_call_bytes(response(args))
    def forbidden(*_args):
        raise AssertionError('numerical tool invoked')
    monkeypatch.setattr(voice_tools, 'compare_receipt_recovery', forbidden)
    assert execute_recovery_tool(raw, context, is_active=lambda *_: False)['status'] == 'suppressed'
    foreign = TrustedRecoveryContext('foreign', context.snapshot_id, context.plan_version,
                                     context.scenario_id, context.session_id, context.turn_id, context.snapshot)
    result = execute_recovery_tool(raw, foreign, is_active=lambda *_: True)
    assert result['status'] == 'rejected' and result['reason'] == 'context_mismatch'


def test_replaced_turn_suppresses_completed_computation(monkeypatch):
    module = bridge()
    _, context, args = fixture_case('recovery_request.json')
    raw = module.proposal_call_bytes(response(args))
    real = voice_tools.compare_receipt_recovery
    active = [True]
    def replace(*values):
        result = real(*values)
        active[0] = False
        return result
    monkeypatch.setattr(voice_tools, 'compare_receipt_recovery', replace)
    outcome = execute_recovery_tool(raw, context, is_active=lambda *_: active[0])
    assert outcome['status'] == 'suppressed' and 'result' not in outcome


@pytest.mark.parametrize('mutation', [
    lambda r: setattr(r, 'candidates', []),
    lambda r: r.candidates.append(deepcopy(r.candidates[0])),
    lambda r: setattr(r.candidates[0], 'finish_reason', types.FinishReason.MAX_TOKENS),
    lambda r: setattr(r.candidates[0], 'continuation_token', b''),
    lambda r: setattr(r.candidates[0].content, 'role', 'user'),
    lambda r: r.candidates[0].content.parts.append(types.Part(text='extra')),
    lambda r: setattr(r.candidates[0].content.parts[0], 'text', ''),
    lambda r: setattr(r.candidates[0].content.parts[0], 'thought', True),
    lambda r: setattr(r.candidates[0].content.parts[0].function_call, 'name', 'send_email'),
    lambda r: setattr(r.candidates[0].content.parts[0].function_call, 'partial_args', []),
    lambda r: setattr(r.candidates[0].content.parts[0].function_call, 'will_continue', True),
    lambda r: setattr(r, 'automatic_function_calling_history', []),
    lambda r: setattr(r, 'parsed', {}),
    lambda r: setattr(r.candidates[0], 'safety_ratings', [types.SafetyRating(blocked=True)]),
    lambda r: setattr(r, 'prompt_feedback', types.GenerateContentResponsePromptFeedback(block_reason_message='blocked')),
])
def test_incomplete_ambiguous_or_blocked_proposal_rejects(mutation):
    module = bridge()
    proposal = response()
    mutation(proposal)
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)


@pytest.mark.parametrize('arguments', [
    {'workspace_id': 'foreign'},
    {'assignments': [{'order_id': 'o', 'period_id': 'p', 'apply': 'yes'}]},
    {'assignments': [{'order_id': 'o', 'period_id': 1}]},
    {'assignments': [{}] * 257},
    {'assignments': [{'order_id': 'x' * 131072}]},
    {'assignments': [[[[[[[[[[[[[[[[['deep']]]]]]]]]]]]]]]]]},
])
def test_forbidden_malformed_or_oversized_args_reject(arguments):
    module = bridge()
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(response(arguments))


def test_fake_response_and_sdk_version_mismatch_fail_closed(monkeypatch):
    module = bridge()
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes({'candidates': []})
    monkeypatch.setattr(module.metadata, 'version', lambda _name: '2.28.0')
    with pytest.raises(module.ProposalError):
        module.sdk_function_declaration()
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(response())


def test_malformed_sdk_storage_and_hostile_accessors_fail_without_leaking_values():
    module = bridge()
    class Hostile(types.GenerateContentResponse):
        @property
        def candidates(self):
            raise RuntimeError('private-proposal-content')
    with pytest.raises(module.ProposalError) as caught:
        module.proposal_call_bytes(Hostile.model_construct())
    assert 'private-proposal-content' not in str(caught.value)

    proposal = response()
    proposal.candidates[0].safety_ratings = [types.SafetyRating.model_construct(blocked='maybe')]
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)

    proposal = response()
    proposal.candidates[0].content.parts[0].__dict__['unexpected_authority'] = 'apply'
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)


def test_aggregate_string_budget_rejects_before_serialization(monkeypatch):
    module = bridge()
    proposal = response({'assignments': [{'order_id': str(i), 'period_id': 'x' * 1000}
                                         for i in range(200)]})
    serialized = []
    real = module.json.dumps
    def tracking(*args, **kwargs):
        serialized.append(True)
        return real(*args, **kwargs)
    monkeypatch.setattr(module.json, 'dumps', tracking)
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)
    assert serialized == []


@pytest.mark.parametrize('field', ['role', 'name'])
def test_mutated_sdk_identity_fields_do_not_run_custom_equality(field):
    module = bridge()
    compared = []
    class EqualityTrap:
        def __eq__(self, _other):
            compared.append(True)
            return True
    proposal = response()
    if field == 'role':
        proposal.candidates[0].content.__dict__['role'] = EqualityTrap()
    else:
        proposal.candidates[0].content.parts[0].function_call.__dict__['name'] = EqualityTrap()
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)
    assert compared == []


def test_mutated_sdk_storage_rejects_before_hashing_keys_or_truth_testing_extra():
    module = bridge()
    invoked = []
    class HashTrap:
        def __hash__(self):
            invoked.append('hash')
            return 1
    class BoolTrap:
        def __bool__(self):
            invoked.append('bool')
            return False
    proposal = response()
    proposal.__dict__[HashTrap()] = 'private'
    invoked.clear()
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)
    assert invoked == []

    proposal = response()
    proposal.__dict__.update({i: None for i in range(1000)})
    proposal.__dict__[HashTrap()] = 'private'
    invoked.clear()
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)
    assert invoked == []

    proposal = response()
    object.__setattr__(proposal, '__pydantic_extra__', BoolTrap())
    with pytest.raises(module.ProposalError):
        module.proposal_call_bytes(proposal)
    assert invoked == []
