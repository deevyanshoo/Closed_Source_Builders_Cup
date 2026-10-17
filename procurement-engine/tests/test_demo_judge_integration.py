"""Integration contracts use offline providers and public synthetic data only."""
from copy import deepcopy
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import pytest
from procure.demo.app import create_app

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
class Offline:
    available = False
    model = 'gemini-offline'
    def __call__(self, *args):
        raise AssertionError('local reads must not call a provider')

def client(**kwargs):
    return TestClient(create_app(clock=lambda: NOW, operator_provider=Offline(), **kwargs), base_url='https://demo.test')
def auth(c):
    s = c.get('/api/demo/session').json()
    return s, {'Origin': 'https://demo.test', 'X-CSRF-Token': s['csrf_token']}
def post(c, h, path, **data):
    return c.post('/api/demo/' + path, headers=h, json={'expected_context_version': 1, 'idempotency_key': path.replace('/', '-')+'-key', **data})

def test_explicit_bootstrap_is_idempotent_and_reads_do_not_seed():
    with client() as c:
        s,h = auth(c)
        assert not s['offers'] and not s['plan']
        r = post(c,h,'judge/start')
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['session']['plan']['selected_offer_id']
        assert body['seed_version'] == 'judge-baseline-v1'
        assert body['session']['recovery'] is None and body['session']['rfq'] is None
        assert all(o['extractor'] == 'rules' for o in body['session']['offers'])
        again = post(c,h,'judge/start').json()
        assert again['session']['plan'] == body['session']['plan']
        assert c.get('/api/demo/judge/evidence').json()['aggregate']['interpreted_articles'] == 0
        assert body['facts']


def test_typed_bridge_request_uses_current_gap_and_factual_read_stays_pure():
    with client() as c:
        s, h = auth(c)
        started = post(c, h, 'judge/start')
        assert started.status_code == 200
        plan = started.json()['session']['plan']
        before = deepcopy(plan)
        factual = post(c, h, 'operator', message='Will our stock run out before the selected arrival?',
                       idempotency_key='coverage-read')
        assert factual.status_code == 200, factual.text
        assert factual.json()['read_only'] is True
        assert '2026-10-24' in factual.json()['assistant_text']
        assert c.get('/api/demo/session').json()['rfq'] is None
        drafted = post(c, h, 'operator', message='Draft a bridge RFQ for earlier delivery to cover the gap.',
                       idempotency_key='bridge-draft')
        assert drafted.status_code == 200, drafted.text
        turn = drafted.json()
        assert turn['read_only'] is False and turn['action_result']['tool'] == 'prepare_rfq'
        state = c.get('/api/demo/session').json()
        assert state['plan'] == before
        assert state['rfq']['bridge'] is True and state['rfq']['status'] == 'draft_unsent'
        assert state['drafts'][-1]['body'] == state['rfq']['text']
        assert state['simulated_outbox'] == []
        assert c.get(f"/api/demo/rfq/{state['rfq']['draft_id']}/download").text == state['rfq']['text']


def test_frontend_bridge_cta_prepares_unsent_canonical_draft():
    with client() as c:
        _, h = auth(c)
        started = post(c, h, 'judge/start')
        assert started.status_code == 200, started.text
        before = deepcopy(started.json()['session']['plan'])
        message = (f"Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap in current reviewed plan {before['plan_id']}. "
                   'Ask the selected supplier for new capacity, price and terms. Save the draft; do not send it.')
        response = post(c, h, 'operator', message=message, idempotency_key='frontend-bridge-cta')
        assert response.status_code == 200, response.text
        turn = response.json()
        assert turn['read_only'] is False and turn['action_result']['tool'] == 'prepare_rfq'
        state = c.get('/api/demo/session').json()
        assert state['plan'] == before
        assert state['rfq']['bridge'] is True and state['rfq']['status'] == 'draft_unsent'
        assert state['drafts'][-1]['body'] == state['rfq']['text']
        assert state['simulated_outbox'] == []
        assert c.get(f"/api/demo/rfq/{state['rfq']['draft_id']}/download").text == state['rfq']['text']


@pytest.mark.parametrize('message', (
    'What is the projected supply gap and when is the bridge needed?',
    'Explain how to prepare a bridge RFQ for earlier delivery.',
    'How do I prepare a bridge RFQ for earlier delivery?',
    'Can you explain how to prepare a bridge RFQ for earlier delivery?',
    'Do not draft a bridge RFQ for earlier delivery.',
    'Do not send or draft a bridge RFQ for earlier delivery.',
    'Prepare a bridge RFQ for earlier delivery, but do not create the draft.',
))
def test_bridge_discussion_and_drafting_negation_leave_operator_read_only(message):
    with client() as c:
        _, h = auth(c)
        started = post(c, h, 'judge/start')
        assert started.status_code == 200, started.text
        before = deepcopy(started.json()['session']['plan'])
        response = post(c, h, 'operator', message=message, idempotency_key='bridge-read-only')
        assert response.status_code == 200, response.text
        assert response.json()['read_only'] is True
        state = c.get('/api/demo/session').json()
        assert state['plan'] == before
        assert state['rfq'] is None and state['drafts'] == [] and state['simulated_outbox'] == []


def test_bridge_request_rejects_unknown_coverage_without_draft():
    with client() as c:
        _, h = auth(c)
        response = post(c, h, 'operator', message='Draft a bridge RFQ for earlier delivery.',
                        idempotency_key='unknown-bridge')
        assert response.status_code == 200
        assert response.json()['status'] == 'needs_clarification'
        assert response.json()['read_only'] is True
        assert c.get('/api/demo/session').json()['drafts'] == []


