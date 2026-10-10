"""Offline-only proposal source contracts. No provider client or service is constructed."""
from __future__ import annotations

import json

import pytest


def projection():
    return {
        'receipts': [{'receipt_id': 'receipt-1', 'arrival_at': '2026-10-10T12:00:00Z'}],
        'orders': [{'order_id': 'order-1'}, {'order_id': 'order-2'}],
        'periods': [{'period_id': 'period-1'}, {'period_id': 'period-2'}],
        'snapshot_id': 'snapshot-1', 'plan_version': 'version-1',
        'as_of': '2026-10-10T00:00:00Z',
    }


def arguments():
    return {
        'arrival_replacements': [{'receipt_id': 'receipt-1',
                                  'expected_arrival_at': '2026-10-10T12:00:00Z',
                                  'arrival_at': '2026-10-11T12:00:00Z'}],
        'assignments': [{'order_id': 'order-1', 'period_id': 'period-1'},
                        {'order_id': 'order-2', 'period_id': 'period-2'}],
    }


def sdk_response(args=None, *, parts=None, finish=None, blocked=False):
    from google.genai import types

    call = types.FunctionCall(name='compare_receipt_recovery', args=args if args is not None else arguments())
    content = types.Content(role='model', parts=parts if parts is not None else [types.Part(function_call=call)])
    candidate = types.Candidate(content=content, finish_reason=finish or types.FinishReason.STOP)
    feedback = types.GenerateContentResponsePromptFeedback(block_reason='SAFETY') if blocked else None
    return types.GenerateContentResponse(candidates=[candidate], prompt_feedback=feedback)


def sdk_continuation():
    from google.genai import types

    return sdk_response(parts=[types.Part(function_call=types.FunctionCall(
        name='compare_receipt_recovery', args=arguments(), will_continue=True))])


def sdk_multiple_candidates():
    response = sdk_response()
    response.candidates.append(response.candidates[0])
    return response


def sdk_wrong_tool():
    from google.genai import types

    return sdk_response(parts=[types.Part(function_call=types.FunctionCall(name='other_tool', args=arguments()))])


def test_one_detached_fake_call_and_pinned_bridge_bytes():
    from procure.agents.gemini_voice_source import propose_once

    source = projection()
    seen = []

    def fake(request):
        seen.append(request)
        assert request['projection']['receipts'][0]['receipt_id'] == 'receipt-1'
        assert request['utterance'] == 'Move receipt-1 to 11 October and assign both orders.'
        assert set(request) == {'projection', 'utterance', 'tool'}
        request['projection']['receipts'][0]['receipt_id'] = 'mutated-by-fake'
        return sdk_response()

    result = propose_once(source, 'Move receipt-1 to 11 October and assign both orders.', fake,
                          is_cancelled=lambda: False)
    assert len(seen) == 1
    assert source == projection()
    assert json.loads(result) == {'name': 'compare_receipt_recovery', 'arguments': arguments()}
    assert isinstance(result, bytes)


@pytest.mark.parametrize('change', [
    lambda p: p.update({'source_text': 'private document text'}),
    lambda p: p['receipts'][0].update({'supplier_name': 'private supplier'}),
    lambda p: p['receipts'].append(dict(p['receipts'][0])),
    lambda p: p['orders'].append(dict(p['orders'][0])),
    lambda p: p['periods'].append(dict(p['periods'][0])),
    lambda p: p['receipts'][0].update({'arrival_at': '2026-10-10T12:00:00+00:00'}),
    lambda p: p['receipts'][0].update({'receipt_id': 'x' * 129}),
    lambda p: p.update({'orders': [{'order_id': 'order-' + str(i)} for i in range(65)]}),
    lambda p: p.update({'receipts': [{'receipt_id': f'r{i}', 'arrival_at': '2026-10-10T12:00:00Z'} for i in range(65)]}),
    lambda p: p.update({'periods': [{'period_id': f'p{i}'} for i in range(65)]}),
])
def test_projection_rejects_invalid_fields_counts_duplicates_and_times_before_fake(change):
    from procure.agents.gemini_voice_source import SourceError, propose_once

    source = projection()
    change(source)
    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(source, 'Move receipt-1.', lambda request: calls.append(request),
                     is_cancelled=lambda: False)
    assert calls == []


