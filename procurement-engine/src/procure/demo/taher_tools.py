"""Read-only bot tools for the price outlook, carbon schemes and country pricing.

The text operator and the Live voice bot call `taher_tool_read`, which reads the same committed config
snapshots and session state as the panels. Nothing is fetched, stored or changed. Answers stay scoped to the
current company and context version, never invent a benefit figure, label synthetic quotes as synthetic and say
when a horizon is a range rather than a prediction.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from .company import get_company

TAHER_TOOLS = ('read_price_outlook', 'read_carbon_schemes', 'read_country_pricing')
RANGES_ONLY = 'No model beat the naive baseline in backtest, so these are ranges, not predictions.'
VERDICT_TEXT = {'PREDICTION': 'prediction', 'RANGE_ONLY': 'range only', 'NO_RELIABLE_FORECAST': 'no reliable forecast'}
_PRICING = re.compile(r'\bbenchmark prices?\b|\bcountry prices?\b|\bprices?\s+(?:by|across|in other)\s+countr', re.I)
_SCHEMES = re.compile(r'\bcarbon (?:schemes?|credits?|programmes?|programs?)\b|\bgreen financ\w*|\bcbam\b', re.I)
_OUTLOOK = re.compile(r'\bprice outlook\b|\bforecast\w*\b|\bprice trends?\b|\boutlook\b', re.I)


def taher_tool_request(message: str) -> str | None:
    """Pick one of the three read-only tools for a text question, or None."""
    if not isinstance(message, str):
        return None
    if _PRICING.search(message):
        return 'read_country_pricing'
    if _SCHEMES.search(message):
        return 'read_carbon_schemes'
    if _OUTLOOK.search(message):
        return 'read_price_outlook'
    return None


def _num(value) -> str:
    """Five significant figures for speech and text; full values stay in the workspace payload."""
    d = Decimal(str(value))
    if d == 0:
        return '0'
    places = max(0, 5 - (d.adjusted() + 1))
    text = f'{round(d, places):,.{places}f}'
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _today(now) -> date:
    return now.date() if isinstance(now, datetime) else now


def _series_row(series: dict) -> dict:
    return {'series_id': series['series_id'], 'name': series['source_series'], 'kind': series['kind'],
            'unit': series['unit'], 'as_of': series['data_date'], 'issue_month': series['issue_month'],
            'stale': series['stale'], 'source': series['source']['publisher'], 'source_url': series['source']['url'],
            'horizons': [{'horizon': h['horizon'], 'target_month': h['target_month'], 'verdict': h['status'],
                          'label': h['label'],
                          **({'p10': h['p10'], 'p50': h['p50'], 'p90': h['p90']} if h['status'] != 'NO_RELIABLE_FORECAST'
                             else {'p10': None, 'p50': None, 'p90': None})}
                         for h in series['horizons']]}


def _series_text(row: dict, title: str) -> str:
    parts = []
    for h in row['horizons']:
        months = f"{h['horizon']} month{'s' if h['horizon'] != 1 else ''}"
        if h['p10'] is None:
            parts.append(f'{months}: no reliable forecast')
        else:
            parts.append(f"{months}: {VERDICT_TEXT[h['verdict']]}, {_num(h['p10'])} to {_num(h['p90'])} {row['unit']}")
    stale = ', older than 45 days' if row['stale'] else ''
    return f"{title} ({row['source']}, data through {row['as_of']}{stale}): " + '; '.join(parts) + '.'


def _price_outlook(value, args: dict, now) -> dict:
    from .price_outlook import get_fx_outlook, get_price_outlook, load_results
    company = get_company(value.company_id)
    results = load_results()
    material_ids = [m['material_id'] for m in company['materials']]
    asked = args.get('material_id')
    if asked is not None and asked not in material_ids:
        return {'status': 'needs_clarification', 'workspace': {},
                'assistant_text': 'Name one of this company materials: ' + ', '.join(material_ids) + '.', 'evidence_ids': []}
    mapped = [row['material_id'] for row in results['material_map'] if row['company_id'] == value.company_id]
    wanted = [asked] if asked else mapped
    today = _today(now)
    rows, lines, notes = [], [], []
    for material_id in wanted:
        view = get_price_outlook(value.company_id, material_id, now=today, context_version=value.context_version,
                                 results=results)
        name = next(m['name'] for m in company['materials'] if m['material_id'] == material_id)
        if view['status'] != 'available':
            notes.append(f"{name}: {view.get('message') or 'No public monthly benchmark series is mapped to this material.'}")
            continue
        row = {**_series_row(view['series']), 'material_id': material_id, 'match': view['match'], 'note': view['note']}
        rows.append(row)
        proxy = ' (proxy benchmark)' if view['match'] == 'proxy' else ''
        lines.append(_series_text(row, f'{name}{proxy}'))
    if not asked:
        fx = get_fx_outlook(value.company_id, now=today, context_version=value.context_version, results=results)
        for item in fx['currencies']:
            if item['status'] == 'available':
                row = _series_row(item['series'])
                rows.append(row)
                lines.append(_series_text(row, f"{item['currency']} per USD"))
            else:
                notes.append(f"{item['currency']}: no FX history available.")
    verdicts = {h['verdict'] for row in rows for h in row['horizons']}
    text = ' '.join(lines + notes)
    if rows and 'PREDICTION' not in verdicts:
        text += ' ' + RANGES_ONLY
    text += ' Projection with uncertainty, not a recommendation.'
    return {'status': 'completed', 'assistant_text': text.strip(),
            'workspace': {'price_outlook': {'company_id': value.company_id, 'context_version': value.context_version,
                                            'series': rows, 'notes': notes}},
            'evidence_ids': [f"prediction_results_v1:{row['series_id']}" for row in rows]}


def _stored_schemes(value) -> dict | None:
    stored = getattr(value, 'carbon_schemes', None)
    if stored and (stored.get('company_id'), stored.get('context_version')) == (value.company_id, value.context_version):
        return stored.get('result')
    return None


def _carbon_schemes(value) -> dict:
    from procure.carbon.schemes import find_schemes, load_schemes
    company = get_company(value.company_id)
    stored = _stored_schemes(value)
    result = stored if stored is not None else find_schemes(load_schemes(), company, {})
    basis = 'your entered inputs' if stored is not None else 'catalogue'
    rows, parts = [], []
    for match in result['matches']:
        source = {'official_url': match['official_url'], 'checked_on': match['checked_on']}
        benefit = match.get('benefit')
        row = {'scheme_id': match['scheme_id'], 'official_name': match['official_name'], 'country': match['country'],
               'publisher': match['publisher'], 'official_url': match['official_url'], 'checked_on': match['checked_on'],
               'offers': match['offers'], 'eligibility': match['eligibility'], 'benefit_status': match['benefit_status']}
        title = f"{match['official_name']} ({match['country']}, {match['publisher']})"
        if benefit and match['official_url'] and match['checked_on']:
            row['benefit'] = {'amount': benefit['amount'], 'currency': benefit['currency'], 'label': benefit['label'],
                              'arithmetic': benefit['arithmetic'], 'inputs': benefit['inputs'], 'source': source}
            parts.append(f"{title}: {benefit['amount']} {benefit['currency']}, {benefit['label']}, from your inputs "
                         f"({benefit['arithmetic']}; source {match['official_url']}, checked {match['checked_on']}).")
        else:
            row['benefit'] = 'not stated'
            if match['benefit_status'] == 'available':
                needed = match.get('missing_inputs') or [field['name'] for field in match.get('input_fields', [])]
                row['inputs_needed'] = needed
                parts.append(f"{title}: benefit not stated; enter {', '.join(needed)} in the Emissions tab to calculate it.")
            else:
                row['reason'] = match.get('benefit_reason')
                parts.append(f"{title}: benefit not stated. {match.get('benefit_reason') or ''}".strip())
        rows.append(row)
    head = (f"Schemes for {company['name']} ({result['label']}; every match is possibly eligible, check conditions):"
            if rows else result.get('no_match_reason') or 'No scheme in the registry matches this company.')
    return {'status': 'completed', 'assistant_text': ' '.join([head] + parts),
            'workspace': {'carbon_schemes': {'company_id': value.company_id, 'context_version': value.context_version,
                                             'basis': basis, 'registry_version': result['registry_version'],
                                             'checked_on': result['checked_on'], 'schemes': rows,
                                             'not_verified_countries': result.get('not_verified_countries', {})}},
            'evidence_ids': [f"carbon_schemes_v1:{row['scheme_id']}" for row in rows]}


def _country_pricing(value, now) -> dict:
    from .country_pricing import QUOTE_NOTE, build_country_pricing, load_benchmarks, load_fx_snapshot
    company = get_company(value.company_id)
    fx = list((getattr(value, 'news', None) or {}).get('fx') or [])
    offers = list((getattr(value, 'offers', None) or {}).values())
    view = build_country_pricing(company, offers, fx, load_benchmarks(), today=_today(now),
                                 fx_snapshot=load_fx_snapshot(), context_version=value.context_version)
    currency = view['reporting_currency']
    parts = []
    for row in view['benchmarks']:
        if row['status'] != 'benchmark':
            parts.append(f"{row['name']}: no public benchmark.")
            continue
        text = f"{row['name']}: {row['value']} {row['currency']} per {row['per_unit']} ({row['series']}, {row['month']}"
        text += ', stale)' if row['stale'] else ')'
        converted = next((c for c in row['conversions'] if c['currency'] == currency and c['status'] == 'converted'), None)
        if converted:
            text += f", about {converted['amount']} {currency} per {row['per_unit']} at {converted['formula']}"
        parts.append(text + '.')
    source = view['source']
    head = (f"Public benchmarks for {company['name']} ({source['publisher']} Pink Sheet, monthly averages, "
            'not quotes for your grade, origin or terms):')
    return {'status': 'completed', 'assistant_text': ' '.join([head] + parts + [f'{QUOTE_NOTE}.']),
            'workspace': {'country_pricing': view},
            'evidence_ids': [f"commodity_benchmarks_v1:{row['material_id']}" for row in view['benchmarks']
                             if row['status'] == 'benchmark']}


def taher_tool_read(value, name: str, args: dict, *, now) -> dict:
    """Return {'status', 'assistant_text', 'workspace': {...}, 'evidence_ids': [...]} for one read-only tool."""
    if name not in TAHER_TOOLS:
        raise ValueError(f'Unknown tool {name}.')
    args = dict(args or {})
    if name == 'read_price_outlook':
        if not set(args) <= {'material_id'}:
            raise ValueError('read_price_outlook takes only an optional material_id.')
        return _price_outlook(value, args, now)
    if args:
        raise ValueError(f'{name} takes no arguments.')
    return _carbon_schemes(value) if name == 'read_carbon_schemes' else _country_pricing(value, now)


def taher_operator_outcome(snapshot: dict, message: str) -> dict | None:
    """Text operator outcome for a matching question, in the operator's deterministic outcome format."""
    name = taher_tool_request(message)
    if name is None:
        return None
    company = snapshot.get('company') or get_company(snapshot['company_id'])
    stored = snapshot.get('carbon_schemes')
    value = SimpleNamespace(company_id=snapshot['company_id'], context_version=snapshot['context_version'],
                            carbon_schemes={'company_id': snapshot['company_id'], 'context_version': snapshot['context_version'],
                                            'result': stored} if stored else None,
                            offers={offer['offer_id']: offer for offer in snapshot.get('offers') or []},
                            news=snapshot.get('news'))
    args = {}
    if name == 'read_price_outlook':
        lowered = message.lower()
        named = [m['material_id'] for m in company['materials']
                 if re.search(rf"\b{re.escape(m['material_id'].lower())}\b", lowered) or m['name'].lower() in lowered]
        if len(named) == 1:
            args['material_id'] = named[0]
    payload = taher_tool_read(value, name, args, now=datetime.now(timezone.utc))
    return {'status': payload['status'], 'assistant_text': payload['assistant_text'], 'workspace': payload['workspace'],
            'agent_steps': [{'agent': 'workspace', 'role': 'deterministic', 'status': 'completed', 'model': 'none',
                             'provider_call': 'none', 'tool': name,
                             'summary': 'Read committed snapshots and current company state; nothing changed.'}],
            'evidence_ids': [], 'candidate': None, 'proposal_kind': None, 'parameters': None, 'utility': None}
