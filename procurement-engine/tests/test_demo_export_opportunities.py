"""Export opportunities API: read-only ranking, CSRF-checked margin scenario, company scoped, no runtime fetch (#131)."""
import json
import re
import socket
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from procure.demo.export_api import (export_opportunities_export, export_opportunities_router,
                                     get_export_opportunities)
from procure.demo.sessions import SessionError

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r'buyer demand|confirmed buyer|\bquote\b|saving|recommend', re.IGNORECASE)
LABELS = ('observed historical trade, not buyer demand, a confirmed buyer or a price quote',
          'scenario from your inputs and historical customs unit values, not a buyer price, quote or forecast')


def test_ranking_shape_for_solara():
    view = get_export_opportunities('solara-cables', context_version=3)
    assert view['kind'] == 'export_opportunities' and view['status'] == 'available'
    assert (view['company_id'], view['context_version'], view['sort']) == ('solara-cables', 3, 'market_size')
    assert view['product']['hs_heading'] == '8544' and view['product']['note'] == 'illustrative mapping for a fictional company'
    assert view['home_country'] == 'India' and view['reporting_currency'] == 'USD'
    assert view['source']['attribution'].startswith('Source: UN Comtrade')
    assert view['source']['years'] == [2020, 2021, 2022, 2023, 2024]
    assert view['markets'] and all({'code', 'name', 'import_value', 'growth', 'home_share', 'unit_value'} <= set(m)
                                   for m in view['markets'])
    values = [m['import_value'] for m in view['markets']]
    reported = [v for v in values if v['status'] == 'available']
    assert reported == sorted(reported, key=lambda v: -float(v['value_usd']))


def test_every_company_ranks_and_unknown_company_is_explicit():
    for company_id in json.loads((ROOT / 'config' / 'export_products_v1.json').read_text())['companies']:
        view = get_export_opportunities(company_id['company_id'], context_version=1)
        assert view['status'] == 'available' and view['company_id'] == company_id['company_id']
    missing = get_export_opportunities('nobody', context_version=1)
    assert missing['status'] == 'unknown_company' and missing['markets'] == []
    with pytest.raises(ValueError):
        get_export_opportunities('solara-cables', sort='best')


def test_margin_inputs_feed_the_margin_sort_and_unknown_markets_are_rejected():
    view = get_export_opportunities('solara-cables', context_version=1)
    code = view['markets'][0]['code']
    inputs = {code: {'unit_cost_per_kg': '1', 'freight_insurance_per_kg': '0.5', 'tariff_rate_percent': '5',
                     'capacity_kg_per_year': '100000', 'target_share_percent': '1'}}
    sorted_view = get_export_opportunities('solara-cables', sort='margin', inputs=inputs, context_version=1)
    first = next(m for m in sorted_view['markets'] if m['code'] == code)
    assert first['margin']['label'] == LABELS[1] and first['margin']['inputs'][0]['source'] == 'user supplied'
    with pytest.raises(ValueError):
        get_export_opportunities('solara-cables', inputs={999999: {}}, context_version=1)


def test_labels_and_forbidden_words():
    for company_id in ('solara-cables', 'forgeline-components', 'atlas-petrochem'):
        view = get_export_opportunities(company_id, context_version=1)
        assert view['label'] == LABELS[0]
        text = json.dumps(view)
        for label in LABELS:
            text = text.replace(label, '')
        assert not FORBIDDEN.search(text), FORBIDDEN.search(text)


def test_export_entry_carries_the_ranking_with_scope():
    view = get_export_opportunities('solara-cables', context_version=4)
    exported = export_opportunities_export(view)
    assert exported['schema'] == 'export-opportunities-export.v1'
    assert (exported['company_id'], exported['context_version']) == ('solara-cables', 4)
    assert exported['markets'] == view['markets'] and exported['label'] == LABELS[0]
    assert exported['source'] == view['source']


