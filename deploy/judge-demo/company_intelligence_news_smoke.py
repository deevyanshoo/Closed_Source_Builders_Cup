#!/usr/bin/env python3
"""Read-only hosted acceptance for observed news across ten fictional firms.

Run against the intended HTTPS revision after deployment. The receipt contains
public headline metadata and source health, never session cookies or CSRF tokens.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import requests

from smoke import (AcceptanceError, MAX_RESPONSE_BYTES, TIMEOUT_SECONDS,
                   base_url, key, origin_of, require)


GLOBAL_SOURCES = {'eia', 'ecb', 'ecb_fx', 'gdelt'}
SOURCE_STATES = {'fresh', 'cached', 'stale', 'empty', 'unavailable'}
RELEVANCE_KINDS = {'direct_material', 'sector', 'route', 'currency', 'unmatched'}
CURRENT_STATES = {'fresh', 'cached'}


class PooledClient:
    """One cookie jar with fixed-origin, bounded HTTPS requests."""

    def __init__(self, base, *, max_transport_attempts=2):
        if type(max_transport_attempts) is not int or max_transport_attempts not in (1, 2):
            raise ValueError('max_transport_attempts must be 1 or 2')
        self.base = base
        self.origin = origin_of(base)
        self.max_transport_attempts = max_transport_attempts
        self.session = requests.Session()
        self.session.trust_env = False
        self.calls = 0
        self.transport_retries = 0

    def close(self):
        self.session.close()

    def json(self, stage, path, *, body=None, csrf=None):
        require(isinstance(path, str) and path.startswith('/') and not path.startswith('//')
                and '?' not in path and '#' not in path, stage, 'invalid_path')
        url = self.base + path
        require(origin_of(url) == self.origin, stage, 'invalid_path')
        payload = None if body is None else json.dumps(body, separators=(',', ':'),
                                                       allow_nan=False).encode('utf-8')
        headers = {'Accept': 'application/json'}
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': self.base})
            if csrf is not None:
                headers['X-CSRF-Token'] = csrf
        for attempt in range(self.max_transport_attempts):
            self.calls += 1
            try:
                with self.session.request('POST' if payload is not None else 'GET', url,
                                          data=payload, headers=headers, timeout=TIMEOUT_SECONDS,
                                          allow_redirects=False, stream=True, verify=True) as response:
                    raw = bytearray()
                    for chunk in response.iter_content(chunk_size=8192):
                        raw.extend(chunk)
                        require(len(raw) <= MAX_RESPONSE_BYTES, stage, 'response_too_large')
                    status = response.status_code
                    break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt + 1 < self.max_transport_attempts:
                    self.transport_retries += 1
                    continue
                raise AcceptanceError(stage, 'transport_error') from None
            except requests.exceptions.RequestException:
                raise AcceptanceError(stage, 'transport_error') from None
        try:
            result = json.loads(raw)
        except (UnicodeError, ValueError):
            raise AcceptanceError(stage, 'invalid_json_response') from None
        require(isinstance(result, dict), stage, 'invalid_response')
        if status != 200:
            error = result.get('error')
            code = error.get('code') if isinstance(error, dict) else None
            raise AcceptanceError(stage, code if isinstance(code, str) else 'unexpected_http_status', status)
        return result


def timestamp(value, stage, code):
    require(isinstance(value, str) and bool(value), stage, code)
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise AcceptanceError(stage, code) from None
    require(result.tzinfo is not None, stage, code)
    return result.astimezone(timezone.utc)


def safe_link(value, stage):
    require(isinstance(value, str) and len(value) <= 2048, stage, 'invalid_article_url')
    try:
        parts = urlsplit(value)
        require(parts.scheme == 'https' and bool(parts.hostname) and not parts.username
                and not parts.password and parts.port in (None, 443), stage, 'invalid_article_url')
    except ValueError:
        raise AcceptanceError(stage, 'invalid_article_url') from None
    return value


def relevant_kind(item):
    """Prefer the new explicit mapping; support older direct exposure fields."""
    kind = item.get('relevance_kind')
    if kind is not None:
        return kind
    if item.get('matched_materials'):
        return 'direct_material'
    if item.get('matched_routes'):
        return 'route'
    if item.get('matched_currencies'):
        return 'currency'
    return 'unmatched'


def check_news(view, profile, context, stage):
    cid = profile['company_id']
    require(isinstance(view, dict) and view.get('company_id') == cid
            and view.get('context_version') == context, stage, 'news_context_mismatch')
    checked = timestamp(view.get('checked_at'), stage, 'invalid_checked_at')
    require(view.get('cache_state') in {'fresh', 'cached', 'stale', 'empty', 'unavailable'},
            stage, 'invalid_cache_state')
    sources = view.get('sources')
    require(isinstance(sources, list), stage, 'invalid_sources')
    health = {}
    for source in sources:
        require(isinstance(source, dict) and isinstance(source.get('source_id'), str)
                and source['source_id'] not in health and source.get('state') in SOURCE_STATES
                and isinstance(source.get('name'), str) and source['name']
                and isinstance(source.get('reason'), str) and source['reason'],
                stage, 'invalid_source_health')
        sid = source['source_id']
        require(sid in GLOBAL_SOURCES or re.fullmatch(r'google_news_[1-3]', sid) is not None,
                stage, 'unexpected_source')
        if source.get('last_checked_at') is not None:
            timestamp(source['last_checked_at'], stage, 'invalid_source_time')
        health[sid] = source
    google_ids = {sid for sid in health if sid.startswith('google_news_')}
    require(GLOBAL_SOURCES <= set(health) and 1 <= len(google_ids) <= 3,
            stage, 'missing_discovery_sources')
    current_google = {sid for sid in google_ids if health[sid]['state'] in CURRENT_STATES}
    stale_google = {sid for sid in google_ids if health[sid]['state'] == 'stale'}
    require(current_google, stage, 'google_discovery_not_current')

    items = view.get('items')
    require(isinstance(items, list) and len(items) <= 80, stage, 'invalid_items')
    material_ids = {row['material_id'] for row in profile['materials']}
    route_ids = {row['route_id'] for row in profile['routes']}
    currencies = set(profile['currencies'])
    eligible = []
    google_eligible = 0
    stale_count = 0
    earliest = latest = None
    providers = {'Google News RSS': google_ids, 'EIA': {'eia'}, 'ECB': {'ecb'}, 'GDELT': {'gdelt'}}
    for item in items:
        require(isinstance(item, dict) and item.get('synthetic') is False
                and isinstance(item.get('title'), str) and bool(item['title'].strip())
                and isinstance(item.get('publisher'), str) and bool(item['publisher'].strip())
                and isinstance(item.get('source_tier'), str) and bool(item['source_tier'])
                and isinstance(item.get('source_reliability'), str) and bool(item['source_reliability'])
                and item.get('provider') in providers, stage, 'invalid_observed_item')
        safe_link(item.get('url'), stage)
        if item['provider'] == 'Google News RSS':
            require(item['source_tier'] == 'discovery'
                    and item['source_reliability'] == 'unverified',
                    stage, 'discovery_reliability_mislabelled')
        retrieved = timestamp(item.get('retrieved_at'), stage, 'invalid_retrieved_at')
        mats = item.get('matched_materials')
        routes = item.get('matched_routes')
        ccys = item.get('matched_currencies')
        operations = item.get('matched_operations', [])
        require(isinstance(mats, list) and isinstance(routes, list) and isinstance(ccys, list)
                and isinstance(operations, list) and all(isinstance(x, str) for x in operations)
                and set(mats) <= material_ids and set(routes) <= route_ids
                and set(ccys) <= currencies, stage, 'foreign_exposure_mapping')
        kind = relevant_kind(item)
        require(kind in RELEVANCE_KINDS, stage, 'invalid_relevance_kind')
        score = item.get('relevance_score')
        require((score is None and item.get('relevance_kind') is None)
                or type(score) in (float, int) and 0 <= score <= 1,
                stage, 'invalid_relevance_score')
        if kind == 'unmatched':
            continue
        published = timestamp(item.get('published_at'), stage, 'invalid_published_at')
        require(published <= datetime.now(timezone.utc).replace(microsecond=0),
                stage, 'future_publication')
        if kind == 'direct_material':
            require(bool(mats), stage, 'direct_without_material')
        if kind == 'route':
            require(bool(routes), stage, 'route_without_match')
        if kind == 'currency':
            require(bool(ccys), stage, 'currency_without_match')
        associated = [health[sid] for sid in providers[item['provider']]
                      if health[sid]['last_checked_at'] == item['retrieved_at']]
        # Retrieval timestamps identify an observed provider snapshot. A stale
        # snapshot remains evidence of an old headline, never current coverage.
        if any(source['state'] in CURRENT_STATES for source in associated):
            eligible.append((published, item, kind))
            google_eligible += item['provider'] == 'Google News RSS'
            earliest = min(earliest, published) if earliest else published
            latest = max(latest, published) if latest else published
        elif any(source['state'] == 'stale' for source in associated):
            stale_count += 1

    require(eligible, stage, 'no_current_relevant_headline')
    require(google_eligible >= 1, stage, 'no_current_relevant_google_headline')
    eligible.sort(key=lambda row: row[0], reverse=True)
    top = []
    for _, item, kind in eligible[:3]:
        top.append({'title': item['title'], 'url': item['url'],
                    'publisher': item['publisher'], 'provider': item['provider'],
                    'published_at': item['published_at'], 'retrieved_at': item['retrieved_at'],
                    'matched_materials': item['matched_materials'],
                    'matched_operations': item.get('matched_operations', []),
                    'relevance_kind': kind, 'relevance_score': item.get('relevance_score')})
    return {'company_id': cid, 'context_version': context, 'cache_state': view['cache_state'],
            'checked_at': checked.isoformat(), 'total_items': len(items),
            'current_relevant_count': len(eligible), 'stale_relevant_count': stale_count,
            'current_google_relevant_count': google_eligible,
            'latest_relevant_publication': latest.isoformat() if latest else None,
            'oldest_relevant_publication': earliest.isoformat() if earliest else None,
            'google_source_states': {sid: health[sid]['state'] for sid in sorted(google_ids)},
            'fresh_google_source_count': sum(health[sid]['state'] == 'fresh' for sid in google_ids),
            'cached_google_source_count': sum(health[sid]['state'] == 'cached' for sid in google_ids),
            'stale_google_source_count': len(stale_google),
            'source_health': {sid: {'state': source['state'], 'last_checked_at': source['last_checked_at'],
                                    'reason': source['reason']} for sid, source in sorted(health.items())},
            'top_headlines': top}


def run(base, receipt):
    client = PooledClient(base)
    try:
        health = client.json('health', '/-/health')
        require(health.get('ready') is True and isinstance(health.get('revision'), str)
                and health['revision'] and health['revision'] != 'local',
                'health', 'not_deployed_revision')
        receipt['deployed_revision'] = health['revision']
        profiles = client.json('catalogue', '/api/demo/companies').get('companies')
        require(isinstance(profiles, list) and len(profiles) == 10
                and len({row['company_id'] for row in profiles}) == 10,
                'catalogue', 'company_count')
        session = client.json('session', '/api/demo/session')
        csrf = session.get('csrf_token')
        context = session.get('context_version')
        require(isinstance(csrf, str) and csrf and type(context) is int and context >= 1
                and session.get('synthetic') is True and session.get('persistence') == 'ephemeral',
                'session', 'invalid_session')
        switches = 0
        for profile in profiles:
            cid = profile['company_id']
            stage = cid
            require(profile.get('synthetic') is True and isinstance(cid, str) and cid,
                    stage, 'invalid_profile')
            if session['company']['company_id'] != cid:
                session = client.json(stage, '/api/demo/company', csrf=csrf, body={
                    'company_id': cid, 'expected_context_version': context,
                    'idempotency_key': key()})
                switches += 1
                require(session['company']['company_id'] == cid
                        and type(session.get('context_version')) is int
                        and session['context_version'] > context, stage, 'company_switch_failed')
                context = session['context_version']
            news = client.json(stage + ':news', '/api/demo/news')
            receipt['companies'].append(check_news(news, profile, context, stage + ':news'))
            receipt['company_count'] = len(receipt['companies'])
            print(cid + ' relevant news verified', flush=True)
        receipt.update({'ok': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
                        'company_count': len(receipt['companies']), 'company_switches': switches,
                        'single_cookie_jar': True,
                        'total_current_relevant_headlines': sum(row['current_relevant_count']
                                                                for row in receipt['companies'])})
        return receipt
    finally:
        receipt['http_requests'] = client.calls
        receipt['transport_retries'] = client.transport_retries
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True, help='Deployed HTTPS origin')
    parser.add_argument('--output', required=True, help='Path for JSON evidence receipt')
    args = parser.parse_args()
    result = {'ok': False, 'company_count': 0, 'companies': [],
              'scope': 'public_news_metadata',
              'started_at': datetime.now(timezone.utc).isoformat()}
    try:
        run(base_url(args.base), result)
    except AcceptanceError as exc:
        result.update({'stage': exc.stage, 'code': exc.code, 'status': exc.status})
    except (KeyError, TypeError, IndexError, ValueError):
        result.update({'stage': 'response', 'code': 'invalid_response', 'status': None})
    result.setdefault('checked_at', datetime.now(timezone.utc).isoformat())
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({name: value for name, value in result.items() if name != 'companies'}))
    if not result['ok']:
        sys.exit(1)
