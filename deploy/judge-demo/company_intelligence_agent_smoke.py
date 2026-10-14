#!/usr/bin/env python3
"""Hosted, stateful acceptance for the typed company operator.

Run only against the intended deployed HTTPS revision. The one cookie jar is
ephemeral; the receipt excludes cookies, CSRF tokens and session identifiers.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from company_intelligence_news_smoke import PooledClient
from smoke import AcceptanceError, base_url, key, require


COMPANIES = ('solara-cables', 'terrapack', 'atlas-petrochem')
MODEL_ROLES = {'coordinator', 'evidence', 'procurement'}


def check_turn(result, stage, company_id, context, status):
    require(result.get('company_id') == company_id and result.get('context_version') == context,
            stage, 'operator_context_mismatch')
    require(result.get('status') == status and isinstance(result.get('assistant_text'), str)
            and bool(result['assistant_text'].strip()), stage, 'operator_status')
    steps = result.get('agent_steps')
    require(isinstance(steps, list), stage, 'missing_agent_steps')
    real = {step.get('agent') for step in steps if step.get('provider_call') == 'real'}
    require(real == MODEL_ROLES, stage, 'missing_real_model_roles')
    require(any(step.get('agent') == 'verifier' and step.get('provider_call') == 'none'
                for step in steps), stage, 'missing_deterministic_verifier')
    return {'status': status, 'assistant_text': result['assistant_text'],
            'model_calls': [{'agent': step.get('agent'), 'role': step.get('role'), 'model': step.get('model'),
                             'provider_call': step.get('provider_call')}
                            for step in steps if step.get('agent') in MODEL_ROLES],
            'evidence_ids': result.get('evidence_ids', []),
            'action_result': result.get('action_result'),
            'proposal': result.get('proposal')}


def run(base, receipt):
    client = PooledClient(base)
    try:
        health = client.json('health', '/-/health')
        require(health.get('ready') is True and isinstance(health.get('revision'), str)
                and health['revision'] not in ('', 'local'), 'health', 'not_deployed_revision')
        receipt['deployed_revision'] = health['revision']
        profiles = client.json('catalogue', '/api/demo/companies').get('companies')
        require(isinstance(profiles, list), 'catalogue', 'invalid_catalogue')
        by_id = {profile['company_id']: profile for profile in profiles}
        require(all(cid in by_id for cid in COMPANIES), 'catalogue', 'missing_companies')
        session = client.json('session', '/api/demo/session')
        csrf = session.get('csrf_token')
        context = session.get('context_version')
        require(isinstance(csrf, str) and bool(csrf) and type(context) is int
                and session.get('synthetic') is True and session.get('persistence') == 'ephemeral',
                'session', 'invalid_session')

        def switch(cid):
            nonlocal session, context
            if session['company_id'] != cid:
                session = client.json(cid + ':switch', '/api/demo/company', csrf=csrf, body={
                    'company_id': cid, 'expected_context_version': context,
                    'idempotency_key': key()})
                require(session.get('company_id') == cid and session.get('context_version', 0) > context,
                        cid + ':switch', 'company_switch_failed')
                context = session['context_version']

        def turn(stage, message, expected):
            nonlocal session
            result = client.json(stage, '/api/demo/operator', csrf=csrf, body={
                'message': message, 'expected_context_version': context,
                'idempotency_key': key()})
            checked = check_turn(result, stage, session['company_id'], context, expected)
            receipt['turns'].append({'stage': stage, 'message': message, **checked})
            if 'session' in result:
                session = result['session']
            return result

        def confirm(stage, proposal):
            nonlocal session
            require(isinstance(proposal, dict) and isinstance(proposal.get('proposal_id'), str),
                    stage, 'missing_review_proposal')
            body = {'proposal_id': proposal['proposal_id'], 'reviewed': True,
                    'expected_context_version': context, 'idempotency_key': key()}
            applied = client.json(stage, '/api/demo/operator/confirm', csrf=csrf, body=body)
            require(applied.get('status') == 'applied' and applied.get('company_id') == session['company_id']
                    and applied.get('context_version') == context
                    and applied.get('kind') == proposal.get('kind'), stage, 'proposal_not_applied')
            replay = client.json(stage + ':replay', '/api/demo/operator/confirm', csrf=csrf, body=body)
            require(replay.get('result') == applied.get('result')
                    and replay.get('kind') == applied.get('kind'), stage, 'apply_replay_changed')
            session = applied['session']
            receipt['applies'].append({'stage': stage, 'kind': applied['kind'],
                                       'status': applied['status'], 'replay_identical': True})
            return applied

        switch('solara-cables')
        solara = by_id['solara-cables']
        copper = next(row for row in solara['materials'] if row['material_id'] == 'copper')
        stock = turn('solara:stock', 'How much copper stock do we have?', 'completed')
        require(copper['stock'] in stock['assistant_text'] and copper['quantity_unit'] in stock['assistant_text'],
                'solara:stock', 'stock_answer_not_grounded')
        supplier = next(row for row in solara['suppliers'] if row['name'].startswith('Europa Copper'))
        lead = turn('solara:lead', f"What is {supplier['name']} supplier lead time for copper?", 'completed')
        require(str(supplier['lead_time_days']) in lead['assistant_text']
                and 'day' in lead['assistant_text'].lower(), 'solara:lead', 'lead_answer_not_grounded')

        filled = turn('solara:fill', 'Fill copper quantity to 12000 kg for a draft; do not run a plan.', 'completed')
        require(filled.get('action_result', {}).get('tool') == 'fill_form'
                and session.get('buyer_draft', {}).get('quantity') == '12000',
                'solara:fill', 'form_not_filled')
        loaded = turn('solara:load', 'Load copper quotes for review; do not save them.', 'completed')
        offers = session.get('offers', [])
        require(loaded.get('action_result', {}).get('tool') == 'load_quotes'
                and 1 <= len(offers) <= 3 and all(offer.get('status') != 'saved' for offer in offers),
                'solara:load', 'quotes_not_draft')
        try:
            client.json('solara:compare_before_review', '/api/demo/operator', csrf=csrf, body={
                'message': 'Compare copper quotes', 'expected_context_version': context,
                'idempotency_key': key()})
        except AcceptanceError as exc:
            require(exc.code == 'quotes_unavailable' and exc.status == 409,
                    'solara:compare_before_review', 'wrong_unsaved_rejection')
            receipt['rejections'].append({'stage': 'solara:compare_before_review',
                                          'code': exc.code, 'status': exc.status})
        else:
            raise AcceptanceError('solara:compare_before_review', 'unsaved_quotes_compared')
        session = client.json('solara:after_rejection', '/api/demo/session')
        require(session.get('plan') is None and all(offer.get('status') != 'saved'
                for offer in session.get('offers', [])), 'solara:after_rejection', 'unsaved_state_changed')

        review = turn('solara:review', 'Review loaded copper sample quotes for saving.', 'proposal')
        proposal = review.get('proposal')
        require(isinstance(proposal, dict) and proposal.get('kind') == 'review_quotes',
                'solara:review', 'review_proposal_missing')
        saved = confirm('solara:review_apply', proposal)
        require(saved.get('result', {}).get('status') == 'reviewed_saved'
                and all(offer.get('status') == 'saved' for offer in session.get('offers', [])),
                'solara:review_apply', 'quotes_not_saved')
        compared = turn('solara:compare', 'Compare reviewed copper quotes.', 'completed')
        require(compared.get('action_result', {}).get('tool') == 'compare'
                and isinstance(session.get('plan'), dict), 'solara:compare', 'plan_missing')
        receipt['form_and_plan'] = {'buyer_quantity_kg': session['buyer_draft'].get('quantity'),
                                    'saved_quote_count': len(session['offers']),
                                    'plan_id': session['plan'].get('plan_id')}

        recipient = next(row['name'] for row in solara['suppliers'] if 'copper' in row['materials'])
        email = turn('solara:email', f'Draft a simulated email to {recipient} about copper. '
                     'Subject: Copper quote inquiry. Body: Please review our fictional request.', 'proposal')
        proposal = email.get('proposal')
        require(isinstance(proposal, dict) and proposal.get('kind') == 'simulate_email',
                'solara:email', 'email_proposal_missing')
        sent = confirm('solara:email_apply', proposal)
        outbox = session.get('simulated_outbox', [])
        require(len(outbox) == 1 and sent.get('result') == outbox[0]
                and outbox[0].get('simulated') is True
                and outbox[0].get('external_delivery') is False
                and outbox[0].get('status') == 'simulated_sent',
                'solara:email_apply', 'outbox_state_wrong')
        receipt['outbox'] = {'count': len(outbox), 'recipient_label': outbox[0]['recipient_label'],
                             'subject': outbox[0]['subject'], 'body': outbox[0]['body'],
                             'status': outbox[0]['status'], 'simulated': True,
                             'external_delivery': False}

        switch('terrapack')
        terra = by_id['terrapack']
        resin = next(row for row in terra['materials'] if row['material_id'] == 'pet-resin')
        answer = turn('terrapack:stock', 'How much PET resin stock do we have?', 'completed')
        require(resin['stock'] in answer['assistant_text'] and resin['quantity_unit'] in answer['assistant_text'],
                'terrapack:stock', 'stock_answer_not_grounded')
        require(session.get('plan') is None and not session.get('simulated_outbox'),
                'terrapack:isolation', 'cross_company_state_leak')

        switch('atlas-petrochem')
        atlas = by_id['atlas-petrochem']
        answer = turn('atlas:currency', 'What is our reporting currency?', 'completed')
        require(atlas['reporting_currency'] in answer['assistant_text'],
                'atlas:currency', 'currency_answer_not_grounded')
        require(session.get('plan') is None and not session.get('simulated_outbox'),
                'atlas:isolation', 'cross_company_state_leak')
        receipt.update({'ok': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
                        'company_count': len(COMPANIES), 'single_cookie_jar': True,
                        'real_model_turns': len(receipt['turns'])})
        return receipt
    finally:
        receipt['http_requests'] = client.calls
        receipt['transport_retries'] = client.transport_retries
        client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True, help='Deployed HTTPS origin')
    parser.add_argument('--output', required=True, help='JSON acceptance receipt path')
    args = parser.parse_args()
    result = {'ok': False, 'scope': 'typed_company_operator', 'companies': list(COMPANIES),
              'turns': [], 'applies': [], 'rejections': [],
              'started_at': datetime.now(timezone.utc).isoformat()}
    try:
        run(base_url(args.base), result)
    except AcceptanceError as exc:
        result.update({'stage': exc.stage, 'code': exc.code, 'status': exc.status})
    except (KeyError, TypeError, IndexError, ValueError):
        result.update({'stage': 'response', 'code': 'invalid_response', 'status': None})
    result.setdefault('checked_at', datetime.now(timezone.utc).isoformat())
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({name: value for name, value in result.items() if name not in ('turns', 'applies')}))
    if not result['ok']:
        sys.exit(1)