def test_native_operate_bridge_uses_same_durable_draft(monkeypatch):
    import procure.demo.live_voice as live
    async def bridge(socket, *, session, operate_workspace, **kwargs):
        await socket.accept()
        request = await socket.receive_json()
        await socket.send_json(await operate_workspace(request['message'], session.context_version,
                                                     'native-bridge'))
        await socket.close()
    monkeypatch.setattr(live, 'run_live_voice', bridge)
    with client() as c:
        _, h = auth(c)
        started = post(c, h, 'judge/start')
        assert started.status_code == 200
        before = deepcopy(started.json()['session']['plan'])
        with c.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'Origin': 'https://demo.test'}) as socket:
            socket.send_json({'message': 'Prepare an earlier delivery RFQ for the supply gap using our current plan. Save the draft without sending it.'})
            result = socket.receive_json()
        assert result['read_only'] is False and result['action_result']['tool'] == 'prepare_rfq'
        state = c.get('/api/demo/session').json()
        assert state['plan'] == before
        draft = state['rfq']
        assert draft['bridge'] is True
        assert draft['status'] == 'draft_unsent'
        assert state['drafts'][-1]['body'] == draft['text']
        assert state['simulated_outbox'] == []
        assert c.get(f"/api/demo/rfq/{draft['draft_id']}/download").text == draft['text']


def _bridge_binding_turn(c, h, monkeypatch, channel, message):
    if channel == 'typed':
        response = post(c, h, 'operator', message=message, idempotency_key='binding-turn')
        assert response.status_code == 200, response.text
        return response.json()
    import procure.demo.live_voice as live
    async def bridge(socket, *, session, operate_workspace, **kwargs):
        await socket.accept()
        request = await socket.receive_json()
        await socket.send_json(await operate_workspace(request['message'], session.context_version,
                                                     'binding-turn'))
        await socket.close()
    monkeypatch.setattr(live, 'run_live_voice', bridge)
    with c.websocket_connect('wss://demo.test/api/demo/voice/live',
                             headers={'Origin': 'https://demo.test'}) as socket:
        socket.send_json({'message': message})
        return socket.receive_json()


