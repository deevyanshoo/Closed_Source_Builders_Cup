"""Company-scoped read model over the labelled synthetic dispatch fixtures, plus hosted capabilities.

build_dispatch_view links each synthetic fixture shipment, in fixture order, to the company's own open
orders, and to a saved offer of this session for that order's material (the selected offer of the current
comparison first). A shipment with no order left, or an order with no saved offer, is shown as unavailable;
nothing is invented and no other company's IDs appear. Statuses, dates and the cutoff come unchanged from
the exported fixtures.

Contract for the coordinator's Task 7 (delays into recovery), not implemented here:
  * input: one row of build_dispatch_view with order.status == 'linked' and offer.status == 'linked', and a
    whole number of delay days stated by the user (never read from a hazard or news item);
  * output: the existing recovery request {'baseline_plan_id', 'expected_versions', 'delay_days_by_offer':
    {offer_id: days}} for the current plan, sent through the existing reviewed recovery proposal and
    confirmation; a repeated event or approval must not apply the delay twice.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi import APIRouter, Request

from .company import get_company

HOSTED_PAGES = ('/demo', '/dispatch')
LABEL = 'Synthetic fixture data: shipments are illustrations, not tracked cargo.'


def load_fixture_shipments(path: Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    shipments = deepcopy(data['shipments']['shipments'])
    for row in shipments:
        row['_as_of'] = data['as_of']
    return shipments


def hosted_capabilities() -> dict:
    """What the hosted server serves; the client uses this instead of any query parameter."""
    return {'hosted': True, 'served_pages': list(HOSTED_PAGES), 'recovery_link': None}


def _offer_material(offer: dict) -> str | None:
    items = offer.get('items') or []
    return ((items[0].get('material') or {}).get('value')) if items else None


def _offer_link(material: str, offers: list, plan: dict | None) -> dict:
    saved = [o for o in offers if o.get('status') == 'saved' and _offer_material(o) == material]
    if plan and plan.get('material') == material and plan.get('selected_offer_id'):
        chosen = next((o for o in saved if o['offer_id'] == plan['selected_offer_id']), None)
        if chosen:
            return {'status': 'linked', 'offer_id': chosen['offer_id'], 'basis': 'selected in the current comparison'}
    if saved:
        return {'status': 'linked', 'offer_id': saved[0]['offer_id'], 'basis': 'first saved quote for this material'}
    return {'status': 'unavailable', 'offer_id': None, 'reason': 'No saved quote in this session for the linked order material.'}


def build_dispatch_view(company: dict, offers: list, plan: dict | None, recovery: dict | None, *,
                        shipments: list[dict], context_version: int | None = None) -> dict:
    orders = [o for o in company.get('orders', []) if o.get('status') in ('open', 'planned')]
    rows = []
    for index, shipment in enumerate(shipments):
        order = orders[index] if index < len(orders) else None
        if order is None:
            order_link = {'status': 'unavailable', 'order_id': None, 'material_id': None,
                          'reason': 'No further open order in this company profile to link.'}
            offer_link = {'status': 'unavailable', 'offer_id': None, 'reason': 'No linked order, so no quote is linked.'}
        else:
            order_link = {'status': 'linked', 'order_id': order['order_id'], 'material_id': order['material_id'],
                          'quantity': order['quantity'], 'unit': order['unit'], 'need_by': order['need_by']}
            offer_link = _offer_link(order['material_id'], offers, plan)
        rows.append({'view_id': f"{company['company_id']}:{shipment['shipment_id']}",
                     'fixture_shipment_id': shipment['shipment_id'], 'status': shipment['status'],
                     'signals': shipment.get('signals', []), 'expected_arrival': shipment.get('expected_arrival'),
                     'baseline_arrival_at': shipment.get('baseline_arrival_at'), 'on_hold': shipment.get('on_hold'),
                     'order': order_link, 'offer': offer_link})
    return {'company_id': company['company_id'], 'context_version': context_version, 'synthetic': True, 'label': LABEL,
            'as_of': shipments[0]['_as_of'] if shipments else None, 'shipments': rows,
            'recovery': {'status': 'not_connected', 'reason': 'Delays are not fed into recovery yet; see the Task 7 contract.'}}


def dispatch_router(*, session, fixtures_path: Path) -> APIRouter:
    """GET /api/demo/capabilities and /api/demo/dispatch on the coordinator's session helper."""
    router = APIRouter()
    loaded = {}

    @router.get('/api/demo/capabilities')
    async def capabilities():
        return hosted_capabilities()

    @router.get('/api/demo/dispatch')
    async def dispatch(request: Request):
        value = session(request)
        if 'shipments' not in loaded:
            loaded['shipments'] = load_fixture_shipments(fixtures_path)
        return build_dispatch_view(get_company(value.company_id), list(value.offers.values()), value.last_plan,
                                   value.recovery, shipments=loaded['shipments'], context_version=value.context_version)

    return router
