"""Decimal input bounds for the date-only operations trace."""
import pytest
from fastapi.testclient import TestClient

from procure.demo.app import create_app
from procure.demo.operations_decision import simulate_daily_service


@pytest.mark.parametrize('field', ['stock', 'daily_demand', 'safety', 'firm_receipt', 'unfirm_receipt'])
@pytest.mark.parametrize('quantity', ['1e10000', '1e1000000', '1e-1000000', '1.2345678901234567890123456789'])
def test_oversized_numeric_inputs_fail_before_service_arithmetic(field, quantity):
    args = dict(stock='30', daily_demand='10', safety='10',
                start='2026-10-19', end='2026-10-20', unit='tonne', receipts=[])
    if field.endswith('receipt'):
        args['receipts'] = [{'arrival_date': '2026-10-19', 'quantity': quantity,
                             'firm': field == 'firm_receipt'}]
    else:
        args[field] = quantity
    with pytest.raises(ValueError):
        simulate_daily_service(**args)


def test_direct_service_rejects_more_than_twelve_receipts():
    with pytest.raises(ValueError):
        simulate_daily_service(stock='30', daily_demand='10', safety='10',
            start='2026-10-19', end='2026-10-20', unit='roll',
            receipts=[{'arrival_date': '2026-10-19', 'quantity': '1', 'firm': False}] * 13)


def test_fractional_and_whole_quantities_remain_exact():
    result = simulate_daily_service(stock='10.25', daily_demand='1.125', safety='2',
        start='2026-10-19', end='2026-10-20', unit='tonne',
        receipts=[{'arrival_date': '2026-10-19', 'quantity': '3.5', 'firm': True}])
    assert [day['closing_stock'] for day in result['trace']] == ['9.125', '11.5']
    assert result['trace'][1]['firm_receipts_usable'] == '3.5'


@pytest.mark.parametrize('quantity', ['1e10000', '1e1000000'])
def test_preview_api_returns_422_for_oversized_exponent(quantity):
    client = TestClient(create_app(), base_url='https://demo.test')
    session = client.get('/api/demo/session').json()
    headers = {'Origin': 'https://demo.test', 'X-CSRF-Token': session['csrf_token']}
    overrides = {'stock': quantity, 'daily_demand': '1', 'safety': '0',
                 'start': '2026-10-19', 'end': '2026-10-20', 'receipts': []}
    body = {'expected_context_version': 1, 'idempotency_key': 'decimal-bound',
            'material_id': 'copper', 'overrides': overrides}
    response = client.post('/api/demo/v1/company-decisions/preview', json=body, headers=headers)
    assert response.status_code == 422, response.text
