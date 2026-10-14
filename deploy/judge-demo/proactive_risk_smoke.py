#!/usr/bin/env python3
"""Bounded, read-only risk acceptance against an intended hosted HTTPS revision.

The only writes are a synthetic buyer draft and company switches in this script's
ephemeral cookie jar. No provider actions, messages, orders, or model calls are made.
The JSON receipt contains identifiers and coverage counts, never cookies or CSRF.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys

from company_intelligence_news_smoke import PooledClient, safe_link, timestamp
from smoke import AcceptanceError, base_url, key, require


CATEGORIES = {'inventory', 'supplier_delay', 'news'}
SEVERITIES = {'high', 'medium', 'low'}
PROVENANCE = {'synthetic_profile', 'synthetic_plan', 'synthetic_delay_scenario', 'observed_news'}
EVIDENCE_KINDS = {'profile', 'order', 'plan', 'news'}
SOURCE_STATES = {'fresh', 'cached', 'stale', 'empty', 'unavailable', 'offline'}
ACTIVE_STATES = {'fresh', 'cached'}
RELEVANCE = {'direct_material', 'sector', 'route', 'currency'}
MUTABLE_VIEW = ('company_id', 'context_version', 'offers', 'plan', 'plan_request',
                'recovery', 'rfq', 'buyer_draft', 'selected_material_id', 'simulated_outbox')
BUYER_FIELDS = ('material', 'quantity', 'current_stock', 'quantity_unit', 'budget',
                'additional_cost_per_unit', 'decision_date', 'need_by', 'reporting_currency')


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def unique_strings(value, stage, code):
    require(isinstance(value, list) and all(nonempty(x) for x in value)
            and len(value) == len(set(value)), stage, code)
    return set(value)


def validate_alert(alert, company_id, materials, suppliers, orders, stage):
    require(isinstance(alert, dict) and nonempty(alert.get('alert_id'))
            and re.fullmatch(r'risk-[0-9a-f]{20}', alert['alert_id']) is not None
            and alert.get('company_id') == company_id
            and alert.get('category') in CATEGORIES and alert.get('severity') in SEVERITIES
            and all(nonempty(alert.get(field)) for field in ('title', 'summary', 'impact'))
            and alert.get('provenance') in PROVENANCE, stage, 'invalid_alert')
    require((alert['category'] == 'news') == (alert['provenance'] == 'observed_news'),
            stage, 'provenance_mismatch')
    material_ids = unique_strings(alert.get('material_ids'), stage, 'invalid_alert_materials')
    require(material_ids <= materials, stage, 'cross_company_material')
    confidence = alert.get('confidence')
    require(isinstance(confidence, dict) and confidence.get('level') in {'high', 'moderate', 'low'}
            and nonempty(confidence.get('reason')), stage, 'invalid_confidence')
    metrics = alert.get('metrics')
    require(isinstance(metrics, dict) and all(isinstance(k, str) and
            (v is None or type(v) in (str, int, float)) for k, v in metrics.items()),
            stage, 'invalid_metrics')
    evidence = alert.get('evidence')
    require(isinstance(evidence, list) and bool(evidence) and len(evidence) <= 20,
            stage, 'missing_evidence')
    news_refs = set()
    for item in evidence:
        require(isinstance(item, dict) and item.get('kind') in EVIDENCE_KINDS
                and all(nonempty(item.get(field)) for field in ('ref_id', 'label', 'detail')),
                stage, 'invalid_evidence')
        if item['kind'] == 'news':
            news_refs.add(item['ref_id'])
            require(alert['category'] == 'news' and nonempty(item.get('url'))
                    and item.get('observed_at') is not None,
                    stage, 'invalid_news_evidence')
            safe_link(item['url'], stage)
            timestamp(item['observed_at'], stage, 'invalid_observed_at')
        else:
            require(item.get('url') is None, stage, 'synthetic_evidence_url')
            if item['kind'] == 'profile':
                require(item['ref_id'] in materials or item['ref_id'] in suppliers,
                        stage, 'foreign_profile_evidence')
            if item['kind'] == 'order':
                require(item['ref_id'] in orders, stage, 'foreign_order_evidence')
    require((alert['category'] != 'news') or bool(news_refs), stage, 'news_without_observation')
    actions = alert.get('recovery_actions')
    require(isinstance(actions, list) and bool(actions) and len(actions) <= 10,
            stage, 'missing_recovery_actions')
    action_ids = set()
    for action in actions:
        require(isinstance(action, dict) and all(nonempty(action.get(field))
                for field in ('action_id', 'label', 'message'))
                and action.get('requires_review') is True, stage, 'unsafe_recovery_action')
        require(action['action_id'] not in action_ids, stage, 'duplicate_recovery_action')
        action_ids.add(action['action_id'])
    return news_refs


def validate_report(view, profiles, active_id, context, stage, *, rows_out=None):
    require(isinstance(view, dict) and view.get('company_id') == active_id
            and view.get('context_version') == context and view.get('monitoring') == 'while_workspace_open'
            and view.get('news_refresh_seconds') == 900, stage, 'risk_context_mismatch')
    timestamp(view.get('checked_at'), stage, 'invalid_checked_at')
    require(isinstance(view.get('limitations'), list)
            and all(nonempty(x) for x in view['limitations']), stage, 'invalid_limitations')
    companies = view.get('companies')
    require(isinstance(companies, list) and len(companies) == 10, stage, 'risk_company_count')
    by_id = {row.get('company_id'): row for row in companies if isinstance(row, dict)}
    require(set(by_id) == set(profiles) and len(by_id) == 10, stage, 'risk_company_isolation')
    counts = dict.fromkeys(SEVERITIES, 0)
    with_alerts = 0
    digest = {}
    receipt_rows = [] if rows_out is None else rows_out
    for cid, report in by_id.items():
        profile = profiles[cid]
        substage = stage + ':' + cid
        require(report.get('company_name') == profile['name']
                and report.get('sector') == profile['sector'], substage, 'company_identity')
        timestamp(report.get('checked_at'), substage, 'invalid_checked_at')
        coverage = report.get('coverage')
        materials = {m['material_id'] for m in profile['materials']}
        suppliers = {s['supplier_id'] for s in profile['suppliers']}
        orders = {o['order_id'] for o in profile['orders']}
        require(isinstance(coverage, dict) and type(coverage.get('materials_evaluated')) is int
                and coverage['materials_evaluated'] == len(materials),
                substage, 'material_coverage')
        require(nonempty(coverage.get('news_state')) and coverage['news_state'] in SOURCE_STATES
                and type(coverage.get('news_items_evaluated')) is int
                and coverage['news_items_evaluated'] >= 0, substage, 'invalid_news_coverage')
        sources = coverage.get('sources')
        require(isinstance(sources, list), substage, 'invalid_source_coverage')
        for source in sources:
            require(isinstance(source, dict) and nonempty(source.get('source_id'))
                    and source.get('state') in SOURCE_STATES, substage, 'invalid_source_coverage')
        gaps = report.get('data_gaps')
        require(isinstance(gaps, list) and all(nonempty(x) for x in gaps),
                substage, 'invalid_data_gaps')
        if coverage['news_state'] not in ACTIVE_STATES:
            require(bool(gaps) or bool(view['limitations']), substage, 'news_gap_hidden')
        alerts = report.get('alerts')
        require(isinstance(alerts, list) and len(alerts) <= 60, substage, 'alert_limit')
        require(sum(a.get('category') == 'news' for a in alerts if isinstance(a, dict)) <= 5,
                substage, 'news_alert_limit')
        ids = []
        news_refs = set()
        for alert in alerts:
            news_refs.update(validate_alert(alert, cid, materials, suppliers, orders, substage))
            ids.append(alert['alert_id'])
            counts[alert['severity']] += 1
        require(len(ids) == len(set(ids)), substage, 'duplicate_alert_id')
        with_alerts += bool(alerts)
        digest[cid] = {a['alert_id']: (a['category'], a['severity'], a['provenance']) for a in alerts}
        receipt_rows.append({'company_id': cid, 'materials_evaluated': coverage['materials_evaluated'],
                             'alert_count': len(alerts), 'news_alert_count': sum(a['category'] == 'news' for a in alerts),
                             'news_state': coverage['news_state'], 'news_items_evaluated': coverage['news_items_evaluated'],
                             'source_states': {s['source_id']: s['state'] for s in sources},
                             'data_gaps': gaps, 'alert_ids': sorted(ids), 'news_ref_ids': sorted(news_refs)})
    summary = view.get('summary')
    require(isinstance(summary, dict) and all(type(summary.get(k)) is int and summary[k] == counts[k]
            for k in SEVERITIES) and summary.get('companies_with_alerts') == with_alerts,
            stage, 'risk_summary_mismatch')
    require(sum(row['materials_evaluated'] for row in receipt_rows) == 52,
            stage, 'total_material_coverage')
    return digest, receipt_rows


def eligible_news(view, profile, stage):
    """Conservative trigger: current, recent, mapped public item from a healthy source."""
    require(isinstance(view, dict) and isinstance(view.get('sources'), list)
            and isinstance(view.get('items'), list), stage, 'invalid_news_view')
    # A mixed cache can be globally stale while one source remains current.
    if view.get('cache_state') not in ACTIVE_STATES | {'stale'}:
        return set()
    healthy = {s.get('source_id') for s in view['sources'] if isinstance(s, dict)
               and s.get('state') in ACTIVE_STATES}
    material_ids = {m['material_id'] for m in profile['materials']}
    route_ids = {r['route_id'] for r in profile['routes']}
    currencies = set(profile['currencies'])
    now = datetime.now(timezone.utc)
    found = set()
    for item in view['items']:
        if not isinstance(item, dict) or item.get('synthetic') is not False:
            continue
        kind = item.get('relevance_kind')
        if kind not in RELEVANCE or not nonempty(item.get('item_id')):
            continue
        try:
            published = timestamp(item.get('published_at'), stage, 'invalid_news_time')
            retrieved = timestamp(item.get('retrieved_at'), stage, 'invalid_news_time')
        except AcceptanceError:
            continue
        if (published > now or published < now - timedelta(days=7)
                or retrieved > now or retrieved < now - timedelta(days=7)):
            continue
        mats = set(item.get('matched_materials') or [])
        routes = set(item.get('matched_routes') or [])
        ccys = set(item.get('matched_currencies') or [])
        if not mats <= material_ids or not routes <= route_ids or not ccys <= currencies:
            continue
        if kind == 'direct_material' and not mats or kind == 'route' and not routes or kind == 'currency' and not ccys:
            continue
        provider = item.get('provider')
        provider_ids = ({'EIA': {'eia'}, 'ECB': {'ecb'}, 'GDELT': {'gdelt'},
                         'Google News RSS': {s for s in healthy if s.startswith('google_news_')}}).get(provider, set())
        if any(s.get('source_id') in provider_ids and s.get('last_checked_at') == item['retrieved_at']
               for s in view['sources'] if isinstance(s, dict) and s.get('source_id') in healthy):
            found.add(item['item_id'])
    return found


def view_state(session):
    return {field: session.get(field) for field in MUTABLE_VIEW}


def run(base, receipt):
    client = PooledClient(base)
    try:
        health = client.json('health', '/-/health')
        require(health.get('ready') is True and nonempty(health.get('revision'))
                and health['revision'] != 'local', 'health', 'not_deployed_revision')
        receipt['deployed_revision'] = health['revision']
        listing = client.json('catalogue', '/api/demo/companies').get('companies')
        require(isinstance(listing, list) and len(listing) == 10, 'catalogue', 'company_count')
        profiles = {p.get('company_id'): p for p in listing if isinstance(p, dict)}
        require(len(profiles) == 10 and all(nonempty(cid) and p.get('synthetic') is True
                and isinstance(p.get('materials'), list) for cid, p in profiles.items()),
                'catalogue', 'invalid_profiles')
        require(sum(len(p['materials']) for p in profiles.values()) == 52,
                'catalogue', 'total_material_count')
        session = client.json('session', '/api/demo/session')
        active_id, context, csrf = session.get('company_id'), session.get('context_version'), session.get('csrf_token')
        require(active_id in profiles and type(context) is int and context >= 1
                and nonempty(csrf) and session.get('synthetic') is True
                and session.get('persistence') == 'ephemeral' and session.get('offers') == [],
                'session', 'not_fresh')
        initial_id = active_id
        # Seed a visible synthetic form so a no-mutation assertion has content.
        scenario = profiles[active_id].get('quote_coverage', {}).get('scenarios', [])
        if scenario:
            draft = {field: scenario[0]['buyer_defaults'][field] for field in BUYER_FIELDS}
            session = client.json('seed_draft', '/api/demo/buyer-draft', csrf=csrf, body={
                'buyer_draft': draft, 'expected_context_version': context, 'idempotency_key': key()})
            require(session.get('buyer_draft') == draft, 'seed_draft', 'draft_not_saved')
            receipt['seeded_synthetic_buyer_draft'] = True
        before = view_state(session)
        first = client.json('risks:first', '/api/demo/risks')
        first_digest, rows = validate_report(first, profiles, active_id, context,
                                             'risks:first', rows_out=receipt['companies'])
        receipt['company_count'] = len(rows)
        receipt['materials_evaluated'] = sum(row['materials_evaluated'] for row in rows)
        after = client.json('session:after_first', '/api/demo/session')
        require(view_state(after) == before, 'risks:first', 'risk_read_mutated_workspace')
        second = client.json('risks:repeat', '/api/demo/risks')
        second_digest, _ = validate_report(second, profiles, active_id, context, 'risks:repeat')
        require(first_digest == second_digest, 'risks:repeat', 'unstable_or_duplicate_alerts')
        after = client.json('session:after_repeat', '/api/demo/session')
        require(view_state(after) == before, 'risks:repeat', 'risk_read_mutated_workspace')
        receipt['stable_repeated_scan'] = True
        receipt['workspace_preserved'] = True

        # News is an observed side channel: missing/failing sources are recorded,
        # and only eligible current metadata requires a corresponding news signal.
        by_id = {row['company_id']: row for row in rows}
        for cid, profile in profiles.items():
            stage = 'news:' + cid
            if active_id != cid:
                switched = client.json(stage + ':switch', '/api/demo/company', csrf=csrf, body={
                    'company_id': cid, 'expected_context_version': context, 'idempotency_key': key()})
                context = switched.get('context_version')
                require(switched.get('company_id') == cid and type(context) is int,
                        stage, 'company_switch_failed')
                active_id = cid
                receipt['company_switches'] += 1
            try:
                news = client.json(stage, '/api/demo/news')
            except AcceptanceError as exc:
                if exc.status in (429, 502, 503, 504) or exc.code in {'news_unavailable', 'transport_error'}:
                    by_id[cid]['news_check_gap'] = {'code': exc.code, 'status': exc.status}
                    receipt['news_check_gaps'] += 1
                    continue
                raise
            require(news.get('company_id') == cid and news.get('context_version') == context,
                    stage, 'news_context_mismatch')
            eligible = eligible_news(news, profile, stage)
            by_id[cid]['eligible_current_news_items'] = len(eligible)
            if eligible:
                matched = set(by_id[cid]['news_ref_ids']) & eligible
                if not matched:
                    # A source may recover between the initial ten-company scan
                    # and this news read. One cached rescan resolves that race.
                    refreshed = client.json(stage + ':risk_rescan', '/api/demo/risks')
                    _, latest_rows = validate_report(refreshed, profiles, cid, context,
                                                     stage + ':risk_rescan')
                    current = next(row for row in latest_rows if row['company_id'] == cid)
                    matched = set(current['news_ref_ids']) & eligible
                    receipt['news_rescans'] += 1
                require(bool(matched), stage, 'eligible_news_without_risk_signal')
                receipt['companies_with_eligible_news'] += 1
            elif news.get('cache_state') not in ACTIVE_STATES:
                by_id[cid]['news_check_gap'] = {'code': 'no_current_eligible_metadata',
                                                'cache_state': news.get('cache_state')}
                receipt['news_check_gaps'] += 1
        scoped = client.json('risks:switched_company', '/api/demo/risks')
        validate_report(scoped, profiles, active_id, context, 'risks:switched_company')
        receipt['switched_context_verified'] = True
        if active_id != initial_id:
            restored = client.json('session:restore_company', '/api/demo/company', csrf=csrf, body={
                'company_id': initial_id, 'expected_context_version': context, 'idempotency_key': key()})
            context = restored.get('context_version')
            require(restored.get('company_id') == initial_id and type(context) is int
                    and restored.get('buyer_draft') == before['buyer_draft']
                    and restored.get('plan') == before['plan']
                    and restored.get('offers') == before['offers'],
                    'session:restore_company', 'company_workspace_not_restored')
            receipt['company_switches'] += 1
            receipt['original_workspace_restored'] = True
        receipt['ok'] = True
        receipt['checked_at'] = datetime.now(timezone.utc).isoformat()
        return receipt
    finally:
        receipt['http_requests'] = client.calls
        receipt['transport_retries'] = client.transport_retries
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='Intended deployed HTTPS origin')
    parser.add_argument('--output', required=True, help='JSON evidence receipt path')
    args = parser.parse_args()
    receipt = {'ok': False, 'scope': 'hosted_proactive_risks',
               'started_at': datetime.now(timezone.utc).isoformat(), 'company_count': 0,
               'company_switches': 0, 'companies_with_eligible_news': 0,
               'news_check_gaps': 0, 'news_rescans': 0, 'companies': []}
    try:
        run(base_url(args.base), receipt)
    except AcceptanceError as exc:
        receipt.update({'stage': exc.stage, 'code': exc.code, 'status': exc.status})
    except (KeyError, TypeError, IndexError, ValueError):
        receipt.update({'stage': 'response', 'code': 'invalid_response', 'status': None})
    receipt['company_count'] = len(receipt['companies'])
    receipt.setdefault('checked_at', datetime.now(timezone.utc).isoformat())
    Path(args.output).write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'companies'}, sort_keys=True))
    return 0 if receipt['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
