"""Walk-forward backtest and outlook for one monthly series, using the existing forecast engine.

Why not procure/forecast/select.select_on_history or simulate.simulate directly: both read settings and the
planning database and simulate a commodity price times USD/INR in INR per kg. This module calls the same
lower-level pieces for a single native series instead:

* procure.forecast.models.fit_series (naive, ets, gbm quantile) for drift and residuals, with the same residual
  bootstrap that simulate.py uses for its paths;
* procure.forecast.monthly_projection.ridge_candidates (fixed alpha-10 ridge, a point forecast only) and
  assemble_persistence for issue and target month labels;
* procure.forecast.select.metrics_from for MAPE, directional accuracy and P10 to P90 coverage, and the
  calibrate_scale grid applied to the validation window only;
* procure.evaluation.forecast_scorecard.evaluate for skill against naive and its block bootstrap.

Timing follows the series catalog: month m is usable only from the end of month m+1. A forecast from
observation month e is issued in month e+1 and targets month e+1+h. The final TEST_MONTHS targets are the
untouched test split; selection and calibration use only the VALIDATION_MONTHS targets before it.
"""
from __future__ import annotations

import zlib
from copy import deepcopy

import numpy as np
import pandas as pd

from procure.evaluation.forecast_scorecard import evaluate as score
from procure.forecast import models as fmodels
from procure.forecast.monthly_projection import assemble_persistence, ridge_candidates
from procure.forecast.select import metrics_from

# Reproducibility: use the scikit-learn quantile GBM fallback that models.py already provides, so a machine
# with LightGBM installed produces the same numbers. models.py itself is not modified.
fmodels._lgb = None
fmodels.GBM_BACKEND = 'sklearn'

DEFAULT_CONFIG = {
    'models': ('naive', 'ets', 'gbm', 'ridge'),
    'windows': (36, 60),
    'horizons': (1, 3, 6),
    'test_months': 24,
    'validation_months': 24,
    'scale_grid': (0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5),
    'n_paths': 1000,
    'gbm_refit_every': 6,
    'min_improvement_vs_naive': 0.02,
    'bootstrap_replicates': 1000,
    'bootstrap_seed': 0,
    'as_of_month': '2026-10',
}
# Pre-registered status rules (do not change after seeing results).
MIN_SKILL = 0.02
COVERAGE_BAND = (0.70, 0.90)
MIN_TEST_ORIGINS = 18
LABELS = {
    'PREDICTION': 'Projection with uncertainty, not a recommendation',
    'RANGE_ONLY': 'range based on past volatility, not a directional forecast',
    'NO_RELIABLE_FORECAST': 'naive baseline, not a prediction',
}
BACKENDS = {'naive': 'persistence-bootstrap', 'ets': 'statsmodels-holt-damped', 'gbm': 'sklearn-quantile-gbm',
            'ridge': 'monthly-projection-ridge-alpha10'}


def _num(x: float) -> float:
    return float(f'{float(x):.10g}')


def _meta(series: dict) -> dict:
    return {series['series_id']: {'unit': series['unit'], 'family': series['series_kind'],
                                  'source_id': series['source_id'],
                                  'source_sha256': series.get('values_sha256') or '0' * 64,
                                  'retrieved_at_utc': series.get('retrieved_at', '2026-01-01T00:00:00Z'),
                                  'error_references': []}}


def forecast_at(series: dict, e: int, model: str, window: int, config: dict) -> dict:
    """P10/P50/P90 for each horizon from data up to and including position e (scale 1)."""
    months, values = series['months'][:e + 1], series['values'][:e + 1]
    months, values = months[-window:], values[-window:]
    horizons = config['horizons']
    steps = max(horizons) + 1
    sid = series['series_id']
    if model == 'ridge':
        panel = {sid: dict(zip(months, values))}
        rows = ridge_candidates(panel, _meta(series))['candidates']
        point = {row['horizon']: _num(row['native_price']) for row in rows}
        return {h: (point[h], point[h], point[h]) for h in horizons}
    log_s = pd.Series(np.log(values), index=pd.to_datetime([m + '-01' for m in months]))
    fit = fmodels.fit_series(model, log_s, steps, name=f'{sid}|w{window}', refit_every=config['gbm_refit_every'])
    residuals = np.asarray(fit.residuals, dtype=float)
    rng = np.random.default_rng(zlib.crc32(f'{sid}|{model}|{window}|{e}'.encode()))
    draws = residuals[rng.integers(0, len(residuals), size=(config['n_paths'], steps))] * fit.noise_scale
    paths = values[-1] * np.exp(np.cumsum(np.asarray(fit.drift[:steps]) + draws, axis=1))
    out = {}
    for h in horizons:
        q10, q50, q90 = np.quantile(paths[:, h], (0.1, 0.5, 0.9))
        out[h] = (_num(q10), _num(q50), _num(q90))
    return out


