"""Grounded explanations for current cost-carbon and shipment read models."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from .company import list_companies
from .news_brief import is_workspace_news_question
from .presentation import supplier_display_name
from .route_display import build_route_display


_EXTERNAL_TOPIC = re.compile(
    r'\b(?:news|headlines?|polic(?:y|ies)|regulations?|grants?|subsid(?:y|ies)|'
    r'programmes?|programs?|research|search|browse|web|internet|external|'
    r'industry|market|public|global|world|government|laws?|compliance)\b', re.I)
_CURRENT_SCOPE = re.compile(
    r'\b(?:this|my|our|the\s+current|current|selected|saved|reviewed)\s+'
    r'(?:company|workspace|purchase|plan|comparison|assessment|order|'
    r'carbon|emissions?|footprint|(?:planned\s+)?shipments?|inbound|consignments?)\b', re.I)
_METHOD_QUESTION = re.compile(
    r'\b(?:sources?|publishers?|factors?|proxies?|proxy|method(?:ology)?|assumptions?|'
    r'provenance|reliab(?:ility|le)|accurac(?:y|ate)|certif(?:ied|ication)|'
    r'calculat(?:ion|ed)|how\s+(?:was|were|is|are))\b', re.I)


def navigation_request(message: str) -> str | None:
    match = re.fullmatch(r'\s*(?:open|show|navigate\s+to|go\s+to)\s+(?:our\s+|the\s+)?'
                         r'(overview|inventory|routes|shipments)(?:\s+view)?[.!]?\s*', message, re.I)
    return match.group(1).lower() if match else None


def integration_request(snapshot: dict, message: str) -> str | None:
    """Recognize narrow fact reads or an explicit reviewable shipment delay."""
    text = message.casefold()
    # A user forbidding public search is still asking for an internal read.
    # Remove only the explicit prohibition before checking for positive search
    # intent; an affirmative search request must keep its consent path.
    intent_text = re.sub(
        r"\b(?:do\s+not|don't|never|without)\s+(?:search(?:ing)?|brows(?:e|ing))\s+"
        r"(?:the\s+)?(?:internet|web)\b", '', text)
    # Internal market reference and existing operations facts answer these
    # reads. A question about timing does not authorize a purchase.
    if (re.search(r'\b(?:forecast|prediction|market|buy\s+(?:now|versus\s+wait)|wait\s+or\s+buy|what\s+if\b.{0,40}\bprice)\b', text)
            and re.search(r'\b(?:our|my|current|this|workspace)\b', text)
            and not re.search(r'\b(?:send|place|submit|approve|execute|purchase\s+now|search|browse|internet|news|headlines?|latest)\b', intent_text)
            and not any(c['company_id'] != snapshot['company_id'] and c['name'].casefold() in text
                        for c in list_companies())):
        return 'read_market'
    if is_workspace_news_question(message) or _EXTERNAL_TOPIC.search(intent_text):
        return None
    if any(c['company_id'] != snapshot['company_id'] and c['name'].casefold() in text
           for c in list_companies()):
        return None
    shipment = bool(re.search(r'\b(?:shipment|shipments|inbound|consignment|consignments)\b', text))
    # These are factual reads, never a way to conceal another requested action.
    safe = re.sub(r'\b(?:do not|don.t|without)\s+(?:change|send|place|apply|submit|order)\w*\b[^.;]*', '', text)
    if re.search(r'\b(?:send|buy|purchase now|place|apply|submit|change|replan|estimate|calculate|set|approve|confirm)\b', safe):
        return None
    if re.search(r'\b(?:our|current|this company.s|company)\b.{0,35}\b(?:routes?|lanes?|chokepoints?)\b', text):
        return 'read_routes'
    current = bool(_CURRENT_SCOPE.search(text) or re.search(r'\bdemo-plan-demand:[\w:-]+\b', text))
    if not current:
        return None
    if shipment and re.search(r'\b(?:prepare|review|compare|recover|recovery)\b', text) and re.search(r'\bdelay(?:ed)?\b', text):
        return 'prepare_shipment_recovery'
    if re.search(r'\b(?:carbon|emission|emissions|footprint|co2e)\b', text):
        return 'read_carbon'
    if (not shipment and _METHOD_QUESTION.search(text)
            and re.search(r'\b(?:factors?|proxies?|proxy)\b', text)
            and re.search(r'\b(?:this|my|our|the\s+current|current|selected|saved|reviewed)\s+'
                          r'(?:purchase|plan|assessment|comparison)\b', text)):
        return 'read_carbon'
    return 'read_shipments' if shipment else None


def _material_scope(snapshot, message):
    text = message.casefold()
    own = {m['material_id'] for m in snapshot['company']['materials']}
    named = set()
    for company in list_companies():
        for material in company['materials']:
            if any(re.search(r'(?<!\w)' + re.escape(label.casefold()) + r'(?!\w)', text)
                   for label in (material['material_id'], material['name'])):
                named.add(material['material_id'])
    return named, own


def _spoken_kg(value):
    """Round spoken kilograms without changing the saved Decimal text."""
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if number and abs(number) < Decimal('0.01'):
        return format(number, 'f')
    try:
        return format(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP).normalize(), 'f')
    except InvalidOperation:
        return format(number, 'f')


def _publishers(factors):
    names = []
    for factor in factors:
        publisher = factor.get('publisher') if isinstance(factor, dict) else None
        if publisher and publisher not in names:
            names.append(publisher)
    return names


def _spoken_unit(quantity, unit):
    try:
        plural = Decimal(str(quantity)) != 1
    except (InvalidOperation, TypeError, ValueError):
        plural = False
    return unit + 's' if plural and unit in {'barrel', 'tonne', 'unit'} else unit


def _route_material_scope(message):
    labels = [(label.casefold(), material['material_id']) for company in list_companies()
              for material in company['materials'] for label in (material['material_id'], material['name'])]
    named, spans = set(), []
    for label, material_id in sorted(set(labels), key=lambda item: -len(item[0])):
        for match in re.finditer(r'(?<![\w-])' + re.escape(label) + r'(?![\w-])', message.casefold()):
            if not any(match.start() < end and start < match.end() for start, end in spans):
                named.add(material_id)
                spans.append(match.span())
    return named


def _read_routes(snapshot, message):
    company = snapshot['company']
    text = message.casefold()
    named = _route_material_scope(message)
    own = {material['material_id'] for material in company['materials']}
    if named - own:
        return {'assistant_text': f'That material is outside the {company["name"]} workspace. Choose a material listed for this company.',
                'workspace': {}}
    if any(other['company_id'] != company['company_id'] and other['name'].casefold() in text
           for other in list_companies()):
        return {'assistant_text': f'That company is outside the {company["name"]} workspace. Choose a route from this company.',
                'workspace': {}}
    routes = company.get('routes', [])
    if len(named) > 1:
        return {'assistant_text': 'Which one material should I use for this itinerary: '
                                  + ', '.join(sorted(named)) + '?', 'workspace': {}}
    owned_ids = {route['route_id'] for route in routes}
    route_ids = [match.group() for match in re.finditer(r'(?<![\w-])[\w-]+-route(?:-\d+)?(?![\w-])', text)]
    if route_ids and (len(set(route_ids)) != 1 or route_ids[0] not in owned_ids):
        return {'assistant_text': f'Choose an exact route ID from the {company["name"]} workspace; that route is unknown or outside this workspace.',
                'workspace': {}}
    route_id = route_ids[0] if route_ids else None
    material_request = re.search(r'\b(?:routes?|[\w-]+-route(?:-\d+)?)\s+for\s+([\w-]+)', text)
    if material_request and not named and material_request.group(1) not in {'this', 'our', 'the', 'company'}:
        return {'assistant_text': f'That material is unknown in the {company["name"]} workspace. Choose a listed material.',
                'workspace': {}}
    if route_id and named:
        route_materials = set(next(route for route in routes if route['route_id'] == route_id)['materials'])
        if not named <= route_materials:
            return {'assistant_text': 'That material is not on the selected profile route. Choose a compatible route and material.',
                    'workspace': {}}

    locations = company.get('locations', [])
    mentioned = [index for index, location in enumerate(locations)
                 if re.search(r'(?<!\w)' + re.escape(location['city'].casefold()) + r'(?!\w)', text)]
    foreign_city = any(location['city'].casefold() in text
                       for other in list_companies() if other['company_id'] != company['company_id']
                       for location in other['locations'] if location['city'].casefold() not in
                       {own['city'].casefold() for own in locations})
    destination = re.search(r'\b(?:routes?\s+to\s+|buyer(?:\s+(?:city|location))?\s+|destination\s+|deliver\s+to\s+)'
                            r'([a-z][a-z .-]*?)(?=\s+for\b|[?.!,]|$)', text)
    unknown_city = bool(destination and not any(re.search(r'(?<!\w)' + re.escape(loc['city'].casefold()) + r'(?!\w)',
                                                          destination.group(1)) for loc in locations))
    if foreign_city or unknown_city or len(set(mentioned)) > 1:
        choices = ', '.join(location['city'] for location in locations)
        return {'assistant_text': f'Choose an exact buyer city in the {company["name"]} profile: {choices}.',
                'workspace': {}}
    requested_buyer_index = mentioned[0] if mentioned else None
    buyer_index = requested_buyer_index
    if buyer_index is None and route_id:
        matches = [index for index, location in enumerate(locations)
                   if next(route for route in routes if route['route_id'] == route_id)['destination'] ==
                   f"{location['city']}, {location['country']}"]
        if len(matches) != 1:
            route = next(route for route in routes if route['route_id'] == route_id)
            choices = ', '.join(location['city'] for location in locations)
            return {'assistant_text': f"Profile route {route_id}: {route['origin']} to {route['destination']}. "
                                      f"Source: company profile. Which buyer city should I use: {choices}? "
                                      'The onward connection is not recorded; no buyer is assumed.',
                    'workspace': {'routes': [deepcopy(route)]}}
        buyer_index = matches[0]

    if not route_id and not named and buyer_index is None:
        via = list(dict.fromkeys(point for route in routes for point in route.get('via', [])
                                 if isinstance(point, str)))
        answer = (f"{company['name']} has {len(routes)} declared profile routes. "
                  + (f"Named via points include {', '.join(via[:4])}. " if via else '')
                  + 'Transport mode, verified distance and a selected supplier binding are not recorded here. '
                  'I can narrow the itinerary by material, route or buyer city.')
        return {'assistant_text': answer, 'workspace': {'routes': deepcopy(routes)}}
    selected = [route for route in routes if (not route_id or route['route_id'] == route_id)
                and (not named or named.intersection(route['materials']))
                and (requested_buyer_index is None or route['destination'] ==
                     f"{locations[requested_buyer_index]['city']}, {locations[requested_buyer_index]['country']}")]
    if not selected:
        return {'assistant_text': 'The selected profile route does not match that material or buyer city. Choose a compatible route or material from this workspace.',
                'workspace': {}}
    if len(selected) > 1 or len(named) > 1 or (len(selected[0]['materials']) > 1 and not named):
        choices = '; '.join(f"{route['route_id']}: {route['origin']} via "
                            f"{', '.join(route['via']) or 'no named via point'} to {route['destination']} "
                            f"({', '.join(route['materials'])})" for route in selected)
        return {'assistant_text': f'{company["name"]} profile route choices: {choices}. Which route and material do you mean? '
                                  'These routes have no selected supplier binding or recorded freight distance.',
                'workspace': {'routes': deepcopy(selected)}}
    route = selected[0]
    material_id = next(iter(named)) if named else route['materials'][0]
    if material_id not in route['materials']:
        return {'assistant_text': 'That material is not on the selected profile route. Choose a compatible route and material.',
                'workspace': {}}
    display = build_route_display(company, route['route_id'], material_id, buyer_index or 0)
    itinerary = display['itinerary']
    stops = ' → '.join([itinerary['origin'], *itinerary['via'], itinerary['destination']])
    limitations = ' '.join(display['limitations'][:2])
    answer = (f"{company['name']} profile route {route['route_id']} for {material_id}: {stops}. "
              f"Source: company profile; display {itinerary['display']}. {limitations} "
              'No selected supplier binding, verified freight distance or planner/carbon effect is recorded.')
    return {'assistant_text': answer, 'workspace': {'routes': deepcopy(selected), 'route_display': display}}


def integration_read(snapshot: dict, kind: str, message: str = '') -> dict:
    company = snapshot['company']['name']
    if kind == 'read_market':
        from .market_prediction import market_read_model
        from .operations_decision import preview_from_snapshot
        named, own = _material_scope(snapshot, message)
        if named - own:
            return {'assistant_text': 'That material is outside this company workspace. Choose an owned material.',
                    'workspace': {}}
        material_id = next(iter(named), None) or snapshot.get('selected_material_id') or snapshot['company']['materials'][0]['material_id']
        if material_id not in own:
            return {'assistant_text': 'Choose a material from this company workspace.', 'workspace': {}}
        market = market_read_model()
        preview = preview_from_snapshot(snapshot, material_id, market)
        gap = (preview.get('service_trace') or {}).get('first_gap_date')
        if preview['status'] == 'REPLAN_REQUIRED':
            action = f'The current dated supply path leaves a stock gap from {gap}; replan before committing.'
        else:
            needed = ', '.join(field.replace('_', ' ') for field in preview['missing_inputs'][:4])
            action = f'An actionable buy or wait decision needs {needed}.'
        answer = (f'{action} {company} {material_id}: market reference is dated '
                  f"{market['anchor_market_session_date']} and its influence is "
                  f"{preview['market_influence']['status'].lower().replace('_', ' ')}. "
                  'Accepted quote costs stay fixed. No order or currency exchange was made.')
        return {'assistant_text': answer, 'workspace': {'prediction': preview}}
    if kind == 'read_routes':
        return _read_routes(snapshot, message)
    named, own = _material_scope(snapshot, message)
    explain_method = bool(_METHOD_QUESTION.search(message))
    if named - own:
        return {'assistant_text': f'That material is outside the {company} workspace. Choose a material listed for this company.',
                'workspace': {}}
    if kind == 'read_carbon':
        record = snapshot.get('carbon_assessment')
        result = deepcopy(record.get('result')) if isinstance(record, dict) else None
        plan = snapshot.get('plan') or {}
        if result is not None and named and result.get('material_id') not in named:
            result = None
        if result is None:
            answer = (f'No current emissions assessment is saved for this purchase at {company}. '
                      'Open Procurement, compare reviewed quotes, then enter freight distance and any required shipping mass in Emissions. '
                      'I can explain the saved calculation and its sources; missing weights or distances are not guessed.')
        else:
            rows = result.get('suppliers', [])
            selected_id = plan.get('selected_offer_id')
            selected_offer = next((offer for offer in plan.get('alternatives', [])
                                   if offer.get('offer_id') == selected_id), None)
            row = next((r for r in rows if r['offer_id'] == selected_id), None) if selected_id else None
            if row is None or selected_offer is None or selected_offer.get('feasible') is False:
                answer = (f'No selected feasible purchase has a supplier emissions summary for {company}. '
                          'The saved comparison contains quote scenarios, but none should be described as the current purchase.')
            else:
                estimate = row['estimate']
                material = next((m['name'] for m in snapshot['company']['materials'] if m['material_id'] == result['material_id']), result['material_id'])
                parts = [f"For {company}, the reviewed plan covers {estimate['quantity']} "
                         f"{_spoken_unit(estimate['quantity'], estimate['unit'])} "
                         f"of {material} from {supplier_display_name(row['supplier'])}:"]
                production, freight = estimate['material'], estimate['transport']
                factor = production.get('factor') or {}
                if production.get('kg_co2e') is not None:
                    publisher = factor.get('publisher')
                    parts.append(f"Production average: {_spoken_kg(production['kg_co2e'])} kg CO2e"
                                 + ((f' from {publisher}.' if publisher else '; source unavailable in this assessment.')
                                    if explain_method else '.'))
                    if explain_method and production.get('product_match') == 'proxy':
                        category = factor.get('category')
                        note = factor.get('note')
                        parts.append('The production factor is a proxy, not an exact product match'
                                     + (f' ({category}).' if category else '.')
                                     + (f' {note}' if note else ''))
                else:
                    reason = production.get('reason')
                    parts.append('For this saved assessment, material production is not estimated'
                                 + (f': {reason}' if reason else '; a usable factor or material mass is unavailable.'))
                if freight.get('kg_co2e') is not None:
                    publishers = _publishers((leg.get('factor') for leg in freight.get('legs', [])
                                              if isinstance(leg, dict)))
                    parts.append(f"Entered freight: {_spoken_kg(freight['kg_co2e'])} kg CO2e"
                                 + ((f" from {'; '.join(publishers)}." if publishers else
                                     '; source unavailable in this assessment.') if explain_method else '.'))
                else:
                    reasons = [leg.get('reason') for leg in freight.get('legs', [])
                               if isinstance(leg, dict) and leg.get('reason')]
                    parts.append('For this saved assessment, entered freight is not fully estimated'
                                 + (': ' + ' '.join(reasons) if reasons else
                                    '; enter a freight mode, distance and any required shipping mass.'))
                if explain_method:
                    for leg in freight.get('legs', []):
                        mode = leg['mode'].replace('_', ' ')
                        distance = leg.get('distance_km')
                        if distance is None:
                            parts.append(f'Freight leg {mode}: distance not entered; no route length is inferred.')
                        else:
                            parts.append(f'Entered freight leg: {mode}, {distance} km (distance entered by you).')
                if estimate.get('combined_status') == 'combined' and estimate.get('total_kg_co2e') is not None:
                    parts.append(f"Supported whole-scenario total: {_spoken_kg(estimate['total_kg_co2e'])} kg CO2e.")
                else:
                    parts.append('No whole-scenario total is supported; components remain separate.'
                                 + ((' ' + estimate['combined_reason']) if explain_method
                                    and estimate.get('combined_reason') else ''))
                if explain_method:
                    parts.append('This estimate uses published averages and entered assumptions; it is not a certified or supplier-specific footprint.')
                answer = ' '.join(parts)
        return {'assistant_text': answer, 'workspace': {'carbon': result}}
    view = deepcopy(snapshot.get('shipments') or {})
    rows = view.get('shipments', [])
    if named:
        rows = [r for r in rows if r.get('material_id') in named]
        view['shipments'] = rows
    planned = [r for r in rows if r.get('plan_id')]
    if planned:
        row = planned[0]
        reference = row.get('order_id') or row.get('demand_reference')
        comparison = ''
        if row.get('scenario_plan_id') and row.get('baseline_plan_id'):
            comparison = (f"Reviewed recovery: original baseline supplier "
                          f"{supplier_display_name(row.get('baseline_supplier') or 'not recorded')}, "
                          f"planned arrival {row['original_expected_arrival']}; ")
            if row.get('scenario_status') == 'infeasible':
                comparison += 'reviewed scenario has no feasible supplier or selected arrival. '
            elif row.get('scenario_selected_offer_id'):
                comparison += (f"reviewed scenario supplier "
                               f"{supplier_display_name(row.get('scenario_supplier') or 'not recorded')}, "
                               f"planned arrival {row['scenario_expected_arrival']}. ")
        if row.get('scenario_status') == 'infeasible':
            answer = (f'{company}: the reviewed disruption has no feasible supplier. '
                      f"The retained baseline planning context was {row['quantity']} "
                      f"{_spoken_unit(row['quantity'], row['quantity_unit'])} "
                      f"of {row['material_name']} from {supplier_display_name(row.get('supplier') or 'the baseline supplier')}. "
                      'This is not a current selected purchase. '
                      f'Demand reference: {reference}. ')
        else:
            answer = (f"{company}: the current inbound planning scenario is {row['quantity']} "
                      f"{_spoken_unit(row['quantity'], row['quantity_unit'])} "
                      f"of {row['material_name']} from {supplier_display_name(row.get('supplier') or 'the selected supplier')}. "
                      f"Demand reference: {reference}. "
                      f"Planned arrival is {row['expected_arrival']}, against a need-by date of {row['need_by']}. ")
        answer = comparison + answer
        if explain_method:
            answer += 'Arrival is based on the reviewed quote lead and planning assumptions, not live carrier tracking. '
        answer += ('Choose explicit delay days to prepare a recovery for review; no plan changes until confirmation.'
                   if row.get('recovery_available') else 'Review the saved recovery comparison before preparing another action.')
    else:
        answer = (f'{company} has {len(rows)} expected-order records in this view. '
                  'No current reviewed supplier plan is linked for recovery. Compare reviewed quotes first. '
                  'These are expected-order planning records.')
    return {'assistant_text': answer, 'workspace': {'shipments': view}}


def integration_outcome(snapshot: dict, message: str) -> dict | None:
    view = navigation_request(message)
    if view is not None:
        return {'status': 'completed', 'assistant_text': f'Open the {view} view.',
                'agent_steps': [{'agent': 'workspace', 'role': 'deterministic', 'status': 'completed',
                                 'model': 'none', 'provider_call': 'none', 'tool': 'navigate_workspace',
                                 'summary': 'Selected a supported workspace view.'}],
                'evidence_ids': [], 'candidate': None, 'proposal_kind': None,
                'parameters': None, 'utility': {'kind': 'navigate', 'parameters': {'view': view}}}
    kind = integration_request(snapshot, message)
    if kind is None:
        return None
    candidate = None
    if kind == 'prepare_shipment_recovery':
        rows = (snapshot.get('shipments') or {}).get('shipments', [])
        eligible = [r for r in rows if r.get('recovery_available')]
        named = [r for r in rows if r['shipment_id'] in message]
        if named:
            eligible = [r for r in named if r.get('recovery_available')]
        elif not re.search(r'\b(?:current|selected|this)\s+(?:inbound\s+)?shipment\b', message, re.I):
            eligible = []
        match = re.search(r'\bdelay(?:ed)?(?:\s+by)?\s+(\d+)\s+days?\b', message, re.I)
        if len(eligible) != 1 or match is None or not 0 <= int(match.group(1)) <= 3650:
            payload = {'assistant_text': 'Choose one current linked shipment and state an explicit whole-day delay to prepare recovery for review.', 'workspace': {}}
        else:
            row, days = eligible[0], int(match.group(1))
            candidate = {'kind': 'recovery', 'parameters': {'delay_days_by_offer': {row['offer_id']: days},
                         'shipment_id': row['shipment_id']}}
            payload = {'assistant_text': f"Review the recovery comparison for {row['material_name']} with an assumed {days}-day shipment delay. No change has run.", 'workspace': {}}
    else:
        payload = integration_read(snapshot, kind, message)
    return {'status': 'proposal' if candidate else 'completed', **payload,
            'agent_steps': [{'agent': 'workspace', 'role': 'deterministic', 'status': 'completed',
                             'model': 'none', 'provider_call': 'none', 'tool': kind,
                             'summary': 'Read current server evidence and preserve reviewed action boundaries.'}],
            'evidence_ids': [], 'candidate': candidate, 'proposal_kind': candidate['kind'] if candidate else None,
            'parameters': candidate['parameters'] if candidate else None, 'utility': None}
