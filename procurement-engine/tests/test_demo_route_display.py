"""The display itinerary cannot become evidence for a shipment or calculation."""
import json
from pathlib import Path

import pytest

from procure.demo.company import get_company, list_companies
from procure.demo.route_display import build_route_display


def profile_route(company):
    return next(route for route in company['routes'] if route['route_id'].endswith('-route-1'))


def test_all_ten_profile_overviews_have_owned_illustrative_routes():
    companies = list_companies()
    assert len(companies) == 10
    for company in companies:
        route = profile_route(company)
        view = build_route_display(company, route['route_id'], route['materials'][0])
        assert view['schema'] == 1
        assert view['company_id'] == company['company_id']
        assert view['route_id'] == route['route_id']
        assert view['material_id'] == route['materials'][0]
        assert view['status'] in {'supported', 'partial', 'unavailable'}
        assert view['supplier_id'] is None and view['offer_id'] is None
        assert view['segments'][0]['kind'] == 'vendor'
        assert view['segments'][-1]['kind'] == 'buyer'
        assert view['segments'][0]['geometry'] is None
        assert view['segments'][-1]['geometry'] is None
        assert all(set(segment) == {'id', 'kind', 'status', 'label', 'geometry', 'source', 'precision'} for segment in view['segments'])
        assert all(segment['status'] != 'supported' for segment in view['segments'])
        assert not any(key in json.dumps(view).lower() for key in ('distance_km', 'emissions', 'carbon_kg', 'travel_time'))


def test_exact_membership_and_multiple_buyer_locations():
    company = get_company('solara-cables')
    with pytest.raises(ValueError):
        build_route_display(company, 'terrapack-route-1', 'copper')
    with pytest.raises(ValueError):
        build_route_display(company, 'solara-cables-route-1', 'polymer')
    with pytest.raises(ValueError):
        build_route_display(company, 'solara-cables-route-1', 'unknown')
    with pytest.raises(ValueError):
        build_route_display(company, 'solara-cables-route-1', 'copper', -1)
    with pytest.raises(ValueError):
        build_route_display(company, 'solara-cables-route-1', 'copper', 2)
    with pytest.raises(ValueError):
        build_route_display({**company, 'company_id': 'terrapack'}, 'solara-cables-route-1', 'copper')
    matching = build_route_display(company, 'solara-cables-route-2', 'copper', 1)
    mismatched = build_route_display(company, 'solara-cables-route-2', 'copper', 0)
    assert matching['status'] in {'supported', 'partial'}
    assert mismatched['status'] in {'partial', 'unavailable'}
    assert mismatched['segments'][-1]['geometry'] is None
    assert any('selected buyer' in note.lower() for note in mismatched['limitations'])


def test_declared_suez_and_land_mode_without_invented_ship():
    sea = build_route_display(get_company('solara-cables'), 'solara-cables-route-2', 'copper', 1)
    assert any(segment['kind'] == 'sea' and 'Suez' in segment['label'] for segment in sea['segments'])
    road = build_route_display(get_company('loomworks'), 'loomworks-route-1', 'cotton')
    assert any(segment['kind'] == 'road' and segment['geometry'] for segment in road['segments'])
    assert not any(segment['kind'] == 'sea' for segment in road['segments'])


def test_unverified_ports_and_missing_facility_geometry_are_explicit():
    view = build_route_display(get_company('terrapack'), 'terrapack-route-1', 'pet-resin')
    ports = [s for s in view['segments'] if s['kind'] in {'origin_port', 'destination_port'}]
    assert ports and all(s['status'] == 'approximate' for s in ports)
    assert all('unverified' in s['precision'].lower() or 'approximate' in s['precision'].lower() for s in ports)
    assert all(s['source'] for s in ports)
    assert any('facility' in note.lower() for note in view['limitations'])


def test_uncurated_profile_route_is_unavailable_without_guessed_geometry():
    company = get_company('stonebridge-materials')
    view = build_route_display(company, 'stonebridge-materials-route-4', 'limestone')
    assert view['status'] == 'unavailable'
    assert all(segment['geometry'] is None for segment in view['segments'])
    assert view['supplier_id'] is None and view['offer_id'] is None


