"""Reusable, source-free monthly price projection with an honest persistence default.

All panels, provenance, and optional model callables are supplied by the caller.
This module performs no I/O or model import at import time and never promotes an
experimental candidate to the selected forecast.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any, Callable


HORIZONS = (1, 3, 6)
TINY_QUANTILES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
C2_QUANTILES = (0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99)
MAX_SERIES = 32
MAX_MONTHS = 240


class InvalidMonthlyProjection(ValueError):
    """Fixed refusal without source prices or provider details in the message."""


def _invalid() -> None:
    raise InvalidMonthlyProjection('INVALID_MONTHLY_PROJECTION')


def _month_index(month: str) -> int:
    if not isinstance(month, str) or re.fullmatch(r'[0-9]{4}-(0[1-9]|1[0-2])', month) is None:
        _invalid()
    year = int(month[:4])
    if year < 1:
        _invalid()
    return year * 12 + int(month[5:]) - 1


def _month_name(index: int) -> str:
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def _number(value: Any) -> tuple[Decimal, float]:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        _invalid()
    try:
        decimal = Decimal(str(value))
        number = float(decimal)
    except (InvalidOperation, OverflowError, ValueError):
        _invalid()
    if not decimal.is_finite() or decimal <= 0 or not math.isfinite(number) or number <= 0:
        _invalid()
    return decimal, number


def _price(value: Decimal | float) -> str:
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        _invalid()
    if not decimal.is_finite() or decimal <= 0:
        _invalid()
    return format(decimal, 'f')


def _validated(panel: dict, metadata: dict, now: datetime) -> tuple[tuple[str, ...], dict[str, tuple[Decimal, ...]], dict[str, tuple[float, ...]], dict]:
    if not isinstance(panel, dict) or not 1 <= len(panel) <= MAX_SERIES or not isinstance(metadata, dict) or set(metadata) != set(panel):
        _invalid()
    ids = tuple(panel)
    if any(not isinstance(sid, str) or not sid or len(sid) > 128 for sid in ids):
        _invalid()
    first_months: tuple[str, ...] | None = None
    decimals: dict[str, tuple[Decimal, ...]] = {}
    floats: dict[str, tuple[float, ...]] = {}
    clean_meta = {}
    for sid in ids:
        series = panel[sid]
        if not isinstance(series, dict) or not 1 <= len(series) <= MAX_MONTHS:
            _invalid()
        month_indices = sorted(_month_index(month) for month in series)
        if len(set(month_indices)) != len(series) or month_indices != list(range(month_indices[0], month_indices[-1] + 1)):
            _invalid()
        if month_indices[-1] >= _month_index(now.strftime('%Y-%m')):
            _invalid()
        months = tuple(_month_name(i) for i in month_indices)
        if first_months is None:
            first_months = months
        elif months != first_months:
            _invalid()
        current = [_number(series[month]) for month in months]
        decimals[sid] = tuple(item[0] for item in current)
        floats[sid] = tuple(item[1] for item in current)
        meta = metadata[sid]
        if not isinstance(meta, dict) or set(meta) != {'unit', 'family', 'source_id', 'source_sha256', 'retrieved_at_utc', 'error_references'}:
            _invalid()
        if any(not isinstance(meta[key], str) or not meta[key] or len(meta[key]) > 128 for key in ('unit', 'family', 'source_id')):
            _invalid()
        if not isinstance(meta['source_sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', meta['source_sha256']) is None:
            _invalid()
        try:
            retrieved = datetime.fromisoformat(meta['retrieved_at_utc'].replace('Z', '+00:00'))
        except (AttributeError, TypeError, ValueError):
            _invalid()
        if retrieved.tzinfo is None or retrieved.utcoffset() != timedelta(0) or retrieved > now:
            _invalid()
        refs = meta['error_references']
        if not isinstance(refs, list) or len(refs) > 16 or any(not isinstance(ref, str) or not ref or len(ref) > 128 for ref in refs):
            _invalid()
        clean_meta[sid] = {**meta, 'error_references': tuple(refs)}
    assert first_months is not None
    return first_months, decimals, floats, clean_meta


def _provenance(meta: dict, observed_through: str) -> dict:
    return {'source_id': meta['source_id'], 'source_sha256': meta['source_sha256'],
            'retrieved_at_utc': meta['retrieved_at_utc'], 'observed_through': observed_through,
            'unit': meta['unit'], 'family': meta['family']}


def _row(sid: str, horizon: int, anchor: str, issue: str, price: str, meta: dict,
         *, model: str, status: str) -> dict:
    return {'series_id': sid, 'horizon': horizon, 'lead_months': horizon + 1,
            'observation_end': anchor, 'issue_month': issue,
            'target_month': _month_name(_month_index(anchor) + horizon + 1),
            'native_price': price, 'unit': meta['unit'], 'model': model,
            'status': status, 'provenance': _provenance(meta, anchor),
            'error_references': list(meta['error_references'])}


def assemble_persistence(panel: dict, metadata: dict) -> dict:
    """Always select exact last-observation persistence, with UTC generation time."""
    generated = datetime.now(timezone.utc)
    months, decimals, _, meta = _validated(panel, metadata, generated)
    anchor = months[-1]
    issue = _month_name(_month_index(anchor) + 1)
    as_of_month = generated.strftime('%Y-%m')
    freshness = 'ISSUE_MONTH_EQUALS_AS_OF_UNVERIFIED' if issue == as_of_month else 'HISTORICAL_NOT_CURRENT'
    rows = [_row(sid, h, anchor, issue, _price(decimals[sid][-1]), meta[sid],
                 model='persistence', status='SELECTED_BASELINE')
            for sid in decimals for h in HORIZONS]
    return {'schema': 'monthly-projection.v1', 'generated_at_utc': generated.isoformat(),
            'as_of_date_utc': generated.date().isoformat(), 'as_of_month_utc': as_of_month,
            'observation_end': anchor, 'issue_month': issue, 'freshness_state': freshness,
            'selected_model': 'persistence', 'selected': rows,
            'candidate_status': 'EXPERIMENTAL_NOT_PROMOTED',
            'limits': 'Issue month is observation end plus one; latest-vintage data and model evidence do not establish current price, calibrated interval, or purchasing savings.'}


def _features(logs: tuple[float, ...], cutoff: int) -> tuple[float, ...]:
    if cutoff < 6:
        _invalid()
    returns = [logs[cutoff - k] - logs[cutoff - k - 1] for k in range(6)]
    avg = sum(returns) / 6
    result = (returns[0], returns[1], returns[2], sum(returns[:3]),
              sum(returns), math.sqrt(sum((value - avg) ** 2 for value in returns) / 6))
    if not all(math.isfinite(v) for v in result):
        _invalid()
    return result


def ridge_candidates(panel: dict, metadata: dict) -> dict:
    """Fit three fixed alpha-10 direct-return ridges per series, on known labels only."""
    now = datetime.now(timezone.utc)
    months, _, floats, meta = _validated(panel, metadata, now)
    last = len(months) - 1
    if last < 13:
        _invalid()
    # Import only on explicit candidate invocation; the baseline needs no NumPy.
    import numpy as np

    anchor_indices = tuple(range(6, last - 7 + 1))
    if not anchor_indices or anchor_indices[-1] + 7 > last:
        _invalid()
    issue = _month_name(_month_index(months[-1]) + 1)
    rows = []
    fits = 0
    for sid, prices in floats.items():
        logs = tuple(math.log(price) for price in prices)
        X = np.asarray([_features(logs, t) for t in anchor_indices], dtype=float)
        mean = X.mean(axis=0)
        scale = np.sqrt(np.mean((X - mean) ** 2, axis=0))
        scale = np.where(scale == 0, 1.0, scale)
        Z = (X - mean) / scale
        center = Z.mean(axis=0)
        W = Z - center
        current = (np.asarray(_features(logs, last)) - mean) / scale
        for horizon in HORIZONS:
            y = np.asarray([logs[t + horizon + 1] - logs[t] for t in anchor_indices], dtype=float)
            ym = float(y.mean())
            A = W.T @ W / len(y) + 10.0 * np.eye(6)
            g = W.T @ (y - ym) / len(y)
            try:
                beta = np.linalg.solve(A, g)
            except np.linalg.LinAlgError:
                _invalid()
            intercept = ym - float(center @ beta)
            predicted_return = intercept + float(current @ beta)
            try:
                prediction = prices[-1] * math.exp(predicted_return)
            except OverflowError:
                _invalid()
            if not np.isfinite(beta).all() or not all(math.isfinite(v) for v in (intercept, predicted_return, prediction)) or prediction <= 0:
                _invalid()
            fits += 1
            rows.append(_row(sid, horizon, months[-1], issue, _price(prediction), meta[sid],
                             model='ridge_alpha_10', status='EXPERIMENTAL_NOT_PROMOTED'))
    if fits != 3 * len(floats) or fits > 3 * MAX_SERIES:
        _invalid()
    return {'schema': 'monthly-projection-candidates.v1', 'method': 'ridge_alpha_10',
            'status': 'EXPERIMENTAL_NOT_PROMOTED', 'fit_attempts': fits,
            'training_anchors_per_series': len(anchor_indices), 'candidates': rows}


def _output_values(output: Any, quantiles: tuple[float, ...]) -> list[list[float]]:
    if not isinstance(output, list) or len(output) != 1 or not isinstance(output[0], list) or len(output[0]) != len(quantiles):
        _invalid()
    values = output[0]
    for series in values:
        if not isinstance(series, list) or len(series) != 7:
            _invalid()
        for value in series:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                _invalid()
    return values


def _call(predict: Callable, histories: list[list[float]], quantiles: tuple[float, ...], *, joint: bool) -> list[list[list[float]]]:
    if not callable(predict):
        _invalid()
    try:
        result = predict(histories, prediction_length=7, cross_learning=joint)
    except Exception:
        _invalid()
    if not isinstance(result, list) or len(result) != len(histories):
        _invalid()
    return [_output_values(value, quantiles) for value in result]


def foundation_candidates(panel: dict, metadata: dict, *, tiny_predict: Callable,
                          tiny_quantiles: list[float], c2_predict: Callable,
                          c2_quantiles: list[float]) -> dict:
    """Use injected plain-list adapters only; never load or download a model."""
    now = datetime.now(timezone.utc)
    months, _, floats, meta = _validated(panel, metadata, now)
    if tiny_quantiles != list(TINY_QUANTILES) or c2_quantiles != list(C2_QUANTILES):
        _invalid()
    ids = list(floats)
    histories = [list(floats[sid]) for sid in ids]
    issue = _month_name(_month_index(months[-1]) + 1)
    tiny = _call(tiny_predict, histories, TINY_QUANTILES, joint=False)
    independent = _call(c2_predict, histories, C2_QUANTILES, joint=False)
    grouped: dict[str, list[str]] = {}
    for sid in ids:
        grouped.setdefault(meta[sid]['family'], []).append(sid)
    joint: dict[str, list[list[float]]] = {}
    calls = 2
    for family_ids in grouped.values():
        family_histories = [list(floats[sid]) for sid in family_ids]
        outputs = _call(c2_predict, family_histories, C2_QUANTILES, joint=True)
        calls += 1
        joint.update(zip(family_ids, outputs, strict=True))
    rows = []
    for position, sid in enumerate(ids):
        for model, output, quantiles in (
                ('chronos_bolt_tiny', tiny[position], TINY_QUANTILES),
                ('chronos2_independent', independent[position], C2_QUANTILES),
                ('chronos2_family_joint', joint[sid], C2_QUANTILES)):
            median = output[quantiles.index(0.5)]
            for horizon in HORIZONS:
                rows.append(_row(sid, horizon, months[-1], issue, _price(median[horizon]),
                                 meta[sid], model=model, status='EXPERIMENTAL_NOT_PROMOTED'))
    return {'schema': 'monthly-projection-candidates.v1', 'method': 'injected_foundation',
            'status': 'EXPERIMENTAL_NOT_PROMOTED', 'model_calls': calls,
            'contexts': 2 * len(ids) + sum(map(len, grouped.values())),
            'fit_attempts': 0, 'candidates': rows,
            'limits': 'Quantile labels are model outputs, not calibrated coverage or confidence.'}
