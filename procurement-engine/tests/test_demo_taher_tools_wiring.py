"""Wiring of the read-only bot tools into the Live voice config and the text operator."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.company import get_company
from procure.demo.taher_tools import RANGES_ONLY as RANGE_ONLY_TEXT, TAHER_TOOLS

NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def test_voice_config_declares_the_three_blocking_tools():
    from procure.demo import live_voice
    config = live_voice._config(get_company('solara-cables'))
    declarations = {d.name: d for d in config.tools[0].function_declarations}
    for name in TAHER_TOOLS:
        assert declarations[name].behavior is not None and declarations[name].behavior.name == 'BLOCKING'
    schema = declarations['read_price_outlook'].parameters_json_schema
    assert schema['required'] == [] and 'material_id' in schema['properties']
    assert declarations['read_country_pricing'].parameters_json_schema['properties'] == {}
    assert 'price outlook' in config.system_instruction.lower()


def test_text_operator_answers_from_the_tools_without_a_model():
    with TestClient(create_app(clock=lambda: NOW), base_url='https://demo.test') as client:
        current = client.get('/api/demo/session').json()
        headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': current['csrf_token']}
        reply = client.post('/api/demo/operator', headers=headers, json={
            'message': 'What is the price outlook for copper?', 'expected_context_version': current['context_version'],
            'idempotency_key': 'taher-outlook-1'})
        assert reply.status_code == 200, reply.text
        body = reply.json()
        assert body['status'] == 'completed' and RANGE_ONLY_TEXT in body['assistant_text']
        schemes = client.post('/api/demo/operator', headers=headers, json={
            'message': 'Which carbon schemes could we use?', 'expected_context_version': current['context_version'],
            'idempotency_key': 'taher-schemes-1'}).json()
        assert 'possibly eligible, check conditions' in schemes['assistant_text']


def test_live_voice_reads_each_tool_through_the_app_handler():
    from contextlib import asynccontextmanager
    import inspect
    from test_demo_taher_native import NamedProvider, voice_tool as harness
    provider = NamedProvider()
    spoken = {'read_price_outlook': 'What is the price outlook for copper?',
              'read_carbon_schemes': 'Which carbon schemes could we use?',
              'read_country_pricing': 'What is the benchmark price for our materials?'}

    def voice_tool(client, provider, name, args, call_id):
        if 'spoken_request' in inspect.signature(harness).parameters:
            return harness(client, provider, name, args, call_id, spoken_request=spoken[name])
        return harness(client, provider, name, args, call_id)

    @asynccontextmanager
    async def factory():
        yield provider
    app = create_app(clock=lambda: NOW, live_client_factory=factory)
    with TestClient(app, base_url='https://demo.test') as client:
        outlook, model = voice_tool(client, provider, 'read_price_outlook', {'material_id': 'copper'}, 'outlook-1')
        assert outlook['status'] == 'completed' and RANGE_ONLY_TEXT in outlook['assistant_text']
        assert model['workspace']['price_outlook']['series'][0]['series_id'] == 'commodity:copper'
        schemes, _ = voice_tool(client, provider, 'read_carbon_schemes', {}, 'schemes-1')
        assert 'possibly eligible, check conditions' in schemes['assistant_text']
        pricing, _ = voice_tool(client, provider, 'read_country_pricing', {}, 'pricing-1')
        assert 'no public benchmark' in pricing['assistant_text']
