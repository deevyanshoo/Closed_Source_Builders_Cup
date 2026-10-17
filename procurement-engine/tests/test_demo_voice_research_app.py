"""The hosted Live route uses the durable public-research consent claim."""
import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from test_demo_research_app import Research


NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
URL = 'wss://demo.test/api/demo/voice/live'
ORIGIN = {'origin': 'https://demo.test'}
QUESTION = 'What is the latest Hormuz blockage impact on petrochemicals?'


class LiveProvider:
    def __init__(self, *, auto_replay=True):
        self.events = asyncio.Queue()
        self.auto_replay = auto_replay
        self.audio_count = 0
        self.responses = []
        self.response_condition = threading.Condition()
        self.client_content = []
        self.proposal_id = None
        self.prompted_query = None
        self.replayed = False

    async def receive(self):
        yield await self.events.get()

    async def send_realtime_input(self, **kwargs):
        if not kwargs.get('audio'):
            return
        self.audio_count += 1
        if self.audio_count == 1:
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='prepare_research', args={'message': QUESTION}, id='prepare')]),
                server_content=None))
        elif self.audio_count == 2:
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='confirm_research', args={'proposal_id': self.proposal_id}, id='consent')]),
                server_content=NS(input_transcription=NS(text='confirm research', finished=True),
                    output_transcription=None, interrupted=False, model_turn=None,
                    turn_complete=False)))
        elif self.audio_count == 3 and not self.auto_replay:
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='confirm_research', args={'proposal_id': self.proposal_id}, id='consent')]),
                server_content=None))

    async def send_tool_response(self, *, function_responses):
        with self.response_condition:
            self.responses.append(function_responses)
            self.response_condition.notify_all()
        if function_responses.name == 'prepare_research':
            proposal = function_responses.response['proposal']
            self.proposal_id = proposal['proposal_id']
            self.prompted_query = proposal['parameters']['query']
            await self.events.put(NS(tool_call=None, server_content=NS(
                input_transcription=None,
                output_transcription=NS(text=f'I can search for: {self.prompted_query}. Say confirm research to start.'),
                interrupted=False, model_turn=NS(parts=[NS(inline_data=NS(
                    data=b'\x01\x00', mime_type='audio/pcm;rate=24000'))]),
                turn_complete=True)))
        elif function_responses.name == 'confirm_research' and self.auto_replay and not self.replayed:
            self.replayed = True
            await self.events.put(NS(tool_call=NS(function_calls=[NS(
                name='confirm_research', args={'proposal_id': self.proposal_id}, id='consent'),
                NS(name='confirm_research', args={'proposal_id': self.proposal_id}, id='new-attempt')]),
                server_content=None))
        elif function_responses.id == 'new-attempt':
            await self.events.put(NS(tool_call=None, server_content=NS(
                input_transcription=None,
                output_transcription=NS(text='The existing research result is ready.'),
                interrupted=False, model_turn=None, turn_complete=False)))

    async def send_client_content(self, *, turns, turn_complete):
        self.client_content.append((turns, turn_complete))

    def wait_responses(self, count, timeout=2):
        with self.response_condition:
            return self.response_condition.wait_for(lambda: len(self.responses) >= count, timeout)


def setup(*, auto_replay=True):
    research, live = Research(), LiveProvider(auto_replay=auto_replay)

    @asynccontextmanager
    async def factory():
        yield live

    app = create_app(clock=lambda: NOW, research_provider=research, live_client_factory=factory)
    return app, research, live


def next_event(socket, kind, *, reject=()):
    while True:
        event = socket.receive_json()
        if event['type'] in reject:
            raise AssertionError(f'Unexpected {event["type"]} before {kind}')
        if event['type'] == kind:
            return event


def start(session):
    return {'type': 'start', 'csrf_token': session['csrf_token'],
            'expected_context_version': session['context_version']}


