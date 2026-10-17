"""Budget-at-risk: tested benchmark ranges turned into money for the current plan quantity (#130)."""
import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from procure.prediction.budget import FX_NOTE, LABEL, budget_at_risk

RESULTS = json.loads((Path(__file__).resolve().parents[1] / 'config' / 'prediction_results_v1.json').read_text())
COPPER = RESULTS['series']['commodity:copper']


def outlook(series_id='commodity:copper', match='exact', note='Copper cathode, LME grade A benchmark.'):
    return {'status': 'available', 'material_id': series_id.split(':')[1], 'match': match, 'note': note,
            'series': {**deepcopy(RESULTS['series'][series_id]), 'series_id': series_id}}


def horizon(result, h):
    return next(row for row in result['horizons'] if row['horizon'] == h)


def test_hand_calculated_copper_example_matches_the_committed_values():
    result = budget_at_risk(outlook_entry=outlook(), fx_entry=None, quantity='8000', unit='kg', reporting_currency='USD')
    assert result['status'] == 'available'
    assert COPPER['latest_value'] == 14326.0
    one = COPPER['horizons'][0]
    assert (one['p10'], one['p50'], one['p90']) == (13220.29766, 14326.0, 15561.8455)
    assert result['quantity'] == {'value': '8000', 'unit': 'kg', 'benchmark_quantity': '8', 'benchmark_unit': 'mt'}
    assert Decimal(result['today']['amount']) == Decimal('114608.000')
    first = horizon(result, 1)
    assert Decimal(first['low']['amount']) == Decimal('105762.38128')
    assert Decimal(first['mid']['amount']) == Decimal('114608.000')
    assert Decimal(first['high']['amount']) == Decimal('124494.764')
    assert Decimal(first['low']['change']) == Decimal('105762.38128') - Decimal('114608')
    assert first['hit_rate'] == {'value': '0.7916666666666666', 'basis': 'naive band coverage', 'test_origins': 24}
    assert first['target_month'] == '2026-10' and first['status'] == 'RANGE_ONLY'
    assert result['today']['display'] == '114,608.00' and first['low']['display'] == '105,762.38'
    three = horizon(result, 3)
    assert three['hit_rate']['value'] == '0.8333333333333334'
    six = horizon(result, 6)
    assert six['status'] == 'NO_RELIABLE_FORECAST' and six['reason'] == 'no tested range for this horizon'
    assert 'low' not in six and 'mid' not in six and 'high' not in six


def test_tonne_quantities_and_per_kg_benchmarks_convert_only_between_kg_and_tonne():
    tonnes = budget_at_risk(outlook_entry=outlook(), fx_entry=None, quantity='8', unit='tonne', reporting_currency='USD')
    assert Decimal(tonnes['today']['amount']) == Decimal('114608')
    cotton = outlook('commodity:cotton', note='Cotton fibre, Cotton A Index.')
    per_kg = budget_at_risk(outlook_entry=cotton, fx_entry=None, quantity='2.5', unit='tonne', reporting_currency='USD')
    assert per_kg['quantity']['benchmark_quantity'] == '2500' and per_kg['quantity']['benchmark_unit'] == 'kg'
    assert Decimal(per_kg['today']['amount']) == Decimal('2500') * Decimal('2.11')
    in_kg = budget_at_risk(outlook_entry=cotton, fx_entry=None, quantity='1200', unit='kg', reporting_currency='USD')
    assert Decimal(in_kg['today']['amount']) == Decimal('1200') * Decimal('2.11')


@pytest.mark.parametrize('unit', ['unit', 'roll', 'litre', 'bale', 'barrel', 'cylinder'])
def test_non_mass_units_need_a_stated_basis(unit):
    result = budget_at_risk(outlook_entry=outlook('commodity:sunflower_oil', note='Sunflower oil per tonne.'),
                            fx_entry=None, quantity='61500', unit=unit, reporting_currency='USD')
    assert result['status'] == 'unit_basis_required'
    assert unit in result['reason'] and 'today' not in result and result['horizons'] == []
    if unit == 'litre':
        assert 'density' in result['reason']


def test_currency_per_usd_rates_multiply_and_show_the_month():
    inr = RESULTS['series']['fx:INR']
    result = budget_at_risk(outlook_entry=outlook(), fx_entry=inr, quantity='8000', unit='kg', reporting_currency='INR')
    assert result['fx'] == {'rate': '95.446733', 'quote': 'INR per USD', 'month': '2026-08', 'series_id': 'fx:INR',
                            'source': inr['source']['attribution'], 'publisher': inr['source']['publisher'],
                            'url': inr['source']['url']}
    assert Decimal(result['today']['amount']) == Decimal('114608.000') * Decimal('95.446733')
    assert result['currency'] == 'INR'