def _scaled(row: dict, scale: float) -> dict:
    return {**row, 'p10': _num(row['p50'] + (row['p10'] - row['p50']) * scale),
            'p90': _num(row['p50'] + (row['p90'] - row['p50']) * scale)}


def _rows(series: dict, model: str, window: int, split: str, targets: range, config: dict) -> list[dict]:
    months, values = series['months'], series['values']
    rows, cache = [], {}
    for t in targets:
        for h in config['horizons']:
            e = t - h - 1
            if e < 1:
                continue
            if e not in cache:
                cache[e] = forecast_at(series, e, model, window, config)
            p10, p50, p90 = cache[e][h]
            diffs = np.abs(np.diff(values[:e + 1]))
            rows.append({'series_kind': series['series_kind'], 'series_id': series['series_id'],
                         'unit': series['unit'], 'source_id': series['source_id'],
                         'is_synthetic': series['is_synthetic'], 'split': split, 'origin': months[e + 1],
                         'target': months[t], 'horizon': h, 'model': model,
                         'backend': f'{BACKENDS[model]}-w{window}', 'training_cutoff': months[e],
                         'p0': values[e], 'p10': p10, 'p50': p50, 'p90': p90, 'actual': values[t],
                         'mase_scale': _num(float(diffs.mean()))})
    return sorted(rows, key=lambda r: (r['origin'], r['horizon']))


def summarize(rows: list[dict], model: str) -> dict:
    """metrics_from (select.py) per horizon: mape, directional_acc, coverage, n_obs."""
    frame = pd.DataFrame([{'h': r['horizon'], 'p0': r['p0'], 'p10': r['p10'], 'p50': r['p50'], 'p90': r['p90'],
                           'actual': r['actual']} for r in rows])
    table = metrics_from(frame, model)
    return {int(r['horizon']): {k: (None if r[k] is None or (isinstance(r[k], float) and np.isnan(r[k]))
                                    else (int(r[k]) if k == 'n_obs' else float(r[k])))
                                for k in ('mape', 'directional_acc', 'coverage', 'n_obs')}
            for r in table.to_dict('records')}


def _mean_mape(rows: list[dict], model: str) -> float:
    return float(np.mean([m['mape'] for m in summarize(rows, model).values()]))


def _calibrate(rows: list[dict], grid) -> float:
    """calibrate_scale's rule: the grid scale whose P10 to P90 coverage is closest to 80%."""
    best, best_gap = 1.0, 1e9
    for s in grid:
        covered = [r['p50'] + (r['p10'] - r['p50']) * s <= r['actual'] <= r['p50'] + (r['p90'] - r['p50']) * s
                   for r in rows]
        gap = abs(float(np.mean(covered)) - 0.8)
        if gap < best_gap - 1e-9:
            best, best_gap = s, gap
    return best


def classify(selected: str, item: dict | None, naive_item: dict | None) -> str:
    lo, hi = COVERAGE_BAND
    if (selected != 'naive' and item and item.get('skill') is not None and item['skill'] >= MIN_SKILL
            and item.get('bootstrap_ci_lower') is not None and item['bootstrap_ci_lower'] > 0
            and item.get('coverage_80') is not None and lo <= item['coverage_80'] <= hi
            and item.get('n_origins', 0) >= MIN_TEST_ORIGINS):
        return 'PREDICTION'
    if naive_item and naive_item.get('coverage_80') is not None and lo <= naive_item['coverage_80'] <= hi:
        return 'RANGE_ONLY'
    return 'NO_RELIABLE_FORECAST'


