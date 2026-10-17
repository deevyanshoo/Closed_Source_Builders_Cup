"""Committed price outlook results: reproducible, consistent with the scorecard run, honestly labelled."""
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from procure.prediction import engine

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'config' / 'prediction_results_v1.json'
RUN = ROOT / 'evaluation' / 'prediction_backtest_v1'


REL_TOL = 1e-3  # statsmodels on Linux differs from macOS in the 4th to 5th significant digit
BOOTSTRAP_REL_TOL = 1e-2  # the bootstrap interval amplifies that noise (0.13% seen on Linux)
BOOTSTRAP_KEYS = {'bootstrap_ci_lower', 'bootstrap_ci_upper'}


def assert_same_outlook(actual, expected, path='$'):
    """Statuses, models, labels, months and other text must match exactly; floats within REL_TOL
    (BOOTSTRAP_REL_TOL for the bootstrap interval bounds)."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key in expected:
            assert_same_outlook(actual[key], expected[key], f'{path}.{key}')
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected)):
            assert_same_outlook(a, e, f'{path}[{i}]')
    elif isinstance(expected, float) or (isinstance(actual, float) and not isinstance(expected, (bool, str)) and expected is not None):
        assert not isinstance(actual, bool) and isinstance(actual, (int, float)), path
        tol = BOOTSTRAP_REL_TOL if path.rsplit('.', 1)[-1] in BOOTSTRAP_KEYS else REL_TOL
        assert math.isclose(actual, expected, rel_tol=tol), (path, actual, expected)
    else:
        assert actual == expected and type(actual) is type(expected), (path, actual, expected)


@pytest.fixture(scope='module')
def results():
    return json.loads(RESULTS.read_text(encoding='utf-8'))


def test_reproduction_check_allows_float_noise_but_not_a_changed_status(results):
    entry = results['series']['fx:INR']
    noisy = json.loads(json.dumps(entry))
    noisy['horizons'][0]['backtest']['mape'] *= 1 + 4e-4
    noisy['latest_value'] *= 1 - 4e-4
    noisy['horizons'][2]['backtest']['bootstrap_ci_lower'] = 0.50229  # Linux value; committed 0.50296
    noisy['horizons'][2]['backtest']['bootstrap_ci_upper'] *= 1 - 8e-3
    assert_same_outlook(noisy, entry)
    for path, value in ((('horizons', 0, 'status'), 'PREDICTION'), (('horizons', 0, 'label'), 'x'),
                        (('horizons', 1, 'target_month'), '1999-01'), (('selection', 'selected_model'), 'gbm'),
                        (('horizons', 0, 'backtest', 'mape'), entry['horizons'][0]['backtest']['mape'] * 1.002),
                        (('horizons', 2, 'backtest', 'bootstrap_ci_lower'), entry['horizons'][2]['backtest']['bootstrap_ci_lower'] * 1.02),
                        (('horizons', 2, 'backtest', 'bootstrap_ci_upper'), entry['horizons'][2]['backtest']['bootstrap_ci_upper'] * 0.98)):
        changed = json.loads(json.dumps(entry))
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(AssertionError):
            assert_same_outlook(changed, entry)


def test_rerunning_the_script_reproduces_the_committed_entry(results):
    out = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_price_predictions.py'), '--series', 'fx:INR'],
                         capture_output=True, text=True, check=True, cwd=ROOT)
    assert_same_outlook(json.loads(out.stdout), results['series']['fx:INR'])


def test_statuses_follow_the_committed_scorecard_run(results):
    with (RUN / 'scorecard_run' / 'metrics.csv').open(newline='', encoding='utf-8') as handle:
        rows = {(r['series_id'], r['model'], int(r['horizon'])): r for r in csv.DictReader(handle) if r['split'] == 'test'}

    def number(value):
        return None if value in ('', None) else float(value)

    for series_id, entry in results['series'].items():
        selected = entry['selection']['selected_model']
        for h in entry['horizons']:
            item = rows.get((series_id, selected, h['horizon'])) if selected != 'naive' else None
            naive = rows[(series_id, 'naive', h['horizon'])]
            parsed = None if item is None else {'skill': number(item['skill']),
                                                'bootstrap_ci_lower': number(item['bootstrap_ci_lower']),
                                                'coverage_80': number(item['coverage_80']),
                                                'n_origins': int(item['n_origins'])}
            assert engine.classify(selected, parsed, {'coverage_80': number(naive['coverage_80'])}) == h['status'], (series_id, h['horizon'])
            assert int(naive['n_origins']) == 24


def test_scorecard_input_keeps_the_test_window_out_of_validation():
    doc = json.loads((RUN / 'scorecard_input.json').read_text(encoding='utf-8'))
    assert doc['schema_version'] == 'forecast-scorecard.v1'
    test_targets = {r['target'] for r in doc['predictions'] if r['split'] == 'test'}
    validation_targets = {r['target'] for r in doc['predictions'] if r['split'] == 'validation'}
    assert min(test_targets) == '2024-09' and max(test_targets) == '2026-08'
    assert max(validation_targets) < min(test_targets)
    assert all(r['training_cutoff'] < r['origin'] for r in doc['predictions'])
    state = json.loads((RUN / 'scorecard_run' / 'RUN_STATE.json').read_text(encoding='utf-8'))
    assert state['status'] == 'complete'


def test_every_horizon_shows_only_what_its_status_allows(results):
    for entry in results['series'].values():
        assert [h['horizon'] for h in entry['horizons']] == [1, 3, 6]
        for h in entry['horizons']:
            assert h['label'] == engine.LABELS[h['status']]
            if h['status'] == 'NO_RELIABLE_FORECAST':
                assert (h['p10'], h['p50'], h['p90']) == (None, None, None)
                assert h['naive_value'] == entry['latest_value']
            else:
                assert h['p10'] <= h['p50'] <= h['p90']
            if h['status'] == 'RANGE_ONLY':
                assert h['p50'] == entry['latest_value']


def test_dates_and_unavailable_series_are_explicit(results):
    assert results['unavailable'] == {'fx:TZS': results['unavailable']['fx:TZS']}
    for entry in results['series'].values():
        assert entry['data_date'] == '2026-08' and entry['issue_month'] == '2026-09'
        assert entry['freshness_state'] == 'HISTORICAL_NOT_CURRENT'
        assert entry['source']['licence'] and entry['source']['url'].startswith('https://')