def test_eur_follows_the_snapshot_direction_in_either_quote():
    eur = RESULTS['series']['fx:EUR']
    assert eur['unit'] == 'EUR per USD'
    stored = budget_at_risk(outlook_entry=outlook(), fx_entry=eur, quantity='8', unit='tonne', reporting_currency='EUR')
    assert Decimal(stored['today']['amount']) == Decimal('114608') * Decimal('0.862603')
    inverse = {**deepcopy(eur), 'unit': 'USD per EUR', 'latest_value': 1.25}
    flipped = budget_at_risk(outlook_entry=outlook(), fx_entry=inverse, quantity='8', unit='tonne', reporting_currency='EUR')
    assert Decimal(flipped['today']['amount']) == Decimal('114608') / Decimal('1.25')
    assert flipped['fx']['quote'] == 'USD per EUR'


def test_usd_needs_no_conversion():
    result = budget_at_risk(outlook_entry=outlook(), fx_entry=RESULTS['series']['fx:INR'], quantity='8', unit='tonne',
                            reporting_currency='USD')
    assert result['fx'] is None and result['currency'] == 'USD'
    assert result['fx_note'] is None


def test_unavailable_paths_return_reasons_and_no_figures():
    def no_figures(result):
        assert 'today' not in result and result['horizons'] == []
    missing_fx = budget_at_risk(outlook_entry=outlook(), fx_entry=None, quantity='8', unit='tonne', reporting_currency='TZS')
    assert missing_fx['status'] == 'fx_unavailable' and 'TZS' in missing_fx['reason']
    no_figures(missing_fx)
    wrong_fx = budget_at_risk(outlook_entry=outlook(), fx_entry=RESULTS['series']['fx:INR'], quantity='8', unit='tonne',
                              reporting_currency='KES')
    assert wrong_fx['status'] == 'fx_unavailable'
    no_series = budget_at_risk(outlook_entry={'status': 'no_series', 'material_id': 'polymer',
                                              'message': 'No public monthly benchmark series is mapped to this material.'},
                               fx_entry=None, quantity='8', unit='tonne', reporting_currency='USD')
    assert no_series['status'] == 'no_series' and 'No public monthly benchmark' in no_series['reason']
    no_figures(no_series)
    for bad in ('-1', '0', 'abc', 'NaN', 'Infinity', '1e400'):
        with pytest.raises(ValueError):
            budget_at_risk(outlook_entry=outlook(), fx_entry=None, quantity=bad, unit='kg', reporting_currency='USD')


def test_values_are_exact_decimal_strings_without_float_drift():
    result = budget_at_risk(outlook_entry=outlook(), fx_entry=None, quantity='0.1', unit='tonne', reporting_currency='USD')
    assert result['today']['amount'] == '1432.60'
    assert horizon(result, 1)['low']['amount'] == '1322.029766'
    assert horizon(result, 1)['low']['display'] == '1,322.03'
    korea = budget_at_risk(outlook_entry=outlook(), fx_entry=RESULTS['series']['fx:KRW'], quantity='1', unit='tonne',
                           reporting_currency='KRW')
    assert '.' not in korea['today']['display']


def test_label_and_fx_note_and_no_advice_words():
    result = budget_at_risk(outlook_entry=outlook('commodity:copper', 'proxy', 'Copper rod: cathode benchmark, rod premium not included.'),
                            fx_entry=RESULTS['series']['fx:MXN'], quantity='15600', unit='kg', reporting_currency='MXN')
    assert result['label'] == LABEL == 'benchmark cost exposure, not a supplier quote, landed cost or recommendation'
    assert result['fx_note'] == FX_NOTE.format(month='2026-08') == 'FX held at 2026-08 rate; see the FX outlook for currency uncertainty.'
    assert result['match'] == 'proxy' and 'rod premium not included' in result['note']
    assert result['data_date'] == '2026-08' and result['freshness_state'] == 'HISTORICAL_NOT_CURRENT'
    assert result['source']['publisher'] == 'World Bank'
    text = json.dumps(result).lower().replace(LABEL, '')
    for word in ('saving', 'recommend', ' buy', 'wait'):
        assert word not in text, word
