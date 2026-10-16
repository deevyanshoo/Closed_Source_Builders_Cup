"""Pure internal-first decision boundary for the synthetic judge workspace."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re

from .agent_tools import inventory_analytics
from .company import list_companies
from .integration_tools import integration_read, integration_request
from .news_brief import build_news_brief
from .quote_service import company_for_offer
from .research import ResearchValidationError, prepare_research


_PUBLIC = re.compile(r'\b(suez|hormuz|red sea|panama|news|headlines?|shipping|port|tariffs?|policy|market|insurance|world|global)\b', re.I)
_LATEST = re.compile(r'\b(latest|fresh|today|now|current|recent|newest|up.to.date)\b', re.I)
_STOCK = re.compile(r'\b(stock|inventory|stock cover|shortage)\b', re.I)
_SOURCING = re.compile(r'\b(sourcing|source|sources|suppliers?|procurement)\b', re.I)
_OPERATIONAL_ACTION = re.compile(r'\b(send|draft|prepare|approve|apply|save|update|change|edit|'
                                 r'load|place|buy|purchase|execute|submit|create)\b', re.I)
_QUOTE = re.compile(r'\b(quotes?|offers?|prices?)\b', re.I)
_DISRUPTION = re.compile(r'\b(delay|delayed|disruption|blockage|blocked|what if|what-if)\b', re.I)
_ROUTE = re.compile(r'\b(suez|hormuz|red sea|panama|port|shipping|route|canal)\b', re.I)
_DURATION = re.compile(r'\b(?P<number>\d+|a|one|two|three|four|seven|fourteen)\s*[- ]\s*'
                       r'(?P<unit>days?|weeks?)\b', re.I)
_HYPOTHETICAL = re.compile(r'\b(?:what[- ]if|suppose|simulate|preview|scenario|assume|assuming|'
                           r'how would|what would|if .* (?:were|was|is|gets|became|becomes))\b', re.I)
_COUNT = re.compile(r'\b(?:how many|number of|count of)\b', re.I)
_EVENT = re.compile(r'\b(suez|hormuz|panama|red sea)\b', re.I)
_TOPICS = {
    'port': re.compile(r'\bports?\b', re.I),
    'policy': re.compile(r'\b(?:polic(?:y|ies)|regulat(?:ion|ions|ory)|tariffs?|dut(?:y|ies))\b', re.I),
    'insurance': re.compile(r'\b(?:insurance|insurer|insured|premiums?)\b', re.I),
    'rates': re.compile(r'\b(?:rates?|pricing)\b', re.I),
    'status': re.compile(r'\b(?:shipping|shipments?|blockage|blocked|disruption|closure|transit|delays?|status)\b', re.I),
}


def _topics(value):
    return {name for name, pattern in _TOPICS.items() if pattern.search(value)}


def _broad_public_brief(message):
    return (not _EVENT.search(message) and not _topics(message)
            and bool(re.search(r'\b(?:news|headlines?|world|global|happening)\b', message, re.I)))


def _public_terms(message):
    topics = _topics(message)
    terms = [name for name in ('port', 'policy', 'insurance', 'rates') if name in topics]
    if 'status' in topics:
        terms.append('shipping status')
    return terms


def _base(snapshot, kind):
    plan = snapshot.get('plan') or snapshot.get('baseline_plan') or {}
    return {'schema': 1, 'kind': kind, 'company_id': snapshot['company_id'],
            'context_version': snapshot['context_version'],
            'baseline_plan_id': plan.get('plan_id'),
            'offer_versions': dict(plan.get('offer_versions') or {}), 'evidence_refs': []}


def _material(snapshot, message):
    company = snapshot['company']
    matches = [row['material_id'] for row in company.get('materials', [])
               if re.search(r'(?<!\w)(?:' + re.escape(row['material_id']) + '|' +
                            re.escape(row['name']) + r')(?!\w)', message, re.I)]
    if len(matches) == 1:
        return matches[0]
    if re.search(r'\b(selected|current|this)\s+material\b', message, re.I):
        return snapshot.get('selected_material_id')
    return None


def _public_part(message):
    event = re.search(r'\b(suez(?: canal)?|strait of hormuz|red sea|panama canal)\b', message, re.I)
    if event:
        name = 'Suez Canal' if event.group().casefold() == 'suez' else event.group()
        terms = _public_terms(message)
        return f"{name} {' '.join(terms) if terms else 'shipping status'} latest public updates"
    terms = _public_terms(message)
    if terms:
        return f"{' '.join(terms)} latest public updates"
    if _broad_public_brief(message):
        return 'What is the latest public supply chain news?'
    return None


def _research(message, company, now):
    try:
        return prepare_research(message, company, now)
    except ResearchValidationError:
        return None


def _duration(message):
    found = []
    words = {'a': 1, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
             'seven': 7, 'fourteen': 14}
    for match in _DURATION.finditer(message):
        if match.start() and message[match.start() - 1] in '-_:':
            continue
        raw = match.group('number').casefold()
        value = int(raw) if raw.isdigit() else words[raw]
        days = value * (7 if match.group('unit').casefold().startswith('week') else 1)
        found.append(days)
    return found[0] if len(found) == 1 and 0 <= found[0] <= 3650 else None


def _foreign_company(snapshot, message):
    for profile in list_companies():
        if profile['company_id'] == snapshot['company_id']:
            continue
        if any(re.search(r'(?<!\w)' + re.escape(label) + r'(?!\w)', message, re.I)
               for label in (profile['company_id'], profile['name'])):
            return True
    return False


def _broad_inventory_read(snapshot, message):
    if not (_STOCK.search(message) and _SOURCING.search(message)):
        return None
    # A request for recommended actions is analysis; these verbs request an effect.
    if _OPERATIONAL_ACTION.search(message):
        return None
    from .operator import _broad_inventory_scope
    if _broad_inventory_scope(snapshot, message) == 'company-wide':
        return 'company-wide'
    if (re.search(r'\bour inventory materials\b.*\bneed attention\b', message, re.I)
            or (re.search(r'\b(?:analy[sz]e|review|assess)\b', message, re.I)
                and re.search(r'\b(?:across )?all materials\b', message, re.I))):
        return 'company-wide'
    return None


def _public_items(snapshot, message, now):
    brief = build_news_brief(snapshot['company'], snapshot.get('news'), now=now)
    items = [item for item in brief['headlines'] + brief['global_watch']['events']
             if item.get('published_at') and item.get('retrieved_at')]
    event = _EVENT.search(message)
    if event:
        items = [item for item in items if event.group().casefold() in _item_topic_text(item)]
    if not _broad_public_brief(message):
        required = _topics(message)
        if required:
            items = [item for item in items if required <= _topics(_item_topic_text(item))]
    return brief, items[:3]


def _item_topic_text(item):
    exposure = item.get('exposure') or {}
    return ' '.join(str(value).casefold() for value in
                    (item.get('title'), item.get('publisher'), item.get('provider'),
                     item.get('source_id'), item.get('topic'), exposure.get('reason')))


def _cached_answer(brief, items):
    state = brief['cache_state']
    return ('Cached public evidence' + (f' ({state})' if state != 'fresh' else '') + ': ' +
            '; '.join(f"{item['title']} (published {item.get('published_at') or 'date unavailable'})"
                      for item in items) + '. Verify route and shipment impact separately.')


def _history(snapshot, message, now):
    event = _EVENT.search(message)
    required = _topics(message)
    stop = {'what', 'when', 'where', 'about', 'this', 'that', 'news', 'latest',
            'current', 'status', 'update', 'public', 'please', 'from'}
    generic = set(re.findall(r'\b[a-z]{4,}\b', message.casefold())) - stop
    for record in (snapshot.get('completed_research') or [])[:12]:
        if (not isinstance(record, dict) or record.get('company_id') != snapshot['company_id'] or
            record.get('context_version') != snapshot['context_version'] or
            record.get('status') != 'completed' or not isinstance(record.get('answer'), str) or
            not record['answer'].strip() or not isinstance(record.get('evidence_refs'), list) or
            not record['evidence_refs']):
            continue
        query = str(record.get('query') or record.get('topic') or '').casefold()
        if event:
            if event.group().casefold() not in query:
                continue
            if not required:
                required = {'status'}
        elif not required and len(generic & (set(re.findall(r'\b[a-z]{4,}\b', query)) - stop)) < 2:
            continue
        if required and not required <= _topics(query):
            continue
        try:
            checked = datetime.fromisoformat(record['checked_at'].replace('Z', '+00:00'))
        except (KeyError, AttributeError, ValueError):
            continue
        if checked.tzinfo is None or checked > now or now - checked > timedelta(days=30):
            continue
        return record, checked.astimezone(timezone.utc).date().isoformat()
    return None


def _profile_preview(snapshot, material, days):
    row = next((item for item in inventory_analytics(snapshot)['materials']
                if item['material_id'] == material), None)
    result = _base(snapshot, 'read_only_preview')
    if not row:
        result['kind'] = 'unavailable'
        result['outcome'] = 'Profile material is unavailable.'
        return result
    cover, lead = row['stock_cover_days'], row['shortest_supplier_lead_days']
    if cover is None or lead is None:
        result['kind'] = 'unavailable'
        result['outcome'] = 'Stock cover or listed supplier lead is unavailable for this profile material.'
        return result
    gap = Decimal(cover) - Decimal(lead + days)
    result['preview_request'] = {'mode': 'profile_stock_cover', 'material_id': material,
                                 'delay_days': days, 'scenario_plan_id': None}
    result['answer'] = (f"Profile-only preview for {row['name']}: stock cover {cover} days versus "
                        f"shortest listed supplier lead {lead} days plus assumed {days}-day delay; "
                        f"cover minus delayed lead is {format(gap, 'f')} days. "
                        'This is a profile timing comparison, not a costed plan recovery.')
    result['evidence_refs'] = [f"profile:{snapshot['company_id']}:{material}"]
    return result


def _preview(snapshot, message):
    if not _DISRUPTION.search(message) or not _HYPOTHETICAL.search(message):
        return None
    route_question = bool(_ROUTE.search(message))
    shipment_question = bool(re.search(r'\b(shipment|inbound|supplier)\b', message, re.I))
    profile_question = not snapshot.get('plan') and _material(snapshot, message) is not None
    if not (route_question or shipment_question or profile_question):
        return None
    result = _base(snapshot, 'needs_input')
    if _foreign_company(snapshot, message):
        result['missing_inputs'] = ['company']
        result['answer'] = 'Choose a shipment or material in the active company workspace.'
        return result
    plan = snapshot.get('plan') or {}
    days = _duration(message)
    material = _material(snapshot, message)
    if material is None and plan and not route_question:
        material = snapshot.get('selected_material_id')
    missing = []
    if material is None:
        missing.append('material')
    if days is None:
        missing.append('delay_days')
    if not plan and not route_question and material and days is not None:
        return _profile_preview(snapshot, material, days)
    # A country, currency, or supplier name never establishes a shipment route.
    location = re.search(r'\b(suez canal|suez|hormuz|red sea|panama canal)\b', message, re.I)
    routes = [row for row in snapshot['company'].get('routes', [])
              if isinstance(row, dict) and row.get('route_id') and row['route_id'] in message]
    location_id = routes[0]['route_id'] if len(routes) == 1 else location.group().lower() if location else None
    if route_question and (not location_id or (location and not routes)):
        missing.append('location_or_route')
    rows = (snapshot.get('shipments') or {}).get('shipments', [])
    eligible = [row for row in rows if isinstance(row, dict) and row.get('recovery_available')
                and row.get('company_id') == snapshot['company_id']
                and row.get('plan_id') == plan.get('plan_id')
                and row.get('material_id') == material and row.get('offer_id') == plan.get('selected_offer_id')
                and row.get('offer_version') == (plan.get('offer_versions') or {}).get(row.get('offer_id'))]
    selected = next((item for item in snapshot['company'].get('materials', [])
                     if item.get('material_id') == material), None)
    selected_supplier_alias = (selected is not None
        and snapshot.get('selected_material_id') in (None, material)
        and plan.get('company_id') == snapshot['company_id'] and plan.get('material') == material
        and bool(re.search(r'\b(?:current|selected|this)\s+(?:'
            + '|'.join(re.escape(label) for label in (selected['material_id'], selected['name']))
            + r')\s+supplier\b', message, re.I)))
    named = [row for row in eligible if row.get('shipment_id') and row['shipment_id'] in message]
    if named:
        eligible = named
    elif not route_question and not (selected_supplier_alias or re.search(
            r'\b(current|selected|this)\s+(?:inbound\s+)?shipment\b', message, re.I)):
        eligible = []
    if route_question:
        # Dispatch currently has no declared route. Only an explicit owned binding qualifies.
        eligible = [row for row in eligible if isinstance(row.get('route'), dict)
                    and row['route'].get('route_id') in {r.get('route_id') for r in routes}
                    and (not location or location.group().casefold() in
                         str(row['route'].get('via', '')).casefold())]
        if not eligible and not missing:
            result['kind'] = 'unavailable'
            result['outcome'] = 'No current owned shipment has a declared matching route; no delay was assumed.'
            return result
    if len(eligible) != 1 and not route_question:
        missing.append('shipment_id')
    if missing:
        result['missing_inputs'] = missing
        result['answer'] = 'Specify ' + ', '.join(missing) + ' for a current owned shipment and an explicit whole-day planning delay.'
        return result
    if len(eligible) != 1:
        result['kind'] = 'needs_input'
        result['missing_inputs'] = ['shipment_id']
        result['answer'] = 'Choose one matching current shipment.'
        return result
    row = eligible[0]
    result['kind'] = 'read_only_preview'
    result['preview_request'] = {'company_id': snapshot['company_id'],
                                 'context_version': snapshot['context_version'],
                                 'baseline_plan_id': plan['plan_id'],
                                 'offer_versions': dict(plan['offer_versions']),
                                 'shipment_id': row['shipment_id'], 'material_id': material,
                                 'delay_days_by_offer': {row['offer_id']: days},
                                 'scenario_plan_id': None}
    result['answer'] = f'Preview a {days}-day planning delay for the selected inbound scenario. No recovery is applied.'
    result['evidence_refs'] = [row['shipment_id'], plan['plan_id']]
    return result


def resolve_judge_turn(snapshot: dict, message: str, *, now: datetime,
                       fresh_requested: bool = False) -> dict | None:
    """Classify only grounded reads or validated previews; defer other turns to Gemini."""
    if not isinstance(message, str) or not message.strip() or now.tzinfo is None:
        return None
    company = snapshot.get('company')
    if not isinstance(company, dict) or company.get('company_id') != snapshot.get('company_id'):
        return None
    if _foreign_company(snapshot, message):
        result = _base(snapshot, 'needs_input')
        result['missing_inputs'] = ['company']
        result['answer'] = 'Choose the active company workspace before using its evidence.'
        return result
    if _STOCK.search(message) and _SOURCING.search(message) and _OPERATIONAL_ACTION.search(message):
        return None
    preview = _preview(snapshot, message)
    if preview is not None:
        return preview
    public = bool(_PUBLIC.search(message))
    fresh = fresh_requested or bool(_LATEST.search(message) and public)
    local_parts = []
    refs = []
    material = _material(snapshot, message)
    analytics = None
    broad_scope = _broad_inventory_read(snapshot, message)
    if broad_scope:
        from .operator import _broad_inventory_answer
        analytics = inventory_analytics(snapshot)
        if len(analytics['materials']) > 12:
            return None
        answer = _broad_inventory_answer(snapshot, broad_scope, message)
        if answer:
            count = len(analytics['materials'])
            local_parts.append(f'Across all {count} profile materials, these are the top '
                               f'{min(count, 3)} priorities. ' + answer)
            refs.extend(f"profile:{snapshot['company_id']}:{row['material_id']}"
                        for row in analytics['materials'])
    elif _STOCK.search(message) and re.search(r'\b(our|my|current|this|workspace)\b', message, re.I):
        rows = inventory_analytics(snapshot)['materials']
        rows = [row for row in rows if row['material_id'] == material] if material else rows[:3]
        local_parts.append('; '.join(f"{row['name']} stock: {row['stock']} {row['unit']} (profile as of {company.get('as_of')})" for row in rows))
        refs.extend(f"profile:{snapshot['company_id']}:{row['material_id']}" for row in rows)
    if not _foreign_company(snapshot, message):
        local_kinds = []
        if public:
            if re.search(r'\b(?:carbon|emissions?|footprint|co2e)\b', message, re.I) and re.search(r'\b(?:our|my|current|this)\b', message, re.I):
                local_kinds.append('read_carbon')
            if re.search(r'\b(?:shipments?|inbound|consignments?)\b', message, re.I) and re.search(r'\b(?:our|my|current|this)\b', message, re.I):
                local_kinds.append('read_shipments')
        else:
            kind = integration_request(snapshot, message)
            if kind in {'read_carbon', 'read_shipments'}:
                local_kinds.append(kind)
        for kind in local_kinds:
            read = integration_read(snapshot, kind, message)
            local_parts.append(read['assistant_text'])
            refs.append(f"workspace:{kind}:{snapshot['company_id']}")
        if _QUOTE.search(message) and re.search(r'\b(our|my|current|reviewed|saved)\b', message, re.I):
            offers = [o for o in snapshot.get('offers', []) if isinstance(o, dict)
                      and o.get('status') == 'saved'
                      and company_for_offer(o) == snapshot['company_id']]
            if _COUNT.search(message) or re.search(r'\b(?:what are|list|show)\b.*\b(?:quotes?|offers?)\b', message, re.I):
                local_parts.append(f'{len(offers)} saved reviewed quote records are in this company workspace.')
                refs.extend(o['offer_id'] for o in offers if isinstance(o.get('offer_id'), str))
            elif re.search(r'\b(?:price|unit cost)\b', message, re.I):
                plan = snapshot.get('plan') or {}
                selected = plan.get('selected_offer_id')
                alternatives = [a for a in plan.get('alternatives', []) if a.get('offer_id') == selected]
                if (plan.get('company_id') == snapshot['company_id'] and selected in (plan.get('offer_versions') or {})
                    and (material is None or material == plan.get('material')) and len(alternatives) == 1
                    and alternatives[0].get('unit_price_per_unit') is not None
                    and alternatives[0].get('quantity_unit') == plan.get('quantity_unit')
                    and plan.get('reporting_currency') and plan.get('decision_date')):
                    local_parts.append(f"Selected reviewed plan unit price: {alternatives[0]['unit_price_per_unit']} "
                                       f"{plan['reporting_currency']} per {plan['quantity_unit']} "
                                       f"(plan decision {plan['decision_date']}).")
                    refs.extend([plan['plan_id'], selected])
    if public:
        brief, items = _public_items(snapshot, message, now)
        if items:
            local_parts.append(_cached_answer(brief, items))
            refs.extend(item['item_id'] for item in items)
        elif not fresh:
            history = _history(snapshot, message, now)
            if history:
                record, checked_day = history
                local_parts.append(f"Previously approved research checked {checked_day} (historical, not current): "
                                   + record['answer'])
                refs.extend(record['evidence_refs'][:8])
    if local_parts:
        result = _base(snapshot, 'internal_answer')
        result.update(answer=' '.join(local_parts), evidence_refs=refs)
        if analytics is not None:
            result['analytics'] = analytics
        if public and (fresh or not items):
            query = _public_part(message)
            proposal = _research(query, company, now) if query else None
            if proposal:
                result['follow_up_research'] = proposal
        return result
    if public:
        query = _public_part(message) or message
        proposal = _research(query, company, now)
        if proposal:
            result = _base(snapshot, 'research_proposal')
            result['research_parameters'] = proposal
            result['answer'] = 'Current public evidence is missing or a fresh check was requested. Review the exact public query before searching.'
            return result
        if fresh:
            result = _base(snapshot, 'needs_input')
            result['missing_inputs'] = ['public_query']
            result['answer'] = 'Ask a public question without private workspace details.'
            return result
    return None
