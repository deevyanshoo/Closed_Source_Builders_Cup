"""Bounded carbon conversation over the current owned plan and cited calculator."""
from __future__ import annotations

from copy import copy, deepcopy
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from procure.carbon import load_registry
from procure.carbon.assessment import current_assessment

from .carbon_api import estimate_current
from .company import list_companies
from .presentation import supplier_display_name
from .quote_service import company_for_offer
from .sessions import SessionError

_CARBON = re.compile(r'\b(?:carbon|emissions?|footprint|co2e)\b', re.I)
_EXTERNAL = re.compile(r'\b(?:search|research|browse|web|internet|latest|news|headlines?|policy|regulations?)\b', re.I)
_COMMAND = re.compile(r'\b(?:calculate|estimate|compute|assess|compare|work\s+out|measure|reduce|lower|cut)\b', re.I)
_QUESTION = re.compile(r'\b(?:what(?:\s+is|\s+are|\s+would\s+be)|how\s+much)\b', re.I)
_METHOD = re.compile(r'\b(?:how\s+(?:is|was|were|are)\b|why|explain|source|factor|method|basis|provenance)\b', re.I)
_NEGATE_ACTION = re.compile(r'\b(?:do\s+not|don.t|never|without|no)\s+(?:yet\s+)?(?:calculate|estimate|compute|assess|measure|cancel|stop)\b', re.I)
_CANCEL = re.compile(r'\b(?:cancel|never\s+mind|stop)\b', re.I)
_DECIMAL = r'(?:0|[1-9][0-9]{0,8})(?:\.[0-9]{1,6})?'
_MODE_WORD = r'(?:sea\s+container|sea\s+bulk|road(?:\s+hgv)?|truck|rail(?:\s+freight)?|air(?:\s+freight)?|sea)'
_DISTANCE_WORD = r'(?<![\w.-])' + _DECIMAL + r'\s*(?:km|kilomet(?:er|re)s?)\b'
_DISTANCE = re.compile(r'(?<![\w.-])(' + _DECIMAL + r')\s*(?:km|kilomet(?:er|re)s?)\b', re.I)
_MODE = re.compile(r'\b(' + _MODE_WORD + r')\b', re.I)
_LEG = re.compile(
    r'\b(?P<forward_mode>' + _MODE_WORD + r')\b(?:\s+(?:transport|freight))?\s*(?::|for)?\s*'
    r'(?P<forward_distance>' + _DECIMAL + r')\s*(?:km|kilomet(?:er|re)s?)\b'
    r'|(?P<reverse_distance>' + _DISTANCE_WORD + r')\s+by\s+'
    r'(?P<reverse_mode>' + _MODE_WORD + r')\b', re.I)
_NEGATED_FREIGHT = re.compile(r'\b(?:do\s+not|don.t|not|without|never|no|minus|negative)\b', re.I)
_MODES = {'sea container': 'sea_container', 'sea bulk': 'sea_bulk', 'sea': 'sea_container',
          'road': 'road_hgv', 'road hgv': 'road_hgv', 'truck': 'road_hgv',
          'rail': 'rail_freight', 'rail freight': 'rail_freight',
          'air': 'air_freight', 'air freight': 'air_freight'}


def is_estimate_request(message: str, *, has_assessment: bool = False) -> bool:
    if not _CARBON.search(message):
        return False
    if _EXTERNAL.search(message):
        return False
    if _NEGATE_ACTION.search(message):
        return True  # Intercept denial before a model can suggest an action.
    if _METHOD.search(message) and not re.search(r'\bhow\s+can\s+we\s+(?:reduce|lower|cut)\b', message, re.I):
        return False
    return bool(_COMMAND.search(message) or (not has_assessment and _QUESTION.search(message)))


def _named_materials(company, message):
    words = message.casefold()
    return {row['material_id'] for row in company['materials']
            if re.search(r'(?<!\w)' + re.escape(row['material_id'].casefold()) + r'(?!\w)', words)
            or re.search(r'(?<!\w)' + re.escape(row['name'].casefold()) + r'(?!\w)', words)}


