"""Company catalogue and context isolation at the hosted API boundary."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
from decimal import Decimal

from fastapi.testclient import TestClient

from procure.demo.app import create_app


NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def headers(client):
    token = client.get('/api/demo/session').json()['csrf_token']
    return {'Origin': 'https://demo.test', 'X-CSRF-Token': token}


def client_for(news_service=None):
    return TestClient(create_app(clock=lambda: NOW, news_service=news_service), base_url='https://demo.test')


def select(client, auth, company_id, version, key):
    return client.post('/api/demo/company', headers=auth, json={
        'company_id': company_id, 'expected_context_version': version, 'idempotency_key': key})


def test_catalogue_has_ten_varied_dated_profiles_with_explicit_units_and_coverage():
    with client_for() as client:
        session = client.get('/api/demo/session').json()
        assert session['company']['company_id'] == 'solara-cables'
        assert session['context_version'] == 1
        profiles = client.get('/api/demo/companies').json()['companies']
        assert {p['company_id'] for p in profiles} == {
            'solara-cables', 'terrapack', 'loomworks', 'harvest-foods', 'meridian-pharma',
            'forgeline-components', 'gridspring-batteries', 'circuitpeak-systems',
            'atlas-petrochem', 'stonebridge-materials'}
        assert len({p['sector'] for p in profiles}) >= 8
        assert len({p['reporting_currency'] for p in profiles}) >= 4
        assert len({len(p['materials']) for p in profiles}) >= 3
        for profile in profiles:
            assert profile['synthetic'] is True and profile['as_of']
            assert profile['locations'] and profile['suppliers'] and profile['orders'] and profile['routes']
            materials = {m['material_id']: m for m in profile['materials']}
            for material in materials.values():
                assert material['quantity_unit']
                for field in ('stock', 'consumption_per_day', 'monthly_requirement'):
                    assert isinstance(material[field], str)
            for order in profile['orders']:
                assert order['material_id'] in materials
                assert order['unit'] == materials[order['material_id']]['quantity_unit']
                assert isinstance(order['quantity'], str)
            for route in profile['routes']:
                assert route['direction'] in ('import', 'export')
                assert set(route['materials']) <= materials.keys()
                assert route['currency'] in profile['currencies']
        solara = next(p for p in profiles if p['company_id'] == 'solara-cables')
        assert solara['quote_coverage']['scenarios'][0]['material_id'] == 'copper'
        assert set(solara['quote_coverage']['scenarios'][0]['fixture_ids']) == {'late-copper', 'express-copper', 'eu-copper'}
        assert solara['buyer_defaults']['material'] == 'copper'
        assert all(len(p['quote_coverage']['fixture_ids']) == 3 * len(p['materials']) for p in profiles)


def test_catalogue_depth_references_and_original_solara_scenario():
    with client_for() as client:
        profiles = {p['company_id']: p for p in client.get('/api/demo/companies').json()['companies']}
        assert len(profiles['atlas-petrochem']['materials']) >= 12
        assert len(profiles['gridspring-batteries']['materials']) >= 10
        assert len(profiles['stonebridge-materials']['materials']) >= 8
        assert all(len(p['materials']) >= 3 for p in profiles.values())
        for profile in profiles.values():
            material_ids = {m['material_id'] for m in profile['materials']}
            assert len(material_ids) == len(profile['materials'])
            for material in profile['materials']:
                for field in ('stock', 'consumption_per_day', 'monthly_requirement'):
                    assert Decimal(material[field]) >= 0
            assert all(set(s['materials']) <= material_ids for s in profile['suppliers'])
            supplied_ids = {material_id for supplier in profile['suppliers']
                            for material_id in supplier['materials']}
            assert material_ids <= supplied_ids, profile['company_id']
            assert all(set(r['materials']) <= material_ids for r in profile['routes'])
            assert all(o['material_id'] in material_ids for o in profile['orders'])
        solara = profiles['solara-cables']
        assert {k: solara['buyer_defaults'][k] for k in (
            'need_by', 'additional_cost_per_kg', 'current_stock_kg', 'quantity_kg', 'budget')} == {
                'need_by': '2026-11-05', 'additional_cost_per_kg': '0.25',
                'current_stock_kg': '2000', 'quantity_kg': '10000', 'budget': '100000'}
        suppliers = {s['name']: s['lead_time_days'] for s in solara['suppliers']}
        assert suppliers['Atacama Red Metals SpA [SYNTHETIC]'] == 45
        assert suppliers['Swift Copper Trading Ltd [SYNTHETIC]'] == 14
        assert suppliers['Europa Copper SA [SYNTHETIC]'] == 10


def test_corrected_supplier_gaps_have_matching_settlement_routes():
    with client_for() as client:
        profiles = {p['company_id']: p for p in client.get('/api/demo/companies').json()['companies']}
        for company_id, material_id, source_country in (
            ('harvest-foods', 'sugar', 'Uganda'),
            ('gridspring-batteries', 'graphite', 'China'),
            ('stonebridge-materials', 'aggregate', 'Mozambique'),
        ):
            profile = profiles[company_id]
            suppliers = [s for s in profile['suppliers'] if material_id in s['materials']
                         and '-supplier-' in s['supplier_id']]
            assert len(suppliers) == 1
            supplier = suppliers[0]
            assert supplier['country'] == source_country
            assert supplier['name'].endswith('[SYNTHETIC]')
            assert supplier['settlement_currency'] in profile['currencies']
            routes = [r for r in profile['routes'] if material_id in r['materials']]
            assert any(r['direction'] == 'import' and
                       r['currency'] == supplier['settlement_currency'] and
                       source_country in r['origin'] for r in routes)
            assert any(o['material_id'] == material_id for o in profile['orders'])


def test_noncovered_company_rejects_copper_and_requires_explicit_context():
    with client_for() as client:
        auth = headers(client)
        selected = select(client, auth, 'terrapack', 1, 'select-terra')
        assert selected.status_code == 200, selected.text
        assert selected.json()['context_version'] == 2
        assert selected.json()['offers'] == []
        payload = {'fixture_id': 'express-copper', 'mode': 'rules', 'idempotency_key': 'terra-copper'}
        assert client.post('/api/demo/extract', headers=auth, json=payload).status_code == 409
        payload['expected_context_version'] = 2
        blocked = client.post('/api/demo/extract', headers=auth, json=payload)
        assert blocked.status_code == 422 and blocked.json()['error']['code'] == 'quote_unavailable'
        exported = client.get('/api/demo/export').json()
        assert exported['company']['company_id'] == 'terrapack'
        assert exported['context_version'] == 2
        assert exported['offers'] == []


def test_switch_restores_only_own_workspace_and_rejects_old_retry():
    with client_for() as client:
        auth = headers(client)
        payload = {'fixture_id': 'express-copper', 'mode': 'rules', 'idempotency_key': 'first'}
        first = client.post('/api/demo/extract', headers=auth, json=payload)
        assert first.status_code == 200, first.text
        assert first.json()['offer']['fixture_id'] == 'express-copper'
        offer_id = first.json()['offer']['offer_id']
        assert select(client, auth, 'loomworks', 1, 'to-loom').json()['offers'] == []
        assert client.get('/api/demo/offers').json()['offers'] == []
        assert client.post('/api/demo/extract', headers=auth, json=payload).status_code == 409
        back = select(client, auth, 'solara-cables', 2, 'back-solara')
        assert back.status_code == 200
        assert back.json()['context_version'] == 3
        assert [o['offer_id'] for o in back.json()['offers']] == [offer_id]
        assert client.post('/api/demo/extract', headers=auth, json=payload).status_code == 409


def test_inflight_extraction_cannot_commit_after_switch(monkeypatch):
    from procure.demo import quote_service
    entered, release = Event(), Event()
    original = quote_service.extract_fixture

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(quote_service, 'extract_fixture', slow)
    with client_for() as client:
        auth = headers(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, '/api/demo/extract', headers=auth, json={
                'fixture_id': 'late-copper', 'mode': 'rules', 'idempotency_key': 'slow',
                'expected_context_version': 1})
            assert entered.wait(5)
            switched = select(client, auth, 'terrapack', 1, 'switch-during-extract')
            assert switched.status_code == 200, switched.text
            release.set()
            assert future.result(timeout=5).status_code == 409
        assert client.get('/api/demo/session').json()['offers'] == []


class FakeNews:
    def __init__(self, entered=None, release=None):
        self.calls = []
        self.entered, self.release = entered, release

    def get(self, company, force=False):
        self.calls.append((company['company_id'], force))
        if self.entered:
            self.entered.set()
            assert self.release.wait(5)
        return {'checked_at': NOW.isoformat(), 'cache_state': 'fresh', 'sources': [],
                'items': [], 'fx': [], 'limitations': ['Synthetic exposure only.']}


def test_news_wrapping_refresh_replay_and_export_are_company_scoped():
    fake = FakeNews()
    with client_for(fake) as client:
        auth = headers(client)
        assert select(client, auth, 'harvest-foods', 1, 'harvest').status_code == 200
        news = client.get('/api/demo/news')
        assert news.status_code == 200, news.text
        assert news.json()['company_id'] == 'harvest-foods'
        assert news.json()['context_version'] == 2
        assert client.get('/api/demo/export').json()['news'] == news.json()
        payload = {'expected_context_version': 2, 'idempotency_key': 'refresh'}
        refreshed = client.post('/api/demo/news/refresh', headers=auth, json=payload)
        assert refreshed.status_code == 200, refreshed.text
        assert client.post('/api/demo/news/refresh', headers=auth, json=payload).json()['replayed'] is True
        assert fake.calls == [('harvest-foods', False), ('harvest-foods', True)]
        assert select(client, auth, 'solara-cables', 2, 'return').status_code == 200
        assert client.post('/api/demo/news/refresh', headers=auth, json=payload).status_code == 409
        assert select(client, auth, 'harvest-foods', 3, 'revisit-harvest').status_code == 200
        assert client.get('/api/demo/export').json()['news'] is None


def test_news_get_rechecks_context_after_threadpool_completion():
    entered, release = Event(), Event()
    with client_for(FakeNews(entered, release)) as client:
        auth = headers(client)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.get, '/api/demo/news')
            assert entered.wait(5)
            assert select(client, auth, 'terrapack', 1, 'switch-news').status_code == 200
            release.set()
            assert future.result(timeout=5).status_code == 409
        assert client.get('/api/demo/export').json()['news'] is None


def test_server_owned_news_context_cannot_be_overwritten_by_adapter_payload():
    class ExtraNews(FakeNews):
        def get(self, company, force=False):
            return {**super().get(company, force=force),
                    'company_id': 'wrong-company', 'context_version': -1}

    with client_for(ExtraNews()) as client:
        headers(client)
        result = client.get('/api/demo/news').json()
        assert result['company_id'] == 'solara-cables'
        assert result['context_version'] == 1