def make_client(state):
    app = FastAPI()

    @app.exception_handler(SessionError)
    async def failure(request, exc):
        return JSONResponse({'code': exc.code, 'message': exc.message}, status_code=exc.status_code)

    def session(request: Request):
        return state

    def mutate(request: Request):
        if request.headers.get('x-csrf-token') != 'token':
            raise SessionError('invalid_csrf', 'Refresh the demo session and try again.', 403)
        return state

    async def body(request: Request):
        return await request.json()

    def shape(data, required, optional=()):
        if not set(required) <= data.keys() or not data.keys() <= set(required) | set(optional):
            raise SessionError('invalid_request', 'Request fields do not match this operation.', 422)

    def context(value, data):
        if data.get('expected_context_version') != value.context_version:
            raise SessionError('stale_context', 'Company context changed. Refresh before continuing.', 409)

    app.include_router(export_opportunities_router(session=session, mutate=mutate, body=body, shape=shape,
                                                   context=context))
    return TestClient(app)


def test_router_is_company_scoped_a_to_b_to_a_and_margin_needs_csrf_and_context():
    state = SimpleNamespace(company_id='solara-cables', context_version=1, lock=Lock())
    with make_client(state) as client:
        first = client.get('/api/demo/export-opportunities').json()
        state.company_id, state.context_version = 'harvest-foods', 2
        second = client.get('/api/demo/export-opportunities', params={'sort': 'growth'}).json()
        state.company_id, state.context_version = 'solara-cables', 3
        third = client.get('/api/demo/export-opportunities').json()
        assert client.get('/api/demo/export-opportunities', params={'sort': 'score'}).status_code == 422
        code = third['markets'][0]['code']
        payload = {'market_code': code, 'expected_context_version': 3,
                   'inputs': {'unit_cost_per_kg': '1', 'freight_insurance_per_kg': '0.5', 'tariff_rate_percent': '5',
                              'capacity_kg_per_year': '100000', 'target_share_percent': '1'}}
        assert client.post('/api/demo/export-opportunities/margin', json=payload).status_code == 403
        headers = {'X-CSRF-Token': 'token'}
        stale = client.post('/api/demo/export-opportunities/margin', json={**payload, 'expected_context_version': 1}, headers=headers)
        assert stale.status_code == 409
        ok = client.post('/api/demo/export-opportunities/margin', json=payload, headers=headers)
        assert ok.status_code == 200
        bad = client.post('/api/demo/export-opportunities/margin', headers=headers,
                          json={**payload, 'inputs': {**payload['inputs'], 'target_share_percent': '150'}})
        assert bad.status_code == 422
        unknown = client.post('/api/demo/export-opportunities/margin', headers=headers, json={**payload, 'market_code': 999999})
        assert unknown.status_code == 422
    assert (first['company_id'], first['context_version']) == ('solara-cables', 1)
    assert (second['company_id'], second['context_version'], second['sort']) == ('harvest-foods', 2, 'growth')
    assert second['product']['hs_heading'] == '1905'
    assert (third['company_id'], third['context_version']) == ('solara-cables', 3)
    margin = ok.json()
    assert (margin['company_id'], margin['context_version'], margin['market_code']) == ('solara-cables', 3, code)
    assert margin['margin']['label'] == LABELS[1]


def test_hosted_runtime_makes_no_network_call(monkeypatch):
    sources = [*(ROOT / 'src' / 'procure' / 'exports').glob('*.py'), ROOT / 'src' / 'procure' / 'demo' / 'export_api.py']
    for path in sources:
        text = path.read_text()
        assert not re.search(r'^\s*(import|from)\s+(urllib|requests|httpx|aiohttp|http\.client|socket)\b', text, re.MULTILINE), path

    def refuse(*args, **kwargs):
        raise AssertionError('network access attempted')
    monkeypatch.setattr(socket, 'socket', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)
    assert get_export_opportunities('stonebridge-materials', context_version=1)['status'] == 'available'
