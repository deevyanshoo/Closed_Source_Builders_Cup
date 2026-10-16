"""Committed price outlook results: reproducible, consistent with the scorecard run, honestly labelled."""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from procure.prediction import engine

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / 'config' / 'prediction_results_v1.json'
RUN = ROOT / 'evaluation' / 'prediction_backtest_v1'


@pytest.fixture(scope='module')
def results():
    return json.loads(RESULTS.read_text(encoding='utf-8'))


def test_rerunning_the_script_reproduces_the_committed_entry_exactly(results):
    out = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_price_predictions.py'), '--series', 'fx:INR'],
                         capture_output=True, text=True, check=True, cwd=ROOT)
    assert json.loads(out.stdout) == results['series']['fx:INR']


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
