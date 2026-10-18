"""Final decision-closure regressions at public typed/native application boundaries."""
from copy import deepcopy
import pytest
from test_demo_judge_integration import client, auth, post, _bridge_binding_turn, Offline

DEFERRED = [
    'Prepare a bridge RFQ only if we approve the extra cost.',
    'Prepare a bridge RFQ unless we reject the extra cost.',
    'Prepare a bridge RFQ once we approve the extra cost.',
    'Prepare a bridge RFQ after approval.',
    'Prepare a bridge RFQ after we approve the extra cost.',
    'Do not draft a bridge RFQ until we approve the extra cost.',
    'Prepare a bridge RFQ until we approve the extra cost.',
    'Do not draft a bridge RFQ.',
    'What if we prepare a bridge RFQ?',
]

@pytest.mark.parametrize('channel', ['typed', 'native'])
@pytest.mark.parametrize('message', DEFERRED + [
    'Draft a bridge RFQ for cotton earlier delivery.',
    'Draft a bridge RFQ for lithium carbonate earlier delivery.',
    'Draft a bridge RFQ for unobtainium earlier delivery.',
])
def test_protected_bridge_requests_do_not_mutate(monkeypatch, channel, message):
    monkeypatch.setattr(Offline, 'available', True)
    with client() as c:
        s, h = auth(c)
        assert post(c, h, 'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan, value.plan_request, value.recovery, value.rfq,
                           value.drafts, value.pending_proposal, value.offers, value.simulated_outbox))
        result = _bridge_binding_turn(c, h, monkeypatch, channel, message)
        assert result['read_only'] is True, result
        assert result['proposal'] is None and not result.get('action_result')
        assert before == (value.last_plan, value.plan_request, value.recovery, value.rfq,
                          value.drafts, value.pending_proposal, value.offers, value.simulated_outbox)
        if ' for ' in message:
            assert result['status'] == 'needs_clarification'

@pytest.mark.parametrize('path', ['recovery', 'operator/confirm'])
def test_final_recovery_ids_match_all_persisted_projections_and_allow_bridge(path):
    with client() as c:
        s, h = auth(c)
        baseline = post(c, h, 'judge/start').json()['session']['plan']
        if path == 'recovery':
            response = post(c, h, path, baseline_plan_id=baseline['plan_id'],
                            expected_versions=baseline['offer_versions'],
                            delay_days_by_offer={baseline['selected_offer_id']: 1})
        else:
            proposal = post(c, h, 'operator', message='Prepare a recovery scenario for a one day delay of our current copper cathode supplier.').json()['proposal']
            response = post(c, h, path, proposal_id=proposal['proposal_id'], reviewed=True)
        assert response.status_code == 200, response.text
        result = response.json() if path == 'recovery' else response.json()['result']
        saved = c.get('/api/demo/session').json()
        stored = c.app.state.sessions.get(s['session_id']).recovery
        for recovery in [result, saved['recovery'], stored]:
            for role in ['baseline', 'scenario']:
                assert recovery[f'{role}_supply_coverage']['plan_id'] == recovery[role]['plan_id']
                assert recovery[f'{role}_supply_coverage'] == recovery[role]['supply_coverage']
        assert saved['plan']['supply_coverage'] == result['scenario_supply_coverage']
        drafted = post(c, h, 'operator', message='Draft a bridge RFQ for the current plan.', idempotency_key='after-recovery')
        assert drafted.json()['action_result']['tool'] == 'prepare_rfq', drafted.text
        assert drafted.json()['session']['rfq']['plan_id'] == result['scenario']['plan_id']

@pytest.mark.parametrize('path', ['operator', 'rfq'])
def test_identical_bridge_fresh_keys_reuse_canonical_draft(path):
    with client() as c:
        s, h = auth(c)
        plan = post(c, h, 'judge/start').json()['session']['plan']
        args = ({'message': 'Draft a bridge RFQ for the current plan.'} if path == 'operator' else
                {'plan_id': plan['plan_id'], 'expected_versions': plan['offer_versions'], 'reviewed': True, 'mode': 'bridge'})
        first = post(c, h, path, **args, idempotency_key='first')
        assert first.status_code == 200, first.text
        before = c.get('/api/demo/session').json()
        second = post(c, h, path, **args, idempotency_key='second')
        assert second.status_code == 200, second.text
        after = c.get('/api/demo/session').json()
        assert after['rfq'] == before['rfq']
        assert after['drafts'] == before['drafts']
        assert after['simulated_outbox'] == []
        assert c.get(f"/api/demo/rfq/{after['rfq']['draft_id']}/download").text == after['rfq']['text']
        replay = post(c, h, path, **args, idempotency_key='second')
        assert replay.status_code == 200, replay.text
        assert c.get('/api/demo/session').json()['drafts'] == before['drafts']

@pytest.mark.parametrize('channel', ['typed', 'native'])
@pytest.mark.parametrize('target,allowed', [
    ('cathode', True), ('copper cathode', True), ('current plan', True),
    ('unobtainium using the current copper plan', False),
    ('copper and unobtainium', False),
])
def test_explicit_bridge_target_alias_and_unknown_tail(monkeypatch, channel, target, allowed):
    with client() as c:
        _, h = auth(c)
        post(c, h, 'judge/start')
        result = _bridge_binding_turn(c, h, monkeypatch, channel, f'Draft a bridge RFQ for {target} earlier delivery.')
        assert result['read_only'] is not allowed, result
        after = c.get('/api/demo/session').json()
        assert bool(after['rfq']) is allowed
        assert len(after['drafts']) == int(allowed)
        assert after['simulated_outbox'] == []