def is_pending_reply(message: str, pending=None, company=None) -> bool:
    if pending and pending.get('kind') == 'material_selection' and company:
        return bool(_named_materials(company, message) or _CANCEL.search(message))
    return bool(re.search(r'\b(?:km|kilomet(?:er|re)s?|shipping\s+mass|cancel|never\s+mind|stop)\b',
                          message, re.I))


def _owned_versions(value):
    return {offer_id: offer['version'] for offer_id, offer in value.offers.items()
            if offer.get('status') == 'saved' and company_for_offer(offer) == value.company_id}


def _current(pending, value, plan, now):
    if (not isinstance(pending, dict) or pending.get('session_id') != value.session_id
            or pending.get('company_id') != value.company_id
            or pending.get('context_version') != value.context_version
            or pending.get('expires_at', '') <= now.isoformat()):
        return False
    if pending.get('kind') == 'material_selection':
        return pending.get('offer_versions') == _owned_versions(value)
    return (pending.get('kind') == 'freight' and plan is not None
            and pending.get('plan_id') == plan.get('plan_id')
            and pending.get('offer_versions') == plan.get('offer_versions'))


def _saved_materials(value, company):
    fixtures = {row['material_id']: set(row['fixture_ids'])
                for row in company.get('quote_coverage', {}).get('scenarios', [])}
    return {material for material, ids in fixtures.items() if any(
        offer.get('status') == 'saved' and offer.get('fixture_id') in ids
        and company_for_offer(offer) == value.company_id
        for offer in value.offers.values())}


def _selection(value, company, now):
    return {'kind': 'material_selection', 'session_id': value.session_id,
            'company_id': value.company_id, 'context_version': value.context_version,
            'expires_at': min(now + timedelta(minutes=10), value.expires_at).isoformat(),
            'plan_id': None, 'offer_versions': _owned_versions(value),
            'material_ids': [row['material_id'] for row in company['materials']],
            'targets': [], 'legs_by_offer': {}, 'mass_by_offer': {}}


def _freight_state(value, plan, now, targets, previous):
    inputs = (previous or {}).get('inputs') or {}
    return {'kind': 'freight', 'session_id': value.session_id,
            'company_id': value.company_id, 'context_version': value.context_version,
            'expires_at': min(now + timedelta(minutes=10), value.expires_at).isoformat(),
            'plan_id': plan['plan_id'], 'offer_versions': deepcopy(plan['offer_versions']),
            'material_ids': [], 'targets': targets,
            'legs_by_offer': deepcopy(inputs.get('legs_by_offer') or {}),
            'mass_by_offer': deepcopy(inputs.get('mass_by_offer') or {})}


def _foreign_supplier(message, plan):
    owned = {supplier_display_name(row['supplier']).casefold() for row in plan['alternatives']}
    lower = message.casefold()
    if any(company['company_id'] != plan.get('company_id') and company['name'].casefold() in lower
           for company in list_companies()):
        return True
    return any((name := supplier_display_name(supplier['name']).casefold()) not in owned
               and name in lower for company in list_companies()
               for supplier in company.get('suppliers', []))