@pytest.mark.parametrize('channel', ('typed', 'native'))
@pytest.mark.parametrize('case', ('wrong_material', 'multiple_materials', 'all_materials', 'stale_cta'))
def test_bridge_binding_rejection_preserves_state_without_provider_fallback(monkeypatch, channel, case):
    calls = []
    def unexpected_provider(self, *args):
        calls.append(args[0])
        raise AssertionError('binding rejection must not fall back to a provider')
    monkeypatch.setattr(Offline, 'available', True)
    monkeypatch.setattr(Offline, '__call__', unexpected_provider)
    with client() as c:
        s, h = auth(c)
        assert post(c, h, 'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        old_plan_id = value.last_plan['plan_id']
        if case == 'stale_cta':
            # A new reviewed plan in the same company/context leaves the old CTA stale.
            old_plan_id = 'ABCDEFGHIJKLMNOPQRSTUVWX'
            value.last_plan = {**value.last_plan, 'plan_id': 'ZYXWVUTSRQPONMLKJIHGFEDC'}
            message = (f'Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap in current reviewed plan {old_plan_id}. '
                       'Ask the selected supplier for new capacity, price and terms. Save the draft; do not send it.')
        else:
            material = {'wrong_material': 'polymer', 'multiple_materials': 'copper and polymer',
                        'all_materials': 'all materials'}[case]
            message = f'Draft a bridge RFQ for {material} earlier delivery.'
        before = c.get('/api/demo/session').json()
        accepted = deepcopy((value.last_plan, value.plan_request, value.recovery))
        result = _bridge_binding_turn(c, h, monkeypatch, channel, message)
        assert result['status'] == 'needs_clarification', result
        assert result['read_only'] is True and result['proposal'] is None
        assert not result.get('action_result')
        after = c.get('/api/demo/session').json()
        for field in ('company_id', 'context_version', 'plan', 'offers', 'drafts', 'rfq', 'simulated_outbox'):
            assert after[field] == before[field], field
        assert (value.last_plan, value.plan_request, value.recovery) == accepted
        assert calls == []


@pytest.mark.parametrize('channel', ('typed', 'native'))
@pytest.mark.parametrize('message,actionable', (
    ('Draft a bridge RFQ for earlier delivery using the current plan to cover the gap.', True),
    ('Prepare an unsent earlier-delivery or bridge RFQ for the production supply gap in current reviewed plan {plan_id}. Ask the selected supplier for new capacity, price and terms. Save the draft; do not send it.', True),
    ('Do not send the bridge RFQ, prepare an unsent bridge RFQ draft for the current supply gap.', True),
    ('Explain how to prepare a bridge RFQ for earlier delivery.', False),
    ('Do not send or draft a bridge RFQ for earlier delivery.', False),
))
def test_bridge_binding_keeps_explicit_unsent_actions_and_read_only_discussion(monkeypatch, channel, message, actionable):
    with client() as c:
        s, h = auth(c)
        assert post(c, h, 'judge/start').status_code == 200
        if 'current reviewed plan {plan_id}' in message:
            value = c.app.state.sessions.get(s['session_id'])
            value.last_plan = {**value.last_plan, 'plan_id': 'ZYXWVUTSRQPONMLKJIHGFEDC'}
        before = c.get('/api/demo/session').json()
        result = _bridge_binding_turn(c, h, monkeypatch, channel,
                                      message.format(plan_id=before['plan']['plan_id']))
        after = c.get('/api/demo/session').json()
        for field in ('plan', 'offers', 'simulated_outbox'):
            assert after[field] == before[field], field
        assert result['read_only'] is not actionable
        if actionable:
            assert result['action_result']['tool'] == 'prepare_rfq'
            assert after['rfq']['bridge'] is True and after['rfq']['status'] == 'draft_unsent'
            assert after['drafts'][-1]['body'] == after['rfq']['text']
            assert c.get(f"/api/demo/rfq/{after['rfq']['draft_id']}/download").text == after['rfq']['text']
        else:
            assert after['drafts'] == before['drafts'] and after['rfq'] == before['rfq']


def test_active_recovery_stockout_read_uses_scenario_arrival_and_gap():
    from procure.demo.recovery import compare
    with client() as c:
        s, h = auth(c)
        assert post(c, h, 'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        baseline = value.last_plan
        delayed = compare(baseline, value.plan_request, list(value.offers.values()),
                          {baseline['selected_offer_id']: 7}, today=NOW.date())
        delayed['scenario']['plan_id'] = 'active-recovery-scenario'
        value.recovery = delayed
        active = c.get('/api/demo/session').json()['plan']['supply_coverage']
        assert active['plan_id'] == 'active-recovery-scenario'
        answer = post(c, h, 'operator', message='Will our stock run out before the selected arrival?',
                      idempotency_key='active-gap-read')
        assert answer.status_code == 200, answer.text
        assert active['selected_arrival_date'] in answer.json()['assistant_text']
        assert '3,666.61 kg' in answer.json()['assistant_text']
        assert 'active-recovery-scenario' in answer.json()['evidence_ids']

@pytest.mark.parametrize('baseline_status', ['gap', 'covered'])
def test_active_unknown_coverage_never_borrows_baseline_answer(baseline_status):
    from procure.demo.experience_service import resolve_local_turn
    from procure.demo.company import get_company
    company = get_company('solara-cables')
    baseline = {'plan_id': 'old-plan', 'company_id': company['company_id'], 'material': 'copper',
                'supply_coverage': {'status': baseline_status, 'quantity_unit': 'kg',
                                    'stock': '2000', 'inventory_as_of': '2026-10-14',
                                    'consumption_per_day': '333.33', 'consumption_as_of': '2026-10-14',
                                    'selected_arrival_date': '2026-10-24', 'stock_depletion_date': '2026-10-20',
                                    'uncovered_days': '4', 'uncovered_quantity': '1333.30',
                                    'bridge_quantity': '1333.30', 'bridge_needed_by': '2026-10-20',
                                    'horizon_end': '2026-11-05'}}
    active = {'plan_id': 'active-plan', 'company_id': company['company_id'], 'material': 'copper',
              'supply_coverage': {'status': 'unknown', 'missing_inputs': ['current accepted quote']}}
    state = {'company_id': company['company_id'], 'context_version': 7, 'company': company,
             'offers': [], 'plan': active, 'last_plan': baseline,
             'recovery': {'scenario': active}, 'selected_material_id': 'copper',
             'shipments': {'shipments': []}}
    result = resolve_local_turn(state, 'Will our stock run out before the selected arrival?', now=NOW)
    assert result['kind'] == 'needs_input'
    assert result['missing_inputs'] == ['current accepted quote']
    assert result['context_version'] == 7
    assert 'active-plan' in result['evidence_refs']
    assert 'unknown' in result['answer'].lower()
    assert 'old-plan' not in result['evidence_refs']
    assert '2026-10-24' not in result['answer']

def test_local_mixed_question_offline_preserves_exact_public_consent():
    with client() as c:
        s,h=auth(c)
        r=post(c,h,'operator',message='What is our current copper stock and search latest Suez canal news?')
        assert r.status_code == 200, r.text
        b=r.json()
        assert '2000' in b['assistant_text']
        assert b['proposal']['kind'] == 'research'
        assert '2000' not in b['proposal']['parameters']['query']
        assert b['read_only'] and not b.get('action_result')

def test_preview_does_not_change_baseline_recovery_rfq_or_drafts():
    with client() as c:
        s,h=auth(c)
        boot=post(c,h,'judge/start')
        assert boot.status_code == 200, boot.text
        value=c.app.state.sessions.get(s['session_id'])
        before=deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        r=post(c,h,'operator',message='What if our current shipment is delayed by two weeks?')
        assert r.status_code == 200, r.text
        b=r.json()
        assert b['proposal'] is None and b['read_only'] and not b.get('session') and not b.get('action_result')
        p=b['workspace']['judge_preview']
        assert p['preview_id'] and p['scenario_plan_id'] is None and p['read_only']
        assert '14' in b['assistant_text']
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


@pytest.mark.parametrize('message', [
    'Prepare a recovery scenario for a 7-day delay of our current copper cathode supplier.',
    'Prepare a recovery scenario for a seven day delay of our current copper cathode supplier.',
    'Can you prepare a recovery scenario for a seven day delay of our current copper cathode supplier?',
    'Create a recovery scenario for a seven day delay of our current copper cathode supplier.',
    'Draft a recovery scenario for a seven day delay of our current copper cathode supplier.',
    'Prepare a recovery scenario for a 7 day delay of our current copper cathode supplier.',
])
def test_explicit_current_supplier_recovery_is_review_proposal(message):
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        result = post(c,h,'operator',message=message).json()
        assert result['status'] == 'proposal', result
        assert result['proposal']['kind'] == 'recovery'
        assert result['proposal']['parameters']['delay_days_by_offer'] == {
            value.last_plan['selected_offer_id']: 7}
        assert result['proposal']['parameters']['shipment_id']
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


def test_possessive_current_supplier_recovery_is_review_proposal():
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        result = post(c,h,'operator',message="Prepare a recovery scenario for our current copper cathode supplier's seven day delay.").json()
        assert result['status'] == 'proposal', result
        assert result['proposal']['kind'] == 'recovery'


@pytest.mark.parametrize('duration', [
    'negative seven day', 'minus seven day', '-7 day', '+7 day',
    'at least seven day', 'about seven day', 'around seven day', 'up to seven day',
    '7.5 day', '1.5 day',
])
def test_qualified_or_signed_recovery_duration_needs_clarification(duration):
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        result = post(c,h,'operator',message=f'Prepare a recovery scenario for a {duration} delay of our current copper cathode supplier.').json()
        assert result['status'] == 'needs_clarification', result
        assert result['proposal'] is None
        assert 'delay' in result['assistant_text'].lower()
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


@pytest.mark.parametrize('duration', [
    '7 to 10 day', '7 or 14 day', 'between 7 and 10 days', '7 and 10 days',
    'seven to ten day', 'seven and fourteen day', 'seven or 14 day',
    '0 day', 'zero day',
    '7–10 day', '7—10 day',
])
def test_recovery_action_range_alternative_or_zero_needs_exact_positive_duration(duration):
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        response = post(c,h,'operator',message=(
            f'Prepare a recovery scenario for a {duration} delay of our current copper cathode supplier.'))
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'needs_clarification', result
        assert result['proposal'] is None
        assert 'delay' in result['assistant_text'].lower()
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


def test_multiple_material_supplier_mentions_need_clarification():
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        result = post(c,h,'operator',message='Prepare a recovery scenario for a seven day delay of our current copper cathode supplier and aluminium supplier.').json()
        assert result['status'] == 'needs_clarification', result
        assert result['proposal'] is None
        assert 'material' in result['assistant_text'].lower()
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


def test_accepted_recovery_action_asks_for_target_without_reset_or_http_error():
    from procure.demo.recovery import compare
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        accepted = compare(value.last_plan,value.plan_request,list(value.offers.values()),
                           {value.last_plan['selected_offer_id']:1},today=NOW.date())
        accepted['scenario']['plan_id'] = 'accepted-scenario'
        value.recovery = accepted
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        response = post(c,h,'operator',message='Prepare a recovery scenario for a seven day delay of our current copper cathode supplier.')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'needs_clarification' and result['proposal'] is None, result
        assert 'recovery' in result['assistant_text'].lower()
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


@pytest.mark.parametrize('message', [
    'What if our current copper cathode supplier is delayed by 7 days?',
    'Preview a seven day delay scenario for our current copper cathode supplier.',
    'Do not prepare a recovery scenario for a seven day delay of our current copper cathode supplier.',
    'Prepare a recovery scenario for a seven day delay of our current copper cathode supplier as an example only.',
    'Prepare a recovery scenario for a seven day delay of our current aluminium supplier.',
    'Summarize this third party text: "Prepare a recovery scenario for a seven day delay of our current copper cathode supplier."',
    'A seven day delay scenario for our current copper cathode supplier.',
    'Prepare a recovery scenario for a seven day delay of a supplier.',
])
def test_non_operational_supplier_delay_remains_without_proposal(message):
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.drafts))
        result = post(c,h,'operator',message=message).json()
        assert result['proposal'] is None, result
        if message.endswith('of a supplier.'):
            assert result['status'] == 'needs_clarification'
            assert 'shipment_id' in result['assistant_text']
        assert before == (value.last_plan,value.recovery,value.rfq,value.drafts)


