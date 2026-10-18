from decimal import Decimal
from procure.demo.operations_decision import simulate_daily_service


def test_date_only_receipt_does_not_rescue_same_day_stockout():
    result = simulate_daily_service(stock='30', daily_demand='10', safety='10',
        start='2026-10-19', end='2026-10-25', unit='tonne',
        receipts=[{'arrival_date': '2026-10-21', 'quantity': '20', 'firm': True},
                  {'arrival_date': '2026-10-23', 'quantity': '80', 'firm': True}])
    assert result['status'] == 'REPLAN_REQUIRED'
    assert result['first_gap_date'] == '2026-10-21'
    assert result['gap_quantity'] == '10'


def test_unconfirmed_receipt_never_closes_gap():
    result = simulate_daily_service(stock='30', daily_demand='10', safety='10',
        start='2026-10-19', end='2026-10-25', unit='tonne',
        receipts=[{'arrival_date': '2026-10-21', 'quantity': '20', 'firm': False}])
    assert result['status'] == 'REPLAN_REQUIRED'
    assert result['first_gap_date'] == '2026-10-21'


def test_unit_is_preserved_without_mass_conversion():
    result = simulate_daily_service(stock='30', daily_demand='10', safety='10',
        start='2026-10-19', end='2026-10-20', unit='roll', receipts=[])
    assert result['unit'] == 'roll'
    assert result['trace'][-1]['closing_stock'] == '10'


def test_uncovered_interval_ends_when_firm_receipt_closes_gap():
    result = simulate_daily_service(stock='0', daily_demand='10', safety='0',
        start='2026-10-19', end='2026-10-22', unit='tonne',
        receipts=[{'arrival_date': '2026-10-19', 'quantity': '100', 'firm': True}])
    assert [day['safety_gap'] for day in result['trace']] == ['10', '0', '0', '0']
    assert result['uncovered_interval'] == {'start': '2026-10-19', 'end': '2026-10-19'}


def test_uncovered_interval_stays_on_first_gap_when_later_gap_is_separate():
    result = simulate_daily_service(stock='0', daily_demand='10', safety='0',
        start='2026-10-19', end='2026-10-22', unit='tonne',
        receipts=[{'arrival_date': '2026-10-19', 'quantity': '30', 'firm': True}])
    assert [day['safety_gap'] for day in result['trace']] == ['10', '0', '0', '10']
    assert result['uncovered_interval'] == {'start': '2026-10-19', 'end': '2026-10-19'}
    assert result['uncovered_intervals'] == [
        {'start': '2026-10-19', 'end': '2026-10-19'},
        {'start': '2026-10-22', 'end': '2026-10-22'},
    ]