def _parse_freight(message, pending, plan):
    owned = {row['offer_id']: row for row in plan['alternatives']}
    labelled = re.match(r'\s*([^:]{1,100})\s*:', message)
    labels = set(owned) | {supplier_display_name(row['supplier']) for row in plan['alternatives']} | set(_MODES)
    if labelled and labelled.group(1).strip().casefold() not in {item.casefold() for item in labels}:
        raise SessionError('invalid_carbon_input', 'That offer is outside the current reviewed comparison.', 422)
    if _foreign_supplier(message, plan):
        raise SessionError('invalid_carbon_input', 'That supplier is outside the current reviewed comparison.', 422)
    named = [row for row in plan['alternatives'] if row['offer_id'] in message or
             supplier_display_name(row['supplier']).casefold() in message.casefold()]
    if len(named) > 1:
        raise SessionError('invalid_carbon_input', 'Give freight for one reviewed supplier at a time.', 422)
    remaining = [row for row in plan['alternatives']
                 if row['offer_id'] in pending['targets'] and row['offer_id'] not in pending['legs_by_offer']]
    row = named[0] if named else remaining[0] if remaining else None
    if row is None or row['offer_id'] not in pending['targets']:
        raise SessionError('invalid_carbon_input', 'This follow-up is bound to the selected reviewed supplier.', 422)
    if _NEGATED_FREIGHT.search(message) or re.search(
            r'-\s*\d+(?:\.\d+)?\s*(?:km|kilomet)', message, re.I):
        return None
    legs = list(_LEG.finditer(message))
    if not legs or len(legs) > 5 or len(legs) != len(_MODE.findall(message)) or len(legs) != len(_DISTANCE.findall(message)):
        return None
    parsed = [{'mode': _MODES[re.sub(r'\s+', ' ',
                                   (match.group('forward_mode') or match.group('reverse_mode')).casefold())],
               'distance_km': match.group('forward_distance') or
                              _DISTANCE.match(match.group('reverse_distance')).group(1)}
              for match in legs]
    mass = None
    if plan.get('quantity_unit') not in {'kg', 'tonne'}:
        match = re.search(r'\b(?:shipping\s+)?mass\s*(?:is|:|of)?\s*(' + _DECIMAL +
                          r')\s*kg\b.{0,120}\bbasis\s*(?:is|:)?\s*([^.;\n]{1,100})', message, re.I)
        if match is None:
            return None
        mass = {'mass_kg': match.group(1), 'basis': match.group(2).strip()}
    return row['offer_id'], parsed, mass


def _prompt(plan, pending):
    for offer_id in pending['targets']:
        if offer_id not in pending['legs_by_offer']:
            row = next(row for row in plan['alternatives'] if row['offer_id'] == offer_id)
            mass = (' Include shipping mass in kg and its stated basis.'
                    if plan.get('quantity_unit') not in {'kg', 'tonne'} else '')
            return (f"For {supplier_display_name(row['supplier'])}, what freight mode and distance in km "
                    f"should I use (up to five explicit legs)?{mass}")
    return ''


def _spoken_number(value):
    if value is None:
        return None
    try:
        number = Decimal(value)
        if number and abs(number) < Decimal('0.01'):
            return format(number.normalize(), 'f')
        return format(number.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP).normalize(), 'f')
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def _summary(comparison, plan, reused_at, prompt):
    selected = next((row['estimate'] for row in comparison['suppliers']
                     if row['offer_id'] == plan.get('selected_offer_id')), None)
    if selected is None:
        text = 'The reviewed plan has no feasible selected purchase; supplier scenarios are in the saved assessment.'
    else:
        material = _spoken_number(selected['material']['kg_co2e'])
        freight = _spoken_number(selected['transport']['kg_co2e'])
        text = (f"Saved the selected purchase assessment: material "
                f"{material + ' kg CO2e' if material is not None else 'unavailable'}; "
                f"freight {freight + ' kg CO2e' if freight is not None else 'not entered'}.")
        if selected['total_kg_co2e'] is not None:
            text += f" Supported total {_spoken_number(selected['total_kg_co2e'])} kg CO2e."
        else:
            text += ' No supported whole-scenario total.'
    if reused_at:
        text += f' Reused buyer-entered freight from the current assessment saved {reused_at}.'
    return text + (' ' + prompt if prompt else '')