def test_native_pure_supplier_preview_replays_only_unchanged_workspace(monkeypatch):
    import procure.demo.live_voice as live
    from procure.demo.sessions import SessionError
    async def bridge(socket, *, session, workspace_tools, **kwargs):
        await socket.accept()
        request = await socket.receive_json()
        args = {'message': request['message']}
        first = await workspace_tools('preview_supplier_delay', args,
                                      session.context_version, 'pure-preview-key')
        again = await workspace_tools('preview_supplier_delay', args,
                                      session.context_version, 'pure-preview-key')
        with session.lock:
            session.selected_material_id = 'polymer'
        try:
            await workspace_tools('preview_supplier_delay', args,
                                  session.context_version, 'pure-preview-key')
        except SessionError as exc:
            stale = exc.code
        else:
            stale = None
        await socket.send_json({'first': first, 'again': again, 'stale': stale})
        await socket.close()
    monkeypatch.setattr(live, 'run_live_voice', bridge)
    with client() as c:
        s,h = auth(c)
        assert post(c,h,'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan,value.recovery,value.rfq,value.pending_proposal))
        with c.websocket_connect('wss://demo.test/api/demo/voice/live',
                                 headers={'Origin':'https://demo.test'}) as socket:
            socket.send_json({'message':'What if our current copper cathode supplier is delayed by 7 days?'})
            result = socket.receive_json()
        assert result['first'] == result['again']
        assert result['first']['workspace']['judge_preview']['read_only'] is True
        assert result['first']['proposal'] is None and result['stale'] == 'stale_context'
        assert before == (value.last_plan,value.recovery,value.rfq,value.pending_proposal)

def test_all_ten_company_route_read_contract_and_foreign_rejection():
    with client() as c:
        s,h=auth(c)
        companies=c.get('/api/demo/companies').json()['companies']
        assert len(companies)==10
        version=1
        for company in companies:
            switched=post(c,h,'company', company_id=company['company_id'], expected_context_version=version,idempotency_key=company['company_id'])
            assert switched.status_code==200,switched.text
            version=switched.json()['context_version']
            for route in company['routes']:
                r=c.get('/api/demo/judge/routes',params={'route_id':route['route_id'],'material_id':route['materials'][0],'buyer_location_index':0})
                assert r.status_code==200,r.text
                assert r.json()['company_id']==company['company_id']
                assert r.json()['offer_id'] is None and r.json()['supplier_id'] is None
        assert c.get('/api/demo/judge/routes', params={'route_id':'foreign','material_id':'copper'}).status_code==422

def test_runtime_maps_key_and_security_policy(monkeypatch):
    monkeypatch.setenv('MAPS_BROWSER_KEY','public-test-browser-key')
    with client() as c:
        auth(c)
        r=c.get('/api/demo/judge/config')
        assert r.status_code==200
        assert r.json()['maps_browser_key']=='public-test-browser-key'
        assert r.json()['maps_enabled'] is True
        assert 'unsafe-eval' not in r.headers['content-security-policy']
        assert r.headers['referrer-policy']=='strict-origin-when-cross-origin'

def test_hindi_receipt_requires_verified_model_version_and_company():
    calls=[]
    def provider(payload):
        calls.append(payload)
        return {'model_id':'gemini-2.5-flash','model_version':'gemini-2.5-flash-001','provider_call_id':'fake-call', 'output':'Indian cotton import relief is regional context; review the dated notice.', 'model_reviewed_flags':['public_policy_change','invented']}
    with client(judge_provider=provider) as c:
        s,h=auth(c)
        assert post(c,h,'judge/interpret-hindi').status_code==422
        switched=post(c,h,'company', company_id='loomworks',idempotency_key='loom')
        assert switched.status_code==200, switched.text
        v=switched.json()['context_version']
        r=post(c,h,'judge/interpret-hindi',expected_context_version=v,idempotency_key='hindi')
        assert r.status_code==200,r.text
        b=c.get('/api/demo/judge/evidence').json()
        assert b['aggregate']['interpreted_articles']==1
        assert b['hindi_case']['status']=='success'
        assert b['aggregate']['receipts'][0]['reviewed_flags']==['public_policy_change']
        assert post(c,h,'judge/interpret-hindi',expected_context_version=v,idempotency_key='hindi').status_code==200
        assert len(calls)==1
        assert set(calls[0])=={'source_id','title','source_url','publication_at','instruction'}

def test_receipts_reread_through_independent_session_store_and_old_schema():
    from procure.demo.sessions import SessionStore
    from procure.demo.session_persistence import MemorySessionPersistence
    from procure.demo.experience_service import sanitize_receipt
    backend=MemorySessionPersistence()
    first=SessionStore(clock=lambda:NOW,persistence_backend=backend)
    s=first.create()
    s.judge_receipts={'loomworks':[sanitize_receipt({'model_id':'gemini-test','model_version':'actual-v1','output':'A sourced English summary.'},NOW)]}
    first.save(s)
    reread=SessionStore(clock=lambda:NOW,persistence_backend=backend).get(s.session_id)
    assert reread.judge_receipts==s.judge_receipts
    reread.select_company('loomworks')
    assert reread.judge_receipts['loomworks']
    from procure.demo.experience_service import evidence
    assert evidence(reread)['aggregate']['interpreted_articles']==1
    reread.select_company('terrapack')
    assert evidence(reread)['aggregate']['interpreted_articles']==0
    state=first._snapshot(s)
    state.pop('judge_receipts');state.pop('judge_attempts')
    restored=SessionStore._restore(s.session_id,state,1,now=NOW)
    assert restored.judge_receipts=={} and restored.judge_attempts==0

def test_unverifiable_receipt_failure_cleans_up_and_never_repeats_claimed_call():
    calls=[]
    def incomplete(payload):
        calls.append(payload)
        return {'model_id':'gemini-test','output':'Cannot verify version.'}
    with client(judge_provider=incomplete) as c:
        s,h=auth(c)
        switched=post(c,h,'company',company_id='loomworks',idempotency_key='loom').json()
        v=switched['context_version']
        assert post(c,h,'judge/interpret-hindi',expected_context_version=v).status_code==503
        assert c.get('/api/demo/judge/evidence').json()['aggregate']['interpreted_articles']==0
        assert post(c,h,'judge/interpret-hindi',expected_context_version=v).status_code==409
        assert len(calls)==1
        value=c.app.state.sessions.get(s['session_id'])
        assert not value.idempotency['judge-interpret-hindi-key']['pending']
        for i in range(2):
            assert post(c,h,'judge/interpret-hindi',expected_context_version=v,idempotency_key=f'new-{i}').status_code==503
        assert post(c,h,'judge/interpret-hindi',expected_context_version=v,idempotency_key='limit').status_code==429
        assert len(calls)==3

def test_completed_research_projection_is_bounded_owned_and_public_only():
    from procure.demo.experience_service import completed_research
    with client() as c:
        s,h=auth(c)
        value=c.app.state.sessions.get(s['session_id'])
        record={'state':'completed','company_id':value.company_id,'context_version':1,'query':'Suez current shipping',
                '_secret_claim':'private','result':{'status':'completed','answer':'Dated public answer','checked_at':NOW.isoformat(),
                  'search_suggestions_html':'<private/>','sources':[{'source_id':'public','title':'Source','url':'https://example.org/evidence'},{'source_id':'private','url':'http://127.0.0.1/private'}]}}
        value.research_records={'one':record,'foreign':{**record,'company_id':'loomworks'},'stale':{**record,'context_version':2}}
        projection=completed_research(value)
        assert len(projection)==1 and projection[0]['evidence_refs']==['public']
        assert 'private' not in str(projection) and 'search_suggestions_html' not in str(projection)

def test_bootstrap_preserves_restored_nonflagship_and_partial_user_work():
    with client() as c:
        s,h=auth(c)
        value=c.app.state.sessions.get(s['session_id'])
        value.buyer_draft={'material':'copper','quantity':'user-value'}
        r=post(c,h,'judge/start')
        assert r.status_code==200 and r.json()['seeded'] is False
        assert value.buyer_draft['quantity']=='user-value' and not value.offers
        switched=post(c,h,'company',company_id='terrapack',idempotency_key='terra').json()
        r=post(c,h,'judge/start',expected_context_version=switched['context_version'],idempotency_key='restore')
        assert r.status_code==200 and r.json()['seeded'] is False
        assert r.json()['session']['company']['company_id']=='terrapack'

def test_hindi_late_result_discarded_on_company_switch():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    started,finish=Event(),Event()
    def blocked(payload):
        started.set(); assert finish.wait(5)
        return {'model_id':'gemini-test','model_version':'v1','output':'Late result'}
    with client(judge_provider=blocked) as c, ThreadPoolExecutor(max_workers=1) as pool:
        s,h=auth(c)
        v=post(c,h,'company',company_id='loomworks',idempotency_key='loom').json()['context_version']
        future=pool.submit(post,c,h,'judge/interpret-hindi',expected_context_version=v)
        assert started.wait(5)
        switched=post(c,h,'company',company_id='terrapack',expected_context_version=v,idempotency_key='switch')
        assert switched.status_code==200
        finish.set()
        assert future.result().status_code==409
        value=c.app.state.sessions.get(s['session_id'])
        assert value.judge_receipts=={}

def test_accepted_recovery_is_preserved_and_preview_uses_original_baseline():
    from procure.demo.recovery import compare
    with client() as c:
        s,h=auth(c)
        assert post(c,h,'judge/start').status_code==200
        value=c.app.state.sessions.get(s['session_id'])
        accepted=compare(value.last_plan,value.plan_request,list(value.offers.values()),{value.last_plan['selected_offer_id']:1},today=NOW.date())
        accepted['scenario']['plan_id']='accepted-scenario'
        value.recovery=accepted; value.rfq={'draft_id':'retained-draft'}; value.drafts=[{'draft_id':'retained-draft'}]
        before=deepcopy((value.recovery,value.rfq,value.drafts))
        r=post(c,h,'operator',message='What if our current shipment is delayed by two weeks?')
        assert r.status_code==200,r.text
        b=r.json()
        p=b['workspace']['judge_preview']
        assert p['baseline_plan_id']==value.last_plan['plan_id']
        assert p['impact']['additional_recovery_cost']['status']=='available'
        assert before==(value.recovery,value.rfq,value.drafts)

def test_native_prepare_and_operate_share_typed_local_preview_and_consent(monkeypatch):
    import procure.demo.live_voice as live
    async def bridge(socket, *, session, store, operate_workspace, workspace_tools, **kwargs):
        await socket.accept()
        request=await socket.receive_json()
        if request['tool']=='operate':
            result=await operate_workspace(request['message'],session.context_version,'native-operate')
        else:
            result=await workspace_tools('prepare_research',{'message':request['message']},session.context_version,'native-prepare')
        await socket.send_json(result)
        await socket.close()
    monkeypatch.setattr(live,'run_live_voice',bridge)
    with client() as c:
        s,h=auth(c)
        post(c,h,'judge/start')
        for tool,message in [('prepare_research','What is our current copper stock and search latest Suez canal news?'),('operate','What if our current shipment is delayed by two weeks?')]:
            with c.websocket_connect('wss://demo.test/api/demo/voice/live',headers={'Origin':'https://demo.test'}) as socket:
                socket.send_json({'tool':tool,'message':message})
                b=socket.receive_json()
                assert b['read_only'] and not b.get('session') and not b.get('action_result')
                if tool=='operate':
                    assert b['workspace']['judge_preview']['scenario_plan_id'] is None and '14' in b['assistant_text']
                else:
                    assert '2000' in b['assistant_text'] and b['proposal']['kind']=='research'
                assert set(b)<= {'turn_id','company_id','context_version','status','assistant_text','agent_steps','evidence_ids','proposal','read_only','workspace'}

def test_existing_offer_work_is_never_overwritten_by_bootstrap():
    with client() as c:
        s,h=auth(c)
        value=c.app.state.sessions.get(s['session_id'])
        value.offers={'user-offer':{'offer_id':'user-offer','version':3,'status':'draft'}}
        before=deepcopy(value.offers)
        r=post(c,h,'judge/start')
        assert r.status_code==200,r.text
        assert r.json()['seeded'] is False
        assert value.offers==before and value.last_plan is None

def test_startup_supplies_paired_factual_example_with_explicit_assumption():
    with client() as c:
        s,h=auth(c)
        b=post(c,h,'judge/start').json()
        example=b['before_after']
        assert example['assumed_delay_days']==7
        assert example['baseline_spend']==b['session']['plan']['total_cost']
        assert example['scenario_plan_id'] is None and example['read_only'] is True
        assert example['additional_recovery_cost'] is not None
        assert 'savings' not in example and 'realized_loss' not in example

def test_mixed_workspace_news_question_keeps_local_stock_without_public_search():
    with client() as c:
        s,h=auth(c)
        r=post(c,h,'operator',message='What is our current copper stock and latest Suez news?')
        assert r.status_code==200,r.text
        assert '2000' in r.json()['assistant_text']
        assert r.json()['proposal'] is None
        assert r.json()['workspace']['news_brief']['company_id']=='solara-cables'

def test_baseline_view_never_rebinds_foreign_company():
    from procure.demo.experience_service import baseline_snapshot
    state={'company_id':'solara-cables','company':{'company_id':'solara-cables'},'last_plan':{'plan_id':'foreign','company_id':'loomworks'},'offers':[]}
    result=baseline_snapshot(state)
    assert result['plan']['company_id']=='loomworks'

def test_spoken_mixed_reply_keeps_local_answer_before_exact_consent_prompt():
    from procure.demo.live_voice import judge_research_spoken_response
    result={'assistant_text':'Copper stock: 2000 kg.', 'agent_steps':[{'tool':'judge_read'}]}
    spoken=judge_research_spoken_response(result,'Suez shipping status')
    assert spoken.startswith('Copper stock: 2000 kg.')
    assert 'Suez shipping status' in spoken and 'Say confirm research to start.' in spoken

def test_receipt_storage_rejects_private_or_unbounded_fields():
    import pytest
    from procure.demo.sessions import SessionStore
    from procure.demo.session_persistence import StorageInvalid
    store=SessionStore(clock=lambda:NOW)
    value=store.create()
    value.judge_receipts={'loomworks':[{'private_payload':'must never persist'}]}
    with pytest.raises(StorageInvalid):
        store._snapshot(value)

def test_evidence_projects_pure_replay_snapshots_at_documented_day_boundaries():
    with client() as c:
        auth(c)
        replay=c.get('/api/demo/judge/evidence').json()['replay']
        timeline=replay['timeline']
        assert len(timeline)==8
        assert timeline[0]['documented_evidence']==[]
        assert [r['published_at'] for r in timeline[1]['documented_evidence']]==['2021-03-24']
        assert all(r['published_at']<='2021-03-26' for r in timeline[3]['documented_evidence'])
        assert all(frame['simulation'] is None for frame in timeline)

def test_if_wording_also_previews_original_baseline_after_accepted_recovery():
    from procure.demo.recovery import compare
    with client() as c:
        s,h=auth(c);post(c,h,'judge/start')
        value=c.app.state.sessions.get(s['session_id'])
        accepted=compare(value.last_plan,value.plan_request,list(value.offers.values()),{value.last_plan['selected_offer_id']:1},today=NOW.date())
        accepted['scenario']['plan_id']='accepted-scenario';value.recovery=accepted
        r=post(c,h,'operator',message='If our current shipment is delayed by two weeks, what happens?')
        assert r.status_code==200,r.text
        assert r.json()['workspace']['judge_preview']['baseline_plan_id']==value.last_plan['plan_id']

def test_exact_world_news_first_turn_reads_cached_company_news_and_stale_variant():
    from test_demo_proactive_risks_app import News
    question="What's happening in the world that can affect my business procurement?"
    for stale in (False,True):
        class Cached(News):
            calls=[]
            def get(self, company, force=False):
                self.calls.append((company['company_id'],force))
                result=super().get(company,force=force)
                if stale: result['cache_state']='stale'
                return result
        feed=Cached()
        with client(news_service=feed) as c:
            s,h=auth(c)
            assert c.app.state.sessions.get(s['session_id']).news is None
            r=post(c,h,'operator',message=question)
            assert r.status_code==200,r.text
            result=r.json()
            assert feed.calls==[('solara-cables',False)]
            assert result['proposal'] is None and result['read_only']
            assert 'Copper' in result['assistant_text'] and 'Public publisher' in result['assistant_text']
            assert '2026-10-14' in result['assistant_text']
            assert result['workspace']['news_brief']['headlines'][0]['exposure']['matched_materials']==['copper']
            assert len(result['assistant_text'])<1400
            if stale: assert 'stale' in result['assistant_text'].lower()
            assert not c.app.state.sessions.get(s['session_id']).research_records

def test_mixed_world_news_first_turn_keeps_stock_and_cached_company_news_typed_and_native(monkeypatch):
    from test_demo_proactive_risks_app import News
    import procure.demo.live_voice as live
    class Cached(News):
        def __init__(self): self.calls=[]
        def get(self, company, force=False):
            self.calls.append((company['company_id'], force))
            return super().get(company, force=force)
    async def bridge(socket, *, session, workspace_tools, **kwargs):
        await socket.accept()
        request=await socket.receive_json()
        result=await workspace_tools('prepare_research', {'message':request['message']},
                                     session.context_version, 'mixed-news-native')
        await socket.send_json(result)
        await socket.close()
    monkeypatch.setattr(live, 'run_live_voice', bridge)
    question="What's happening in the world that could affect our copper stock and procurement?"
    for channel in ('typed', 'native'):
        feed=Cached()
        with client(news_service=feed) as c:
            s,h=auth(c)
            assert c.app.state.sessions.get(s['session_id']).news is None
            if channel == 'typed':
                r=post(c,h,'operator',message=question)
                assert r.status_code == 200, r.text
                result=r.json()
            else:
                with c.websocket_connect('wss://demo.test/api/demo/voice/live',
                                         headers={'Origin':'https://demo.test'}) as socket:
                    socket.send_json({'message':question})
                    result=socket.receive_json()
            assert feed.calls == [('solara-cables', False)]
            assert 'Copper cathode stock: 2000 kg' in result['assistant_text']
            assert 'Public publisher' in result['assistant_text']
            assert result['workspace']['news_brief']['headlines'][0]['exposure']['matched_materials'] == ['copper']
            assert result['proposal'] is None and result['read_only']
            assert not c.app.state.sessions.get(s['session_id']).research_records

@pytest.mark.parametrize('channel', ('typed', 'native'))
def test_news_wait_uses_current_same_context_shipment_plan_typed_and_native(monkeypatch, channel):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from test_demo_proactive_risks_app import News
    import procure.demo.live_voice as live

    async def bridge(socket, *, session, workspace_tools, **kwargs):
        await socket.accept()
        request=await socket.receive_json()
        result=await workspace_tools('prepare_research', {'message':request['message']},
                                     session.context_version, 'news-race-native')
        await socket.send_json(result)
        await socket.close()

    monkeypatch.setattr(live, 'run_live_voice', bridge)
    question="What's happening in the world that could affect our current shipments and procurement?"
    entered, release = Event(), Event()
    class GatedNews(News):
        def __init__(self): self.calls=[]
        def get(self, company, force=False):
            self.calls.append((company['company_id'], force))
            entered.set()
            assert release.wait(10), 'News gate was not released'
            return super().get(company, force=force)
    feed=GatedNews()
    with client(news_service=feed) as c:
        s,h=auth(c)
        value=c.app.state.sessions.get(s['session_id'])
        assert value.last_plan is None
        def ask():
            if channel == 'typed':
                r=post(c,h,'operator',message=question)
                assert r.status_code == 200, r.text
                return r.json()
            with c.websocket_connect('wss://demo.test/api/demo/voice/live',
                                     headers={'Origin':'https://demo.test'}) as socket:
                socket.send_json({'message':question})
                return socket.receive_json()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(ask)
            try:
                assert entered.wait(10), 'News read did not reach the gate'
                boot=post(c,h,'judge/start')
                assert boot.status_code == 200, boot.text
                assert value.last_plan is not None
                assert value.context_version == s['context_version']
            finally:
                release.set()
            result=future.result(timeout=10)
        assert feed.calls == [('solara-cables', False)]
        assert 'current inbound planning scenario' in result['assistant_text']
        assert 'No current reviewed supplier plan is linked' not in result['assistant_text']
        assert 'Public publisher' in result['assistant_text']
        assert result['proposal'] is None and result['read_only']
        assert not value.research_records

def test_spoken_mixed_shipment_and_carbon_read_precedes_exact_public_consent():
    from procure.demo.live_voice import judge_research_spoken_response
    cases=[('What are our current shipments and search latest Suez Canal status?', 'read_shipments'),
           ('What are our current carbon emissions and search latest Suez Canal policy?', 'read_carbon')]
    for question, tool in cases:
        with client() as c:
            s,h=auth(c)
            r=post(c,h,'operator',message=question)
            assert r.status_code == 200, r.text
            result=r.json()
            assert result['proposal'] is not None, result
            assert result['proposal']['kind'] == 'research'
            assert result['agent_steps'][0]['tool'] == tool
            local=result['assistant_text']
            query=result['proposal']['parameters']['query']
            assert 'our current' not in query.lower()
            spoken=judge_research_spoken_response(result, query)
            assert spoken.startswith(local + ' ')
            assert spoken.endswith('I can search the public web for: ' + query + '. Say confirm research to start.')

def test_broad_inventory_typed_and_native_offline_covers_all_materials(monkeypatch):
    import procure.demo.live_voice as live
    async def bridge(socket, *, session, operate_workspace, **kwargs):
        await socket.accept();request=await socket.receive_json()
        await socket.send_json(await operate_workspace(request['message'],session.context_version,'inventory-native'))
        await socket.close()
    monkeypatch.setattr(live,'run_live_voice',bridge)
    cases=[('atlas-petrochem','Which of our inventory materials need attention and what sourcing actions should we take?'),
           ('gridspring-batteries','Analyze our inventory and sourcing across all materials')]
    for company_id,message in cases:
        with client() as c:
            s,h=auth(c)
            changed=post(c,h,'company',company_id=company_id,idempotency_key='switch').json()
            version=changed['context_version']
            expected={m['material_id'] for m in changed['company']['materials']}
            for channel in ('typed','native'):
                if channel=='typed':
                    response=post(c,h,'operator',message=message,expected_context_version=version,idempotency_key=company_id+'-typed').json()
                else:
                    with c.websocket_connect('wss://demo.test/api/demo/voice/live',headers={'Origin':'https://demo.test'}) as socket:
                        socket.send_json({'message':message});response=socket.receive_json()
                assert response['proposal'] is None and response['read_only']
                assert {m['material_id'] for m in response['analytics']['materials']}==expected
                assert 'lead' in response['assistant_text'].lower() and 'quote' in response['assistant_text'].lower()
                assert not c.app.state.sessions.get(s['session_id']).research_records

def test_verified_hindi_source_reuses_receipt_with_fresh_key():
    calls=[]
    def provider(payload):
        calls.append(payload)
        return {'model_id':'gemini-2.5-flash','model_version':'real-version','output':'Review the dated India cotton notice.'}
    with client(judge_provider=provider) as c:
        s,h=auth(c)
        version=post(c,h,'company',company_id='loomworks',idempotency_key='loom').json()['context_version']
        first=post(c,h,'judge/interpret-hindi',expected_context_version=version,idempotency_key='first')
        second=post(c,h,'judge/interpret-hindi',expected_context_version=version,idempotency_key='fresh')
        assert first.status_code==second.status_code==200
        assert first.json()['aggregate']==second.json()['aggregate']
        assert len(calls)==1
        assert c.app.state.sessions.get(s['session_id']).judge_attempts==1

def test_native_read_research_excludes_previous_context_after_return(monkeypatch):
    import procure.demo.live_voice as live
    async def bridge(socket, *, session, workspace_tools, **kwargs):
        await socket.accept();await socket.receive_json()
        try: result=await workspace_tools('read_research',{},session.context_version,'read-old')
        except Exception as exc: result={'error':getattr(exc,'code','unknown')}
        await socket.send_json(result);await socket.close()
    monkeypatch.setattr(live,'run_live_voice',bridge)
    with client() as c:
        s,h=auth(c)
        value=c.app.state.sessions.get(s['session_id'])
        value.research_records['old']={'company_id':'solara-cables','context_version':1,'state':'completed',
            'result':{'status':'completed','research_id':'old','answer':'Old answer'}}
        b=post(c,h,'company',company_id='loomworks',idempotency_key='away').json()
        post(c,h,'company',company_id='solara-cables',expected_context_version=b['context_version'],idempotency_key='back')
        with c.websocket_connect('wss://demo.test/api/demo/voice/live',headers={'Origin':'https://demo.test'}) as socket:
            socket.send_json({});assert socket.receive_json()['error']=='research_not_found'
