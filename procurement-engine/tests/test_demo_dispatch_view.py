"""Company-scoped synthetic dispatch view and hosted capabilities (product review, 16 Oct)."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from procure.demo.company import get_company, list_companies
from procure.demo.dispatch_view import (HOSTED_PAGES, build_dispatch_view, dispatch_router, hosted_capabilities,
                                        load_fixture_shipments)

FIXTURES = Path(__file__).resolve().parents[2] / 'apps' / 'web' / 'src' / 'dispatch-fixtures.json'


@pytest.fixture(scope='module')
def shipments():
    return load_fixture_shipments(FIXTURES)


def offer(offer_id, material):
    return {'offer_id': offer_id, 'status': 'saved', 'items': [{'material': {'value': material}}]}


def test_every_company_sees_only_its_own_order_and_offer_ids(shipments):
    all_orders = {o['order_id'] for s in list_companies() for o in get_company(s['company_id'])['orders']}
    for summary in list_companies():
        company = get_company(summary['company_id'])
        own_orders = {o['order_id'] for o in company['orders']}
        material = company['orders'][0]['material_id']
        own_offer = offer(f"{company['company_id']}-quote", material)
        view = build_dispatch_view(company, [own_offer], None, None, shipments=shipments)
        assert view['company_id'] == company['company_id']
        assert view['label'] == 'Synthetic fixture data: shipments are illustrations, not tracked cargo.'
        for row in view['shipments']:
            assert row['view_id'].startswith(company['company_id'] + ':')
            if row['order']['status'] == 'linked':
                assert row['order']['order_id'] in own_orders
            else:
                assert row['order']['order_id'] is None and row['order']['reason']
            assert row['order']['order_id'] not in (all_orders - own_orders)
            if row['offer']['status'] == 'linked':
                assert row['offer']['offer_id'] == own_offer['offer_id']
                assert row['order']['material_id'] == material


def test_missing_links_are_shown_as_unavailable_not_guessed(shipments):
    company = get_company('meridian-pharma')
    view = build_dispatch_view(company, [], None, None, shipments=shipments)
    assert len(view['shipments']) == len(shipments)
    linked = [r for r in view['shipments'] if r['order']['status'] == 'linked']
    assert len(linked) == min(len(shipments), len(company['orders']))
    for row in view['shipments']:
        reason = ('No saved quote in this session for the linked order material.' if row['order']['status'] == 'linked'
                  else 'No linked order, so no quote is linked.')
        assert row['offer'] == {'status': 'unavailable', 'offer_id': None, 'reason': reason}
    extra = [r for r in view['shipments'] if r['order']['status'] == 'unavailable']
    assert all(r['order']['reason'] == 'No further open order in this company profile to link.' for r in extra)


def test_offer_link_prefers_the_selected_plan_offer(shipments):
    company = get_company('solara-cables')
    material = company['orders'][0]['material_id']
    offers = [offer('a', material), offer('b', material)]
    plan = {'plan_id': 'p1', 'material': material, 'selected_offer_id': 'b'}
    view = build_dispatch_view(company, offers, plan, None, shipments=shipments)
    first = view['shipments'][0]
    assert first['offer']['offer_id'] == 'b' and first['offer']['basis'] == 'selected in the current comparison'
    assert view['recovery'] == {'status': 'not_connected', 'reason': 'Delays are not fed into recovery yet; see the Task 7 contract.'}


def test_fixture_statuses_and_cutoff_are_passed_through_unchanged(shipments):
    view = build_dispatch_view(get_company('solara-cables'), [], None, None, shipments=shipments)
    assert [r['fixture_shipment_id'] for r in view['shipments']] == [s['shipment_id'] for s in shipments]
    assert [r['status'] for r in view['shipments']] == [s['status'] for s in shipments]
    assert view['as_of'] == '2026-10-16T12:00:00+00:00'


def test_hosted_capabilities_come_from_server_configuration_not_the_url():
    assert HOSTED_PAGES == ('/demo', '/dispatch')
    assert hosted_capabilities() == {'hosted': True, 'served_pages': ['/demo', '/dispatch'],
                                     'recovery_link': None}
    state = SimpleNamespace(company_id='solara-cables', context_version=2, offers={}, last_plan=None, recovery=None)
    app = FastAPI()

    def session(request: Request):
        return state
    app.include_router(dispatch_router(session=session, fixtures_path=FIXTURES))
    with TestClient(app) as client:
        bare = client.get('/api/demo/capabilities').json()
        with_query = client.get('/api/demo/capabilities?from=demo').json()
        view = client.get('/api/demo/dispatch').json()
    assert bare == with_query == hosted_capabilities()
    assert (view['company_id'], view['context_version']) == ('solara-cables', 2)
