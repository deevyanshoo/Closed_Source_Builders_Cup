"""Margin scenario: only from entered inputs, historical unit values and the dated FX snapshot (#131)."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from procure.exports.margin import INPUTS, LABEL, TARIFF_UNAVAILABLE, margin_scenario

FX = json.loads((Path(__file__).resolve().parents[1] / 'config' / 'export_fx_v1.json').read_text())['rates']


def market(usd_per_kg='8', basis='CIF', weight='1000000', status='available'):
    unit_value = {'status': status, 'usd_per_kg': usd_per_kg, 'year': 2024, 'basis': basis, 'net_weight_kg': weight,
                  'label': 'historical customs unit value'} if status == 'available' else \
        {'status': 'unit value suppressed', 'reason': 'net weight not reported', 'year': 2024}
    return {'code': 842, 'name': 'USA', 'unit_value': unit_value}


FULL = {'unit_cost_per_kg': '500', 'freight_insurance_per_kg': '40', 'tariff_rate_percent': '10',
        'capacity_kg_per_year': '50000', 'target_share_percent': '2'}


def test_cif_hand_example_in_inr_counts_freight_once():
    result = margin_scenario(market=market(), inputs=FULL, fx=FX['INR'], reporting_currency='INR')
    assert result['status'] == 'available' and result['label'] == LABEL
    assert LABEL == 'scenario from your inputs and historical customs unit values, not a buyer price, quote or forecast'
    # 8 USD/kg x 95.446733 = 763.573864 INR/kg (CIF, so freight is inside it)
    assert Decimal(result['unit_value_converted']) == Decimal('763.573864')
    # 763.573864 - 500 - 40 - 10% x 763.573864 = 147.2164776
    assert Decimal(result['margin_per_kg']) == Decimal('147.2164776')
    # volume = min(50,000, 2% x 1,000,000) = 20,000 kg
    assert Decimal(result['volume_kg']) == Decimal('20000')
    assert Decimal(result['contribution']) == Decimal('2944329.552')
    assert result['currency'] == 'INR' and result['basis'] == 'CIF'
    assert result['fx'] == {'rate': '95.446733', 'quote': 'INR per USD', 'month': '2026-08',
                            'publisher': 'Bank for International Settlements (BIS)', 'url': FX['INR']['url']}
    assert all(item['source'] == 'user supplied' for item in result['inputs'])
    assert [item['name'] for item in result['inputs']] == list(INPUTS)
    assert sum('freight' in line for line in result['arithmetic']) == 1


def test_fob_basis_adds_freight_once_to_reach_a_delivered_value():
    inputs = {**FULL, 'unit_cost_per_kg': '5', 'freight_insurance_per_kg': '1'}
    result = margin_scenario(market=market(basis='FOB'), inputs=inputs, fx=None, reporting_currency='USD')
    # delivered = 8 + 1 = 9; margin = 9 - 5 - 1 - 10% x 9 = 2.1 (= 8 - 5 - 0.9)
    assert Decimal(result['margin_per_kg']) == Decimal('2.1') and result['basis'] == 'FOB'
    assert result['fx'] is None
    assert any('FOB' in note and 'freight' in note for note in result['notes'])


def test_fx_direction_follows_the_snapshot_quote():
    eur = margin_scenario(market=market(), inputs=FULL, fx=FX['EUR'], reporting_currency='EUR')
    assert FX['EUR']['quote'] == 'EUR per USD'
    assert Decimal(eur['unit_value_converted']) == Decimal('8') * Decimal('0.862603')
    inverse = {**FX['EUR'], 'quote': 'USD per EUR', 'rate': '1.25'}
    flipped = margin_scenario(market=market(), inputs=FULL, fx=inverse, reporting_currency='EUR')
    assert Decimal(flipped['unit_value_converted']) == Decimal('6.4')
    assert flipped['fx']['month'] == '2026-08'


def test_no_margin_when_anything_is_missing_and_the_list_is_exact():
    empty = margin_scenario(market=market(), inputs={}, fx=FX['INR'], reporting_currency='INR')
    assert empty['status'] == 'unavailable' and empty['missing'] == list(INPUTS)
    assert TARIFF_UNAVAILABLE == 'tariff unavailable, check the official schedule'
    assert TARIFF_UNAVAILABLE in empty['notes']
    assert 'margin_per_kg' not in empty and 'contribution' not in empty
    partial = margin_scenario(market=market(), inputs={**FULL, 'tariff_rate_percent': ' '}, fx=FX['INR'], reporting_currency='INR')
    assert partial['missing'] == ['tariff_rate_percent']
    suppressed = margin_scenario(market=market(status='suppressed'), inputs=FULL, fx=FX['INR'], reporting_currency='INR')
    assert suppressed['missing'] == ['historical customs unit value (net weight not reported)']
    no_fx = margin_scenario(market=market(), inputs=FULL, fx=None, reporting_currency='TZS')
    assert no_fx['missing'] == ['dated FX rate for TZS']
    no_volume = margin_scenario(market=market(weight=None), inputs=FULL, fx=FX['INR'], reporting_currency='INR')
    assert no_volume['missing'] == ['market import volume (net weight)']


@pytest.mark.parametrize('field,value', [('unit_cost_per_kg', '-1'), ('target_share_percent', '101'),
                                         ('tariff_rate_percent', 'abc'), ('capacity_kg_per_year', 'Infinity')])
def test_invalid_inputs_are_rejected(field, value):
    with pytest.raises(ValueError):
        margin_scenario(market=market(), inputs={**FULL, field: value}, fx=FX['INR'], reporting_currency='INR')


def test_no_defaults_and_no_float_drift():
    result = margin_scenario(market=market(usd_per_kg='0.1'), inputs={**FULL, 'unit_cost_per_kg': '0.2',
                             'freight_insurance_per_kg': '0.1', 'tariff_rate_percent': '0'}, fx=None, reporting_currency='USD')
    assert result['margin_per_kg'] == '-0.2'
    assert 'loss' in ' '.join(result['notes'])


def test_arithmetic_text_rounds_for_display_while_fields_stay_exact():
    long = market(usd_per_kg='9.236253773289553798445969341', weight='31832834.0')
    result = margin_scenario(market=long, inputs={**FULL, 'unit_cost_per_kg': '600', 'freight_insurance_per_kg': '45',
                                                  'tariff_rate_percent': '0', 'capacity_kg_per_year': '200000',
                                                  'target_share_percent': '1'},
                             fx={**FX['INR'], 'rate': '123.100769', 'quote': 'INR per USD'}, reporting_currency='INR')
    assert result['margin_per_kg'] == '491.989942171095732255569831'
    assert result['arithmetic'][0] == 'unit value: 9.2363 USD/kg x 123.100769 INR per USD (2026-08) = 1,136.99 INR/kg (CIF, 2024)'
    assert result['arithmetic'][-1] == 'contribution: 491.99 x 200,000 = 98,397,988.43 INR'