def evaluate_series(series: dict, config: dict | None = None) -> dict:
    config = {**DEFAULT_CONFIG, **(config or {})}
    n = len(series['values'])
    test_start = n - config['test_months']
    validation = range(test_start - config['validation_months'], test_start)
    test = range(test_start, n)
    # 1. Validation only: best training window per model, then model selection and interval calibration.
    best_window, validation_mape, validation_rows = {}, {}, {}
    for model in config['models']:
        scored = []
        for window in config['windows']:
            rows = _rows(series, model, window, 'validation', validation, config)
            scored.append((_mean_mape(rows, model), window, rows))
        mape, window, rows = min(scored, key=lambda item: (item[0], item[1]))
        best_window[model], validation_mape[model], validation_rows[model] = window, _num(mape), rows
    challengers = {m: v for m, v in validation_mape.items() if m != 'naive'}
    best = min(challengers, key=lambda m: (challengers[m], m)) if challengers else None
    # A pegged series has zero naive error: nothing can beat it, so no improvement ratio exists.
    improvement = (1 - challengers[best] / validation_mape['naive']) if best and validation_mape['naive'] > 0 else None
    beats = improvement is not None and improvement >= config['min_improvement_vs_naive']
    selection = {'selected_model': best if beats else 'naive', 'best_challenger': best,
                 'validation_mape': validation_mape, 'best_window': best_window,
                 'improvement_vs_naive': None if improvement is None else _num(improvement),
                 'beats_naive_on_validation': bool(beats)}
    scales = {m: (1.0 if m == 'ridge' else _calibrate(validation_rows[m], config['scale_grid'])) for m in config['models']}
    # 2. Untouched test split: every model at its chosen window and calibrated scale.
    test_rows = []
    for model in config['models']:
        test_rows += [_scaled(r, scales[model]) for r in _rows(series, model, best_window[model], 'test', test, config)]
    all_validation = [r for m in config['models'] for r in validation_rows[m]]
    doc = {'schema_version': 'forecast-scorecard.v1', 'metadata': {'series_id': series['series_id']},
           'predictions': all_validation + test_rows}
    metrics = [m for m in score(doc, bootstrap_replicates=config['bootstrap_replicates'],
                                seed=config['bootstrap_seed'])['metrics'] if m['split'] == 'test']
    by = {(m['model'], m['horizon']): m for m in metrics}
    selected = selection['selected_model']
    test_summary = {m: summarize([r for r in test_rows if r['model'] == m], m) for m in config['models']}
    # 3. Outlook from the latest usable month, with the projection library's date labels.
    e = n - 1
    labels = assemble_persistence({series['series_id']: dict(zip(series['months'], series['values']))}, _meta(series))
    targets = {row['horizon']: row['target_month'] for row in labels['selected']}
    live = {m: forecast_at(series, e, m, best_window[m], config) for m in {selected, 'naive'}}
    horizons = []
    for h in config['horizons']:
        status = classify(selected, by.get((selected, h)), by.get(('naive', h)))
        item = {'horizon': h, 'target_month': targets[h], 'status': status, 'label': LABELS[status],
                'model': selected if status == 'PREDICTION' else 'naive', 'p10': None, 'p50': None, 'p90': None,
                'naive_value': series['values'][-1]}
        if status == 'PREDICTION':
            q = live[selected][h]
            s = scales[selected]
            item.update(p10=_num(q[1] + (q[0] - q[1]) * s), p50=q[1], p90=_num(q[1] + (q[2] - q[1]) * s))
        elif status == 'RANGE_ONLY':
            q = live['naive'][h]
            s = scales['naive']
            item.update(p10=_num(q[1] + (q[0] - q[1]) * s), p50=series['values'][-1],
                        p90=_num(q[1] + (q[2] - q[1]) * s))
        sel, nai = by.get((selected, h)) if selected != 'naive' else None, by.get(('naive', h))
        sel_mape = test_summary[selected][h]['mape'] if selected != 'naive' else None
        naive_mape = test_summary['naive'][h]['mape']
        item['backtest'] = {
            'split': 'test', 'selected_model': selected, 'n_origins': (sel or nai or {}).get('n_origins'),
            'mape': sel_mape, 'naive_mape': naive_mape,
            'mape_ratio': None if sel_mape is None or not naive_mape else _num(sel_mape / naive_mape),
            'directional_acc': test_summary[selected][h]['directional_acc'],
            'coverage': (sel or {}).get('coverage_80'), 'naive_coverage': (nai or {}).get('coverage_80'),
            'skill': (sel or {}).get('skill'), 'bootstrap_ci_lower': (sel or {}).get('bootstrap_ci_lower'),
            'bootstrap_ci_upper': (sel or {}).get('bootstrap_ci_upper')}
        horizons.append(item)
    outlook = {'observation_end': series['months'][-1], 'latest_value': series['values'][-1],
               'issue_month': labels['issue_month'],
               'freshness_state': ('ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED' if labels['issue_month'] == config['as_of_month']
                                   else 'HISTORICAL_NOT_CURRENT'),
               'horizons': horizons}
    return {'series_id': series['series_id'], 'months': list(series['months']), 'values': list(series['values']),
            'selection': selection, 'scales': scales, 'validation_rows': all_validation, 'test_rows': test_rows,
            'scorecard': deepcopy(metrics), 'test_summary': test_summary, 'outlook': outlook}
