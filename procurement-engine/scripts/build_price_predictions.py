"""Accuracy gate and outlook build for the hosted price outlook (run offline; the hosted app only reads JSON).

Grid and rules are declared here and fixed before results were seen:
  models          naive, ets, gbm (procure/forecast/models.py) and the fixed ridge from monthly_projection.py
  training window 36 or 60 months (chosen per model on the validation split)
  horizons        1, 3 and 6 months after the issue month (issue = last usable month + 1)
  splits          final 24 target months = test (untouched); the 24 before = validation
  calibration     calibrate_scale grid on validation only
  statuses        PREDICTION: selected model has scorecard skill >= 0.02 vs naive, bootstrap interval lower bound
                  above 0, P10 to P90 coverage 70 to 90%, at least 18 test origins.
                  RANGE_ONLY: otherwise, if the calibrated naive band covers 70 to 90% on test.
                  NO_RELIABLE_FORECAST: otherwise.

Outputs:
  evaluation/prediction_backtest_v1/scorecard_input.json  forecast-scorecard.v1 rows (validation and test)
  evaluation/prediction_backtest_v1/scorecard_run/        scripts/evaluate_forecasts.py run on that input
  config/prediction_results_v1.json                       outlook per series, read by procure/demo/price_outlook.py
  ../docs/prediction_backtest_v1.md                       the full test-split table

Use --series to rebuild one series and print its JSON entry (tests compare it with the committed file).
Requires numpy, pandas, statsmodels and scikit-learn from requirements.txt in the local .venv only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from procure.prediction import engine  # noqa: E402
from procure.prediction.history import load_history  # noqa: E402

CONFIG = {**engine.DEFAULT_CONFIG,
          'models': ('naive', 'ets', 'gbm', 'ridge'), 'windows': (36, 60), 'horizons': (1, 3, 6),
          'test_months': 24, 'validation_months': 24, 'n_paths': 1000, 'gbm_refit_every': 6,
          'bootstrap_replicates': 1000, 'bootstrap_seed': 0, 'as_of_month': '2026-10'}
SNAPSHOT = ROOT / 'config' / 'prediction_history_v1.json'
RESULTS = ROOT / 'config' / 'prediction_results_v1.json'
RUN_DIR = ROOT / 'evaluation' / 'prediction_backtest_v1'
REPORT = ROOT.parent / 'docs' / 'prediction_backtest_v1.md'
BUILT_ON = '2026-10-16'


def series_input(series_id: str, history: dict) -> dict:
    s = history['series'][series_id]
    source = history['sources'][s['source_id']]
    return {'series_id': series_id, 'series_kind': s['kind'], 'unit': s['unit'], 'source_id': s['source_id'],
            'is_synthetic': False, 'months': [m for m, _ in s['observations']],
            'values': [float(v) for _, v in s['observations']], 'values_sha256': s['values_sha256'],
            'retrieved_at': source['retrieved_at']}


def result_entry(series_id: str, history: dict, result: dict) -> dict:
    s = history['series'][series_id]
    source = history['sources'][s['source_id']]
    return {'series_id': series_id, 'kind': s['kind'], 'unit': s['unit'], 'source_series': s['source_series'],
            'source': {'publisher': source['publisher'], 'url': source['page_url'], 'licence': source['licence'],
                       'attribution': source['attribution'], 'retrieved_at': source['retrieved_at']},
            'data_date': result['outlook']['observation_end'], 'latest_value': result['outlook']['latest_value'],
            'issue_month': result['outlook']['issue_month'], 'freshness_state': result['outlook']['freshness_state'],
            'selection': result['selection'], 'scales': result['scales'], 'horizons': result['outlook']['horizons']}


def run_series(series_id: str, history: dict) -> tuple[dict, dict]:
    result = engine.evaluate_series(series_input(series_id, history), CONFIG)
    return result, result_entry(series_id, history, result)


def _pct(x):
    return '' if x is None else f'{100 * x:.1f}%'


def _f(x, digits=3):
    return '' if x is None else f'{x:.{digits}f}'


def report(entries: dict, history: dict) -> str:
    counts = {status: 0 for status in engine.LABELS}
    lines = ['# Price outlook backtest, version 1', '',
             f'Built {BUILT_ON} by `procurement-engine/scripts/build_price_predictions.py` from '
             '`config/prediction_history_v1.json` (World Bank Pink Sheet and BIS monthly averages, 2016-09 to 2026-08, '
             'month m usable from the end of m+1). Test split: the final 24 target months (2024-09 to 2026-08), never '
             'used for model choice or interval calibration. Skill, bootstrap interval and coverage come from '
             '`scripts/evaluate_forecasts.py` (forecast-scorecard.v1) on the test split; MAPE and directional accuracy '
             'from `select.metrics_from`.', '',
             'Pre-registered statuses: **PREDICTION** needs skill vs naive of at least 0.02, a bootstrap interval lower '
             'bound above 0, P10 to P90 coverage 70 to 90% and at least 18 test origins; **RANGE_ONLY** needs the '
             'calibrated naive band to cover 70 to 90%; otherwise **NO_RELIABLE_FORECAST**.', '',
             '| Series | Horizon | Selected model | MAPE | Naive MAPE | MAPE ratio | Skill | Bootstrap CI (abs. error reduction) | Coverage | Naive coverage | Directional | Origins | Status |',
             '|---|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|']
    unavailable = []
    for series_id in sorted(history['series']):
        if series_id not in entries:
            unavailable.append(series_id)
            continue
        entry = entries[series_id]
        for h in entry['horizons']:
            b = h['backtest']
            counts[h['status']] += 1
            ci = '' if b['bootstrap_ci_lower'] is None else f"{b['bootstrap_ci_lower']:.4g} to {b['bootstrap_ci_upper']:.4g}"
            lines.append(f"| {series_id} | {h['horizon']} | {b['selected_model']} | {_pct(b['mape'])} | "
                         f"{_pct(b['naive_mape'])} | {_f(b['mape_ratio'])} | {_f(b['skill'])} | {ci} | "
                         f"{_pct(b['coverage'])} | {_pct(b['naive_coverage'])} | {_pct(b['directional_acc'])} | "
                         f"{b['n_origins']} | {h['status']} |")
    total = sum(counts.values())
    lines += ['', f'**Status counts over {total} series-horizon rows:** ' +
              ', '.join(f'{k} {v}' for k, v in counts.items()) + '.', '']
    selected = {}
    for entry in entries.values():
        selected[entry['selection']['selected_model']] = selected.get(entry['selection']['selected_model'], 0) + 1
    lines.append('**Model selected on the validation split, per series:** ' +
                 ', '.join(f'{k} {v}' for k, v in sorted(selected.items())) + '.')
    if unavailable:
        lines += ['', '**No history available:** ' + ', '.join(unavailable) + '.']
    lines += ['', 'These are backtest results on latest-vintage public data, not point-in-time publication vintages. '
              'The ridge candidate is a point forecast (P10 = P50 = P90), so it cannot meet the coverage rule. '
              'Nothing here is a purchasing recommendation.', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--series', help='rebuild one series and print its results entry')
    args = parser.parse_args(argv)
    history = load_history(SNAPSHOT)
    available = [sid for sid, s in sorted(history['series'].items()) if s['status'] == 'available']
    if args.series:
        print(json.dumps(run_series(args.series, history)[1], indent=1))
        return 0
    entries, predictions = {}, []
    for sid in available:
        result, entry = run_series(sid, history)
        entries[sid] = entry
        predictions += result['validation_rows'] + result['test_rows']
        print(f'{sid}: ' + ', '.join(f"h{h['horizon']} {h['status']}" for h in entry['horizons']), flush=True)
    snapshot_sha = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    scorecard_input = RUN_DIR / 'scorecard_input.json'
    scorecard_input.write_text(json.dumps({
        'schema_version': 'forecast-scorecard.v1',
        'metadata': {'snapshot': 'config/prediction_history_v1.json', 'snapshot_sha256': snapshot_sha,
                     'built_on': BUILT_ON, 'config': {k: list(v) if isinstance(v, tuple) else v for k, v in CONFIG.items()},
                     'availability_policy': history['availability_policy']},
        'predictions': predictions}, indent=0) + '\n', encoding='utf-8')
    run = RUN_DIR / 'scorecard_run'
    if run.exists():
        shutil.rmtree(run)  # a rebuild replaces the previous run of this same script
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'evaluate_forecasts.py'), '--input', str(scorecard_input),
                    '--output-dir', str(run), '--bootstrap-replicates', str(CONFIG['bootstrap_replicates']),
                    '--seed', str(CONFIG['bootstrap_seed'])], check=True)
    RESULTS.write_text(json.dumps({
        'results_version': 'prediction-results-v1', 'built_on': BUILT_ON, 'snapshot_sha256': snapshot_sha,
        'engine': {'package': 'procure/prediction/engine.py', 'gbm_backend': 'sklearn',
                   'config': {k: list(v) if isinstance(v, tuple) else v for k, v in CONFIG.items()},
                   'rules': {'min_skill': engine.MIN_SKILL, 'coverage_band': list(engine.COVERAGE_BAND),
                             'min_test_origins': engine.MIN_TEST_ORIGINS}, 'labels': engine.LABELS},
        'series': entries,
        'unavailable': {sid: s.get('reason') for sid, s in history['series'].items() if s['status'] != 'available'},
        'material_map': history['material_map']}, indent=1) + '\n', encoding='utf-8')
    REPORT.write_text(report(entries, history), encoding='utf-8')
    print(f'Wrote {RESULTS}, {REPORT} and {run}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
