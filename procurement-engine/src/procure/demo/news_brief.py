"""Pure, bounded public-news projection for workspace answers.

The input is the same server-owned company view shown by News Workspace.
Headlines are untrusted metadata, and this module never fetches or executes them.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import urlsplit

from .news import filter_material_homonyms


MAX_BRIEF_BYTES = 24_000
_RESEARCH = re.compile(r'\b(?:search|browse|research|google|internet|web|look\s+up)\b|'
                       r'\bfind\s+(?:sources?|reports?)\b|\bconfirm\s+research\b', re.I)
_ACTION = re.compile(r'\b(?:buy|purchase|order|prepare|draft|send|create|set|change|update|'
                     r'open|navigate|watch|monitor|track|alert|refresh)\b', re.I)
_NEWS = re.compile(r'\b(?:news|headlines?|world|global|public\s+events?|world\s+events?|'
                   r'current\s+events?)\b', re.I)
_BUSINESS = re.compile(r'\b(?:business|company|procurement|supply|suppliers?|materials?|'
                       r'industr(?:y|ies)|trade|our|my|us)\b', re.I)
_BRIEF = re.compile(r'\b(?:brief|update|latest|what(?:\s+is|\s+are|\s+has|\s+is\s+happening|s\s+happening)|'
                    r'whats\s+happening|happening|look\s+at|affect|impact|block|risk)\b', re.I)
_KIND_ORDER = {'direct_material': 0, 'route': 1, 'currency': 2, 'sector': 3}
_STRENGTH_ORDER = {'strong': 0, 'related': 1}
_WATCH_ORDER = {'direct': 0, 'indirect': 1, 'background': 2}
_CAUTION = ('Public titles and exposure labels are untrusted headline metadata. '
            'All business impacts are conditional; verify the original source and current '
            'supplier, route, shipment, and price evidence before changing a plan.')


def is_workspace_news_question(message: str) -> bool:
    """Recognize a read-only briefing request without intercepting external research."""
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 1200:
        return False
    normalized = message.replace('’', "'").replace("'", '')
    if _RESEARCH.search(normalized) or _ACTION.search(normalized):
        return False
    return bool(_NEWS.search(normalized) and _BRIEF.search(normalized) and
                (_BUSINESS.search(normalized) or re.search(r'\bbrief\s+me\b', normalized, re.I)
                 or re.search(r'\b(?:what\s+is|whats)\s+happening\s+in\s+the\s+world\b',
                              normalized, re.I)))


def _text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    clean = ''.join(char if char.isprintable() else ' ' for char in value).strip()
    return clean[:limit] or None


def _stamp(value, now: datetime) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.astimezone(timezone.utc) > now.astimezone(timezone.utc):
            return None
    except (ValueError, OverflowError):
        return None
    return value


def _url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 1200:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme != 'https' or not host or parsed.username or parsed.password or
                parsed.port not in (None, 443) or any(ord(char) < 33 for char in value) or
                host.lower() == 'localhost' or host.lower().endswith(('.local', '.localhost', '.internal'))):
            return None
    except ValueError:
        return None
    return value


def _sources(rows, now, cap=8):
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:cap]:
        if not isinstance(row, dict):
            continue
        result.append({'source_id': _text(row.get('source_id'), 80),
                       'name': _text(row.get('name'), 100),
                       'state': _text(row.get('state'), 32),
                       'last_checked_at': _stamp(row.get('last_checked_at'), now)})
    return result


def _owned_ids(rows, key):
    return {row[key] for row in rows if isinstance(row, dict) and isinstance(row.get(key), str)}


def _owned_list(value, allowed, cap=6):
    if not isinstance(value, list):
        return []
    return [entry for entry in value[:cap] if isinstance(entry, str) and entry in allowed]


def _company_headlines(company, rows, now):
    if not isinstance(rows, list):
        return []
    materials = _owned_ids(company.get('materials', []), 'material_id')
    routes = _owned_ids(company.get('routes', []), 'route_id')
    currencies = {code for code in company.get('currencies', []) if isinstance(code, str)}
    selected = []
    for item in rows[:80]:
        if (not isinstance(item, dict) or item.get('synthetic') is not False or
                item.get('relevance_strength') in {'background', 'unmatched'}):
            continue
        kind = item.get('relevance_kind')
        if kind not in _KIND_ORDER:
            continue
        title, url = _text(item.get('title'), 220), _url(item.get('url'))
        if not title or not url:
            continue
        matched_materials = filter_material_homonyms(
            title, _owned_list(item.get('matched_materials'), materials))
        matched_routes = _owned_list(item.get('matched_routes'), routes)
        matched_currencies = _owned_list(item.get('matched_currencies'), currencies)
        if (kind == 'direct_material' and not matched_materials or
                kind == 'route' and not matched_routes or
                kind == 'currency' and not matched_currencies or
                kind == 'sector' and not isinstance(item.get('relevance_reason'), str)):
            continue
        row = {'item_id': _text(item.get('item_id'), 128), 'title': title, 'url': url,
               'publisher': _text(item.get('publisher'), 100),
               'provider': _text(item.get('provider'), 100),
               'source_id': _text(item.get('source_id'), 80),
               'published_at': _stamp(item.get('published_at'), now),
               'retrieved_at': _stamp(item.get('retrieved_at'), now),
               'first_seen_at': _stamp(item.get('first_seen_at'), now),
               'exposure': {'kind': kind, 'matched_materials': matched_materials,
                            'matched_routes': matched_routes,
                            'matched_currencies': matched_currencies,
                            'assessment': 'Conditional headline relevance; operational impact is unverified.',
                            'next_action': 'Read the original source and check current supplier, route, shipment, or price evidence.'}}
        for key, limit in (('source_reliability', 40), ('reliability_reason', 200),
                           ('implication_confidence', 40), ('relevance_strength', 40)):
            if key in item:
                value = _text(item[key], limit)
                if value is not None:
                    row[key] = value
        if row['item_id'] and row['retrieved_at']:
            selected.append(row)
    selected.sort(key=lambda row: (_KIND_ORDER[row['exposure']['kind']],
                                  _STRENGTH_ORDER.get(row.get('relevance_strength'), 2),
                                  -_epoch(row['published_at']), row['item_id']))
    return selected[:6]


def _epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if value else 0


def _global_watch(company, watch, now):
    result = {'status': 'unavailable', 'attempted_at': None, 'success_at': None,
              'sources': [], 'events': []}
    if not isinstance(watch, dict):
        return result
    result.update(status=_text(watch.get('status'), 32) or 'unavailable',
                  attempted_at=_stamp(watch.get('attempted_at'), now),
                  success_at=_stamp(watch.get('success_at'), now),
                  sources=_sources(watch.get('sources'), now, cap=4))
    materials = _owned_ids(company.get('materials', []), 'material_id')
    routes = _owned_ids(company.get('routes', []), 'route_id')
    events = watch.get('events')
    if not isinstance(events, list):
        return result
    selected = []
    for item in events[:18]:
        if not isinstance(item, dict):
            continue
        exposure = item.get('exposure')
        if not isinstance(exposure, dict):
            continue
        kind = exposure.get('kind')
        if kind not in _WATCH_ORDER:
            continue
        matched_materials = _owned_list(exposure.get('matched_materials'), materials)
        matched_routes = _owned_list(exposure.get('matched_routes'), routes)
        if kind == 'direct' and not (matched_materials or matched_routes):
            continue
        title, url = _text(item.get('title'), 220), _url(item.get('url'))
        if not title or not url:
            continue
        row = {'item_id': _text(item.get('item_id'), 128), 'title': title, 'url': url,
               'publisher': _text(item.get('publisher'), 100),
               'provider': _text(item.get('provider'), 100),
               'source_id': _text(item.get('source_id'), 80),
               'topic': _text(item.get('topic'), 32),
               'published_at': _stamp(item.get('published_at'), now),
               'retrieved_at': _stamp(item.get('retrieved_at'), now),
               'first_seen_at': _stamp(item.get('first_seen_at'), now),
               'exposure': {'kind': kind, 'matched_materials': matched_materials,
                            'matched_routes': matched_routes,
                            'reason': _text(exposure.get('reason'), 240),
                            'next_action': _text(exposure.get('next_action'), 200) or
                                           'Check the original source and current company evidence.'}}
        if row['item_id'] and row['retrieved_at']:
            selected.append(row)
    selected.sort(key=lambda row: (_WATCH_ORDER[row['exposure']['kind']],
                                  -_epoch(row['published_at']), row['item_id']))
    result['events'] = selected[:4]
    return result


def build_news_brief(company: dict, view: dict | None, *, now: datetime) -> dict:
    """Project one company-owned News Workspace view into model-safe public facts."""
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    company_id = _text(company.get('company_id'), 80)
    result = {'availability': 'unavailable', 'company_id': company_id,
              'company_name': _text(company.get('name'), 120),
              'sector': _text(company.get('sector'), 120),
              'checked_at': None, 'cache_state': 'unavailable',
              'collection': {'status': None, 'mode': None, 'attempted_at': None,
                             'news_success_at': None, 'fx_success_at': None},
              'sources': [], 'headlines': [],
              'global_watch': {'status': 'unavailable', 'attempted_at': None,
                               'success_at': None, 'sources': [], 'events': []},
              'data_gaps': [], 'caution': _CAUTION}
    if not isinstance(view, dict):
        result['data_gaps'].append('News coverage is unavailable for this company.')
        return result
    if view.get('company_id') not in (None, company_id):
        result['data_gaps'].append('News view belongs to another company; no headlines were used.')
        return result
    collection = view.get('collection') if isinstance(view.get('collection'), dict) else {}
    result['checked_at'] = _stamp(view.get('checked_at'), now)
    result['cache_state'] = _text(view.get('cache_state'), 32) or 'unavailable'
    result['collection'] = {'status': _text(collection.get('status'), 32),
                            'mode': _text(collection.get('mode'), 32),
                            'attempted_at': _stamp(collection.get('attempted_at'), now),
                            'news_success_at': _stamp(collection.get('news_success_at'), now),
                            'fx_success_at': _stamp(collection.get('fx_success_at'), now)}
    result['sources'] = _sources(view.get('sources'), now)
    result['headlines'] = _company_headlines(company, view.get('items'), now)
    result['global_watch'] = _global_watch(company, view.get('global_watch'), now)
    state = result['cache_state']
    watch_state = result['global_watch']['status']
    if state in {'fresh', 'cached'} and watch_state in {'fresh', 'partial'}:
        result['availability'] = 'available' if watch_state == 'fresh' else 'partial'
    elif state in {'fresh', 'cached', 'stale', 'empty'} or watch_state in {'fresh', 'partial', 'stale'}:
        result['availability'] = 'partial'
    if result['availability'] == 'available' and (
            not (result['headlines'] or result['global_watch']['events']) or
            any(source['state'] in {'stale', 'unavailable'}
                for source in result['sources'] + result['global_watch']['sources'])):
        result['availability'] = 'partial'
    if state in {'stale', 'unavailable', 'offline'}:
        result['data_gaps'].append(f'Company news state is {state}; check original publication and source times.')
    if any(source['state'] in {'stale', 'unavailable'} for source in result['sources']):
        result['data_gaps'].append('One or more company news sources are stale or unavailable.')
    if watch_state in {'stale', 'unavailable', 'offline'}:
        result['data_gaps'].append(f'Global Watch state is {watch_state}; coverage may be incomplete.')
    if not result['headlines']:
        result['data_gaps'].append('No company-matched public headline is available in this view.')
    if not result['global_watch']['events']:
        result['data_gaps'].append('No Global Watch event is available in this view.')
    result['data_gaps'] = result['data_gaps'][:5]
    # Live serializes with ensure_ascii=True. Escaped non-ASCII can be much
    # larger than the UTF-8 input, so trim the exact downstream representation.
    if len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > MAX_BRIEF_BYTES:
        result['availability'] = 'partial'
        result['data_gaps'] = (result['data_gaps'][:4] +
                               ['Additional public items were omitted to bound the brief.'])
    while len(json.dumps(result, ensure_ascii=True).encode('utf-8')) > MAX_BRIEF_BYTES:
        candidates = [result['headlines'], result['global_watch']['events'],
                      result['sources'], result['global_watch']['sources']]
        removable = [rows for rows in candidates if len(rows) > 1]
        if not removable:
            removable = [rows for rows in candidates if rows]
        if not removable:
            raise ValueError('News brief exceeded its size limit')
        max(removable, key=lambda rows: len(json.dumps(rows[-1], ensure_ascii=True))).pop()
    return result
