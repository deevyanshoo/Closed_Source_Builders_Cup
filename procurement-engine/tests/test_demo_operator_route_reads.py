"""Route reads select only owned profile evidence and preserve read-only boundaries."""

import pytest

from procure.demo.company import get_company, list_companies
from procure.demo.integration_tools import integration_read, integration_request


def state(company_id='solara-cables'):
    company = get_company(company_id)
    return {'company_id': company_id, 'company': company}


def test_copper_choices_and_two_distinct_solara_itineraries():
    current = state()
    choices = integration_read(current, 'read_routes', 'Show our routes for copper')
    assert [row['route_id'] for row in choices['workspace']['routes']] == [
        'solara-cables-route-1', 'solara-cables-route-2']
    assert 'which' in choices['assistant_text'].lower()
    assert 'solara-cables-route-3' not in choices['assistant_text']
    for city, route_id, origin in [('Pune', 'solara-cables-route-1', 'Valparaiso'),
                                   ('Chennai', 'solara-cables-route-2', 'Hamburg')]:
        result = integration_read(current, 'read_routes', f'Show our copper route to {city}')
        assert [row['route_id'] for row in result['workspace']['routes']] == [route_id]
        display = result['workspace']['route_display']
        assert display['route_id'] == route_id and display['material_id'] == 'copper'
        assert origin in result['assistant_text'] and city in result['assistant_text']
        assert display['supplier_id'] is None and display['offer_id'] is None
        assert 'profile' in result['assistant_text'].lower()
        assert 'distance' in result['assistant_text'].lower()


def test_exact_route_and_material_must_be_compatible():
    current = state()
    selected = integration_read(current, 'read_routes', 'Show solara-cables-route-2 for copper cathode')
    assert [row['route_id'] for row in selected['workspace']['routes']] == ['solara-cables-route-2']
    mismatch = integration_read(current, 'read_routes', 'Show solara-cables-route-2 for polymer')
    assert mismatch['workspace'] == {}
    assert 'not' in mismatch['assistant_text'].lower()


@pytest.mark.parametrize('message', [
    'Show terrapack-route-1 for copper',
    'Show solara-cables-route-999 for copper',
    'Show our routes for PET resin',
    'Show our routes for unobtanium',
    'Show our copper route to Berlin',
    'Show solara-cables-route-2 for copper to Pune',
    'Show TerraPack Films routes for copper',
])
def test_foreign_or_wrong_route_material_location_clarifies(message):
    result = integration_read(state(), 'read_routes', message)
    assert result['workspace'] == {}
    assert any(word in result['assistant_text'].lower() for word in ('choose', 'outside', 'unknown'))


def test_generic_all_routes_and_all_ten_profiles_are_deterministic():
    companies = list_companies()
    assert len(companies) == 10
    for company in companies:
        current = state(company['company_id'])
        generic = integration_read(current, 'read_routes', 'What are our routes?')
        assert generic['workspace']['routes'] == company['routes']
        route = company['routes'][0]
        specific = integration_read(current, 'read_routes',
                                    f"Show {route['route_id']} for {route['materials'][0]}")
        assert [row['route_id'] for row in specific['workspace']['routes']] == [route['route_id']]
        assert specific['workspace']['route_display']['company_id'] == company['company_id']


def test_route_read_intent_is_narrow_and_safe():
    current = state()
    assert integration_request(current, 'Show our routes for copper') == 'read_routes'
    assert integration_request(current, 'Show our routes for copper and submit an order') is None
    assert integration_request(current, 'Show TerraPack Films routes for copper') is None
    assert integration_request(current, 'Show routes for copper') is None


def test_explicit_multiple_materials_ask_for_one():
    current = state('gridspring-batteries')
    result = integration_read(current, 'read_routes', 'Show our routes for cobalt and manganese')
    assert 'which' in result['assistant_text'].lower()
    assert 'route_display' not in result['workspace']


def test_exact_owned_export_route_remains_readable_without_buyer_city():
    result = integration_read(state('terrapack'), 'read_routes',
                              'Show terrapack-route-2 for aluminium-film')
    assert [row['route_id'] for row in result['workspace']['routes']] == ['terrapack-route-2']
    assert 'route_display' not in result['workspace']
    assert result['workspace']['routes'][0]['destination'] == 'Dubai, UAE'
    assert 'which buyer city' in result['assistant_text'].lower()
    assert 'onward connection' in result['assistant_text'].lower()


def test_multiple_material_read_asks_one_clear_question():
    result = integration_read(state(), 'read_routes', 'Show solara-cables-route-1 for copper and polymer')
    assert result['workspace'] == {}
    assert 'which one material' in result['assistant_text'].lower()
    assert 'copper' in result['assistant_text'] and 'polymer' in result['assistant_text']


def test_unknown_material_with_exact_route_is_not_defaulted():
    result = integration_read(state(), 'read_routes', 'Show solara-cables-route-1 for kryptonite')
    assert result['workspace'] == {}
    assert 'unknown' in result['assistant_text'].lower()


def test_generic_route_answers_stay_brief_for_every_company():
    for company in list_companies():
        result = integration_read(state(company['company_id']), 'read_routes', 'What are our routes?')
        assert len(result['assistant_text']) < 650
        assert result['workspace']['routes'] == company['routes']