@pytest.mark.parametrize('utterance', ['', '   ', 'x' * 4097, 'é' * 2049],
                         ids=['empty', 'blank', 'ascii_oversize', 'utf8_oversize'])
def test_utterance_utf8_cap_before_fake(utterance):
    from procure.agents.gemini_voice_source import SourceError, propose_once

    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(projection(), utterance, lambda request: calls.append(request),
                     is_cancelled=lambda: False)
    assert calls == []


def test_aggregate_utf8_cap_before_fake():
    from procure.agents.gemini_voice_source import SourceError, propose_once

    source = {'receipts': [], 'orders': [], 'periods': []}
    for i in range(64):
        source['receipts'].append({'receipt_id': f'r{i}-' + 'r' * 120,
                                   'arrival_at': '2026-10-10T12:00:00Z'})
        source['orders'].append({'order_id': f'o{i}-' + 'o' * 120})
        source['periods'].append({'period_id': f'p{i}-' + 'p' * 120})
    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(source, 'x' * 4096, lambda request: calls.append(request),
                     is_cancelled=lambda: False)
    assert calls == []


class PoisonKey:
    def __init__(self, collision):
        self.armed = False
        self.calls = 0
        self.hash_value = hash(collision)

    def __hash__(self):
        if self.armed:
            self.calls += 1
            raise AssertionError('source invoked custom key hash')
        return self.hash_value

    def __eq__(self, other):
        if self.armed:
            self.calls += 1
            raise AssertionError('source invoked custom key equality')
        return self is other


@pytest.mark.parametrize('placement', ['top', 'large_top', 'row', 'bounded_top', 'bounded_row'])
def test_nonbuiltin_keys_refused_before_hash_or_equality(placement):
    from procure.agents.gemini_voice_source import SourceError, propose_once

    source = projection()
    poison = PoisonKey('receipt_id' if placement in ('row', 'bounded_row') else
                       'snapshot_id' if placement == 'bounded_top' else 'receipts')
    if placement == 'top':
        source[poison] = 'unexpected'
    elif placement == 'large_top':
        source.update({f'extra_{i}': 'unexpected' for i in range(1000)})
        source[poison] = 'unexpected'
    elif placement == 'bounded_top':
        source.pop('snapshot_id')
        source[poison] = 'unexpected'
        assert len(source) == 6
    elif placement == 'bounded_row':
        source['receipts'][0].pop('receipt_id')
        source['receipts'][0][poison] = 'unexpected'
        assert len(source['receipts'][0]) == 2
    else:
        source['receipts'][0][poison] = 'unexpected'
    poison.armed = True
    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(source, 'Move receipt-1.', lambda request: calls.append(request),
                     is_cancelled=lambda: False)
    assert poison.calls == 0
    assert calls == []


