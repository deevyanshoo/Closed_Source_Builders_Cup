"""Price outlook backtest engine: leakage, splits, metrics, statuses and determinism.

Uses synthetic series only. The engine wraps procure/forecast (models.fit_series, select.metrics_from,
monthly_projection.ridge_candidates) and scores with procure/evaluation/forecast_scorecard.evaluate.
"""
import math

import numpy as np
import pytest

from procure.prediction import engine

FAST = {**engine.DEFAULT_CONFIG, 'models': ('naive', 'ets'), 'windows': (36, 60), 'n_paths': 400,
        'bootstrap_replicates': 200}


def months(n, start=(2010, 1)):
    y, m = start
    out = []
    for _ in range(n):
        out.append(f'{y:04d}-{m:02d}')
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def trending(n=120, seed=1):
    rng = np.random.default_rng(seed)
    return [float(100 * math.exp(0.03 * t + rng.normal(0, 0.004))) for t in range(n)]


def random_walk(n=120, seed=2):
    rng = np.random.default_rng(seed)
    return [float(v) for v in 100 * np.exp(np.cumsum(rng.normal(0, 0.05, n)))]


def series(values, sid='synthetic:test'):
    return {'series_id': sid, 'series_kind': 'commodity', 'unit': 'USD per mt', 'source_id': 'synthetic',
            'is_synthetic': True, 'months': months(len(values)), 'values': values}


@pytest.mark.parametrize('model', ['naive', 'ets', 'gbm', 'ridge'])
def test_changing_future_values_does_not_change_the_forecast_at_that_origin(model):
    values = random_walk()
    e = 70
    before = engine.forecast_at(series(values), e, model, 60, FAST)
    changed = values[:e + 1] + [v * 3 for v in values[e + 1:]]
    from procure.forecast import models
    models.clear_cache()  # a cached fit must not hide a leak
    after = engine.forecast_at(series(changed), e, model, 60, FAST)
    assert before == after


def test_no_leakage_per_origin_rows_use_only_months_up_to_the_cutoff():
    result = engine.evaluate_series(series(random_walk()), FAST)
    for row in result['validation_rows'] + result['test_rows']:
        assert row['training_cutoff'] < row['origin'] < row['target']  # month m usable from end of m+1
        cutoff = result['months'].index(row['training_cutoff'])
        assert row['p0'] == result['values'][cutoff]


def test_test_window_is_the_final_24_months_and_never_used_in_selection_or_calibration():
    values = random_walk()
    first = engine.evaluate_series(series(values), FAST)
    test_months = set(first['months'][-24:])
    assert {row['target'] for row in first['test_rows']} == test_months
    assert all(row['target'] not in test_months for row in first['validation_rows'])
    assert max(row['target'] for row in first['validation_rows']) < min(test_months)
    # Scrambling the test window changes nothing that was decided before it
    scrambled = values[:-24] + [v * 1.7 for v in values[-24:]]
    second = engine.evaluate_series(series(scrambled), FAST)
    assert second['selection'] == first['selection']
    assert second['scales'] == first['scales']
    assert second['validation_rows'] == first['validation_rows']


def test_quantiles_are_ordered_everywhere():
    result = engine.evaluate_series(series(trending()), FAST)
    for row in result['validation_rows'] + result['test_rows']:
        assert row['p10'] <= row['p50'] <= row['p90']
    for item in result['outlook']['horizons']:
        if item['p10'] is not None:
            assert item['p10'] <= item['p50'] <= item['p90']


def test_mape_and_coverage_maths_on_a_hand_made_example():
    rows = [
        {'horizon': 1, 'origin': '2020-01', 'p0': 100.0, 'p10': 90.0, 'p50': 100.0, 'p90': 110.0, 'actual': 105.0},
        {'horizon': 1, 'origin': '2020-02', 'p0': 100.0, 'p10': 95.0, 'p50': 100.0, 'p90': 104.0, 'actual': 125.0},
        {'horizon': 1, 'origin': '2020-03', 'p0': 100.0, 'p10': 80.0, 'p50': 110.0, 'p90': 120.0, 'actual': 80.0},
        {'horizon': 1, 'origin': '2020-04', 'p0': 100.0, 'p10': 99.0, 'p50': 101.0, 'p90': 103.0, 'actual': 100.0},
    ]
    summary = engine.summarize(rows, 'ets')[1]
    # |100-105|/105 + |100-125|/125 + |110-80|/80 + |101-100|/100, divided by 4
    assert summary['mape'] == pytest.approx((5 / 105 + 25 / 125 + 30 / 80 + 1 / 100) / 4)
    assert summary['coverage'] == pytest.approx(3 / 4)  # inclusive bounds; the 125 is outside
    # engine rule (select.metrics_from): sign(p50 - p0) == sign(actual - p0); no row agrees here
    assert summary['directional_acc'] == pytest.approx(0.0)
    assert summary['n_obs'] == 4


