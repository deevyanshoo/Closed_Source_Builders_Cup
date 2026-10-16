"""Read-only, approximate profile itineraries for the demo map.

These coordinates are presentation data. Do not pass them to a planner, carbon
calculator, shipment record, or provider routing request.
"""
from __future__ import annotations

import json
from pathlib import Path

from procure.demo.company import get_company


_REGISTRY = Path(__file__).resolve().parents[3] / 'config' / 'route_display_v1.json'
_KINDS = {'vendor', 'road', 'origin_port', 'sea', 'destination_port', 'buyer'}


def _segment(segment_id, kind, status, label, geometry=None, source='company profile', precision='unknown'):
    assert kind in _KINDS
    return {'id': segment_id, 'kind': kind, 'status': status, 'label': label,
            'geometry': geometry, 'source': source, 'precision': precision}


def _point(node):
    lat, lng = node['lat'], node['lng']
    if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise ValueError('invalid display coordinate')
    return {'lat': lat, 'lng': lng}


def build_route_display(company, route_id, material_id, buyer_location_index=0):
    """Build a profile itinerary after exact membership and destination checks.

    A route's presence in a company profile does not bind it to any supplier,
    offer, dispatch shipment, or selected plan. The registry supplies only a
    hand-curated visual corridor for a subset of profile routes.
    """
    if not isinstance(company, dict) or not isinstance(company.get('company_id'), str):
        raise ValueError('unknown company')
    canonical = get_company(company['company_id'])
    if canonical is None:
        raise ValueError('unknown company')
    if type(buyer_location_index) is not int or not 0 <= buyer_location_index < len(canonical['locations']):
        raise ValueError('unknown buyer location')
    if company.get('locations') != canonical['locations']:
        raise ValueError('foreign buyer locations')
    if material_id not in {item['material_id'] for item in canonical['materials']}:
        raise ValueError('unknown material')
    route = next((row for row in canonical['routes'] if row['route_id'] == route_id), None)
    if route is None or route not in company.get('routes', []):
        raise ValueError('unknown or foreign route')
    if material_id not in route['materials']:
        raise ValueError('material not on route')

    buyer = canonical['locations'][buyer_location_index]
    destination = f"{buyer['city']}, {buyer['country']}"
    destination_matches = route['destination'] == destination
    registry = json.loads(_REGISTRY.read_text(encoding='utf-8'))
    if registry.get('schema') != 1:
        raise ValueError('unsupported route display registry')
    record = next((item for item in registry['routes'] if item['company_id'] == company['company_id']
                   and item['route_id'] == route_id and item['material_id'] == material_id), None)
    if record and any(record[key] != route[key] for key in ('origin', 'destination', 'via')):
        raise ValueError('stale route display registry')

    segments = [_segment('vendor', 'vendor', 'unavailable', 'Vendor or facility not located',
                         source='profile does not bind route to a supplier or facility', precision='no coordinate')]
    limitations = []
    if not destination_matches:
        limitations.append(f'Profile route ends at {route["destination"]}; selected buyer is {destination}. The onward connection is unavailable.')
    if record:
        nodes = record['points']
        mode = record['mode']
        if mode == 'sea' and nodes[0]['kind'] == 'origin_port' and any(node['kind'] == 'destination_port' for node in nodes):
            dest_index = next(i for i, node in enumerate(nodes) if node['kind'] == 'destination_port')
            origin, port = nodes[0], nodes[dest_index]
            segments.append(_segment('initial-road', 'road', 'unavailable', 'Vendor to origin port connection unavailable',
                                     source='no supplier or facility bound to this profile route', precision='no road geometry'))
            segments.append(_segment('origin-port', 'origin_port', 'approximate', origin['label'],
                                     [_point(origin)], origin['source'], origin['precision']))
            sea_nodes = nodes[:dest_index + 1]
            via = ', '.join(route['via']) if route['via'] else 'declared corridor'
            segments.append(_segment('sea', 'sea', 'approximate', f'Ship corridor via {via}',
                                     [_point(node) for node in sea_nodes],
                                     'company profile via; hand-curated illustrative waypoints', 'broad corridor, not maritime routing'))
            segments.append(_segment('destination-port', 'destination_port', 'approximate', port['label'],
                                     [_point(port)], port['source'], port['precision']))
            if len(nodes) > dest_index + 1 and destination_matches:
                city = nodes[-1]
                segments.append(_segment('final-road', 'road', 'approximate', f'Illustrative truck corridor toward {city["label"]}',
                                         [_point(port), _point(city)],
                                         'hand-curated illustrative city corridor; no road routing', 'broad corridor'))
            else:
                label = (f'Onward road from {route["destination"]} to selected buyer {destination} unavailable'
                         if not destination_matches else 'Destination port to buyer facility connection unavailable')
                segments.append(_segment('final-road', 'road', 'unavailable', label,
                                         source='no declared last-mile route or buyer facility coordinate', precision='no road geometry'))
        elif mode == 'land' and len(nodes) >= 2 and destination_matches:
            segments.append(_segment('road', 'road', 'approximate',
                                     'Truck corridor via ' + (', '.join(route['via']) if route['via'] else 'profile origin and destination'),
                                     [_point(node) for node in nodes],
                                     'company profile via; hand-curated illustrative city/border points', 'broad corridor, not road routing'))
        else:
            segments.append(_segment('road', 'road', 'unavailable', 'Transport mode or matching buyer corridor unavailable',
                                     source='profile does not declare a usable transport mode', precision='no route geometry'))
            limitations.append('Transport mode or corridor unavailable for this profile route.')
    else:
        segments.append(_segment('road', 'road', 'unavailable', 'No curated display corridor for this profile route',
                                 source='route_display_v1 registry has no matching entry', precision='no route geometry'))
        limitations.append('This profile route has no curated display geometry.')

    if not limitations:
        limitations.append('Supplier, offer, and facility coordinates are unavailable; this is an illustrative profile route.')
    segments.append(_segment('buyer', 'buyer', 'unavailable',
                             f'Buyer facility not located · {destination}' if destination_matches else f'Selected buyer location differs · {destination}',
                             source='company profile city only; no buyer facility coordinate', precision='no coordinate'))
    if any(segment['kind'] in {'origin_port', 'destination_port'} for segment in segments):
        limitations.append('Port points are approximate and unverified; displayed lines are not navigable routes.')
    return {'schema': 1, 'company_id': company['company_id'], 'route_id': route_id,
            'material_id': material_id, 'buyer_location_index': buyer_location_index,
            'supplier_id': None, 'offer_id': None,
            'status': ('partial' if any(s['geometry'] and s['kind'] in {'road', 'sea'} for s in segments)
                       else 'unavailable'), 'segments': segments,
            'limitations': limitations}