def test_entire_callback_request_includes_fixed_tool_at_32_kib_boundary():
    from procure.agents.gemini_voice_bridge import sdk_function_declaration
    from procure.agents.gemini_voice_source import SourceError, propose_once

    source = {'receipts': [], 'orders': [], 'periods': []}
    for i in range(64):
        source['receipts'].append({'receipt_id': f'r{i}-' + 'r' * 120,
                                   'arrival_at': '2026-10-10T12:00:00Z'})
        source['orders'].append({'order_id': f'o{i}-' + 'o' * 120})
        source['periods'].append({'period_id': f'p{i}-' + 'p' * 120})
    tool_json = sdk_function_declaration().model_dump_json(by_alias=True, exclude_none=True)
    assert len(tool_json.encode('utf-8')) <= 8192
    tool = json.loads(tool_json)
    def size(utterance):
        return len(json.dumps({'utterance': utterance, 'projection': source, 'tool': tool},
                              separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    allowed_count = 32768 - size('')
    assert 1 <= allowed_count < 4096
    assert size('x' * allowed_count) == 32768
    assert len(json.dumps({'utterance': 'x' * (allowed_count + 1), 'projection': source},
                          separators=(',', ':'), ensure_ascii=False).encode('utf-8')) <= 32768
    args = {
        'arrival_replacements': [{'receipt_id': source['receipts'][0]['receipt_id'],
                                  'expected_arrival_at': source['receipts'][0]['arrival_at'],
                                  'arrival_at': '2026-10-11T12:00:00Z'}],
        'assignments': [{'order_id': order['order_id'], 'period_id': source['periods'][0]['period_id']}
                        for order in source['orders']],
    }
    calls = []
    def fake(request):
        calls.append(request)
        return sdk_response(args)
    assert json.loads(propose_once(source, 'x' * allowed_count, fake,
                                   is_cancelled=lambda: False))['arguments'] == args
    assert len(calls) == 1
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(source, 'x' * (allowed_count + 1), fake,
                     is_cancelled=lambda: False)
    assert len(calls) == 1


@pytest.mark.parametrize('change', [
    lambda a: a.update({'arrival_replacements': []}),
    lambda a: a['arrival_replacements'][0].update({'arrival_at': '2026-10-10T12:00:00Z'}),
    lambda a: a['arrival_replacements'][0].update({'receipt_id': 'other'}),
    lambda a: a['arrival_replacements'][0].update({'expected_arrival_at': '2026-10-09T12:00:00Z'}),
    lambda a: a['arrival_replacements'][0].update({'arrival_at': 'tomorrow'}),
    lambda a: a['arrival_replacements'].append(dict(a['arrival_replacements'][0])),
    lambda a: a.update({'assignments': a['assignments'][:1]}),
    lambda a: a['assignments'][0].update({'order_id': 'other'}),
    lambda a: a['assignments'][0].update({'period_id': 'other'}),
    lambda a: a['assignments'][1].update({'order_id': 'order-1'}),
])
def test_bad_typed_references_and_noop_refused(change):
    from procure.agents.gemini_voice_source import SourceError, propose_once

    args = arguments()
    change(args)
    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(projection(), 'Move receipt-1.',
                     lambda request: calls.append(request) or sdk_response(args),
                     is_cancelled=lambda: False)
    assert len(calls) == 1


@pytest.mark.parametrize('bad_response', [
    lambda: object(),
    lambda: sdk_response(parts=[]),
    lambda: sdk_response(parts=[sdk_response().candidates[0].content.parts[0]] * 2),
    lambda: sdk_response(blocked=True),
    sdk_continuation,
    sdk_multiple_candidates,
    sdk_wrong_tool,
])
def test_malformed_sdk_response_is_sanitized_without_retry(bad_response):
    from procure.agents.gemini_voice_source import SourceError, propose_once

    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$') as exc:
        propose_once(projection(), 'Move receipt-1.',
                     lambda request: calls.append(request) or bad_response(),
                     is_cancelled=lambda: False)
    assert len(calls) == 1
    assert exc.value.__cause__ is None


def test_wrong_sdk_version_refuses_before_fake(monkeypatch):
    from procure.agents import gemini_voice_bridge
    from procure.agents.gemini_voice_source import SourceError, propose_once

    monkeypatch.setattr(gemini_voice_bridge.metadata, 'version', lambda name: '0.0.0')
    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(projection(), 'Move receipt-1.', lambda request: calls.append(request),
                     is_cancelled=lambda: False)
    assert calls == []


def test_cancellation_and_fake_exception_are_sanitized():
    from procure.agents.gemini_voice_source import SourceError, propose_once

    calls = []
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(projection(), 'Move receipt-1.', lambda request: calls.append(request),
                     is_cancelled=lambda: True)
    assert calls == []

    cancelled = False

    def fake(request):
        nonlocal cancelled
        calls.append(request)
        cancelled = True
        return sdk_response()

    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        propose_once(projection(), 'Move receipt-1.', fake,
                     is_cancelled=lambda: cancelled)
    assert len(calls) == 1

    def failed(request):
        calls.append(request)
        raise RuntimeError('secret prompt should never surface')

    with pytest.raises(SourceError, match='^invalid offline proposal$') as exc:
        propose_once(projection(), 'Move receipt-1.', failed,
                     is_cancelled=lambda: False)
    assert len(calls) == 2
    assert exc.value.__cause__ is None
    assert 'secret' not in str(exc.value)


def test_configured_fake_sdk_single_tool_request_and_canonical_bytes(monkeypatch):
    from google import genai
    from google.genai import client as genai_client, types
    from procure.agents import gemini_voice_source

    def unexpected_client(*args, **kwargs):
        raise AssertionError('SDK Client construction is prohibited')

    monkeypatch.setattr(genai, 'Client', unexpected_client)
    monkeypatch.setattr(genai_client.Client, '__init__', unexpected_client)

    configured = getattr(gemini_voice_source, 'propose_configured_once', None)
    if not callable(configured):
        raise AssertionError('configured_source_missing')
    source = projection()
    seen = []

    def fake_generate_content(*, model, contents, config):
        seen.append((model, contents, config))
        assert model == 'gemini-2.5-flash'
        assert type(contents) is str
        payload = json.loads(contents)
        assert set(payload) == {'utterance', 'projection'}
        assert payload == {
            'utterance': 'Move receipt-1 to 11 October and assign both orders.',
            'projection': projection(),
        }
        assert len(contents.encode('utf-8')) <= 32 << 10
        assert type(config) is types.GenerateContentConfig
        assert config.candidate_count == 1
        assert config.max_output_tokens == 512
        assert type(config.tools) is list and len(config.tools) == 1
        declarations = config.tools[0].function_declarations
        assert type(declarations) is list and len(declarations) == 1
        assert declarations[0].name == 'compare_receipt_recovery'
        calling = config.tool_config.function_calling_config
        assert calling.mode is types.FunctionCallingConfigMode.ANY
        assert calling.allowed_function_names == ['compare_receipt_recovery']
        assert config.automatic_function_calling.disable is True
        assert config.http_options.timeout == 3500
        assert config.http_options.retry_options.attempts == 1
        return sdk_response()

    result = configured(
        source,
        'Move receipt-1 to 11 October and assign both orders.',
        model='gemini-2.5-flash',
        generate_content=fake_generate_content,
        is_cancelled=lambda: False,
    )
    assert len(seen) == 1
    assert source == projection()
    assert isinstance(result, bytes)
    assert json.loads(result) == {
        'name': 'compare_receipt_recovery',
        'arguments': arguments(),
    }


def test_configured_fake_sdk_invalid_model_and_pre_cancel_no_call():
    from procure.agents import gemini_voice_source
    from procure.agents.gemini_voice_source import SourceError

    configured = getattr(gemini_voice_source, 'propose_configured_once', None)
    if not callable(configured):
        raise AssertionError('configured_source_missing')
    calls = []

    def fake_generate_content(**kwargs):
        calls.append(kwargs)
        return sdk_response()

    for model in ('', 'unsafe\nmodel'):
        with pytest.raises(SourceError, match='^invalid offline proposal$'):
            configured(projection(), 'Move receipt-1.', model=model,
                       generate_content=fake_generate_content,
                       is_cancelled=lambda: False)
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        configured(projection(), 'Move receipt-1.', model='gemini-2.5-flash',
                   generate_content=fake_generate_content,
                   is_cancelled=lambda: True)
    assert calls == []


def test_configured_fake_sdk_bad_response_exception_and_post_cancel_no_retry():
    from google.genai import types
    from procure.agents import gemini_voice_source
    from procure.agents.gemini_voice_source import SourceError

    configured = getattr(gemini_voice_source, 'propose_configured_once', None)
    if not callable(configured):
        raise AssertionError('configured_source_missing')
    calls = []

    def run(fake, *, is_cancelled=lambda: False):
        with pytest.raises(SourceError, match='^invalid offline proposal$') as exc:
            configured(projection(), 'Move receipt-1.', model='gemini-2.5-flash',
                       generate_content=fake, is_cancelled=is_cancelled)
        assert exc.value.__cause__ is None
        assert 'private fake failure' not in str(exc.value)

    def malformed(**kwargs):
        calls.append(kwargs)
        return sdk_response(parts=[])

    def failed(**kwargs):
        calls.append(kwargs)
        raise RuntimeError('private fake failure')

    cancelled = False

    def cancel_after_request(**kwargs):
        nonlocal cancelled
        calls.append(kwargs)
        cancelled = True
        return sdk_response()

    run(malformed)
    assert len(calls) == 1
    run(failed)
    assert len(calls) == 2
    run(cancel_after_request, is_cancelled=lambda: cancelled)
    assert len(calls) == 3

    valid_part = sdk_response().candidates[0].content.parts[0]
    bad_responses = (
        sdk_multiple_candidates,
        lambda: sdk_response(parts=[valid_part, types.Part(text='unexpected extra part')]),
        lambda: sdk_response(parts=[valid_part, valid_part]),
        sdk_continuation,
        lambda: sdk_response(finish=types.FinishReason.MAX_TOKENS),
    )
    for make_response in bad_responses:
        before = len(calls)

        def one_bad_response(**kwargs):
            calls.append(kwargs)
            return make_response()

        run(one_bad_response)
        assert len(calls) == before + 1


def test_configured_fake_sdk_input_caps_and_detached_context():
    from procure.agents import gemini_voice_source
    from procure.agents.gemini_voice_bridge import sdk_function_declaration
    from procure.agents.gemini_voice_source import SourceError

    configured = getattr(gemini_voice_source, 'propose_configured_once', None)
    if not callable(configured):
        raise AssertionError('configured_source_missing')
    source = projection()
    calls = []

    def fake_generate_content(*, model, contents, config):
        calls.append((model, contents, config))
        payload = json.loads(contents)
        payload['projection']['receipts'][0]['receipt_id'] = 'mutated-copy'
        return sdk_response()

    result = configured(
        source, 'Move receipt-1.', model='gemini-2.5-flash',
        generate_content=fake_generate_content, is_cancelled=lambda: False,
    )
    assert isinstance(result, bytes)
    assert len(calls) == 1
    assert source == projection()

    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        configured(projection(), 'x' * 4097, model='gemini-2.5-flash',
                   generate_content=fake_generate_content,
                   is_cancelled=lambda: False)
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        configured(projection(), 'é' * 2049, model='gemini-2.5-flash',
                   generate_content=fake_generate_content,
                   is_cancelled=lambda: False)
    oversized = projection()
    oversized['orders'] = [
        {'order_id': f'order-{index}'} for index in range(65)
    ]
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        configured(oversized, 'Move receipt-1.', model='gemini-2.5-flash',
                   generate_content=fake_generate_content,
                   is_cancelled=lambda: False)
    assert len(calls) == 1

    # Exact UTF-8 utterance ceiling is accepted when the full request is small.
    edge = configured(projection(), 'é' * 2048, model='gemini-2.5-flash',
                      generate_content=fake_generate_content,
                      is_cancelled=lambda: False)
    assert isinstance(edge, bytes) and len(calls) == 2

    aggregate = {'receipts': [], 'orders': [], 'periods': []}
    for index in range(64):
        aggregate['receipts'].append({'receipt_id': f'r{index}-' + 'r' * 120,
                                      'arrival_at': '2026-10-10T12:00:00Z'})
        aggregate['orders'].append({'order_id': f'o{index}-' + 'o' * 120})
        aggregate['periods'].append({'period_id': f'p{index}-' + 'p' * 120})
    tool = json.loads(sdk_function_declaration().model_dump_json(
        by_alias=True, exclude_none=True))

    def entire_size(utterance):
        return len(json.dumps({'utterance': utterance, 'projection': aggregate,
                               'tool': tool}, separators=(',', ':'),
                              ensure_ascii=False).encode('utf-8'))

    allowed_count = 32768 - entire_size('')
    assert 1 <= allowed_count < 4096
    assert entire_size('x' * allowed_count) == 32768
    assert entire_size('x' * (allowed_count + 1)) == 32769
    complete_args = {
        'arrival_replacements': [{
            'receipt_id': aggregate['receipts'][0]['receipt_id'],
            'expected_arrival_at': aggregate['receipts'][0]['arrival_at'],
            'arrival_at': '2026-10-11T12:00:00Z',
        }],
        'assignments': [
            {'order_id': order['order_id'], 'period_id': aggregate['periods'][0]['period_id']}
            for order in aggregate['orders']
        ],
    }
    boundary_calls = []

    def boundary_fake(*, model, contents, config):
        boundary_calls.append((model, contents, config))
        return sdk_response(complete_args)

    assert isinstance(configured(aggregate, 'x' * allowed_count,
                                 model='gemini-2.5-flash',
                                 generate_content=boundary_fake,
                                 is_cancelled=lambda: False), bytes)
    assert len(boundary_calls) == 1
    with pytest.raises(SourceError, match='^invalid offline proposal$'):
        configured(aggregate, 'x' * (allowed_count + 1),
                   model='gemini-2.5-flash', generate_content=boundary_fake,
                   is_cancelled=lambda: False)
    assert len(boundary_calls) == 1
