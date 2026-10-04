"""Forecast models for monthly log returns of one series (commodity USD price or USD/INR).

Every model returns a SeriesForecast: a median drift path (log return per future month)
plus a residual series used by the Monte Carlo simulator for noise. Keeping the
interface identical lets the simulator and the evaluator treat all models the same way.
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

MODELS = ("naive", "ets", "gbm")
RESID_WINDOW = 120  # months of residual history used for bootstrapping noise

try:  # LightGBM needs libomp on macOS; fall back to scikit-learn if it cannot load.
    import lightgbm as _lgb  # type: ignore

    GBM_BACKEND = "lightgbm"
except Exception as _exc:  # noqa: BLE001
    _lgb = None
    GBM_BACKEND = "sklearn"
    log.info("LightGBM unavailable (%s); using scikit-learn GradientBoostingRegressor", _exc)


@dataclass
class SeriesForecast:
    drift: np.ndarray        # shape (H,), median log return for each future month
    residuals: pd.Series     # 1-step residual log returns, indexed by month
    noise_scale: float = 1.0  # multiplier on bootstrapped residuals (conditional spread)


def _returns(log_s: pd.Series) -> pd.Series:
    return log_s.diff().dropna()


def fit_naive(log_s: pd.Series, horizon: int) -> SeriesForecast:
    """Random walk: zero drift, noise from demeaned historical returns."""
    r = _returns(log_s).iloc[-RESID_WINDOW:]
    return SeriesForecast(np.zeros(horizon), r - r.mean())


def fit_ets(log_s: pd.Series, horizon: int) -> SeriesForecast:
    """Holt exponential smoothing (additive damped trend) on log price."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    y = log_s.iloc[-RESID_WINDOW - 1:]
    y = pd.Series(y.values, index=pd.date_range(y.index[0], periods=len(y), freq="MS"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(y, trend="add", damped_trend=True).fit(optimized=True)
    fc = np.asarray(fit.forecast(horizon))
    drift = np.diff(np.concatenate([[y.iloc[-1]], fc]))
    resid = pd.Series(np.asarray(fit.resid)[1:], index=log_s.index[-len(y) + 1:])
    return SeriesForecast(drift, resid - resid.mean())


def _gbm_features(log_s: pd.Series) -> pd.DataFrame:
    r = log_s.diff()
    return pd.DataFrame({
        "r0": r, "r1": r.shift(1), "r2": r.shift(2),
        "mom3": log_s.diff(3), "mom6": log_s.diff(6),
        "vol6": r.rolling(6).std(), "moy": log_s.index.month,
    }, index=log_s.index)


def _quantile_model(alpha: float):
    if _lgb is not None:
        return _lgb.LGBMRegressor(objective="quantile", alpha=alpha, n_estimators=150, learning_rate=0.05,
                                  num_leaves=7, min_child_samples=10, verbose=-1)
    from sklearn.ensemble import GradientBoostingRegressor

    return GradientBoostingRegressor(loss="quantile", alpha=alpha, n_estimators=120, max_depth=2,
                                     learning_rate=0.05, min_samples_leaf=8, random_state=0)


_GBM_MODELS: dict[tuple, dict] = {}


def _train_gbm(log_s: pd.Series, horizon: int) -> dict:
    """Train the quantile models on all rows of `log_s` (targets must be observable inside it)."""
    X = _gbm_features(log_s)
    valid = X.dropna().index
    models: dict = {"p50": []}
    for h in range(1, horizon + 1):
        y = log_s.shift(-h) - log_s
        idx = valid.intersection(y.dropna().index)
        models["p50"].append(_quantile_model(0.5).fit(X.loc[idx].values, y.loc[idx].values))
    y1 = log_s.shift(-1) - log_s
    idx1 = valid.intersection(y1.dropna().index)
    for a in (0.1, 0.9):
        models[a] = _quantile_model(a).fit(X.loc[idx1].values, y1.loc[idx1].values)
    return models


def fit_gbm(log_s: pd.Series, horizon: int, refit_every: int = 1, name: str = "") -> SeriesForecast:
    """Quantile gradient boosting on lagged features.

    Direct strategy: one P50 model per horizon h predicts the cumulative h-month return.
    P10 and P90 models at h=1 give a conditional spread, used to scale bootstrapped noise.
    With refit_every > 1 (walk-forward), models are trained at the most recent anchor
    origin on or before the current one, using only data up to that anchor.
    """
    n = len(log_s)
    anchor = n if refit_every <= 1 else max(60, n - (n % refit_every))
    anchor = min(anchor, n)
    key = (name, log_s.index[anchor - 1], anchor, horizon, round(float(log_s.iloc[anchor - 1]), 10))
    if key not in _GBM_MODELS:
        if len(_GBM_MODELS) > 500:
            _GBM_MODELS.clear()
        _GBM_MODELS[key] = _train_gbm(log_s.iloc[:anchor], horizon)
    models = _GBM_MODELS[key]
    x_now = _gbm_features(log_s).iloc[[-1]].values
    cum = [float(m.predict(x_now)[0]) for m in models["p50"]]
    drift = np.diff(np.concatenate([[0.0], cum]))
    q10, q90 = float(models[0.1].predict(x_now)[0]), float(models[0.9].predict(x_now)[0])
    r = _returns(log_s).iloc[-RESID_WINDOW:]
    resid = r - r.mean()
    emp_spread = float(np.quantile(resid, 0.9) - np.quantile(resid, 0.1))
    pred_spread = q90 - q10
    scale = float(np.clip(pred_spread / emp_spread, 0.5, 2.0)) if emp_spread > 0 and pred_spread > 0 else 1.0
    return SeriesForecast(drift, resid, noise_scale=scale)


FITTERS = {"naive": fit_naive, "ets": fit_ets, "gbm": fit_gbm}

_CACHE: dict[tuple, SeriesForecast] = {}


def fit_series(model: str, log_s: pd.Series, horizon: int, name: str = "",
               refit_every: int = 1) -> SeriesForecast:
    """Fit (memoised on model, series identity, origin and length)."""
    key = (model, name, log_s.index[-1], len(log_s), round(float(log_s.iloc[-1]), 10),
           round(float(log_s.iloc[0]), 10), horizon, refit_every)
    if key not in _CACHE:
        if len(_CACHE) > 5000:
            _CACHE.clear()
        if model == "gbm":
            _CACHE[key] = fit_gbm(log_s, horizon, refit_every=refit_every, name=name)
        else:
            _CACHE[key] = FITTERS[model](log_s, horizon)
    return _CACHE[key]


def clear_cache() -> None:
    _CACHE.clear()
    _GBM_MODELS.clear()