@pytest.mark.parametrize('change', ['gap', 'plan', 'offer_version', 'company', 'context', 'material', 'terms', 'mode'])
def test_bridge_semantic_dedup_never_reuses_changed_or_unowned_draft(change):
    with client() as c:
        s, h = auth(c)
        post(c, h, 'judge/start')
        message = 'Draft a bridge RFQ for the current plan.'
        assert post(c, h, 'operator', message=message, idempotency_key='initial').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        first = deepcopy(value.rfq)
        if change in ('gap', 'plan'):
            request = deepcopy(value.plan_request)
            if change == 'gap':
                request['current_stock'] = '1500'
                if 'current_stock_kg' in request:
                    request['current_stock_kg'] = '1500'
            response = c.post('/api/demo/plan', headers=h, json={**request, 'expected_context_version': 1})
            assert response.status_code == 200, response.text
            if change == 'gap':
                # Isolate gap/content matching from the plan identity gate.
                value.last_plan['plan_id'] = first['plan_id']
        elif change == 'terms':
            value.drafts[-1]['body'] += '\nUnrelated payment commitment.'
        elif change == 'mode':
            value.drafts[-1]['bridge_semantics']['bridge'] = False
        else:
            field, wrong = {'company': ('company_id', 'loomworks'), 'context': ('context_version', 2),
                            'material': ('material_id', 'cotton'), 'offer_version': ('offer_versions', {})}[change]
            value.drafts[-1][field] = wrong
        before = deepcopy(value.drafts)
        result = post(c, h, 'operator', message=message, idempotency_key='changed')
        assert result.status_code == 200, result.text
        assert value.rfq['draft_id'] != first['draft_id']
        assert value.drafts[:-1] == before
        assert len(value.drafts) == 2
        assert value.simulated_outbox == []
        assert c.get(f"/api/demo/rfq/{value.rfq['draft_id']}/download").text == value.rfq['text']
        if change == 'gap':
            assert value.rfq['quantity'] != first['quantity']

@pytest.mark.parametrize('legacy', [False, True])
def test_bridge_reuses_current_canonical_draft_across_direct_and_operator_boundaries(legacy):
    with client() as c:
        s, h = auth(c)
        plan = post(c, h, 'judge/start').json()['session']['plan']
        first = post(c, h, 'rfq', plan_id=plan['plan_id'], expected_versions=plan['offer_versions'],
                     reviewed=True, mode='bridge', idempotency_key='direct-first')
        assert first.status_code == 200, first.text
        value = c.app.state.sessions.get(s['session_id'])
        if legacy:
            value.drafts[-1].pop('bridge_semantics', None)
        before = deepcopy(value.drafts)
        result = post(c, h, 'operator', message='Draft a bridge RFQ for the current plan.', idempotency_key='operator-next')
        assert result.status_code == 200, result.text
        assert result.json()['session']['rfq'] == first.json()
        assert value.drafts == before
        assert value.simulated_outbox == []

@pytest.mark.parametrize('message', DEFERRED + [
    'Draft a bridge RFQ for cotton earlier delivery.',
    'Draft a bridge RFQ for lithium carbonate earlier delivery.',
    'Draft a bridge RFQ for unobtainium earlier delivery.',
])
def test_native_generic_rfq_at_public_socket_preserves_protected_state(message):
    from contextlib import asynccontextmanager
    from test_demo_live_voice_rfq import RfqProvider, ask_for_rfq
    provider = RfqProvider()
    provider.spoken = message
    provider.tool_first = True
    @asynccontextmanager
    async def factory():
        yield provider
    with client(live_client_factory=factory) as c:
        s, h = auth(c)
        assert post(c, h, 'judge/start').status_code == 200
        value = c.app.state.sessions.get(s['session_id'])
        before = deepcopy((value.last_plan, value.plan_request, value.recovery, value.rfq,
                           value.drafts, value.pending_proposal, value.offers, value.simulated_outbox))
        result, _ = ask_for_rfq(c, provider)
        assert not result.get('action_result'), result
        assert before == (value.last_plan, value.plan_request, value.recovery, value.rfq,
                          value.drafts, value.pending_proposal, value.offers, value.simulated_outbox)


def test_native_generic_fresh_bridge_commands_reuse_one_canonical_draft():
    from contextlib import asynccontextmanager
    from test_demo_live_voice_rfq import RfqProvider, ask_for_rfq
    provider = RfqProvider()
    provider.spoken = 'Draft a bridge RFQ for the current plan. Do not send it.'
    @asynccontextmanager
    async def factory():
        yield provider
    with client(live_client_factory=factory) as c:
        _, h = auth(c)
        assert post(c, h, 'judge/start').status_code == 200
        first, _ = ask_for_rfq(c, provider)
        second, _ = ask_for_rfq(c, provider)
        assert second['session']['rfq'] == first['session']['rfq']
        state = c.get('/api/demo/session').json()
        assert len(state['drafts']) == 1 and state['simulated_outbox'] == []
        assert c.get(f"/api/demo/rfq/{state['rfq']['draft_id']}/download").text == state['drafts'][0]['body']
