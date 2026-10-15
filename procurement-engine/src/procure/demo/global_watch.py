"""One bounded public-event collection shared by every fictional company view."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests

from .news import (_default_transport, _now, _rss, _term_in, _unavailable_reason,
                   MAX_BYTES, TIMEOUT, REFRESH_DEADLINE_SECONDS, _RateLimited)

MAX_EVENTS = 18
TTL_SECONDS = 900
_QUERIES = (
    ('conflict', '(conflict OR hostilities) (shipping OR supply chain OR trade)'),
    ('chokepoint', '(Suez Canal OR Red Sea OR Strait of Hormuz OR Panama Canal) (shipping OR port OR transit)'),
    ('port', '(port congestion OR port closure OR port strike) (cargo OR shipping OR container)'),
    ('restriction', '(export restriction OR import restriction OR trade sanction OR tariff) (materials OR manufacturing)'),
)
_CHOKEPOINT_ALIASES = {'Strait of Hormuz': ('Hormuz',), 'Suez Canal': ('Suez',),
                       'Panama Canal': ('Panama Canal',), 'Bab el-Mandeb': ('Bab el Mandeb',)}


def _sources():
    return [(f'global_{key}', f'Google News RSS · {key}',
             'https://news.google.com/rss/search?' + urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'}))
            for key, query in _QUERIES]


def exposure(event, company):
    """A topic match is a reason to check evidence, never proof of disruption."""
    title = event['title'].casefold()
    operational = any(_term_in(title, term) for term in
                      ('shipping', 'port', 'transit', 'supply', 'export', 'import',
                       'restriction', 'tariff', 'closure', 'delay', 'congestion'))
    materials = [m['material_id'] for m in company.get('materials', [])
                 if any(_term_in(title, word) for word in [m.get('name', ''), *m.get('keywords', [])]
                        if len(word) >= 4)]
    routes = [r['route_id'] for r in company.get('routes', [])
              if operational and any(_term_in(title, variant) for place in
                                     [*r.get('via', []),
                                      *(value for value in (r.get('origin', ''), r.get('destination', '')) if ',' in value)]
                                     for variant in (place, *_CHOKEPOINT_ALIASES.get(place, ()))
                                     if len(variant) >= 5)]
    countries = {s.get('country', '') for s in company.get('suppliers', [])}
    countries.update(loc.get('country', '') for loc in company.get('locations', []))
    indirect = operational and any(_term_in(title, country) for country in countries if len(country) >= 5)
    kind = 'direct' if operational and (materials or routes) else 'indirect' if indirect else 'background'
    reason = ('Headline mentions a recorded material or route and an operational term; actual exposure needs verification.'
              if kind == 'direct' else
              'Headline mentions a company or supplier country; no affected route or supplier is verified.'
              if kind == 'indirect' else
              'Public global context only; no recorded company exposure is established.')
    next_action = ('Check the named supplier, route, and current shipment evidence before changing a plan.'
                   if kind == 'direct' else
                   'Check current suppliers and routes for a specific connection before acting.'
                   if kind == 'indirect' else
                   'Read the original source; monitor for a specific company connection.')
    return {'kind': kind, 'reason': reason, 'matched_materials': materials,
            'matched_routes': routes, 'next_action': next_action}


def present(collection, company):
    return {**collection, 'sources': deepcopy(collection['sources']),
            'events': [{**event, 'exposure': exposure(event, company)}
                       for event in collection['events']]}


def carry_watch(current, previous):
    """Carry only failed-source events with their original evidence clocks."""
    if not previous:
        return current
    current = deepcopy(current)
    old_sources = {row['source_id']: row for row in previous.get('sources', [])}
    old_events = {row['url']: row for row in previous.get('events', [])}
    events = {row['url']: row for row in current['events']}
    for url, row in events.items():
        if url in old_events:
            row['first_seen_at'] = old_events[url]['first_seen_at']
    stale = False
    for source in current['sources']:
        if source['state'] != 'unavailable':
            continue
        old = old_sources.get(source['source_id'])
        if not old or old['state'] not in ('fresh', 'empty', 'stale'):
            continue
        source.update(state='stale', last_checked_at=old['last_checked_at'],
                      reason='Source unavailable; showing previously observed metadata.')
        for row in previous.get('events', []):
            if row['source_id'] == source['source_id']:
                events.setdefault(row['url'], row)
        stale = True
    current['events'] = _bounded_events(events)
    if stale:
        current['status'] = 'stale' if not any(s['state'] in ('fresh', 'empty') for s in current['sources']) else 'partial'
    if not any(s['state'] in ('fresh', 'empty') for s in current['sources']):
        current['success_at'] = previous.get('success_at')
    return current


def _bounded_events(events):
    """Round-robin recent metadata so one busy query cannot hide other topics."""
    buckets = {}
    for topic, _ in _QUERIES:
        buckets[topic] = sorted((row for row in events.values() if row['topic'] == topic),
                                key=lambda row: (row['published_at'] or row['first_seen_at'], row['item_id']),
                                reverse=True)
    chosen = []
    while len(chosen) < MAX_EVENTS and any(buckets.values()):
        for topic, _ in _QUERIES:
            if buckets[topic] and len(chosen) < MAX_EVENTS:
                chosen.append(buckets[topic].pop(0))
    return chosen


class GlobalWatch:
    def __init__(self, *, clock=None, transport=None, offline=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.transport = transport or _default_transport
        self.offline = bool(offline)
        self._condition = threading.Condition()
        self._refreshing = False
        self._collection = None
        self._cache_time = None

    def get(self, company, force=False):
        now = _now(self.clock)
        if self.offline:
            return {'attempted_at': now.isoformat(), 'success_at': None, 'status': 'offline',
                    'sources': [], 'events': []}
        with self._condition:
            deadline = time.monotonic() + REFRESH_DEADLINE_SECONDS + 2
            while self._refreshing:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    old = self._collection or {'attempted_at': now.isoformat(), 'success_at': None,
                                               'status': 'unavailable', 'sources': [], 'events': []}
                    return present({**old, 'status': 'stale' if old['events'] else 'unavailable'}, company)
                self._condition.wait(remaining)
            if not force and self._collection and (now - self._cache_time).total_seconds() < TTL_SECONDS:
                return present(self._collection, company)
            self._refreshing = True
        try:
            fresh = self._refresh(now)
            with self._condition:
                self._collection = carry_watch(fresh, self._collection)
                self._cache_time = now
                return present(self._collection, company)
        finally:
            with self._condition:
                self._refreshing = False
                self._condition.notify_all()

    def _refresh(self, now):
        stamp = now.isoformat()
        sources, events = [], {}
        fixed = _sources()
        pool = ThreadPoolExecutor(max_workers=len(fixed), thread_name_prefix='global-watch')
        try:
            futures = {sid: pool.submit(self.transport, url, timeout=TIMEOUT, max_bytes=MAX_BYTES)
                       for sid, _, url in fixed}
            done, _ = wait(futures.values(), timeout=REFRESH_DEADLINE_SECONDS)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        for sid, name, _ in fixed:
            try:
                if futures[sid] not in done:
                    raise TimeoutError('Global Watch deadline')
                payload = futures[sid].result()
                if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
                    raise ValueError('Invalid response size')
                found = _rss(payload, 'Google News RSS', stamp)
                for item in found:
                    events.setdefault(item['url'], {
                        'item_id': item['item_id'], 'title': item['title'], 'url': item['url'],
                        'publisher': item['publisher'], 'provider': item['provider'],
                        'published_at': item['published_at'], 'first_seen_at': item['first_seen_at'],
                        'retrieved_at': item['retrieved_at'], 'source_id': sid,
                        'topic': sid.removeprefix('global_')})
                sources.append({'source_id': sid, 'name': name, 'state': 'fresh' if found else 'empty',
                                'last_checked_at': stamp, 'reason': 'Public headline metadata received.' if found else 'No valid items returned.'})
            except (_RateLimited, requests.RequestException, ValueError, TypeError, TimeoutError, ET.ParseError) as error:
                sources.append({'source_id': sid, 'name': name, 'state': 'unavailable',
                                'last_checked_at': stamp, 'reason': _unavailable_reason(error)})
        ok = any(s['state'] in ('fresh', 'empty') for s in sources)
        status = 'fresh' if all(s['state'] in ('fresh', 'empty') for s in sources) else 'partial' if ok else 'unavailable'
        return {'attempted_at': stamp, 'success_at': stamp if ok else None,
                'status': status, 'sources': sources, 'events': _bounded_events(events)}
