"""Regression: the reported voice question reads the connected news without web consent."""
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
import json
import pytest
from fastapi.testclient import TestClient
from procure.demo.app import create_app
from procure.demo.company import list_companies, get_company
from test_demo_proactive_risks_app import NOW, News
from test_demo_live_voice import Provider

QUESTION = "What's happening in the world that can affect my business procurement?"

@pytest.mark.parametrize('company_id', [row['company_id'] for row in list_companies()])
@pytest.mark.parametrize('tool', ['read_news', 'prepare_research'])
def test_world_briefing_reads_news_on_first_turn_without_research_or_mutation(tool, company_id, caplog):
    class NewsProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                await self.events.put(NS(tool_call=NS(function_calls=[NS(name=tool,
                    args={'message': QUESTION} if tool == 'prepare_research' else {}, id='news')]),
                    server_content=None))
    provider = NewsProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, news_service=News(), live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        if before['company_id'] != company_id:
            before = client.post('/api/demo/company', headers={'Origin': 'https://demo.test', 'X-CSRF-Token': before['csrf_token']},
                json={'company_id': company_id, 'expected_context_version': before['context_version'], 'idempotency_key': 'company'}).json()
        value = app.state.sessions.get(before['session_id'])
        assert value.news is None
        with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'origin': 'https://demo.test'}) as ws:
            ws.send_json({'type': 'start', 'csrf_token': before['csrf_token'], 'expected_context_version': before['context_version']})
            assert ws.receive_json()['type'] == 'ready'
            ws.send_json({'type': 'audio', 'data': 'AQACAA=='})
            events = []
            while True:
                event = ws.receive_json(); events.append(event)
                if event['type'] in ('turn_complete', 'error'):
                    break
            results = [event['result'] for event in events if event['type'] == 'tool_result']
            assert results and results[0]['status'] == 'completed', events
            result = results[0]
            brief = result['workspace']['news_brief']
            assert brief['company_id'] == before['company_id']
            assert brief['headlines'][0]['title'] == get_company(company_id)['materials'][0]['name'] + ' supply shortage'
            assert brief['headlines'][0]['published_at'] == NOW.isoformat()
            assert result['proposal'] is None and result['read_only']
            assert any(event['type'] == 'audio' for event in events)
            model = provider.tool_response.response
            assert model['workspace']['news_brief']['headlines']
            assert before['session_id'] not in json.dumps(model)
            assert before['csrf_token'] not in json.dumps(model)
            ws.send_json({'type': 'stop'})
        assert value.operator_turns == 0 and not value.research_records
        assert client.get('/api/demo/session').json() == before

        telemetry = [json.loads(record.message) for record in caplog.records if '"event":"voice_tool_result"' in record.message]
        assert telemetry and telemetry[-1]['news_headlines'] == 1
        assert telemetry[-1]['status'] == 'completed'
        assert before['session_id'] not in json.dumps(telemetry)
        assert QUESTION not in json.dumps(telemetry)


def test_news_unavailable_is_spoken_as_a_coverage_gap_without_private_error():
    class Unavailable:
        def get(self, *_):
            raise RuntimeError('private upstream auth diagnostic')
    class NewsProvider(Provider):
        async def send_realtime_input(self, **kwargs):
            if kwargs.get('audio'):
                await self.events.put(NS(tool_call=NS(function_calls=[NS(name='read_news',args={},id='news')]),server_content=None))
    provider = NewsProvider()
    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, news_service=Unavailable(), live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        before = client.get('/api/demo/session').json()
        with client.websocket_connect('wss://demo.test/api/demo/voice/live', headers={'origin': 'https://demo.test'}) as ws:
            ws.send_json({'type':'start','csrf_token':before['csrf_token'],'expected_context_version':1})
            assert ws.receive_json()['type'] == 'ready'
            ws.send_json({'type':'audio','data':'AQACAA=='})
            events=[]
            while True:
                event=ws.receive_json();events.append(event)
                if event['type'] in ('turn_complete','error'):break
            result=next(e['result'] for e in events if e['type']=='tool_result')
            assert result['workspace']['news_brief']['availability']=='unavailable'
            assert result['workspace']['news_brief']['headlines']==[]
            assert 'unavailable' in result['assistant_text']
            assert 'private upstream' not in json.dumps(events)
            assert any(e['type']=='audio' for e in events)
            ws.send_json({'type':'stop'})


def test_large_workspace_preserves_news_when_other_model_context_is_trimmed():
    import asyncio
    from procure.demo import live_voice
    from test_demo_live_voice import Socket, setup, start, connection, next_type
    async def scenario():
        store, session = setup()
        provider, socket = Provider(), Socket(start(session))
        async def tool(*_):
            return {'turn_id': 'news', 'company_id': session.company_id, 'context_version': 1,
                'status': 'completed', 'assistant_text': 'Dated news is ready.', 'agent_steps': [],
                'evidence_ids': [], 'proposal': None, 'read_only': True,
                'workspace': {'company': {'large': 'x' * 80_000},
                    'news_brief': {'availability': 'partial', 'headlines': [{'title': 'Copper port closure'}],
                                   'global_watch': {'events': []}}}}
        task = asyncio.create_task(live_voice.run_live_voice(socket, session=session, store=store,
            operate_workspace=lambda *_:None, workspace_tools=tool, client_factory=connection(provider)))
        await next_type(socket, 'ready')
        await provider.events.put(NS(tool_call=NS(function_calls=[NS(name='read_workspace', args={}, id='read')]),server_content=None))
        await next_type(socket, 'tool_result'); await next_type(socket, 'turn_complete')
        assert provider.tool_response.response['workspace']['news_brief']['headlines'][0]['title']=='Copper port closure'
        assert 'company' not in provider.tool_response.response['workspace']
        assert len(json.dumps(provider.tool_response.response,ensure_ascii=True)) < 64_000
        await socket.frames.put('{"type":"stop"}'); await asyncio.wait_for(task,2)
    asyncio.run(scenario())
