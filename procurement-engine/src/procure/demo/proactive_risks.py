"""Pure, bounded risk flags from fictional company facts and observed headline metadata.

No scheduler, provider access, state change, forecast, or supplier communication lives here.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re

from .company import get_company
from .quote_service import company_for_offer
from .recovery import compare


_NEWS_LIMIT = 5
_ALERT_LIMIT = 60
_RISK_WORDS = re.compile(r'\b(?:delay|shortage|disruption|strike|closure|closed|halt|outage|'
                         r'export ban|supply concern|bottleneck|blockade)\b', re.IGNORECASE)
_SOURCE_FOR_PROVIDER = {'GDELT': 'gdelt', 'EIA': 'eia', 'ECB': 'ecb'}


def _number(raw, *, positive=False):
    try:
        result = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 and (not positive or result > 0) else None


def _plain(value: Decimal) -> str:
    return format(value.normalize(), 'f')


def _date(raw):
    try:
        if not isinstance(raw, str):
            return None
        parsed = date.fromisoformat(raw)
        return parsed if parsed.isoformat() == raw else None
    except ValueError:
        return None


def _timestamp(raw):
    try:
        if not isinstance(raw, str):
            return None
        result = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _identity(company_id, category, source):
    payload = f'{company_id}|{category}|{source}'.encode()
    return 'risk-' + hashlib.sha256(payload).hexdigest()[:20]


def _evidence(kind, ref_id, label, detail, *, url=None, observed_at=None):
    return {'kind': kind, 'ref_id': ref_id, 'label': label, 'detail': detail,
            'url': url, 'observed_at': observed_at}


def _action(alert_id, suffix, label, message):
    return {'action_id': f'{alert_id}-{suffix}', 'label': label,
            'message': message, 'requires_review': True}


def _alert(company_id, category, severity, source, title, summary, impact, material_ids,
           provenance, confidence, evidence, metrics, actions):
    alert_id = _identity(company_id, category, source)
    return {'alert_id': alert_id, 'company_id': company_id, 'category': category,
            'severity': severity, 'title': title, 'summary': summary, 'impact': impact,
            'material_ids': material_ids, 'provenance': provenance,
            'confidence': confidence, 'evidence': evidence, 'metrics': metrics,
            'recovery_actions': [_action(alert_id, *item) for item in actions]}


def _buyer_stock(snapshot, company, gaps):
    draft = snapshot.get('buyer_draft')
    if draft is None:
        return None
    from .agent_tools import ToolValidationError, validate_buyer_draft
    try:
        buyer = validate_buyer_draft(company, draft)
    except ToolValidationError:
        gaps.append('Buyer draft is invalid for this company/material/unit; profile stock was used.')
        return None
    return buyer


def _inventory(company, buyer, gaps):
    alerts = []
    for material in company['materials']:
        material_id, unit = material['material_id'], material['quantity_unit']
        stock_source = 'synthetic_profile'
        stock_raw = material.get('stock')
        if buyer and buyer['material'] == material_id and buyer['quantity_unit'] == unit:
            stock_raw = buyer['current_stock']
            stock_source = 'buyer_synthetic_assumption'
        buyer_stock = stock_source == 'buyer_synthetic_assumption'
        stock = _number(stock_raw)
        daily = _number(material.get('consumption_per_day'), positive=True)
        leads = [supplier['lead_time_days'] for supplier in company['suppliers']
                 if material_id in supplier.get('materials', []) and
                 type(supplier.get('lead_time_days')) is int and supplier['lead_time_days'] >= 0]
        if stock is None or daily is None or not leads:
            gaps.append(f'{material_id}: stock, daily use, or supplier lead is unavailable.')
            continue
        with localcontext() as context:
            context.prec = 28
            cover = stock / daily
        shortest = min(leads)
        if cover > shortest:
            continue
        deficit = Decimal(shortest) - cover
        severity = 'high' if deficit >= 3 or material.get('criticality') == 'high' else 'medium'
        alerts.append(_alert(
            company['company_id'], 'inventory', severity, material_id,
            f"{material['name']} stock cover meets or falls below supplier lead",
            f"Stated synthetic stock covers {_plain(cover)} days; the shortest listed lead is {shortest} days.",
            'Potential stockout before a new delivery under the stated fictional use and lead assumptions; verify the current inventory and supplier schedule.',
            [material_id], 'synthetic_profile',
            {'level': 'high', 'reason':
             'Exact arithmetic over a buyer synthetic stock assumption, fictional daily use, and listed leads; actual stock is unverified.'
             if buyer_stock else
             'Exact arithmetic over fictional profile stock, daily use, and listed leads; actual stock is unverified.'},
            [_evidence('profile', material_id,
                       f"{material['name']} buyer stock assumption" if buyer_stock else material['name'],
                       (f'Buyer synthetic assumption: stock {stock_raw} {unit}; ' if buyer_stock else
                        f'Fictional profile stock {stock_raw} {unit}; ') +
                       f"profile daily use {material['consumption_per_day']} {unit}; shortest listed lead {shortest} days.")],
            {'stock': _plain(stock), 'daily_use': _plain(daily), 'unit': unit,
             'stock_cover_days': _plain(cover), 'shortest_lead_days': shortest,
             'lead_gap_days': _plain(deficit), 'stock_source': stock_source},
            [('inspect', 'Inspect inventory', f'Inspect {material["name"]} ({material_id}) inventory and supplier lead times.'),
             ('quotes', 'Load quote alternatives', f'Load {material_id} sample quotes for review.')]))
    return alerts


def _orders(company, gaps):
    alerts = []
    scenario_day = _date(company.get('as_of'))
    if scenario_day is None:
        gaps.append('Company synthetic scenario date is unavailable; order lead exposure was not assessed.')
        return alerts
    material_map = {row['material_id']: row for row in company['materials']}
    for order in company.get('orders', []):
        material_id = order.get('material_id')
        material = material_map.get(material_id)
        need = _date(order.get('need_by'))
        leads = [supplier['lead_time_days'] for supplier in company['suppliers']
                 if material_id in supplier.get('materials', []) and
                 type(supplier.get('lead_time_days')) is int and supplier['lead_time_days'] >= 0]
        if not material or not need or not leads or order.get('unit') != material['quantity_unit'] or _number(order.get('quantity'), positive=True) is None:
            gaps.append(f"{order.get('order_id', 'unknown order')}: dated same-unit supplier comparison is unavailable.")
            continue
        earliest = scenario_day + timedelta(days=min(leads))
        if earliest <= need:
            supported = [supplier for supplier in company['suppliers']
                         if material_id in supplier.get('materials', []) and
                         type(supplier.get('lead_time_days')) is int and supplier['lead_time_days'] >= 0 and
                         isinstance(supplier.get('supplier_id'), str) and supplier['supplier_id'] and
                         isinstance(supplier.get('name'), str) and supplier['name']]
            late_sources = [supplier for supplier in supported
                            if scenario_day + timedelta(days=supplier['lead_time_days']) > need]
            if not late_sources:
                continue
            slow = max(late_sources, key=lambda supplier: (supplier['lead_time_days'], supplier['supplier_id']))
            projected = scenario_day + timedelta(days=slow['lead_time_days'])
            faster_count = sum(scenario_day + timedelta(days=supplier['lead_time_days']) <= need
                               for supplier in supported)
            late = (projected - need).days
            order_id = order['order_id']
            alerts.append(_alert(
                company['company_id'], 'supplier_delay', 'medium' if late >= 7 else 'low',
                f"{order_id}|{slow['supplier_id']}|slow-option",
                f"{material['name']} has a potentially late fictional supplier option",
                f"Listed supplier {slow['name']} ({slow['supplier_id']}) has a {slow['lead_time_days']}-day lead from {scenario_day.isoformat()}, projecting {projected.isoformat()} after the {need.isoformat()} need-by date.",
                'Potential lateness applies only if this fictional supplier option is used; faster listed options meet the date and are available for review. No supplier selection or actual shipment delay is confirmed.',
                [material_id], 'synthetic_delay_scenario',
                {'level': 'moderate', 'reason': 'Dated comparison of a listed fictional supplier lead and synthetic order deadline; selection and availability are unverified.'},
                [_evidence('order', order_id, f'{material["name"]} fictional order',
                           f"Quantity {order['quantity']} {order['unit']}; need by {need.isoformat()}."),
                 _evidence('profile', slow['supplier_id'], slow['name'],
                           f"Fictional supplier lead {slow['lead_time_days']} days from scenario date {scenario_day.isoformat()}; projected arrival {projected.isoformat()}. Regional availability is unverified.")],
                {'supplier_id': slow['supplier_id'], 'supplier_name': slow['name'],
                 'supplier_lead_days': slow['lead_time_days'], 'scenario_date': scenario_day.isoformat(),
                 'projected_arrival': projected.isoformat(), 'need_by': need.isoformat(),
                 'lateness_days': late, 'faster_option_count': faster_count,
                 'quantity': order['quantity'], 'unit': order['unit']},
                [('quotes', 'Review faster alternatives', f'Load {material_id} sample quotes for review.'),
                 ('supplier', 'Check supplier assumptions', f'Inspect {material["name"]} ({material_id}) supplier lead times.')]))
            continue
        late = (earliest - need).days
        order_id = order['order_id']
        alerts.append(_alert(
            company['company_id'], 'supplier_delay', 'high' if late >= 7 else 'medium',
            order_id, f"{material['name']} order has synthetic lead-time exposure",
            f"The earliest listed supplier lead from {scenario_day.isoformat()} reaches {earliest.isoformat()}, after the stated {need.isoformat()} need-by date.",
            'Potential late arrival under fictional order and supplier assumptions; no actual shipment delay has been observed.',
            [material_id], 'synthetic_delay_scenario',
            {'level': 'moderate', 'reason': 'Dated comparison of fictional order deadline and listed supplier lead; supplier availability is not verified.'},
            [_evidence('order', order_id, f'{material["name"]} order',
                       f"Quantity {order['quantity']} {order['unit']}; need by {need.isoformat()}."),
             _evidence('profile', material_id, material['name'],
                       f'Shortest listed supplier lead {min(leads)} days from scenario date {scenario_day.isoformat()}.')],
            {'earliest_arrival': earliest.isoformat(), 'need_by': need.isoformat(),
             'lateness_days': late, 'quantity': order['quantity'], 'unit': order['unit'],
             'scenario_date': scenario_day.isoformat()},
            [('quotes', 'Inspect alternatives', f'Load {material_id} sample quotes for review.'),
             ('supplier', 'Check supplier assumptions', f'Inspect {material["name"]} ({material_id}) supplier lead times.')]))
    return alerts


def _owned_plan(snapshot, company, gaps):
    plan = snapshot.get('last_plan') or snapshot.get('plan')
    if plan is None:
        gaps.append('No current reviewed plan; plan recovery exposure is unavailable.')
        return None
    if not isinstance(plan, dict) or plan.get('company_id') != company['company_id']:
        gaps.append('Current plan is foreign or malformed and was ignored.')
        return None
    material_ids = {row['material_id'] for row in company['materials']}
    versions = plan.get('offer_versions')
    request = snapshot.get('plan_request')
    ids = request.get('offer_ids') if isinstance(request, dict) else None
    if (plan.get('material') not in material_ids or not isinstance(versions, dict) or not versions or
            any(not isinstance(offer_id, str) or type(version) is not int
                for offer_id, version in versions.items()) or
            not isinstance(request, dict) or request.get('material') != plan.get('material') or
            request.get('expected_versions') != versions or
            not isinstance(ids, list) or any(not isinstance(offer_id, str) for offer_id in ids) or
            len(ids) != len(set(ids)) or set(ids) != set(versions) or
            plan.get('selected_offer_id') not in versions):
        gaps.append('Current plan/request material or offer binding is invalid.')
        return None
    by_id = {offer.get('offer_id'): offer for offer in snapshot.get('offers', [])
             if isinstance(offer, dict) and isinstance(offer.get('offer_id'), str)}
    def current(offer_id, version):
        offer = by_id.get(offer_id)
        if not offer or offer.get('version') != version or offer.get('status') != 'saved':
            return False
        try:
            return company_for_offer(offer) == company['company_id']
        except (AttributeError, TypeError):
            return False

    if any(not current(offer_id, version) for offer_id, version in versions.items()):
        gaps.append('Current plan offer version, save state, or company ownership is stale.')
        return None
    return plan, request, [by_id[offer_id] for offer_id in request['offer_ids']]


def _recorded_recovery(snapshot, company, binding, gaps):
    recorded = snapshot.get('recovery')
    if recorded is None:
        return []
    if binding is None or not isinstance(recorded, dict):
        gaps.append('Recorded recovery cannot be tied to a current reviewed plan.')
        return []
    plan, request, offers = binding
    scenario_request = recorded.get('scenario_request')
    baseline = recorded.get('baseline')
    scenario = recorded.get('scenario')
    delays = scenario_request.get('delay_days_by_offer') if isinstance(scenario_request, dict) else None
    original_delays = request.get('delay_days_by_offer') or {}
    if (not isinstance(baseline, dict) or baseline.get('plan_id') != plan.get('plan_id') or
            not isinstance(scenario, dict) or not isinstance(delays, dict) or
            not set(delays) <= set(plan['offer_versions']) or
            any(type(days) is not int or not 0 <= days <= 3650 for days in delays.values()) or
            delays == original_delays):
        gaps.append('Recorded recovery delay assumptions or baseline are invalid.')
        return []
    scenario_day = _date(company.get('as_of'))
    if scenario_day is None:
        gaps.append('Company synthetic scenario date is unavailable for recovery replay.')
        return []
    try:
        projected = compare(plan, request, offers, delays, today=scenario_day)
    except Exception:
        gaps.append('Recorded recovery could not be reproduced from current reviewed offers.')
        return []
    computed = projected['scenario']
    if (computed.get('selected_offer_id') != scenario.get('selected_offer_id') or
            computed.get('total_cost') != scenario.get('total_cost') or
            projected['cost_delta'] != recorded.get('cost_delta')):
        gaps.append('Recorded recovery projection disagrees with the current reviewed inputs.')
        return []
    material_id = plan['material']
    material = next(row for row in company['materials'] if row['material_id'] == material_id)
    selected = computed.get('selected_offer_id')
    severity = 'high' if selected is None else 'medium' if projected['selection_changed'] else 'low'
    effect = ('no feasible selected offer' if selected is None else
              f'a changed selected offer ({selected})' if projected['selection_changed'] else
              'the same selected offer under the replacement delay')
    source = plan['plan_id'] + '|' + json.dumps(delays, sort_keys=True)
    return [_alert(
        company['company_id'], 'supplier_delay', severity, source,
        f"{material['name']} recorded synthetic recovery",
        f"A reviewed replacement-delay scenario produces {effect}.",
        'The hypothetical delay may change sourcing feasibility or cost; this is a synthetic projection, not an observed shipment event.',
        [material_id], 'synthetic_plan',
        {'level': 'high', 'reason': 'Recomputed with the current saved, version-bound fictional offers and authoritative planner.'},
        [_evidence('plan', plan['plan_id'], 'Reviewed baseline plan',
                   f"Selected offer {plan['selected_offer_id']}; decision {plan.get('decision_date', scenario_day.isoformat())}.")],
        {'baseline_offer_id': plan['selected_offer_id'], 'scenario_offer_id': selected,
         'cost_delta': projected['cost_delta'],
         'selection_changed': 'true' if projected['selection_changed'] else 'false',
         'outcome': projected['outcome'], 'quantity_unit': plan.get('quantity_unit'),
         'reporting_currency': plan.get('reporting_currency'),
         'delay_days_by_offer': json.dumps(delays, sort_keys=True)},
        [('inspect', 'Inspect recovery', f'Inspect {material["name"]} ({material_id}) recorded recovery comparison.'),
         ('rfq', 'Prepare unsent RFQ', f'Draft an unsent RFQ for the current {material_id} plan after review.')])]


def _news(snapshot, company, news, now, gaps):
    if not isinstance(news, dict):
        gaps.append('News is unavailable; observed headline risk was not assessed.')
        return [], {'materials_evaluated': len(company['materials']), 'news_state': 'unavailable',
                    'news_items_evaluated': 0, 'sources': []}
    state = news.get('cache_state') if isinstance(news.get('cache_state'), str) else 'unavailable'
    items = news.get('items') if isinstance(news.get('items'), list) else []
    sources = [{key: source.get(key) for key in ('source_id', 'name', 'state', 'last_checked_at', 'reason')}
               for source in news.get('sources', []) if isinstance(source, dict)]
    coverage = {'materials_evaluated': len(company['materials']), 'news_state': state,
                'news_items_evaluated': len(items), 'sources': sources}
    if state not in {'fresh', 'cached', 'stale'}:
        gaps.append(f'News state {state}; stale/unavailable headlines were not treated as current risk.')
        return [], coverage
    if state == 'stale':
        gaps.append('News cache contains stale sources; only individually fresh or cached sources were assessed.')
    source_states = {source['source_id']: source['state'] for source in sources
                     if isinstance(source['source_id'], str)}
    material_ids = {row['material_id'] for row in company['materials']}
    routes = {row['route_id']: row for row in company['routes']}
    currencies = set(company['currencies'])
    candidates = []
    seen = set()
    for item in items:
        if not isinstance(item, dict) or item.get('synthetic') is not False:
            continue
        item_id, title, url = item.get('item_id'), item.get('title'), item.get('url')
        if (not isinstance(item_id, str) or not item_id or not isinstance(title, str) or
                not title.strip() or not isinstance(url, str) or not url.startswith('https://')):
            continue
        key = url.split('#', 1)[0]
        if key in seen:
            continue
        published, retrieved = _timestamp(item.get('published_at')), _timestamp(item.get('retrieved_at'))
        if (published is None or retrieved is None or published > now or retrieved > now or
                now - published > timedelta(days=7) or now - retrieved > timedelta(days=7)):
            continue
        source_id = item.get('source_id') or _SOURCE_FOR_PROVIDER.get(item.get('provider'))
        if not isinstance(source_id, str):
            continue
        source_state = source_states.get(source_id)
        if source_state not in {'fresh', 'cached'}:
            continue
        direct = item.get('matched_materials')
        matched_routes = item.get('matched_routes')
        matched_currencies = item.get('matched_currencies')
        operations = item.get('matched_operations')
        kind = item.get('relevance_kind')
        if kind == 'direct_material' and isinstance(direct, list) and direct and all(
                isinstance(material_id, str) and material_id in material_ids for material_id in direct):
            affected = list(dict.fromkeys(direct))
        elif (kind == 'route' and isinstance(matched_routes, list) and matched_routes and all(
                isinstance(route_id, str) and route_id in routes for route_id in matched_routes) and
                isinstance(operations, list) and 'logistics' in operations):
            affected = list(dict.fromkeys(material_id for route_id in matched_routes
                                          for material_id in routes[route_id].get('materials', [])
                                          if material_id in material_ids))
        elif kind == 'currency' and isinstance(matched_currencies, list) and matched_currencies and all(
                isinstance(code, str) and code in currencies for code in matched_currencies):
            affected = list(dict.fromkeys(material_id for supplier in company['suppliers']
                                          if supplier.get('settlement_currency') in matched_currencies
                                          for material_id in supplier.get('materials', [])
                                          if material_id in material_ids))
        elif (kind == 'sector' and isinstance(operations, list) and operations and
              isinstance(item.get('relevance_reason'), str) and
              item['relevance_reason'].startswith(
                  'Headline connects the company sector to operations:')):
            affected = []
        else:
            continue
        seen.add(key)
        risk_word = bool(_RISK_WORDS.search(title))
        candidates.append(({'direct_material': 0, 'route': 1, 'currency': 2, 'sector': 3}[kind],
                           not risk_word, -published.timestamp(), item_id, key, item, kind,
                           affected, source_state))
    candidates.sort()
    alerts = []
    for _, _, _, item_id, key, item, kind, affected, source_state in candidates[:_NEWS_LIMIT]:
        title = item['title'].strip()[:180]
        source_id = item.get('source_id') or _SOURCE_FOR_PROVIDER.get(item.get('provider'))
        source_quality = ('company_news_rss_metadata' if source_id in {
            'google_news_1', 'google_news_2', 'google_news_3'} else
            'aggregated_headline_metadata' if source_id == 'gdelt' else
            'official_publication_metadata' if source_id in {'eia', 'ecb'} else
            'public_headline_metadata')
        alerts.append(_alert(
            company['company_id'], 'news',
            'high' if kind == 'direct_material' and _RISK_WORDS.search(title) else
            'medium' if kind in {'route', 'currency'} and _RISK_WORDS.search(title) else 'low',
            key, f'Observed headline: {title}',
            f'{kind.replace("_", " ").title()} relevance from current public headline metadata; underlying event and company effect are unverified.',
            'Conditional exposure only: this headline may warrant a supplier, route, or price check; it does not confirm a delay or price movement.',
            affected, 'observed_news',
            {'level': 'low', 'reason': 'Headline metadata is a relevance signal, not verified operational impact; source freshness is tracked separately.'},
            [_evidence('news', item_id, str(item.get('publisher') or item.get('provider') or 'Public headline')[:80],
                       title, url=item['url'], observed_at=item['published_at'])],
            {'relevance_kind': kind, 'source_state': source_state,
             'source_id': source_id, 'source_quality': source_quality,
             'published_at': item['published_at'],
             'retrieved_at': item['retrieved_at']},
            [('explain', 'Explain headline', f'Explain {item_id} for this company and distinguish conditional impact.')]))
    if items and not alerts:
        gaps.append('No fresh, owned, relevant news headline with a usable source was available.')
    return alerts, coverage


def assess_company(snapshot: dict, news: dict | None, now: datetime) -> dict:
    """Build a company-scoped report without mutating inputs or calling providers."""
    if not isinstance(snapshot, dict) or not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('A trusted company snapshot and timezone-aware clock are required.')
    company = snapshot.get('company')
    company_id = snapshot.get('company_id')
    if (not isinstance(company, dict) or not isinstance(company_id, str) or
            company.get('company_id') != company_id or get_company(company_id) is None):
        raise ValueError('The company snapshot is foreign or unavailable.')
    now = now.astimezone(timezone.utc)
    gaps = []
    buyer = _buyer_stock(snapshot, company, gaps)
    alerts = _inventory(company, buyer, gaps) + _orders(company, gaps)
    binding = _owned_plan(snapshot, company, gaps)
    alerts.extend(_recorded_recovery(snapshot, company, binding, gaps))
    news_alerts, coverage = _news(snapshot, company, news, now, gaps)
    alerts.extend(news_alerts)
    return {'company_id': company_id, 'company_name': company['name'],
            'sector': company['sector'], 'checked_at': now.isoformat(),
            'alerts': alerts[:_ALERT_LIMIT], 'coverage': coverage,
            'data_gaps': list(dict.fromkeys(gaps))}
