"""Integration gates for authoritative progress and reusable scoped context."""
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo import live_voice, research
from test_demo_research_app import Research, setup, propose, approve
from test_demo_operator import Provider


def test_default_research_adapter_is_shared_but_results_are_fresh(monkeypatch):
    adapters = []
    def factory(**kwargs):
        adapter = Research()
        adapters.append(adapter)
        return adapter
    monkeypatch.setattr(research, 'VertexResearchProvider', factory)
    client = TestClient(create_app(), base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
    for index in range(2):
        proposal = propose(client, headers, key=f'turn-{index}').json()['proposal']
        assert approve(client, headers, proposal, key=f'approval-{index}').status_code == 200
    assert len(adapters) == 1
    assert len(adapters[0].calls) == 2  # No grounded answer cache.


def test_voice_research_start_follows_durable_claim_and_budget(monkeypatch):
    events = []
    class ObservedResearch(Research):
        def research(self, query):
            events.append('provider')
            return super().research(query)
    client, headers, provider, app = setup(provider=ObservedResearch())
    proposal = propose(client, headers).json()['proposal']
    store = app.state.sessions
    original_save = store.save
    original_budget = store.reserve_research_budget
    def saved(value):
        if any(record['state'] == 'running' for record in value.research_records.values()):
            events.append('durable_claim')
        return original_save(value)
    def reserve(*args):
        events.append('budget')
        return original_budget(*args)
    monkeypatch.setattr(store, 'save', saved)
    monkeypatch.setattr(store, 'reserve_research_budget', reserve)
    async def bridge(websocket, *, research_tools=None, **kwargs):
        await websocket.accept()
        assert callable(research_tools), 'Dedicated research callback is not wired'
        async def progress(phase):
            events.append(phase)
        result = await research_tools({'proposal_id': proposal['proposal_id']}, 1, 'spoken-approval', progress)
        assert result['workspace']['research']['status'] == 'completed'
        # Replaying the same durable approval never starts another provider call.
        await research_tools({'proposal_id': proposal['proposal_id']}, 1, 'spoken-approval', progress)
        await websocket.send_json({'ok': True})
    monkeypatch.setattr(live_voice, 'run_live_voice', bridge)
    with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'Origin': 'https://demo.test'}) as ws:
        assert ws.receive_json() == {'ok': True}
    assert events.index('durable_claim') < events.index('budget') < events.index('started') < events.index('provider')
    assert events.count('started') == events.count('provider') == len(provider.calls) == 1


def test_app_reuses_only_prepared_context_inside_one_session():
    now = [datetime(2026, 10, 15, tzinfo=timezone.utc)]
    app = create_app(clock=lambda: now[0], operator_provider=Provider())
    client = TestClient(app, base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
    for index in range(2):
        response = client.post('/api/demo/operator', headers=headers, json={'message': 'Inspect inventory',
            'expected_context_version': 1, 'idempotency_key': f'context-turn-{index}'})
        assert response.status_code == 200
        now[0] += timedelta(seconds=1)
    assert app.state.operator_context_cache.hits == 1
    assert app.state.operator_context_cache.misses == 1
