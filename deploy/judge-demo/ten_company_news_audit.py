#!/usr/bin/env python3
"""Read-only hosted news/Global Watch audit for one exact Cloud Run revision.

Only the public demo's health, readiness, catalogue, anonymous session/company,
news and risk routes are called. No refresh, operator, research or provider route
is reachable from this script. The receipt excludes cookies, CSRF and full URLs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
from time import monotonic
from urllib.parse import urlsplit

from smoke import AcceptanceError, Client


BASE = 'https://closed-source-quote-demo-ryoeyuqvta-el.a.run.app'
EXPECTED_REVISION = 'closed-source-quote-demo-00040-n4g'
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / 'output/evidence/hosted-audit-news-20261015.json'
AFTER_1500_UTC = datetime(2026, 10, 15, 15, tzinfo=timezone.utc)
STRENGTH = {'strong': 3, 'related': 2, 'background': 1, 'unmatched': 0}
SOURCE_STATES = {'fresh', 'cached', 'stale', 'empty', 'unavailable', 'offline'}
WATCH_STATES = {'fresh', 'partial', 'stale', 'unavailable', 'offline'}
PROVIDERS = {'EIA', 'ECB', 'GDELT', 'Google News RSS'}
PUBLIC_SOURCE_REASONS = {
    'Provider request timed out.', 'Provider connection failed.',
    'Provider response exceeded the size limit.', 'Source could not be read or validated.',
    'Source unavailable; showing previously observed metadata.',
    'Public headline metadata received.', 'No valid items returned.',
}


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def _safe_id(value):
    return value[:48] if isinstance(value, str) and value.isascii() and value.replace('-', '').replace('_', '').isalnum() else 'invalid'


def _issue(issues, code, item_id=None):
    if len(issues) < 60:
        row = {'code': code}
        if item_id is not None:
            row['item_id'] = _safe_id(item_id)
        issues.append(row)


def _url_host(value):
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.hostname.lower()
    except (TypeError, ValueError):
        return None


def _rank_key(item, now):
    strength = STRENGTH.get(item.get('relevance_strength'), -1)
    published = _stamp(item.get('published_at'))
    dated = -1 if published and published > now else 1 if published else 0
    return (strength, dated, published.timestamp() if dated == 1 else 0,
            str(item.get('title') or '').casefold(), str(item.get('url') or ''))


def audit_news(view: dict, profile: dict, version: int, now: datetime) -> tuple[dict, list[dict]]:
    """Inspect all returned rows, retain only bounded public metadata in receipt."""
    issues = []
    cid = profile['company_id']
    if not isinstance(view, dict):
        return {'item_count': 0}, [{'code': 'invalid_news_response'}]
    if view.get('company_id') != cid or view.get('context_version') != version:
        _issue(issues, 'news_context_mismatch')
    checked = _stamp(view.get('checked_at'))
    if not checked or checked > now + timedelta(minutes=5):
        _issue(issues, 'invalid_news_checked_at')

    collection = view.get('collection')
    collection = collection if isinstance(collection, dict) else {}
    mode = collection.get('mode')
    slot = _stamp(collection.get('slot'))
    if mode != 'scheduled':
        _issue(issues, 'not_scheduled_snapshot')
    if mode == 'scheduled' and (slot is None or (now >= AFTER_1500_UTC + timedelta(minutes=15)
                                                 and slot < AFTER_1500_UTC) or
                                (slot is not None and slot > now + timedelta(minutes=5))):
        _issue(issues, 'scheduled_slot_before_1500')
    for key in ('attempted_at', 'news_success_at', 'fx_success_at', 'completed_at'):
        clock = _stamp(collection.get(key)) if collection.get(key) is not None else None
        if collection.get(key) is not None and (not clock or clock > now + timedelta(minutes=5)):
            _issue(issues, 'invalid_collection_clock')
    if mode == 'scheduled' and not _stamp(collection.get('completed_at')):
        _issue(issues, 'invalid_collection_clock')
    collection_summary = {key: collection.get(key) for key in
                          ('mode', 'status', 'slot', 'completed_at',
                           'attempted_at', 'news_success_at', 'fx_success_at')}

    sources = view.get('sources')
    if not isinstance(sources, list) or len(sources) > 8:
        _issue(issues, 'invalid_news_sources')
        sources = []
    source_rows = []
    for source in sources:
        if not isinstance(source, dict) or source.get('state') not in SOURCE_STATES:
            _issue(issues, 'invalid_news_source_state')
            continue
        if source.get('last_checked_at') is not None and not _stamp(source['last_checked_at']):
            _issue(issues, 'invalid_news_source_clock')
        row = {key: source.get(key) for key in ('source_id', 'state', 'last_checked_at')}
        if source.get('reason') in PUBLIC_SOURCE_REASONS:
            row['reason'] = source['reason']
        source_rows.append(row)

    items = view.get('items')
    if not isinstance(items, list) or len(items) > 80:
        _issue(issues, 'invalid_item_cap')
        items = []
    mids = {row.get('material_id') for row in profile.get('materials', []) if isinstance(row, dict)}
    rids = {row.get('route_id') for row in profile.get('routes', []) if isinstance(row, dict)}
    currencies = set(profile.get('currencies', []))
    strength_counts, provider_counts = Counter(), Counter()
    prior_key = None
    for item in items:
        if not isinstance(item, dict):
            _issue(issues, 'invalid_item_schema')
            continue
        item_id = item.get('item_id')
        if not item_id or not item.get('title') or item.get('synthetic') is not False:
            _issue(issues, 'invalid_item_schema', item_id)
        provider = item.get('provider')
        if provider not in PROVIDERS:
            _issue(issues, 'invalid_provider', item_id)
        provider_counts[str(provider)] += 1
        host = _url_host(item.get('url'))
        if not host or (provider == 'EIA' and host != 'www.eia.gov') or (
                provider == 'ECB' and host != 'www.ecb.europa.eu'):
            _issue(issues, 'invalid_source_provenance', item_id)
        for key, allowed, code in (('matched_materials', mids, 'foreign_material'),
                                   ('matched_routes', rids, 'foreign_route'),
                                   ('matched_currencies', currencies, 'foreign_currency')):
            values = item.get(key)
            if not isinstance(values, list) or any(not isinstance(x, str) or x not in allowed for x in values):
                _issue(issues, code, item_id)
        published = item.get('published_at')
        pub_dt = _stamp(published) if published is not None else None
        if published is not None and pub_dt is None:
            _issue(issues, 'invalid_publication_clock', item_id)
        if pub_dt and pub_dt > now + timedelta(days=1):
            _issue(issues, 'future_publication', item_id)
        seen, retrieved = _stamp(item.get('first_seen_at')), _stamp(item.get('retrieved_at'))
        if not seen or not retrieved or seen > retrieved + timedelta(minutes=5) or retrieved > now + timedelta(minutes=5):
            _issue(issues, 'invalid_observation_clock', item_id)
        strength = item.get('relevance_strength')
        if strength not in STRENGTH or not isinstance(item.get('ranking_reason'), str):
            _issue(issues, 'invalid_ranking_metadata', item_id)
        if (strength in ('strong', 'related') and
                'price target' in str(item.get('title') or '').casefold() and
                not item.get('matched_materials') and not item.get('matched_routes')):
            _issue(issues, 'sector_stock_target_promoted', item_id)
        strength_counts[str(strength)] += 1
        key = _rank_key(item, now)
        if prior_key is not None and key > prior_key:
            _issue(issues, 'ranking_inversion', item_id)
        prior_key = key

    watch = view.get('global_watch')
    if not isinstance(watch, dict):
        _issue(issues, 'global_watch_missing')
        watch = {}
    if watch.get('status') not in WATCH_STATES:
        _issue(issues, 'invalid_watch_status')
    attempted, success = _stamp(watch.get('attempted_at')), _stamp(watch.get('success_at'))
    if not attempted or (watch.get('success_at') is not None and not success):
        _issue(issues, 'invalid_watch_clock')
    watch_sources = watch.get('sources')
    if not isinstance(watch_sources, list) or len(watch_sources) > 4:
        _issue(issues, 'invalid_watch_sources')
        watch_sources = []
    watch_source_rows = []
    for source in watch_sources:
        if not isinstance(source, dict) or source.get('state') not in SOURCE_STATES:
            _issue(issues, 'invalid_watch_source_state')
            continue
        if source.get('last_checked_at') is not None and not _stamp(source['last_checked_at']):
            _issue(issues, 'invalid_watch_source_clock')
        row = {key: source.get(key) for key in ('source_id', 'state', 'last_checked_at')}
        if source.get('reason') in PUBLIC_SOURCE_REASONS:
            row['reason'] = source['reason']
        watch_source_rows.append(row)
    events = watch.get('events')
    if not isinstance(events, list) or len(events) > 18:
        _issue(issues, 'invalid_watch_event_cap')
        events = []
    event_ids, topics, exposures = [], Counter(), Counter()
    source_ids = {x.get('source_id') for x in watch_source_rows}
    for event in events:
        if not isinstance(event, dict):
            _issue(issues, 'invalid_watch_event')
            continue
        item_id = event.get('item_id')
        event_ids.append(_safe_id(item_id))
        if not _url_host(event.get('url')) or event.get('source_id') not in source_ids:
            _issue(issues, 'invalid_watch_provenance', item_id)
        for key in ('published_at', 'first_seen_at', 'retrieved_at'):
            if event.get(key) is not None and not _stamp(event[key]):
                _issue(issues, 'invalid_watch_event_clock', item_id)
        if event.get('published_at') and _stamp(event['published_at']) and _stamp(event['published_at']) > now + timedelta(days=1):
            _issue(issues, 'future_watch_publication', item_id)
        exposure = event.get('exposure')
        if not isinstance(exposure, dict) or exposure.get('kind') not in ('direct', 'indirect', 'background'):
            _issue(issues, 'invalid_watch_exposure', item_id)
            continue
        for key, allowed in (('matched_materials', mids), ('matched_routes', rids)):
            values = exposure.get(key)
            if not isinstance(values, list) or any(not isinstance(x, str) or x not in allowed for x in values):
                _issue(issues, 'foreign_watch_exposure', item_id)
        if exposure['kind'] == 'direct' and not (exposure.get('matched_materials') or exposure.get('matched_routes')):
            _issue(issues, 'unsupported_direct_exposure', item_id)
        if not exposure.get('reason') or not exposure.get('next_action'):
            _issue(issues, 'missing_exposure_caveat', item_id)
        exposures[exposure['kind']] += 1
        topics[str(event.get('topic'))] += 1
    if not events:
        _issue(issues, 'global_watch_empty')
    return ({'cache_state': view.get('cache_state'), 'checked_at': view.get('checked_at'),
             'collection': collection_summary, 'sources': source_rows, 'item_count': len(items),
             'strengths': dict(strength_counts), 'providers': dict(provider_counts),
             'top_items': [{'item_id': _safe_id(x.get('item_id')),
                            'title': x.get('title', '')[:160],
                            'strength': x.get('relevance_strength'),
                            'ranking_reason': x.get('ranking_reason'),
                            'provider': x.get('provider'),
                            'published_at': x.get('published_at'),
                            'matched_materials': x.get('matched_materials'),
                            'matched_routes': x.get('matched_routes')}
                           for x in items[:5] if isinstance(x, dict) and isinstance(x.get('title'), str)],
             'global_watch': {'status': watch.get('status'), 'attempted_at': watch.get('attempted_at'),
                              'success_at': watch.get('success_at'), 'sources': watch_source_rows,
                              'event_count': len(events), 'event_ids': event_ids,
                              'topics': dict(topics), 'exposures': dict(exposures)}}, issues)


def audit_risks(payload: dict, company_ids: set[str]) -> tuple[dict, list[dict]]:
    issues = []
    rows = payload.get('companies') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or {x.get('company_id') for x in rows if isinstance(x, dict)} != company_ids:
        _issue(issues, 'risk_company_coverage')
        rows = rows if isinstance(rows, list) else []
    summaries = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            _issue(issues, 'invalid_risk_report')
            continue
        alerts = row.get('alerts')
        if not isinstance(alerts, list) or len(alerts) > 60:
            _issue(issues, 'invalid_risk_alert_cap')
            alerts = []
        if any(not isinstance(a, dict) or a.get('company_id') != row.get('company_id') or
               a.get('severity') not in ('high', 'medium', 'low') for a in alerts):
            _issue(issues, 'foreign_or_invalid_risk_alert')
        summaries.append({'company_id': row.get('company_id'), 'alert_count': len(alerts),
                          'categories': dict(Counter(str(a.get('category')) for a in alerts if isinstance(a, dict))),
                          'data_gap_count': len(row.get('data_gaps', [])) if isinstance(row.get('data_gaps'), list) else None})
    return {'companies': summaries}, issues


def _save(path: Path, receipt: dict, *, first: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if first:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(receipt, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def run(client: Client, output: Path, *, now: datetime | None = None,
        expected_revision: str = EXPECTED_REVISION) -> dict:
    now = now or datetime.now(timezone.utc)
    receipt = {'observed_at': now.isoformat(), 'expected_revision': expected_revision,
               'status': 'running', 'stage': 'health', 'companies': [], 'findings': []}
    _save(output, receipt, first=True)
    try:
        health = client.json('health', '/-/health')
        if health.get('revision') != expected_revision:
            raise AcceptanceError('health', 'revision_mismatch')
        receipt['revision'] = health['revision']
        receipt['stage'] = 'readiness'
        _save(output, receipt)
        ready = client.json('readiness', '/-/ready')
        if ready.get('ready') is not True:
            raise AcceptanceError('readiness', 'dependency_unready')
        receipt['stage'] = 'catalogue'
        _save(output, receipt)
        catalogue = client.json('catalogue', '/api/demo/companies')
        profiles = catalogue.get('companies')
        if not isinstance(profiles, list) or len(profiles) != 10 or len({
                p.get('company_id') for p in profiles if isinstance(p, dict)}) != 10:
            raise AcceptanceError('catalogue', 'not_ten_companies')
        session = client.json('session', '/api/demo/session')
        csrf, version = session.get('csrf_token'), session.get('context_version')
        if not isinstance(csrf, str) or not csrf or type(version) is not int:
            raise AcceptanceError('session', 'invalid_anonymous_session')
        shared_ids = None
        for profile in profiles:
            cid = profile['company_id']
            stage = f'company_{len(receipt["companies"]) + 1}'
            receipt['stage'] = stage
            _save(output, receipt)
            selected = client.json(stage, '/api/demo/company', body={
                'company_id': cid, 'expected_context_version': version,
                'idempotency_key': secrets.token_hex(12)}, csrf=csrf)
            version = selected.get('context_version')
            csrf = selected.get('csrf_token')
            if selected.get('company_id') != cid or type(version) is not int or not isinstance(csrf, str):
                raise AcceptanceError(stage, 'company_context_mismatch')
            started = monotonic()
            view = client.json(stage, '/api/demo/news')
            summary, findings = audit_news(view, profile, version, now)
            row = {'company_id': cid, 'news': summary,
                   'elapsed_ms': min(180_000, round((monotonic() - started) * 1000)),
                   'findings': findings}
            receipt['companies'].append(row)
            receipt['findings'].extend({'company_id': cid, **finding} for finding in findings)
            ids = set(summary.get('global_watch', {}).get('event_ids', []))
            if shared_ids is None:
                shared_ids = ids
            elif ids != shared_ids:
                receipt['findings'].append({'company_id': cid, 'code': 'global_watch_not_shared'})
            _save(output, receipt)
            if summary.get('collection', {}).get('mode') != 'scheduled':
                receipt['findings'].append({'company_id': cid, 'code': 'stop_on_live_fallback'})
                break  # Avoid further GETs that could trigger live source refresh.
        if len(receipt['companies']) == 10:
            receipt['stage'] = 'risks'
            _save(output, receipt)
            risks = client.json('risks', '/api/demo/risks')
            receipt['risks'], risk_findings = audit_risks(risks, {p['company_id'] for p in profiles})
            receipt['findings'].extend(risk_findings)
        receipt['stage'] = 'complete'
        receipt['status'] = 'passed' if not receipt['findings'] else 'findings'
    except AcceptanceError as exc:
        receipt['status'] = 'failed'
        receipt['failure'] = {'stage': exc.stage, 'code': exc.code, 'status': exc.status}
    except Exception:
        receipt['status'] = 'failed'
        receipt['failure'] = {'stage': receipt['stage'], 'code': 'unexpected_exception'}
    _save(output, receipt)
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    result = run(Client(BASE), args.output, expected_revision=args.expected_revision)
    print(json.dumps({'status': result['status'], 'companies': len(result['companies']),
                      'findings': len(result['findings']), 'receipt': str(args.output)}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