def test_harvest_default_buyer_exposes_mombasa_to_nairobi_gap_first():
    view = build_route_display(get_company('harvest-foods'), 'harvest-foods-route-1', 'wheat')
    assert view['status'] == 'partial'
    assert [s['kind'] for s in view['segments']] == [
        'vendor', 'road', 'origin_port', 'sea', 'destination_port', 'road', 'buyer']
    assert view['segments'][1]['geometry'] is None
    assert view['segments'][5]['geometry'] is None
    assert 'Mombasa' in view['segments'][5]['label']
    assert 'Nairobi' in view['segments'][5]['label']
    assert 'Mombasa' in view['limitations'][0] and 'Nairobi' in view['limitations'][0]
    assert view['segments'][4]['geometry'] == [{'lat': -4.04, 'lng': 39.67}]


def test_sea_legs_have_explicit_first_and_last_road_even_when_missing():
    view = build_route_display(get_company('solara-cables'), 'solara-cables-route-2', 'copper', 1)
    assert [s['kind'] for s in view['segments']] == [
        'vendor', 'road', 'origin_port', 'sea', 'destination_port', 'road', 'buyer']
    assert view['segments'][1]['status'] == 'unavailable'
    assert view['segments'][5]['status'] == 'unavailable'
    assert view['segments'][1]['geometry'] is None
    assert view['segments'][5]['geometry'] is None
    land = build_route_display(get_company('forgeline-components'), 'forgeline-components-route-1', 'steel')
    assert [s['kind'] for s in land['segments']] == ['vendor', 'road', 'buyer']


def test_suez_approaches_use_sea_waypoints_not_direct_land_chords():
    hamburg = build_route_display(get_company('solara-cables'), 'solara-cables-route-2', 'copper', 1)
    alexandria = build_route_display(get_company('stonebridge-materials'), 'stonebridge-materials-route-1', 'gypsum')
    sea1 = next(s for s in hamburg['segments'] if s['kind'] == 'sea')['geometry']
    sea2 = next(s for s in alexandria['segments'] if s['kind'] == 'sea')['geometry']
    assert sea1[:8] == [
        {'lat': 53.54, 'lng': 9.98}, {'lat': 54.5, 'lng': 7.5},
        {'lat': 50.1, 'lng': -1}, {'lat': 45, 'lng': -6},
        {'lat': 36, 'lng': -5.5}, {'lat': 37, 'lng': 8},
        {'lat': 34, 'lng': 25}, {'lat': 31.27, 'lng': 32.3},
    ]
    assert sea2[:3] == [
        {'lat': 31.2, 'lng': 29.9}, {'lat': 31.27, 'lng': 32.3},
        {'lat': 30.48, 'lng': 32.34},
    ]
    assert {'lat': 30.48, 'lng': 32.34} in sea1


def test_unknown_mode_is_unavailable_not_illustrative():
    view = build_route_display(get_company('meridian-pharma'), 'meridian-pharma-route-1', 'api')
    assert view['status'] == 'unavailable'
    assert not any(s['geometry'] for s in view['segments'])
    assert 'transport mode' in view['limitations'][0].lower()


def test_pacific_and_indian_corridors_include_coastal_approaches():
    cases = [
        ('solara-cables', 'copper', ['Pacific Ocean corridor', 'Singapore Strait corridor', 'Arabian Sea corridor']),
        ('harvest-foods', 'wheat', ['North Pacific corridor', 'Singapore Strait corridor', 'Indian Ocean corridor']),
        ('gridspring-batteries', 'lithium', ['Pacific Ocean corridor', 'Korea Strait corridor']),
        ('terrapack', 'pet-resin', ['Malacca Strait corridor', 'Andaman Sea corridor']),
        ('circuitpeak-systems', 'chips', ['South China Sea corridor', 'Malacca Strait corridor']),
    ]
    for company_id, material_id, expected in cases:
        company = get_company(company_id)
        view = build_route_display(company, f'{company_id}-route-1', material_id)
        sea = next(s for s in view['segments'] if s['kind'] == 'sea')
        registry = json.loads((Path(__file__).parents[1] / 'config' / 'route_display_v1.json').read_text())
        record = next(r for r in registry['routes']
                      if r['route_id'] == f'{company_id}-route-1')
        labels = [p['label'] for p in record['points']]
        assert all(label in labels for label in expected), company_id
        assert len(sea['geometry']) >= 5, company_id
