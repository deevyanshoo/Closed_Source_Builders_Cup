"""Bounded hourly refresh of the ten public-news company views."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
import requests
from google.auth.exceptions import GoogleAuthError
import sys
import time

from .company import list_companies
from .global_watch import carry_watch, present as present_watch
from .news import NewsService, _now, _company_item
from .news_ranking import rank_items
from .news_snapshots import GcsSnapshotStore, _slot, catalog_version, validate_bundle

_DEADLINE = 120
_PUBLICATION_RESERVE = 30  # Two bounded cross-region writes, with a small buffer.
_NEWS_STATES = {'fresh', 'empty', 'cached'}


def _checked_this_run(source, started):
    try:
        checked = datetime.fromisoformat(source['last_checked_at'].replace('Z', '+00:00'))
        return started <= checked.astimezone(timezone.utc) <= started + timedelta(seconds=_DEADLINE)
    except (KeyError, AttributeError, TypeError, ValueError):
        return False


def _carry(current, previous, company, now):
    """Retain only failed-source observations, preserving original evidence clocks."""
    if not previous:
        return current
    old_watch = previous.get('global_watch')
    if old_watch:
        current_watch = current.get('global_watch')
        if current_watch is None:
            current_watch = {'attempted_at': now.isoformat(), 'success_at': None,
                             'status': 'unavailable', 'events': [],
                             'sources': [{**source, 'state': 'unavailable',
                                          'last_checked_at': now.isoformat()}
                                         for source in old_watch['sources']]}
        current['global_watch'] = carry_watch(current_watch, old_watch)
    prior_items = {item.get('url'): item for item in previous.get('items', []) if item.get('url')}
    for item in current['items']:
        old_item = prior_items.get(item.get('url'))
        if old_item and old_item.get('first_seen_at'):
            item['first_seen_at'] = old_item['first_seen_at']
    if not current['sources']:
        current['sources'] = [
            {**deepcopy(source),
             'state': 'stale' if source.get('state') in ('fresh', 'empty', 'cached', 'stale') else 'unavailable',
             'reason': 'Refresh failed; showing previously observed metadata.'}
            for source in previous.get('sources', [])]
        current['items'] = deepcopy(previous.get('items', []))
        current['fx'] = deepcopy(previous.get('fx', []))
        current['cache_state'] = ('stale' if current['items'] or current['fx'] or
                                  any(s['state'] == 'stale' for s in current['sources']) else 'unavailable')
        return current
    old_sources = {s['source_id']: s for s in previous.get('sources', [])}
    sources = []
    carried_items = []
    for source in current['sources']:
        old = old_sources.get(source['source_id'])
        if source['state'] == 'unavailable' and old and old['state'] in ('fresh', 'empty', 'cached', 'stale'):
            source = {**source, 'state': 'stale', 'last_checked_at': old.get('last_checked_at'),
                      'reason': 'Refresh failed; showing previously observed metadata.'}
            sid = source['source_id']
            provider = {'eia': 'EIA', 'ecb': 'ECB', 'gdelt': 'GDELT'}.get(sid)
            carried_items.extend(item for item in previous.get('items', [])
                                 if item.get('source_id') == sid or (provider and item.get('provider') == provider))
            if sid == 'ecb_fx':
                current['fx'] = previous.get('fx', [])
        sources.append(source)
    current['sources'] = sources
    has_current_items = bool(current['items'])
    existing = {item.get('url') for item in current['items']}
    current['items'].extend(item for item in carried_items if item.get('url') not in existing)
    if has_current_items and carried_items:
        current['items'] = rank_items((_company_item(item, company) for item in current['items']),
                                      company, 80, as_of=now)
    if any(s['state'] == 'stale' for s in sources):
        current['cache_state'] = 'stale'
    elif all(s['state'] == 'unavailable' for s in sources):
        current['cache_state'] = 'unavailable'
    return current


def refresh_portfolio(*, live, store, clock, company_profiles=None) -> dict:
    profiles = company_profiles if company_profiles is not None else list_companies()
    if len(profiles) != 10 or len({p['company_id'] for p in profiles}) != 10:
        raise ValueError('Expected ten distinct catalog profiles')
    started = _now(clock)
    slot = _slot(started)
    deadline = time.monotonic() + _DEADLINE
    previous = None
    for hours in range(1, 9):
        if time.monotonic() >= deadline - _PUBLICATION_RESERVE:
            break
        try:
            candidate = store.read(slot - timedelta(hours=hours))
            if candidate:
                validate_bundle(candidate, profiles)
                if datetime.fromisoformat(candidate['slot'].replace('Z', '+00:00')) != slot - timedelta(hours=hours):
                    continue
                previous = candidate
                break
        except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
            continue
    if previous is None and callable(getattr(store, 'recent_slots', None)) and time.monotonic() < deadline - _PUBLICATION_RESERVE:
        try:
            slots = store.recent_slots(slot - timedelta(hours=1))
        except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
            slots = []
        oldest = slot - timedelta(days=7)
        upper = slot - timedelta(hours=9)
        eligible = sorted({candidate for candidate in slots if isinstance(candidate, datetime)
                           and candidate.tzinfo is not None and oldest <= candidate <= upper}, reverse=True)
        for candidate_slot in eligible[:8]:
            if time.monotonic() >= deadline - _PUBLICATION_RESERVE:
                break
            try:
                candidate = store.read(candidate_slot)
                if candidate is None:
                    continue
                validate_bundle(candidate, profiles)
                if datetime.fromisoformat(candidate['slot'].replace('Z', '+00:00')) != candidate_slot:
                    continue
                previous = candidate
                break
            except (OSError, ValueError, KeyError, TypeError, GoogleAuthError, requests.RequestException):
                continue
    prior_views = previous['views'] if previous else {}
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='news-portfolio')
    try:
        futures = {p['company_id']: pool.submit(live.get, p) for p in profiles}
        watch = getattr(live, 'global_watch', None)
        watch_future = pool.submit(watch.get, profiles[0], force=True) if watch is not None else None
        done, _ = wait([*futures.values(), *([watch_future] if watch_future else [])],
                       timeout=max(0, deadline-time.monotonic()-_PUBLICATION_RESERVE))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    global_watch = None
    if watch_future in done:
        try:
            global_watch = watch_future.result()
        except Exception:
            pass
    views = {}
    news_success, fx_success = False, False
    for profile in profiles:
        cid = profile['company_id']
        try:
            if futures[cid] not in done:
                raise TimeoutError('Portfolio deadline')
            current = futures[cid].result()
            if not isinstance(current, dict):
                raise ValueError('Invalid live view')
        except (Exception):
            current = {'checked_at': started.isoformat(), 'cache_state': 'unavailable',
                       'sources': [], 'items': [], 'fx': [], 'limitations': []}
        if global_watch is not None:
            current['global_watch'] = present_watch(global_watch, profile)
        current = _carry(current, prior_views.get(cid), profile, started)
        if any(s.get('state') in _NEWS_STATES and s.get('source_id') != 'ecb_fx'
               and _checked_this_run(s, started) for s in current['sources']):
            news_success = True
        if any(s.get('source_id') == 'ecb_fx' and s.get('state') in ('fresh', 'cached')
               and _checked_this_run(s, started) for s in current['sources']):
            fx_success = True
        old_collection = prior_views.get(cid, {}).get('collection') or (previous['collection'] if previous else {})
        own_news = any(s.get('source_id') != 'ecb_fx' and s.get('state') in _NEWS_STATES
                       and _checked_this_run(s, started) for s in current['sources'])
        own_fx = any(s.get('source_id') == 'ecb_fx' and s.get('state') in ('fresh', 'cached')
                     and _checked_this_run(s, started) for s in current['sources'])
        current['collection'] = {'attempted_at': started.isoformat(),
                                 'news_success_at': started.isoformat() if own_news else old_collection.get('news_success_at'),
                                 'fx_success_at': started.isoformat() if own_fx else old_collection.get('fx_success_at'),
                                 'status': ('partial' if own_news and any(s.get('state') in ('unavailable', 'stale', 'offline') for s in current['sources'])
                                            else 'fresh' if own_news else 'stale' if current['items'] else 'unavailable')}
        views[cid] = current
    old_collection = previous['collection'] if previous else {}
    status = ('fresh' if news_success and all(v['collection']['status'] == 'fresh' for v in views.values())
              else 'partial' if news_success else 'news_unavailable')
    collection = {'attempted_at': started.isoformat(),
                  'news_success_at': started.isoformat() if news_success else old_collection.get('news_success_at'),
                  'fx_success_at': started.isoformat() if fx_success else old_collection.get('fx_success_at'),
                  'status': status}
    bundle = {'schema_version': 1, 'slot': slot.isoformat(), 'started_at': started.isoformat(),
              'completed_at': _now(clock).isoformat(), 'catalog_version': catalog_version(profiles),
              'company_ids': [p['company_id'] for p in profiles], 'views': views, 'collection': collection}
    validate_bundle(bundle, profiles)
    watch_success = bool(global_watch and global_watch.get('success_at')
                         and global_watch['status'] in ('fresh', 'partial')
                         and _slot(datetime.fromisoformat(global_watch['success_at'].replace('Z', '+00:00'))) == slot)
    receipt = {'slot': slot.isoformat(), 'attempted_at': started.isoformat(),
               'completed_at': bundle['completed_at'], 'status': status,
               'news_success': news_success, 'fx_success': fx_success}
    if watch_future is not None:
        receipt.update(watch_attempted_at=(global_watch['attempted_at'] if global_watch else started.isoformat()),
                       watch_success=watch_success)
    if hasattr(store, 'create_attempt'):
        store.create_attempt(slot, receipt)
    published = bool((news_success or watch_success) and store.create(slot, bundle))
    return {**bundle, 'collection': {**collection, 'published': published}}


def main():
    bucket = os.environ.get('NEWS_SNAPSHOT_BUCKET', '')
    if not bucket:
        print('NEWS_SNAPSHOT_BUCKET is required', file=sys.stderr)
        return 2
    result = refresh_portfolio(live=NewsService(), store=GcsSnapshotStore(bucket),
                               clock=lambda: datetime.now(timezone.utc))
    print(json.dumps({'slot': result['slot'], 'collection': result['collection']}))
    # A create-only precondition loser is a healthy idempotent execution.
    # Storage transport failures still raise; a news outage remains a failure.
    return 0 if result['collection']['published'] or result['collection']['status'] in ('fresh', 'partial') else 1


if __name__ == '__main__':
    raise SystemExit(main())
