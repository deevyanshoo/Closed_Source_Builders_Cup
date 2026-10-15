"""Deterministic company fit ordering for headline metadata."""
from datetime import datetime, timezone
import re

_EVENT = ('strike', 'shutdown', 'shut-in', 'closure', 'closed', 'disruption',
          'shortage', 'curb', 'ban', 'storm', 'hurricane', 'flood', 'sanction',
          'outage', 'delay', 'halt', 'suspend', 'fire', 'explosion')
_OPERATING = ('production', 'supply', 'shipment', 'shipping', 'export', 'import',
              'refinery', 'refining', 'cracker', 'feedstock', 'plant', 'factory',
              'port', 'freight', 'terminal', 'pipeline', 'inventory', 'price')
_CONSUMER = ('farmer', 'farmers', 'household', 'home heating', 'toy', 'lego',
             'cookout', 'barbecue', 'residential')
_MARKET_CATALYST = ('stock', 'stocks', 'shares', 'share price', 'price target',
                    'analyst', 'volatility', 'coinmarketcap', 'crypto', 'token',
                    'ticker', 'nasdaq', 'nyse')
_CHEMICAL_CATALYST = ('chemical', 'petrochemical', 'process', 'industrial',
                      'refinery', 'refining', 'cracker', 'feedstock', 'plant',
                      'reactor', 'catalytic', 'synthesis', 'polymer', 'methanol',
                      'naphtha', 'hydrogen', 'ethylene', 'ammonia')
_SECTOR = {'petrochemical blending': ('petrochemical', 'chemical', 'refinery', 'cracker', 'feedstock'),
           'electrical cable manufacturing': ('cable', 'electrical', 'wire', 'conductor')}
_ORDER = {'strong': 3, 'related': 2, 'background': 1, 'unmatched': 0}


def _has(text, terms):
    return any(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text) for term in terms)


def _date(item, as_of):
    # First-seen time is not a publication date and must not make undated news look new.
    raw = item.get('published_at')
    if not raw:
        return (0, 0)
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return (0, 0)
        parsed = parsed.astimezone(timezone.utc)
        if parsed > as_of:
            return (-1, 0)
        return (1, parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return (0, 0)


def rank_item(item, company):
    item = dict(item)
    text = str(item.get('title') or '').casefold()
    kind = item.get('relevance_kind', 'unmatched')
    material = bool(item.get('matched_materials'))
    route = bool(item.get('matched_routes'))
    sector = _has(text, _SECTOR.get(str(company.get('sector', '')).casefold(), ()))
    event = _has(text, _EVENT)
    operation = _has(text, _OPERATING)
    incidental = _has(text, _CONSUMER) and not sector and not route
    metaphorical_catalyst = (set(item.get('matched_materials') or ()) == {'catalyst'}
        and _has(text, _MARKET_CATALYST) and not _has(text, _CHEMICAL_CATALYST))
    if kind == 'unmatched':
        strength, reason = 'unmatched', 'No recorded company exposure is named.'
    elif metaphorical_catalyst:
        strength, reason = 'background', 'Catalyst is used in a financial context without chemical operations.'
    elif material and event and (sector or route or operation) and not incidental:
        strength, reason = 'strong', 'Recorded material and an operating event appear together.'
    elif route and event:
        strength, reason = 'strong', 'Recorded route and a disruption appear together.'
    elif material and operation and not incidental:
        strength, reason = 'related', 'Recorded material and an operating term appear together.'
    elif kind == 'sector' and (event or operation):
        strength, reason = 'related', 'Sector and operating context appear together.'
    elif material:
        strength, reason = 'background', 'Recorded material is mentioned without a clear company operation.'
    else:
        strength, reason = 'background', 'Possible route, sector or currency context needs review.'
    item['relevance_strength'] = strength
    item['ranking_reason'] = reason
    return item


def rank_items(items, company, cap=None, *, as_of=None):
    as_of = as_of or datetime.now(timezone.utc)
    ranked = [rank_item(item, company) for item in items]
    ranked.sort(key=lambda x: (_ORDER[x['relevance_strength']], *_date(x, as_of),
                               str(x.get('title') or '').casefold(), str(x.get('url') or '')),
                reverse=True)
    return ranked if cap is None else ranked[:cap]
