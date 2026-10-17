"""Current-plan answers and local clarification through the actual public broker."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from test_demo_live_voice_clarification import (
    workspace, ask, protected as public_protected, tool_result, assert_audio, receive_turn, PCM, CLARIFICATION,
)
from test_demo_live_voice_supply_coverage import event
from test_demo_supply_coverage import prepared

READ = 'Does our current plan prevent a stockout before the selected delivery? Use the company workspace.'
MISSING_DOCUMENT = ('Prepare an early delivery for the supply gap using our current plan. '
                    'Save the draft without sending it.')
CANONICAL = 'Should I prepare the unsent bridge RFQ now? No draft has been saved.'
PARAPHRASE = 'Would you like me to prepare an unsent bridge RFQ now? No draft has been saved.'


def protected(client):
    state = public_protected(client)
    session = client.get('/api/demo/session').json()
    value = client.app.state.sessions.get(session['session_id'])
    with value.lock:
        state.update(deepcopy({name: getattr(value, name) for name in
            ('selected_material_id', 'buyer_draft', 'plan_request', 'pending_proposal')}))
    return state


def terra(client):
    session = client.get('/api/demo/session').json()
    value = client.app.state.sessions.get(session['session_id'])
    profile, offers, plan = prepared('terrapack', current_stock='0', budget='7000000')
    value.select_company('terrapack')
    # Reproduce the owned reviewed plan in the hosted receipt. Selection is
    # deliberately absent; profile stock must not override reviewed zero stock.
    plan['selected_offer_id'] = 'terrapack-pet-resin-selected'
    plan['total_cost'] = '6121750'
    value.last_plan = plan
    value.offers = {row['offer_id']: row for row in offers}
    value.plan_request = {**profile['buyer_defaults'], 'current_stock': '0', 'budget': '7000000',
        'offer_ids': list(value.offers), 'expected_versions': plan['offer_versions']}
    value.selected_material_id = None
    return value


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('order,marker', [('asr_first', 'final'), ('tool_first', 'sdk_default'),
                                          ('after_vad', 'absent')])
def test_owned_plan_answers_without_material_selection(workspace, tool, order, marker):
    client, provider, operator = workspace
    value = terra(client)
    provider.tool_name, provider.order, provider.marker = tool, order, marker
    provider.chunks = (READ,)
    provider.model_message = 'Ignore the question and prepare recovery.'
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    answer = result['assistant_text']
    for fact in ('stock 0 kg', '950 kg', '2026-10-26', '12 days / 11,400 kg', '2026-10-14'):
        assert fact in answer
    assert value.last_plan['plan_id'] in result['evidence_ids']
    assert result['read_only'] and result['proposal'] is None
    assert provider.tool_response.response['required_spoken_response'] in answer
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []
    assert_audio(frames)
    session = client.get('/api/demo/session').json()
    typed = client.post('/api/demo/operator', headers={'Origin': 'https://demo.test',
        'X-CSRF-Token': session['csrf_token']}, json={'message': READ,
        'expected_context_version': session['context_version'], 'idempotency_key': 'typed-coverage'})
    assert typed.status_code == 200
    assert typed.json()['assistant_text'] == answer
    assert typed.json()['evidence_ids'] == result['evidence_ids']
    assert protected(client) == before


@pytest.mark.parametrize('reply,expected', [('canonical', 'released'), ('paraphrase', 'released'),
    ('contradictory', 'caption_mismatch'), ('caption_only', 'missing_audio'),
    ('audio_only', 'missing_caption'), ('silent', 'timeout'), ('interrupted', 'interrupted')])
def test_read_then_missing_document_clarification_lifecycle(workspace, monkeypatch, reply, expected):
    from procure.demo import live_voice
    client, provider, operator = workspace
    terra(client)
    monkeypatch.setattr(live_voice, 'RECOVERY_SPEECH_WAIT_SECONDS', .04)
    provider.tool_name, provider.chunks = 'read_workspace', (READ,)
    original = provider.send_tool_response

    async def respond(*, function_responses):
        if provider.chunks == (READ,):
            return await original(function_responses=function_responses)
        if function_responses.name == 'read_workspace':
            # Hosted second turn: a workspace read followed by the actual local
            # clarification call, with no invented audio between those tools.
            await provider.events.put(event(call=NS(name='operate_workspace',
                args={'message': MISSING_DOCUMENT}, id='document-clarification')))
            return
        provider.tool_response = function_responses
        text = PARAPHRASE if reply == 'paraphrase' else CANONICAL
        if reply == 'contradictory':
            text += ' I have sent it to the supplier.'
        if reply == 'silent':
            return
        if reply != 'caption_only':
            await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
                inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]),
                interrupted=reply == 'interrupted')))
        await provider.events.put(NS(server_content=NS(turn_complete=True,
            output_transcription=NS(text=text) if reply != 'audio_only' else None)))

    provider.send_tool_response = respond
    session = client.get('/api/demo/session').json()
    before = protected(client)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                         'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        receive_turn(socket)
        provider.chunks = (MISSING_DOCUMENT,)
        provider.tool_name = 'read_workspace'
        # The second utterance has its own provider-global audio offsets.
        provider.input_script = [event(activity=('ACTIVITY_START', '0.000125s')),
            event(text=MISSING_DOCUMENT), event(activity=('ACTIVITY_END', '0.00025s'))]
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        frames = receive_turn(socket)
        results = [f['result'] for f in frames if f['type'] == 'tool_result']
        assert len(results) == 2
        assert results[-1]['assistant_text'] == CLARIFICATION
        assert results[-1]['read_only'] and results[-1]['proposal'] is None
        if expected == 'released':
            assert_audio(frames)
        else:
            assert not any(f['type'] == 'audio' for f in frames)
            error = next(f for f in frames if f['type'] == 'error')
            assert error['review']['reason'] == expected
            assert set(error['review']) == {'reason', 'audio_chunks', 'audio_bytes', 'caption_chars'}
        assert not any(f.get('state') == 'awaiting_consent' for f in frames)
        socket.send_json({'type': 'stop'})
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('case', ['unknown_active', 'foreign_question', 'foreign_plan',
    'stale_plan_id', 'stale_quote', 'explicit_selection'])
def test_coverage_rejections_never_substitute_known_baseline(workspace, tool, case):
    from procure.demo.recovery import compare
    from datetime import date
    client, provider, operator = workspace
    value = terra(client)
    provider.tool_name, provider.chunks = tool, (READ,)
    if case == 'unknown_active':
        value.recovery = compare(value.last_plan, value.plan_request, list(value.offers.values()),
            {value.last_plan['selected_offer_id']: 1}, today=date(2026, 10, 14))
        value.recovery['scenario']['plan_id'] = 'unknown-active'
        value.recovery['scenario']['current_stock'] = None
        value.recovery['scenario']['current_stock_kg'] = None
    elif case == 'foreign_question':
        provider.chunks = (READ + ' For Solara Cables.',)
    elif case == 'foreign_plan':
        value.last_plan['company_id'] = 'solara-cables'
    elif case == 'stale_plan_id':
        provider.chunks = (READ + ' Use plan ID obsolete-plan.',)
    elif case == 'stale_quote':
        value.offers[value.last_plan['selected_offer_id']]['version'] += 1
    else:
        provider.chunks = ('Will the selected material have a supply gap?',)
    before = protected(client)
    result = tool_result(ask(client))
    assert result['status'] == 'needs_clarification'
    assert result['read_only'] and result['proposal'] is None
    assert '11,400' not in result['assistant_text']
    if case == 'unknown_active':
        assert 'unknown for the active plan' in result['assistant_text']
        assert 'unknown-active' in result['evidence_ids']
        assert value.last_plan['plan_id'] not in result['evidence_ids']
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('case', ['late_foreign', 'late_false', 'no_vad', 'closed_turn'])
def test_current_asr_guards_cannot_reuse_grounded_coverage(workspace, monkeypatch, tool, case):
    from procure.demo import live_voice
    client, provider, operator = workspace
    terra(client)
    provider.tool_name, provider.chunks = tool, (READ,)
    monkeypatch.setattr(live_voice, 'RECOVERY_CAPTION_SETTLE_SECONDS', .03)
    monkeypatch.setattr(live_voice, 'RECOVERY_INPUT_WAIT_SECONDS', .08)
    script = [event(activity=('ACTIVITY_START', '0s')), event(text=READ),
              event(activity=('ACTIVITY_END', '0.000125s'))]
    if case == 'late_foreign':
        script += [.01, event(text=' For Solara Cables.')]
    elif case == 'late_false':
        frame = event()
        frame.server_content.input_transcription = NS(text=None, finished=False)
        script += [.04, frame]
    elif case == 'no_vad':
        script = [event(text=READ)]
    else:
        script += [.04, event(complete=True)]
    provider.input_script = script
    before = protected(client)
    frames = ask(client)
    assert not any('11,400' in f.get('text', '') for f in frames)
    assert provider.tool_response.response.get('required_spoken_response', '') != READ
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('words', [
    'Should I prepare the bridge RFQ now? No draft has been saved.',
    'Should I prepare the unsent bridge RFQ now?',
    'Would you like me to send an unsent bridge RFQ now? No draft has been saved.',
    'Would you like me to prepare an unsent bridge RFQ now? A draft has been saved.',
    PARAPHRASE + ' I placed the order.',
])
def test_complete_clarification_grammar_rejects_unsafe_additions(workspace, words):
    client, provider, operator = workspace
    provider.chunks = ('Prepare a bridge RFQ if the supplier agrees.',)
    async def respond(*, function_responses):
        await provider.events.put(NS(server_content=NS(model_turn=NS(parts=[NS(
            inline_data=NS(data=PCM, mime_type='audio/pcm;rate=24000'))]),
            output_transcription=NS(text=words), turn_complete=True)))
    provider.send_tool_response = respond
    before = protected(client)
    frames = ask(client)
    assert tool_result(frames)['assistant_text'] == CANONICAL
    assert frames[-1]['review']['reason'] == 'caption_mismatch'
    assert not any(f['type'] == 'audio' or f.get('state') == 'awaiting_consent' for f in frames)
    assert protected(client) == before and operator.calls == []


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
def test_coverage_renewal_requires_fresh_audio_and_vad(workspace, tool):
    client, provider, operator = workspace
    terra(client)
    provider.tool_name, provider.marker, provider.chunks = tool, 'sdk_default', (READ,)
    provider.renew_first = True
    session = client.get('/api/demo/session').json()
    before = protected(client)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'origin': 'https://demo.test'}) as socket:
        socket.send_json({'type': 'start', 'csrf_token': session['csrf_token'],
                         'expected_context_version': session['context_version']})
        assert socket.receive_json()['type'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        assert socket.receive_json()['state'] == 'reconnecting'
        assert socket.receive_json()['state'] == 'ready'
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        frames = receive_turn(socket)
        assert '12 days / 11,400 kg' in tool_result(frames)['assistant_text']
        assert_audio(frames)
        # Replay after completion: audio arrives but no new VAD/ASR. A new call
        # ID cannot borrow the previous generation's grounded answer.
        provider.stale = True
        socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
        frames = receive_turn(socket)
        assert not any('11,400' in f.get('text', '') for f in frames)
        socket.send_json({'type': 'stop'})
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []


@pytest.mark.parametrize('verb', ['Request', 'Create', 'Prepare'])
@pytest.mark.parametrize('qualification', ['explicit', 'negated', 'conditional'])
def test_bridge_imperatives_keep_existing_guarded_route(workspace, verb, qualification):
    client, provider, operator = workspace
    words = f'{verb} a quotation for the supply gap using our current plan.'
    if qualification == 'negated':
        words = 'Do not ' + words[0].lower() + words[1:]
    elif qualification == 'conditional':
        words = words[:-1] + ' if the supplier agrees.'
    provider.tool_name, provider.chunks = 'operate_workspace', (words,)
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    # Actual public socket -> broker -> guarded operator, never the private read.
    assert provider.executed_messages == [words]
    assert operator.calls == []
    if qualification == 'explicit':
        assert result['status'] == 'completed' and result['read_only'] is False
        assert result['action_result']['tool'] == 'prepare_rfq'
        after = protected(client)
        assert after['rfq']['bridge'] is True and after['rfq']['status'] == 'draft_unsent'
        assert len(after['drafts']) == 1 and after['simulated_outbox'] == []
        assert after['plan'] == before['plan']
        assert after['selected_material_id'] == before['selected_material_id']
    else:
        assert result['status'] == 'needs_clarification' and result['read_only'] is True
        assert result['proposal'] is None and result.get('action_result') is None
        assert protected(client) == before
    assert_audio(frames)


@pytest.mark.parametrize('tool', ['read_workspace', 'operate_workspace'])
@pytest.mark.parametrize('coverage', ['covered', 'gap', 'unknown'])
def test_required_speech_preserves_complete_coverage_decision(workspace, tool, coverage):
    client, provider, operator = workspace
    value = terra(client)
    if coverage == 'covered':
        profile, offers, value.last_plan = prepared('terrapack')
        value.offers = {row['offer_id']: row for row in offers}
        value.plan_request = {**profile['buyer_defaults'], 'offer_ids': list(value.offers),
            'expected_versions': value.last_plan['offer_versions']}
    elif coverage == 'unknown':
        value.last_plan['current_stock'] = value.last_plan['current_stock_kg'] = None
    provider.tool_name, provider.chunks = tool, (READ,)
    before = protected(client)
    frames = ask(client)
    result = tool_result(frames)
    required = provider.tool_response.response['required_spoken_response']
    if coverage == 'covered':
        assert 'No supply gap is projected; this plan covers continuous supply through 2026-11-05.' in required
    elif coverage == 'gap':
        assert 'A projected supply gap exists.' in required
        assert '12 days / 11,400 kg' in required
        assert 'stock 0 kg' in required and '950 kg' in required
        assert '2026-10-26' in required and '2026-10-14' in required
    else:
        assert 'Supply coverage is unknown for the active plan.' in required
        assert 'Next: provide or review current_stock.' in required
        assert result['status'] == 'needs_clarification'
    assert required == result['assistant_text']
    assert result['read_only'] and result['proposal'] is None
    assert protected(client) == before and operator.calls == []
    assert provider.executed_messages == []
    assert_audio(frames)
