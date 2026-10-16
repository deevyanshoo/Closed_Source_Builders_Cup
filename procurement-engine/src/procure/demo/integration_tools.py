"""Grounded explanations for current cost-carbon and shipment read models."""
from __future__ import annotations

from copy import deepcopy
import re

from .company import list_companies
from .news_brief import is_workspace_news_question
from .presentation import supplier_display_name


_EXTERNAL_TOPIC = re.compile(
    r'\b(?:news|headlines?|polic(?:y|ies)|regulations?|grants?|subsid(?:y|ies)|'
    r'programmes?|programs?|research|search|browse|web|internet|external|'
    r'industry|market|public|global|world|government|laws?|compliance)\b', re.I)
_CURRENT_SCOPE = re.compile(
    r'\b(?:this|my|our|the\s+current|current|selected|saved|reviewed)\s+'
    r'(?:company|workspace|purchase|plan|comparison|assessment|order|'
    r'carbon|emissions?|footprint|shipments?|inbound|consignments?)\b', re.I)


def integration_request(snapshot: dict, message: str) -> str | None:
    """Recognize narrow fact reads or an explicit reviewable shipment delay."""
    text = message.casefold()
    if is_workspace_news_question(message) or _EXTERNAL_TOPIC.search(text):
        return None
    if any(c['company_id'] != snapshot['company_id'] and c['name'].casefold() in text
           for c in list_companies()):
        return None
    shipment = bool(re.search(r'\b(?:shipment|shipments|inbound|consignment|consignments)\b', text))
    # These are factual reads, never a way to conceal another requested action.
    safe = re.sub(r'\b(?:do not|don.t|without)\s+(?:change|send|place|apply|submit|order)\w*\b[^.;]*', '', text)
    if re.search(r'\b(?:send|buy|purchase now|place|apply|submit|change|replan|estimate|calculate|set|approve|confirm)\b', safe):
        return None
    current = bool(_CURRENT_SCOPE.search(text) or re.search(r'\bdemo-plan-demand:[\w:-]+\b', text))
    if not current:
        return None
    if shipment and re.search(r'\b(?:prepare|review|compare|recover|recovery)\b', text) and re.search(r'\bdelay(?:ed)?\b', text):
        return 'prepare_shipment_recovery'
    if re.search(r'\b(?:carbon|emission|emissions|footprint|co2e)\b', text):
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


def integration_read(snapshot: dict, kind: str, message: str = '') -> dict:
    company = snapshot['company']['name']
    named, own = _material_scope(snapshot, message)
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
                parts = [f"For {company}, {estimate['quantity']} {estimate['unit']} of {material}, using {supplier_display_name(row['supplier'])}:"]
                production, freight = estimate['material'], estimate['transport']
                if production.get('kg_co2e') is not None:
                    parts.append(f"Production average: {production['kg_co2e']} kg CO2e.")
                else:
                    parts.append('A usable production factor or material mass is unavailable.')
                if freight.get('kg_co2e') is not None:
                    parts.append(f"Entered freight: {freight['kg_co2e']} kg CO2e.")
                else:
                    parts.append('Freight is not fully estimated; enter mode, distance and any required mass.')
                for leg in freight.get('legs', []):
                    mode = leg['mode'].replace('_', ' ')
                    distance = leg.get('distance_km')
                    if distance is None:
                        parts.append(f'Freight leg {mode}: distance not entered; no route length is inferred.')
                    else:
                        parts.append(f'Entered freight leg: {mode}, {distance} km (distance entered by you).')
                if estimate.get('combined_status') == 'combined' and estimate.get('total_kg_co2e') is not None:
                    parts.append(f"Production average plus the entered freight leg: {estimate['total_kg_co2e']} kg CO2e.")
                else:
                    parts.append('No whole-scenario total is supported; components remain separate.')
                factor = production.get('factor') or {}
                sources = [factor['publisher']] if factor.get('publisher') else []
                for leg in freight.get('legs', []):
                    publisher = (leg.get('factor') or {}).get('publisher')
                    if publisher and publisher not in sources:
                        sources.append(publisher)
                if sources:
                    parts.append('Sources: ' + '; '.join(sources) + '.')
                parts.append('This is an estimate from published averages and entered assumptions, not a certified or supplier-specific footprint.')
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
        answer = (f"{company}: the current inbound planning scenario is {row['quantity']} {row['quantity_unit']} "
                  f"of {row['material_name']} from {supplier_display_name(row.get('supplier') or 'the selected supplier')}. "
                  f"Demand reference: {reference}. ")
        if row.get('scenario_status') == 'infeasible':
            answer += 'The reviewed disruption has no feasible supplier; the original shipment context is retained for review. '
        else:
            answer += f"Planned arrival is {row['expected_arrival']}, against a need-by date of {row['need_by']}. "
        answer += 'Arrival is based on the reviewed quote lead and planning assumptions, not live carrier tracking. '
        answer += ('Choose explicit delay days to prepare a recovery for review; no plan changes until confirmation.'
                   if row.get('recovery_available') else 'Review the saved recovery comparison before preparing another action.')
    else:
        answer = (f'{company} has {len(rows)} expected-order records in this view. '
                  'No current reviewed supplier plan is linked for recovery. Compare reviewed quotes first. '
                  'These are planning records, not live carrier tracking.')
    return {'assistant_text': answer, 'workspace': {'shipments': view}}


def integration_outcome(snapshot: dict, message: str) -> dict | None:
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
