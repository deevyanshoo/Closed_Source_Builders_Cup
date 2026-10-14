#!/usr/bin/env python3
"""One hosted, rules-only visit through all fictional material quote scenarios.

The deployment owner runs this against the intended HTTPS revision. No paid
provider is called. The receipt contains no session secret or generated ID.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import requests

from smoke import (AcceptanceError, MAX_RESPONSE_BYTES, TIMEOUT_SECONDS, base_url,
                   denied, key, origin_of, require)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'procurement-engine' / 'src'))
from procure.demo.quote_service import fixture_bytes, fixture_review_values


class PooledClient:
    """Fixed-origin, bounded HTTP with one connection retry and one cookie jar."""

    def __init__(self, base):
        self.base = base
        self.origin = origin_of(base)
        self.session = requests.Session()
        self.session.trust_env = False
        self.calls = 0
        self.transport_retries = 0

    def close(self):
        self.session.close()

    def _request(self, stage, path, *, payload=None, csrf=None, origin=None, accept='application/json'):
        require(isinstance(path, str) and path.startswith('/') and not path.startswith('//')
                and '?' not in path and '#' not in path, stage, 'invalid_path')
        url = self.base + path
        require(origin_of(url) == self.origin and urlsplit(url).fragment == '',
                stage, 'invalid_path')
        headers = {'Accept': accept}
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': origin or self.base})
            if csrf is not None:
                headers['X-CSRF-Token'] = csrf
        for attempt in range(2):
            self.calls += 1
            try:
                with self.session.request('POST' if payload is not None else 'GET', url,
                                          data=payload, headers=headers, timeout=TIMEOUT_SECONDS,
                                          allow_redirects=False, stream=True, verify=True) as response:
                    raw = bytearray()
                    for chunk in response.iter_content(chunk_size=8192):
                        raw.extend(chunk)
                        require(len(raw) <= MAX_RESPONSE_BYTES, stage, 'response_too_large')
                    return response.status_code, response.headers, bytes(raw)
            except requests.exceptions.SSLError:
                raise AcceptanceError(stage, 'transport_error') from None
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
                if attempt == 0:
                    self.transport_retries += 1
                    continue
                raise AcceptanceError(stage, 'transport_error') from None
            except requests.exceptions.RequestException:
                raise AcceptanceError(stage, 'transport_error') from None
        raise AcceptanceError(stage, 'transport_error')

    def json(self, stage, path, *, body=None, csrf=None, origin=None, expected_status=200):
        payload = None if body is None else json.dumps(body, separators=(',', ':'),
                                                      allow_nan=False).encode('utf-8')
        status, _, raw = self._request(stage, path, payload=payload, csrf=csrf, origin=origin)
        try:
            data = json.loads(raw)
        except (UnicodeError, ValueError):
            raise AcceptanceError(stage, 'invalid_json_response') from None
        require(isinstance(data, dict), stage, 'invalid_response')
        if status != expected_status:
            error = data.get('error')
            code = error.get('code') if isinstance(error, dict) else None
            raise AcceptanceError(stage, code if isinstance(code, str) else 'unexpected_http_status', status)
        return data

    def text(self, stage, path, *, expected_status=200):
        status, headers, raw = self._request(stage, path, accept='text/plain')
        if status != expected_status:
            try:
                error = json.loads(raw).get('error', {})
                code = error.get('code') if isinstance(error, dict) else None
            except (UnicodeError, ValueError, AttributeError):
                code = None
            raise AcceptanceError(stage, code if isinstance(code, str) else 'unexpected_http_status', status)
        content_type = headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
        disposition = headers.get('Content-Disposition', '')
        require(content_type == 'text/plain' and 'synthetic-rfq-draft.txt' in disposition,
                stage, 'download_headers_wrong')
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise AcceptanceError(stage, 'download_not_utf8') from None


def number(value, stage):
    require(isinstance(value, str), stage, 'non_decimal_value')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise AcceptanceError(stage, 'non_decimal_value') from None
    require(result.is_finite(), stage, 'non_decimal_value')
    return result


def corrected_field(offer, field):
    if field.startswith('items[0].'):
        return offer['items'][0][field[len('items[0].'):]]
    return offer['fields'][field]


def _run(base, client, receipt):
    health = client.json('health', '/-/health')
    require(health.get('ready') is True and isinstance(health.get('revision'), str)
            and health['revision'] and health['revision'] != 'local', 'health', 'not_ready')
    receipt['deployed_revision'] = health['revision']
    profiles = client.json('catalogue', '/api/demo/companies').get('companies')
    require(isinstance(profiles, list) and len(profiles) == 10, 'catalogue', 'company_count')
    listed = client.json('fixtures', '/api/demo/fixtures')
    fixtures = listed.get('fixtures')
    require(listed.get('synthetic') is True and isinstance(fixtures, list)
            and len(fixtures) == 156, 'fixtures', 'fixture_count')
    fixture_by_id = {entry['id']: entry for entry in fixtures}
    require(len(fixture_by_id) == 156, 'fixtures', 'duplicate_fixture')

    scenarios = [(profile, scenario) for profile in profiles
                 for scenario in profile['quote_coverage']['scenarios']]
    fixture_ids = [fixture for _, scenario in scenarios for fixture in scenario['fixture_ids']]
    require(len(scenarios) == 52 and len(fixture_ids) == 156
            and len(set(fixture_ids)) == 156 and set(fixture_ids) == set(fixture_by_id),
            'catalogue', 'scenario_coverage')

    session = client.json('session', '/api/demo/session')
    csrf = session.get('csrf_token')
    require(isinstance(csrf, str) and csrf and session.get('synthetic') is True
            and session.get('persistence') == 'ephemeral' and session.get('offers') == [],
            'session', 'not_fresh')
    context = session['context_version']
    require(type(context) is int and context >= 1, 'session', 'bad_context')
    rows = receipt['scenarios']
    checked_fixtures = 0
    corrections = 0
    company_switches = 0
    maximum_active_offers = 0

    for profile in profiles:
        cid = profile['company_id']
        if session['company']['company_id'] != cid:
            session = client.json(cid, '/api/demo/company', csrf=csrf, body={
                'company_id': cid, 'expected_context_version': context,
                'idempotency_key': key()})
            context = session['context_version']
            require(session['company']['company_id'] == cid and session['offers'] == [],
                    cid, 'company_switch_not_fresh')
            company_switches += 1

        def post(stage, path, body):
            return client.json(stage, path, csrf=csrf,
                               body={**body, 'expected_context_version': context})

        company_scenarios = profile['quote_coverage']['scenarios']
        require({scenario['material_id'] for scenario in company_scenarios}
                == {material['material_id'] for material in profile['materials']},
                cid, 'material_coverage')
        for scenario_index, scenario in enumerate(company_scenarios, start=1):
            material = scenario['material_id']
            stage = cid + ':' + material
            defaults = {name: value for name, value in scenario['buyer_defaults'].items()
                        if name not in ('offer_ids', 'expected_versions', 'quantity_kg',
                                        'current_stock_kg', 'additional_cost_per_kg')}
            unit = defaults['quantity_unit']
            expected_unit = next(row['quantity_unit'] for row in profile['materials']
                                 if row['material_id'] == material)
            require(defaults['material'] == material and unit == expected_unit
                    and len(scenario['fixture_ids']) == 3, stage, 'scenario_defaults')

            offers = []
            for fixture_id in scenario['fixture_ids']:
                filename, original_bytes = fixture_bytes(fixture_id)
                expected_hash = 'sha256:' + hashlib.sha256(original_bytes).hexdigest()
                listing = fixture_by_id[fixture_id]
                review_values = fixture_review_values(fixture_id)
                require(listing['filename'] == filename and listing['review_values'] == review_values,
                        stage, 'fixture_review_drift')
                extraction = post(stage, '/api/demo/extract', {
                    'fixture_id': fixture_id, 'mode': 'rules', 'idempotency_key': key()})
                offer = extraction['offer']
                require(extraction.get('replayed') is False and offer['fixture_id'] == fixture_id
                        and offer['document']['document_id'] == expected_hash
                        and offer['extractor'] == 'rules' and offer['synthetic'] is True,
                        stage, 'unverified_extraction')
                for field, value in review_values.items():
                    offer = post(stage, '/api/demo/offers/' + offer['offer_id'] + '/correct', {
                        'field': field, 'value': value, 'expected_version': offer['version'],
                        'idempotency_key': key()})['offer']
                    reviewed = corrected_field(offer, field)
                    require(reviewed.get('status') == 'corrected' and reviewed.get('value') == value
                            and isinstance(reviewed.get('original'), dict),
                            stage, 'correction_provenance')
                    corrections += 1
                offer = post(stage, '/api/demo/offers/' + offer['offer_id'] + '/save', {
                    'expected_version': offer['version'], 'idempotency_key': key()})['offer']
                require(offer['status'] == 'saved' and not offer['blocking_reasons']
                        and offer['document']['document_id'] == expected_hash,
                        stage, 'review_not_saved')
                offers.append(offer)
                checked_fixtures += 1
                receipt['fixture_count'] = checked_fixtures

            maximum_active_offers = max(maximum_active_offers, 3 * scenario_index)
            versions = {offer['offer_id']: offer['version'] for offer in offers}
            request = {**defaults, 'offer_ids': list(versions), 'expected_versions': versions}
            plan = post(stage, '/api/demo/plan', request)
            purchase = number(defaults['quantity'], stage) - number(defaults['current_stock'], stage)
            require(purchase > 0 and plan['synthetic'] is True and plan['company_id'] == cid
                    and plan['material'] == material and plan['quantity_unit'] == unit
                    and number(plan['required_purchase'], stage) == purchase
                    and plan['offer_versions'] == versions and plan['forecast_action'] == 'ABSTAIN'
                    and plan['customer_savings'] is None, stage, 'plan_context_or_purchase')
            alternatives = {row['offer_id']: row for row in plan['alternatives']}
            require(set(alternatives) == set(versions), stage, 'alternatives_wrong')
            for alternative in alternatives.values():
                require(alternative['quantity_unit'] == unit
                        and number(alternative['quantity'], stage) == purchase
                        and alternative['unit_price_per_unit'] is not None,
                        stage, 'unit_price_missing')
                if alternative['total_cost'] is not None:
                    calculated = purchase * (number(alternative['unit_price_per_unit'], stage)
                                             + number(defaults['additional_cost_per_unit'], stage))
                    require(number(alternative['total_cost'], stage) == calculated,
                            stage, 'price_arithmetic')
            selected = plan['selected_offer_id']
            require(selected in alternatives and alternatives[selected]['feasible'] is True
                    and number(plan['total_cost'], stage)
                    == number(alternatives[selected]['total_cost'], stage),
                    stage, 'selected_price_wrong')
            expected_role = 2 if cid == 'solara-cables' and material == 'copper' else 1
            require(selected == offers[expected_role]['offer_id'], stage, 'selected_quote_wrong')

            wrong_unit = 'barrel' if unit == 'kg' else 'kg'
            denied(client, stage, '/api/demo/plan',
                   {**request, 'quantity_unit': wrong_unit,
                    'expected_context_version': context}, csrf, 422, 'unit_mismatch')
            recovery = post(stage, '/api/demo/recovery', {
                'baseline_plan_id': plan['plan_id'], 'expected_versions': versions,
                'delay_days_by_offer': {selected: 60}, 'idempotency_key': key()})
            revised = recovery['scenario']
            revised_alternatives = {row['offer_id']: row for row in revised['alternatives']}
            require(recovery['outcome'] == 'changed' and revised['company_id'] == cid
                    and revised['material'] == material and revised['quantity_unit'] == unit
                    and revised['offer_versions'] == versions
                    and recovery['scenario_request']['delay_days_by_offer'] == {selected: 60}
                    and revised['selected_offer_id'] in revised_alternatives
                    and revised['selected_offer_id'] != selected
                    and number(revised['required_purchase'], stage) == purchase
                    and number(revised['total_cost'], stage)
                    == number(revised_alternatives[revised['selected_offer_id']]['total_cost'], stage),
                    stage, 'recovery_not_changed')

            draft_request = {'plan_id': revised['plan_id'], 'expected_versions': versions,
                             'reviewed': True, 'idempotency_key': key()}
            draft = post(stage, '/api/demo/rfq', draft_request)
            replay = post(stage, '/api/demo/rfq', draft_request)
            require(draft['draft_id'] == replay['draft_id'] and draft['status'] == 'draft_unsent'
                    and draft['synthetic'] is True and draft['company_id'] == cid
                    and draft['material'] == material and draft['quantity_unit'] == unit
                    and number(draft['quantity'], stage) == purchase
                    and number(draft['total_cost'], stage) == number(revised['total_cost'], stage)
                    and f'Requested quantity: {draft["quantity"]} {unit}' in draft['text']
                    and 'UNSENT REQUEST FOR QUOTATION' in draft['text'], stage, 'rfq_wrong')
            require(client.text(stage, '/api/demo/rfq/' + draft['draft_id'] + '/download')
                    == draft['text'], stage, 'rfq_download_wrong')
            rows.append({'company_id': cid, 'material': material, 'unit': unit,
                         'purchase': str(purchase), 'baseline_cost': plan['total_cost'],
                         'recovery_cost': revised['total_cost'],
                         'currency': plan['reporting_currency'], 'rules_fixtures': 3,
                         'wrong_unit_rejected': True, 'recovery_changed': True,
                         'rfq_unsent_replayed_downloaded': True})
            receipt['scenario_count'] = len(rows)
            print(f'{stage} verified', flush=True)

        current = client.json(cid, '/api/demo/session')
        expected_active = 3 * len(company_scenarios)
        require(current['company']['company_id'] == cid and current['context_version'] == context
                and len(current['offers']) == expected_active and expected_active <= 48,
                cid, 'active_offer_limit')

    final_session = client.json('final_session', '/api/demo/session')
    final_export = client.json('final_export', '/api/demo/export')
    require(final_session['company']['company_id'] == profiles[-1]['company_id']
            and final_export['company']['company_id'] == profiles[-1]['company_id']
            and final_export['context_version'] == context
            and len(final_export['offers']) == len(final_session['offers'])
            and final_export['plan'] == final_session['plan']
            and final_export['rfq'] == final_session['rfq']
            and final_export['rfq']['status'] == 'draft_unsent'
            and final_export['plan']['material'] == rows[-1]['material']
            and final_export['synthetic'] is True and final_export['persistence'] == 'ephemeral',
            'final_export', 'single_visit_state_wrong')
    idempotency_keys = checked_fixtures * 2 + corrections + company_switches + len(rows) * 2
    require(checked_fixtures == 156 and len(rows) == 52 and maximum_active_offers <= 48
            and idempotency_keys <= 2400 and client.calls <= 4000,
            'coverage', 'incomplete')
    return {'ok': True, 'base': base, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'deployed_revision': health['revision'], 'mode': 'rules',
            'single_browser_session': True, 'company_count': len(profiles),
            'scenario_count': len(rows), 'fixture_count': checked_fixtures,
            'maximum_active_offers': maximum_active_offers,
            'idempotency_keys_used': idempotency_keys, 'http_requests': client.calls,
            'transport_retries': client.transport_retries,
            'final_export_ephemeral': True, 'scenarios': rows}


def run(base, receipt):
    client = PooledClient(base)  # One cookie jar for the browser-equivalent visit.
    try:
        return _run(base, client, receipt)
    finally:
        receipt['http_requests'] = client.calls
        receipt['transport_retries'] = client.transport_retries
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = {'ok': False, 'scenario_count': 0, 'fixture_count': 0, 'scenarios': []}
    try:
        result.update(run(base_url(args.base), result))
    except AcceptanceError as exc:
        result.update({'stage': exc.stage, 'code': exc.code, 'status': exc.status})
    except (KeyError, TypeError, IndexError, StopIteration):
        result.update({'stage': 'response', 'code': 'invalid_response', 'status': None})
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'scenarios'}))
    if not result['ok']:
        sys.exit(1)