def test_spoken_consent_claims_once_and_returns_authoritative_research_result():
    app, research, live = setup()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        with client.websocket_connect(URL, headers=ORIGIN) as socket:
            socket.send_json(start(session))
            assert next_event(socket, 'ready')['context_version'] == 1
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            prepared = next_event(socket, 'tool_result')['result']
            assert prepared['proposal']['kind'] == 'research'
            assert research.calls == []
            assert next_event(socket, 'turn_complete')['type'] == 'turn_complete'
            assert live.prompted_query == prepared['proposal']['parameters']['query']

            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            applied = next_event(socket, 'tool_result')['result']
            assert set(applied) == {'turn_id', 'company_id', 'context_version', 'status',
                                    'assistant_text', 'agent_steps', 'evidence_ids',
                                    'proposal', 'read_only', 'session', 'action_result', 'workspace'}
            assert applied['status'] == 'completed' and applied['read_only'] is False
            assert applied['proposal'] is None
            assert applied['session']['session_id'] == session['session_id']
            assert applied['action_result']['tool'] == 'confirm_research'
            assert applied['workspace']['research']['status'] == 'completed'
            assert set(applied['workspace']) == {'research'}
            assert applied['workspace']['research']['query'] == live.prompted_query
            assert research.calls == [live.prompted_query]

            assert live.wait_responses(4, timeout=2)
            first, replay, later = live.responses[1:4]
            assert first.id == 'consent' and first.response['read_only'] is False
            assert replay.id == 'consent' and replay.response['read_only'] is True
            assert later.id == 'new-attempt' and later.response['read_only'] is True
            from procure.demo.live_voice import research_spoken_result
            expected_speech = research_spoken_result(applied)
            for response in (first, replay, later):
                assert response.response['required_spoken_response'] == expected_speech
                assert 'workspace' not in response.response and 'assistant_text' not in response.response
            assert first.response['action_applied'] is True
            assert first.response['action_result'] == {'tool': 'confirm_research'}
            record = next(item for item in app.state.sessions.get(session['session_id']).research_records.values()
                          if item.get('state') == 'completed')
            assert record['result']['answer'] == applied['workspace']['research']['answer']
            assert record['result']['sources'] == applied['workspace']['research']['sources']
            for reused in (replay, later):
                assert reused.response['action_applied'] is False
                assert reused.response['tool_succeeded'] is True
                assert 'session' not in reused.response and 'action_result' not in reused.response
            marker = next_event(socket, 'transcript', reject={'tool_result', 'error'})
            assert marker['text'] == 'The existing research result is ready.'
            assert research.calls == [live.prompted_query]
            assert 'session' not in live.responses[1].response
            socket.send_json({'type': 'stop'})


def test_replaced_proposal_cannot_be_confirmed_by_old_spoken_prompt():
    app, research, live = setup()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        auth = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        with client.websocket_connect(URL, headers=ORIGIN) as socket:
            socket.send_json(start(session))
            next_event(socket, 'ready')
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            old = next_event(socket, 'tool_result')['result']['proposal']
            next_event(socket, 'turn_complete')
            replacement = client.post('/api/demo/operator', headers=auth, json={
                'message': QUESTION, 'expected_context_version': 1,
                'idempotency_key': 'new-public-request'})
            assert replacement.status_code == 200, replacement.text
            assert replacement.json()['proposal']['proposal_id'] != old['proposal_id']
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            denied = next_event(socket, 'tool_result')['result']
            assert denied['status'] == 'needs_clarification'
            assert research.calls == []
            socket.send_json({'type': 'stop'})


def test_company_switch_closes_voice_before_old_research_can_run():
    app, research, _ = setup()
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        auth = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        with client.websocket_connect(URL, headers=ORIGIN) as socket:
            socket.send_json(start(session))
            next_event(socket, 'ready')
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            assert next_event(socket, 'tool_result')['result']['proposal']['kind'] == 'research'
            next_event(socket, 'turn_complete')
            switched = client.post('/api/demo/company', headers=auth, json={
                'company_id': 'terrapack', 'expected_context_version': 1,
                'idempotency_key': 'switch-before-spoken-consent'})
            assert switched.status_code == 200, switched.text
            assert next_event(socket, 'error')['code'] == 'stale_context'
            assert research.calls == []


def test_cached_confirmation_replay_cannot_restore_older_buyer_form():
    app, research, live = setup(auto_replay=False)
    with TestClient(app, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        auth = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
        with client.websocket_connect(URL, headers=ORIGIN) as socket:
            socket.send_json(start(session))
            next_event(socket, 'ready')
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            next_event(socket, 'tool_result')
            next_event(socket, 'turn_complete')
            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            applied = next_event(socket, 'tool_result')['result']
            assert applied['read_only'] is False

            defaults = next(s['buyer_defaults'] for s in session['company']['quote_coverage']['scenarios']
                            if s['material_id'] == 'polymer')
            draft = {k: v for k, v in defaults.items() if k not in
                     ('offer_ids', 'expected_versions', 'quantity_kg', 'current_stock_kg',
                      'additional_cost_per_kg')}
            saved = client.post('/api/demo/buyer-draft', headers=auth, json={
                'buyer_draft': draft, 'expected_context_version': 1,
                'idempotency_key': 'draft-after-research'})
            assert saved.status_code == 200, saved.text
            assert saved.json()['buyer_draft']['material'] == 'polymer'

            socket.send_json({'type': 'audio', 'data': 'AQACAA=='})
            assert live.wait_responses(3, timeout=2)
            replay = live.responses[2]
            assert replay.id == 'consent' and replay.response['read_only'] is True
            assert replay.response['action_applied'] is False
            assert 'session' not in replay.response and 'action_result' not in replay.response
            assert client.get('/api/demo/session').json()['buyer_draft']['material'] == 'polymer'
            assert research.calls == [live.prompted_query]
            socket.send_json({'type': 'stop'})