def estimate_turn(value, message, *, active_plan, validate_plan_current, compare_quotes,
                  company, now):
    """Save a material estimate now; collect only explicit, plan-bound freight later."""
    plan = active_plan(value)
    pending = value.pending_carbon
    request = is_estimate_request(message, has_assessment=value.carbon_assessment is not None)
    if _NEGATE_ACTION.search(message):
        return {'status': 'completed', 'assistant_text': 'No carbon calculation or cancellation was applied.',
                'comparison': None, 'changed': False}
    if pending is not None and not _current(pending, value, plan, now):
        value.pending_carbon = None
        pending = None
        if not request:
            raise SessionError('stale_plan', 'The carbon conversation expired or the plan changed. Ask for a new estimate.', 409)
    if _CANCEL.search(message) and pending:
        value.pending_carbon = None
        return {'status': 'completed', 'assistant_text': 'Carbon follow-up cancelled; the saved assessment remains available.',
                'comparison': None, 'changed': True}

    staged = None
    if plan is None:
        named = _named_materials(company, message)
        saved = _saved_materials(value, company)
        if not named and pending and pending['kind'] == 'material_selection':
            return {'status': 'needs_clarification', 'assistant_text':
                    'Name one current company material to assess.', 'comparison': None, 'changed': False}
        if not named and len(saved) == 1:
            named = saved
        if len(named) != 1:
            value.pending_carbon = _selection(value, company, now)
            return {'status': 'needs_clarification', 'assistant_text':
                    'Which current company material should I assess?', 'comparison': None, 'changed': True}
        staged = copy(value)
        for key in ('offers', 'idempotency', 'buyer_draft', 'last_plan', 'plan_request',
                    'recovery', 'rfq', 'pending_proposal', 'carbon_assessment'):
            setattr(staged, key, deepcopy(getattr(value, key)))
        try:
            plan = compare_quotes(staged, named.pop())
        except SessionError as error:
            if error.code == 'quotes_unavailable':
                return {'status': 'needs_clarification', 'assistant_text':
                        'No reviewed quote is saved for that material. Attach a quote or ask me to prepare sample quotes for review.',
                        'comparison': None, 'changed': False}
            raise
        validate_plan_current(staged, plan)
    else:
        validate_plan_current(value, plan)

    previous = current_assessment(value.carbon_assessment, company_id=value.company_id, plan=plan)
    explicit = bool(re.search(r'\b(?:compare|across|all\s+suppliers|alternatives?|reduce|lower|cut)\b', message, re.I))
    ordered = ([plan['selected_offer_id']] if plan.get('selected_offer_id') else []) + [
        row['offer_id'] for row in plan['alternatives'] if row['offer_id'] != plan.get('selected_offer_id')]
    targets = (ordered if explicit else
               [plan['selected_offer_id']] if plan.get('selected_offer_id') else [])
    working = (deepcopy(pending) if pending and pending.get('kind') == 'freight' and not request
               else _freight_state(value, plan, now, targets, previous))
    if is_pending_reply(message, working, company) and not request and pending and pending.get('kind') == 'freight':
        parsed = _parse_freight(message, working, plan)
        if parsed is None:
            return {'status': 'needs_clarification', 'assistant_text':
                    'Give each freight leg as a mode and distance in km; include shipping mass and basis when needed.',
                    'comparison': None, 'changed': False}
        offer_id, legs, mass = parsed
        working['legs_by_offer'][offer_id] = legs
        if mass is not None:
            working['mass_by_offer'][offer_id] = mass
    reused_at = (previous['created_at'][:10] if previous and previous['inputs'].get('legs_by_offer')
                 and not (pending and pending.get('kind') == 'freight' and not request) else None)
    try:
        result = estimate_current(staged or value, plan=plan,
            legs_by_offer=working['legs_by_offer'], mass_by_offer=working['mass_by_offer'],
            registry=load_registry(), now=now)
    except (OSError, ValueError, KeyError):
        raise SessionError('carbon_unavailable', 'Carbon factors are unavailable in this deployment.', 503) from None
    if staged is not None:
        value.last_plan = deepcopy(staged.last_plan)
        value.plan_request = deepcopy(staged.plan_request)
        value.buyer_draft = deepcopy(staged.buyer_draft)
        value.selected_material_id = staged.selected_material_id
        value.carbon_assessment = deepcopy(staged.carbon_assessment)
    prompt = _prompt(plan, working) if working['targets'] else ''
    value.pending_carbon = working if prompt else None
    return {'status': 'completed', 'assistant_text': _summary(result, plan, reused_at, prompt),
            'comparison': result, 'changed': True}
