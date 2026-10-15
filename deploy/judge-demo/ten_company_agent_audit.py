#!/usr/bin/env python3
"""Bounded hosted action audit: 20 real typed turns plus rules-only workflows.

Each company gets a fresh cookie jar. A receipt is created before the first
request and checkpointed before every later request. A pending request after a
transport failure has an uncertain outcome and must not be retried blindly.
No request searches the Internet or delivers an external message.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sys

from smoke import AcceptanceError, Client, base_url, key, require

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'procurement-engine' / 'src'))
from procure.demo.quote_service import fixture_review_values


MODEL_ROLES = {'coordinator', 'evidence', 'procurement'}
MAX_TYPED_TURNS = 20


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(path):
    path = re.sub(r'^(/api/demo/offers/)[^/]+(/(?:correct|save))$',
                  r'\1{id}\2', path)
    return re.sub(r'^(/api/demo/rfq/)[^/]+(/download)$', r'\1{id}\2', path)


class Receipt:
    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data

    @classmethod
    def create(cls, path, data):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.write('\n')
        return cls(path, data)

    def checkpoint(self):
        temporary = self.path.with_name(self.path.name + '.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(self.data, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def before(self, stage, path, method):
        self.data['pending'] = {'stage': stage, 'path': safe_path(path),
                                'method': method, 'started_at': now()}
        self.checkpoint()

    def after(self, *, code='ok', status=200):
        pending = self.data.pop('pending')
        self.data['calls'].append({**pending, 'finished_at': now(), 'code': code, 'status': status})
        self.checkpoint()


class AuditedClient:
    def __init__(self, base, receipt):
        self.client = Client(base)
        self.receipt = receipt

    def json(self, stage, path, *, body=None, csrf=None):
        self.receipt.before(stage, path, 'POST' if body is not None else 'GET')
        try:
            result = self.client.json(stage, path, body=body, csrf=csrf)
        except AcceptanceError as exc:
            if exc.status is not None:
                self.receipt.after(code=exc.code, status=exc.status)
            raise
        self.receipt.after()
        return result

    def text(self, stage, path):
        self.receipt.before(stage, path, 'GET')
        try:
            result = self.client.text(stage, path)
        except AcceptanceError as exc:
            if exc.status is not None:
                self.receipt.after(code=exc.code, status=exc.status)
            raise
        self.receipt.after()
        return result


def scenario_and_messages(profile):
    scenarios = profile['quote_coverage']['scenarios']
    require(bool(scenarios), profile['company_id'], 'no_quote_scenario')
    scenario = scenarios[0]
    material = next(row for row in profile['materials']
                    if row['material_id'] == scenario['material_id'])
    defaults = scenario['buyer_defaults']
    require(defaults['quantity_unit'] == material['quantity_unit']
            and defaults['material'] == material['material_id']
            and len(scenario['fixture_ids']) == 3,
            profile['company_id'], 'scenario_defaults')
    messages = (
        f"Analyze inventory stock cover for {material['name']} ({material['material_id']}).",
        f"Fill {material['material_id']} quantity to {defaults['quantity']} "
        f"{material['quantity_unit']} for a draft; do not run a plan.",
    )
    return scenario, material, messages


def check_model_steps(result, stage):
    steps = result.get('agent_steps')
    require(isinstance(steps, list), stage, 'missing_agent_steps')
    real = [step.get('agent') for step in steps if step.get('provider_call') == 'real']
    require(len(real) == 3 and set(real) == MODEL_ROLES and
            all(step.get('model') not in (None, '', 'deterministic') for step in steps
                if step.get('agent') in MODEL_ROLES) and
            any(step.get('agent') == 'verifier' and step.get('provider_call') == 'none'
                for step in steps), stage, 'missing_real_model_roles')
    return len(real)


def selected_profiles(profiles, start_index):
    require(type(start_index) is int and 0 <= start_index < len(profiles),
            'catalogue', 'invalid_company_start')
    return profiles[start_index:]


def _check_context(payload, company_id, version, stage):
    require(payload.get('company_id') == company_id and
            payload.get('context_version') == version, stage, 'context_mismatch')


def _corrected_field(offer, field):
    return (offer['items'][0][field[9:]] if field.startswith('items[0].')
            else offer['fields'][field])


def direct_workflow(client, receipt, profile, scenario, session, csrf):
    """One current material through exact rules fixtures and reviewed RFQ."""
    cid = profile['company_id']
    material = scenario['material_id']
    context = session['context_version']
    stage = cid + ':direct'

    def post(step, path, payload):
        return client.json(stage + ':' + step, path, csrf=csrf,
                           body={**payload, 'expected_context_version': context})

    offers = []
    for fixture_id in scenario['fixture_ids']:
        extracted = post('extract', '/api/demo/extract', {
            'fixture_id': fixture_id, 'mode': 'rules', 'idempotency_key': key()})
        offer = extracted['offer']
        require(offer['fixture_id'] == fixture_id and offer['extractor'] == 'rules'
                and offer['synthetic'] is True and offer['status'] != 'saved',
                stage, 'quote_not_draft')
        for field, value in fixture_review_values(fixture_id).items():
            offer = post('correct', '/api/demo/offers/' + offer['offer_id'] + '/correct', {
                'field': field, 'value': value, 'expected_version': offer['version'],
                'idempotency_key': key()})['offer']
            checked = _corrected_field(offer, field)
            require(checked['status'] == 'corrected' and checked['value'] == value,
                    stage, 'quote_review_mismatch')
        offer = post('save', '/api/demo/offers/' + offer['offer_id'] + '/save', {
            'expected_version': offer['version'], 'idempotency_key': key()})['offer']
        require(offer['status'] == 'saved' and not offer['blocking_reasons'],
                stage, 'quote_not_saved')
        offers.append(offer)

    versions = {offer['offer_id']: offer['version'] for offer in offers}
    defaults = {field: value for field, value in scenario['buyer_defaults'].items()
                if field not in {'offer_ids', 'expected_versions', 'quantity_kg',
                                 'current_stock_kg', 'additional_cost_per_kg'}}
    plan = post('plan', '/api/demo/plan', {**defaults,
        'offer_ids': list(versions), 'expected_versions': versions})
    purchase = Decimal(defaults['quantity']) - Decimal(defaults['current_stock'])
    require(plan['company_id'] == cid and plan['material'] == material
            and plan['quantity_unit'] == defaults['quantity_unit']
            and Decimal(plan['required_purchase']) == purchase and purchase > 0
            and plan['offer_versions'] == versions and
            plan['selected_offer_id'] in versions,
            stage, 'plan_wrong')
    selected = plan['selected_offer_id']
    recovery = post('recovery', '/api/demo/recovery', {
        'baseline_plan_id': plan['plan_id'], 'expected_versions': versions,
        'delay_days_by_offer': {selected: 60}, 'idempotency_key': key()})
    revised = recovery['scenario']
    require(recovery['outcome'] == 'changed' and revised['company_id'] == cid
            and revised['material'] == material and revised['offer_versions'] == versions
            and revised['selected_offer_id'] != selected and
            Decimal(revised['required_purchase']) == purchase,
            stage, 'recovery_wrong')
    draft_body = {'plan_id': revised['plan_id'], 'expected_versions': versions,
                  'reviewed': True, 'idempotency_key': key()}
    draft = post('rfq', '/api/demo/rfq', draft_body)
    replay = post('rfq_replay', '/api/demo/rfq', draft_body)
    require(draft['draft_id'] == replay['draft_id']
            and draft['status'] == 'draft_unsent' and draft['company_id'] == cid
            and draft['material'] == material and draft['synthetic'] is True,
            stage, 'rfq_wrong')
    download = client.text(stage + ':rfq_download',
                           '/api/demo/rfq/' + draft['draft_id'] + '/download')
    require(download == draft['text'] and 'UNSENT REQUEST FOR QUOTATION' in download,
            stage, 'rfq_download_wrong')
    current = client.json(stage + ':session', '/api/demo/session')
    _check_context(current, cid, context, stage)
    require(current['plan']['plan_id'] == revised['plan_id']
            and current['rfq']['draft_id'] == draft['draft_id']
            and len(current['offers']) == 3 and
            any(item['draft_id'] == draft['draft_id'] and item['status'] == 'draft_unsent'
                for item in current['drafts']), stage, 'draft_not_persisted')
    receipt.data['companies'][-1]['direct'] = {
        'mode': 'rules', 'material_id': material, 'saved_quote_count': 3,
        'plan_compared': True, 'recovery_changed': True,
        'rfq_unsent_replay': True, 'rfq_download': True,
        'draft_persisted': True}
    receipt.checkpoint()
    return current


def audit_company(base, receipt, profile, *, typed_enabled):
    cid = profile['company_id']
    scenario, material, messages = scenario_and_messages(profile)
    receipt.data['companies'].append({'company_id': cid, 'material_id': material['material_id'],
                                      'typed': [], 'direct': None})
    receipt.checkpoint()
    client = AuditedClient(base, receipt)
    session = client.json(cid + ':session_new', '/api/demo/session')
    csrf = session.get('csrf_token')
    require(isinstance(csrf, str) and csrf and session.get('synthetic') is True
            and session.get('persistence') == 'firestore' and
            not session.get('offers') and session.get('plan') is None
            and session.get('rfq') is None and not session.get('drafts')
            and not session.get('simulated_outbox'), cid, 'session_not_fresh')
    context = session['context_version']
    if session['company_id'] != cid:
        session = client.json(cid + ':select', '/api/demo/company', csrf=csrf, body={
            'company_id': cid, 'expected_context_version': context,
            'idempotency_key': key()})
        require(session['company_id'] == cid and session['context_version'] > context,
                cid, 'company_switch_failed')
        context = session['context_version']
    if typed_enabled:
        for index, message in enumerate(messages):
            stage = cid + (':analytics' if index == 0 else ':form')
            if receipt.data['typed_turns'] >= receipt.data['typed_limit']:
                break
            try:
                result = client.json(stage, '/api/demo/operator', csrf=csrf, body={
                    'message': message, 'expected_context_version': context,
                    'idempotency_key': key()})
            except AcceptanceError as exc:
                if exc.status == 429:
                    receipt.data['typed_stopped'] = {'stage': stage, 'code': exc.code,
                        'remaining_planned': receipt.data['typed_limit'] - receipt.data['typed_turns']}
                    receipt.checkpoint()
                    typed_enabled = False
                    break
                raise
            receipt.data['typed_turns'] += 1
            _check_context(result, cid, context, stage)
            calls = check_model_steps(result, stage)
            expected_tool = None if index == 0 else 'fill_form'
            require(result.get('status') == 'completed' and bool(result.get('assistant_text'))
                    and result.get('proposal') is None
                    and (expected_tool is None and result.get('read_only') is True
                         or expected_tool is not None and
                         result.get('action_result', {}).get('tool') == expected_tool),
                    stage, 'typed_action_wrong')
            if index == 1:
                session = result.get('session') or client.json(stage + ':session', '/api/demo/session')
                require(session.get('buyer_draft', {}).get('quantity') ==
                        scenario['buyer_defaults']['quantity'] and
                        session['buyer_draft']['quantity_unit'] == material['quantity_unit'],
                        stage, 'form_not_saved')
            receipt.data['companies'][-1]['typed'].append({
                'kind': 'inventory_analytics' if index == 0 else 'explicit_form_fill',
                'status': 'completed', 'real_model_roles': calls,
                'model': next(step['model'] for step in result['agent_steps']
                              if step['agent'] == 'coordinator')})
            receipt.checkpoint()
    direct_workflow(client, receipt, profile, scenario, session, csrf)
    return typed_enabled


def run(base, expected_revision, receipt, *, start_company_index=0):
    catalogue_client = AuditedClient(base, receipt)
    health = catalogue_client.json('health', '/-/health')
    require(health.get('ready') is True and health.get('revision') == expected_revision
            and health.get('persistence') == 'firestore',
            'health', 'unexpected_revision_or_storage')
    receipt.data['revision'] = expected_revision
    profiles = catalogue_client.json('catalogue', '/api/demo/companies').get('companies')
    require(isinstance(profiles, list) and len(profiles) == 10 and
            len({item['company_id'] for item in profiles}) == 10,
            'catalogue', 'not_ten_companies')
    receipt.checkpoint()
    selected = selected_profiles(profiles, start_company_index)
    require(receipt.data['typed_limit'] <= 2 * len(selected),
            'catalogue', 'typed_limit_exceeds_matrix')
    typed_enabled = True
    for profile in selected:
        typed_enabled = audit_company(base, receipt, profile,
                                      typed_enabled=typed_enabled)
    receipt.data['ok'] = (len(receipt.data['companies']) == len(selected) and
                          receipt.data['typed_turns'] == receipt.data['typed_limit'] and
                          all(item['direct'] is not None for item in receipt.data['companies']))
    receipt.data['checked_at'] = now()
    receipt.checkpoint()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--start-company-index', type=int, default=0)
    parser.add_argument('--max-typed-turns', type=int, default=MAX_TYPED_TURNS)
    args = parser.parse_args()
    if not 0 <= args.max_typed_turns <= MAX_TYPED_TURNS:
        parser.error('--max-typed-turns must be between 0 and 20')
    data = {'ok': False, 'scope': 'ten_company_agent_actions',
            'started_at': now(), 'revision': None, 'typed_limit': args.max_typed_turns,
            'start_company_index': args.start_company_index,
            'typed_turns': 0, 'typed_stopped': None, 'companies': [], 'calls': [],
            'pending': None, 'coverage_note': 'Only inventory analytics and form fill use real typed models; quote, recovery and RFQ checks use rules-mode REST. Simulated email is local injected-model coverage.'}
    receipt = Receipt.create(args.output, data)
    try:
        run(base_url(args.base), args.expected_revision, receipt,
            start_company_index=args.start_company_index)
    except AcceptanceError as exc:
        data['failure'] = {'stage': exc.stage, 'code': exc.code, 'status': exc.status}
        receipt.checkpoint()
    except (KeyError, TypeError, ValueError, IndexError):
        data['failure'] = {'stage': 'response', 'code': 'invalid_response', 'status': None}
        receipt.checkpoint()
    print(json.dumps({key: data[key] for key in
                      ('ok', 'revision', 'typed_turns', 'typed_stopped', 'failure') if key in data}))
    if not data['ok']:
        sys.exit(1)


if __name__ == '__main__':
    main()
