"""Grounded reads remain available without consuming live-model capacity."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from procure.demo import app as demo_app


class NoModel:
    def __init__(self, available):
        self.available = available

    def __call__(self, role, prompt):
        raise AssertionError('A deterministic inventory read must not call a model')


def ask(client, session, message, key, context=None):
    return client.post('/api/demo/operator',
        headers={'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']},
        json={'message': message, 'expected_context_version': context or session['context_version'],
              'idempotency_key': key})


@pytest.mark.parametrize('available', [False, True])
def test_broad_read_works_without_provider_or_model_budget_and_preserves_guards(monkeypatch, available):
    monkeypatch.setattr(demo_app, 'MAX_OPERATOR_PROCESS_CALLS_PER_HOUR', 0)
    application = demo_app.create_app(
        clock=lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
        operator_provider=NoModel(available))
    with TestClient(application, base_url='https://demo.test') as client:
        session = client.get('/api/demo/session').json()
        prompt = 'Explain the inventory position and sourcing options for Copper cathode at Solara Cables.'
        response = ask(client, session, prompt, 'grounded-read')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['status'] == 'completed' and result['read_only'] is True
        assert '2000 kg' in result['assistant_text'] and '10 days' in result['assistant_text']
        assert result['proposal'] is None and not result.get('action_result')
        assert ask(client, session, prompt, 'grounded-read').json() == result
        current = client.get('/api/demo/session').json()
        for field in ('offers', 'drafts', 'simulated_outbox', 'plan', 'rfq',
                      'buyer_draft', 'selected_material_id', 'company_id', 'context_version'):
            assert current.get(field) == session.get(field)
        assert ask(client, session, prompt, 'wrong-context', context=2).status_code == 409
        # A read path must not exempt unrelated model/action requests from limits.
        ordinary = ask(client, session, 'Watch copper', 'model-action')
        if available:
            assert ordinary.status_code == 429
        else:
            assert ordinary.status_code == 200 and ordinary.json()['status'] == 'unavailable'