def test_known_answer_trend_reaches_prediction():
    result = engine.evaluate_series(series(trending()), FAST)
    assert result['selection']['selected_model'] == 'ets'
    assert 'PREDICTION' in {h['status'] for h in result['outlook']['horizons']}


def test_known_answer_random_walk_never_reaches_prediction():
    for seed in (2, 3, 4):
        result = engine.evaluate_series(series(random_walk(seed=seed), f'synthetic:rw-{seed}'), FAST)
        assert {h['status'] for h in result['outlook']['horizons']} <= {'RANGE_ONLY', 'NO_RELIABLE_FORECAST'}


def test_status_rules_are_the_preregistered_thresholds():
    rule = engine.classify
    good = {'skill': 0.05, 'bootstrap_ci_lower': 0.1, 'coverage_80': 0.8, 'n_origins': 24}
    naive = {'coverage_80': 0.8}
    assert rule('ets', good, naive) == 'PREDICTION'
    assert rule('ets', {**good, 'skill': 0.019}, naive) == 'RANGE_ONLY'
    assert rule('ets', {**good, 'bootstrap_ci_lower': 0.0}, naive) == 'RANGE_ONLY'
    assert rule('ets', {**good, 'coverage_80': 0.95}, naive) == 'RANGE_ONLY'
    assert rule('ets', {**good, 'n_origins': 17}, naive) == 'RANGE_ONLY'
    assert rule('naive', None, naive) == 'RANGE_ONLY'
    assert rule('naive', None, {'coverage_80': 0.65}) == 'NO_RELIABLE_FORECAST'
    assert rule('ets', {**good, 'skill': None}, {'coverage_80': 0.91}) == 'NO_RELIABLE_FORECAST'


def test_no_reliable_forecast_shows_only_the_labelled_naive_value():
    result = engine.evaluate_series(series(random_walk(seed=3), 'synthetic:rw-labels'), FAST)
    for item in result['outlook']['horizons']:
        if item['status'] == 'NO_RELIABLE_FORECAST':
            assert (item['p10'], item['p50'], item['p90']) == (None, None, None)
            assert item['naive_value'] == result['values'][-1]
            assert item['label'] == 'naive baseline, not a prediction'
        elif item['status'] == 'RANGE_ONLY':
            assert item['label'] == 'range based on past volatility, not a directional forecast'
            assert item['p50'] == result['values'][-1]


def test_runs_are_deterministic():
    one = engine.evaluate_series(series(random_walk(seed=5), 'synthetic:det'), FAST)
    two = engine.evaluate_series(series(random_walk(seed=5), 'synthetic:det'), FAST)
    assert one == two


def test_live_outlook_uses_projection_date_labels():
    result = engine.evaluate_series(series(trending()), FAST)
    outlook = result['outlook']
    assert outlook['observation_end'] == result['months'][-1]
    assert outlook['issue_month'] == months(121)[-1]  # observation end plus one
    assert [h['target_month'] for h in outlook['horizons']] == [months(122)[-1], months(124)[-1], months(127)[-1]]
    assert outlook['freshness_state'] in {'ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED', 'HISTORICAL_NOT_CURRENT'}


def test_a_pegged_constant_series_has_no_skill_and_no_prediction():
    result = engine.evaluate_series(series([3.6725] * 120, 'synthetic:peg'), FAST)
    assert result['selection']['selected_model'] == 'naive'
    assert result['selection']['improvement_vs_naive'] is None
    for item in result['outlook']['horizons']:
        assert item['status'] != 'PREDICTION'
        assert item['backtest']['mape_ratio'] is None
