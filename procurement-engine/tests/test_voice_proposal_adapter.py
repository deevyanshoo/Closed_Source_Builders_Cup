"""Offline private projection-to-SDK mapping; provider access is forbidden."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

from google import genai
from google.genai import client as genai_client, types
import pytest

from procure.agents.gemini_voice_bridge import proposal_call_bytes
from procure.agents.voice_tools import TrustedRecoveryContext, execute_recovery_tool


EXAMPLES = Path(__file__).resolve().parents[2] / 'contracts' / 'examples'


@pytest.fixture(autouse=True)
def no_provider(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('provider, network or credential access attempted')
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


def adapter():
    return importlib.import_module('procure.agents.voice_proposal_adapter')


def sdk_response(arguments=None):
    if arguments is None:
        arguments = {'arrival_replacements': [], 'assignments': [{'order_id': 'o', 'period_id': 'p'}]}
    return types.GenerateContentResponse(candidates=[types.Candidate(
        finish_reason=types.FinishReason.STOP,
        content=types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(
            name='compare_receipt_recovery', args=arguments))]))])


def envelope(response):
    return {'proposal_version': '1',
            'sdk_response': response.model_dump(mode='json', exclude_none=True)}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def public_case(name):
    fixture = json.loads((EXAMPLES / name).read_text())
    snapshot = fixture['snapshot']
    context = TrustedRecoveryContext(
        workspace_id=snapshot['workspace_id'], snapshot_id=snapshot['snapshot_id'],
        plan_version=snapshot['plan_version'], scenario_id=fixture['scenario']['scenario_id'],
        session_id='session-1', turn_id='turn-1', snapshot=snapshot)
    arguments = {'arrival_replacements': fixture['scenario']['arrival_replacements'],
                 'assignments': fixture['recovery']['assignments']}
    return context, arguments


@pytest.mark.parametrize('name', [
    'recovery_request.json', 'recovery_noop_request.json',
    'recovery_precision_request.json', 'recovery_unsupported_request.json',
])
def test_projected_actual_sdk_response_maps_to_identical_owned_bytes(name):
    module = adapter()
    _, arguments = public_case(name)
    sdk = sdk_response(arguments)
    request = envelope(sdk)
    raw = module.map_local_proposal(encoded(request))
    assert type(raw) is bytes
    assert raw == proposal_call_bytes(sdk)
    arguments['assignments'][0]['order_id'] = 'changed'
    assert json.loads(raw)['arguments']['assignments'][0]['order_id'] != 'changed'


def test_missing_critical_known_field_reaches_existing_clarification():
    module = adapter()
    context, arguments = public_case('recovery_request.json')
    del arguments['assignments'][0]['period_id']
    raw = module.map_local_proposal(encoded(envelope(sdk_response(arguments))))
    outcome = execute_recovery_tool(raw, context, is_active=lambda *_: True)
    assert outcome['status'] == 'clarification_required'
    assert outcome['missing_fields'] == ['arguments.assignments[].period_id']


@pytest.mark.parametrize('change', [
    lambda r: r.update({'proposal_version': '2'}),
    lambda r: r.update({'workspace_id': 'foreign'}),
    lambda r: r['sdk_response'].update({'usage_metadata': {'total_token_count': 1}}),
    lambda r: r['sdk_response'].update({'candidates': []}),
    lambda r: r['sdk_response']['candidates'].append(r['sdk_response']['candidates'][0]),
    lambda r: r['sdk_response']['candidates'][0].update({'finish_reason': 'MAX_TOKENS'}),
    lambda r: r['sdk_response']['candidates'][0].update({'continuation_token': ''}),
    lambda r: r['sdk_response']['candidates'][0].update({'safety_ratings': [{'blocked': True}]}),
    lambda r: r['sdk_response'].update({'prompt_feedback': {'block_reason_message': 'blocked'}}),
    lambda r: r['sdk_response']['candidates'][0]['content'].update({'role': 'user'}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'].append({'text': 'extra'}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0].update({'text': ''}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0].update({'thought_signature': 'opaque'}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0]['function_call'].update({'partial_args': []}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0]['function_call'].update({'will_continue': True}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0]['function_call']['args'].update({'workspace_id': 'foreign'}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0]['function_call']['args']['assignments'][0].update({'apply': 'yes'}),
    lambda r: r['sdk_response']['candidates'][0]['content']['parts'][0]['function_call']['args']['assignments'][0].update({'period_id': 1}),
])
def test_ambiguous_incomplete_authority_or_coercible_projection_rejects(change):
    module = adapter()
    request = envelope(sdk_response())
    change(request)
    with pytest.raises(ValueError, match='invalid local proposal'):
        module.map_local_proposal(encoded(request))


@pytest.mark.parametrize('raw', [
    b'{"proposal_version":"1","proposal_version":"1","sdk_response":{}}',
    b'{"proposal_version":"1","sdk_response":{}} trailing',
    b'{"proposal_version":"1","sdk_response":{"candidates":NaN}}',
    b'\xff',
    b'x' * ((256 << 10) + 1),
    encoded({'proposal_version': '1', 'sdk_response': {'candidates': [[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]}}),
    encoded({'proposal_version': '1', 'sdk_response': {'candidates': [{}] * 257}}),
])
def test_malformed_duplicate_deep_or_oversized_input_rejects(raw):
    module = adapter()
    with pytest.raises(ValueError, match='invalid local proposal'):
        module.map_local_proposal(raw)


def test_cli_success_is_canonical_bytes_and_failure_is_fixed(monkeypatch, capsys):
    module = adapter()
    request = encoded(envelope(sdk_response()))
    class Input:
        buffer = SimpleNamespace(read=lambda _limit: request)
    output = bytearray()
    class Output:
        buffer = SimpleNamespace(write=lambda value: output.extend(value))
    monkeypatch.setattr(sys, 'stdin', Input())
    monkeypatch.setattr(sys, 'stdout', Output())
    assert module.main() == 0
    assert bytes(output) == module.map_local_proposal(request)
    assert capsys.readouterr().err == ''

    output.clear()
    Input.buffer = SimpleNamespace(read=lambda _limit: b'bad')
    assert module.main() == 1
    assert bytes(output) == b'{"proposal_version":"1","error":"invalid_proposal"}'
    assert capsys.readouterr().err == ''


def test_actual_cli_subprocess_uses_fixed_stdin_stdout_protocol():
    source = Path(__file__).resolve().parents[1] / 'src'
    env = {'PATH': '/usr/bin:/bin', 'PYTHONPATH': str(source),
           'PYTHONDONTWRITEBYTECODE': '1'}
    request = encoded(envelope(sdk_response()))
    success = subprocess.run([sys.executable, '-m', 'procure.agents.voice_proposal_adapter'],
                             input=request, capture_output=True, env=env, timeout=10, check=False)
    assert success.returncode == 0
    assert success.stdout == proposal_call_bytes(sdk_response())
    assert success.stderr == b''
    invalid = subprocess.run([sys.executable, '-m', 'procure.agents.voice_proposal_adapter'],
                             input=b'bad', capture_output=True, env=env, timeout=10, check=False)
    assert invalid.returncode == 1
    assert invalid.stdout == b'{"proposal_version":"1","error":"invalid_proposal"}'
    assert invalid.stderr == b''
