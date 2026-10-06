"""Monte Carlo joint paths of commodity price and USD/INR, converted to INR per kg.

Noise is bootstrapped from same-month residual pairs (commodity, FX) so the historical
correlation between the two is preserved. Drift is the selected model's median.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..settings import assumptions, material_cfg
from .models import fit_series


@dataclass
class SimResult:
    inr_paths: np.ndarray        # (n_paths, H+1); column 0 is observed by default, forecast when bridged
    months: list[pd.Timestamp]   # calendar month for each column
    model: str
    news_index: float = 0.0
    meta: dict = field(default_factory=dict)

    def quantiles(self, qs=(0.1, 0.5, 0.9)) -> np.ndarray:
        return np.quantile(self.inr_paths, qs, axis=0)


class PlanningCalendarUnavailable(ValueError):
    """The requested planning dates cannot be backed by dated forecast paths."""


def month_start(value) -> pd.Timestamp:
    """Normalize a date to its calendar month without changing its timezone meaning."""
    try:
        month = pd.Timestamp(value)
        if pd.isna(month) or month.tz is not None:
            raise ValueError("invalid or timezone-aware planning date")
        return month.to_period("M").to_timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise PlanningCalendarUnavailable("Invalid planning or observation month") from exc


def news_overlay(drift: np.ndarray, news_index: float, news_horizon: int,
                 start: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Heuristic: shift drift by index * max_news_drift and widen noise for the news horizon."""
    cfg = assumptions()["news"]
    h = len(drift)
    shift = np.zeros(h)
    widen = np.ones(h)
    start = int(np.clip(start, 0, h))
    k = int(min(max(news_horizon, 0), h - start))
    shift[start:start + k] = news_index * cfg["max_news_drift"]
    widen[start:start + k] = 1.0 + cfg["vol_widen_factor"] * abs(news_index)
    return drift + shift, widen


def simulate(df: pd.DataFrame, material: str, model: str, horizon: int | None = None,
             n_paths: int | None = None, seed: int | None = None, news_index: float = 0.0,
             news_horizon: int | None = None, residual_scale: float = 1.0,
             refit_every: int = 1, planning_month: pd.Timestamp | None = None) -> SimResult:
    """Simulate INR/kg paths using only the rows in `df` (callers truncate for backtests)."""
    sim_cfg = assumptions()["simulation"]
    if horizon is not None and (isinstance(horizon, bool) or not isinstance(horizon, (int, np.integer)) or horizon < 1):
        raise ValueError("Simulation horizon must be a positive integer")
    H = int(sim_cfg["horizon"] if horizon is None else horizon)
    if planning_month is not None:
        idx = df.index
        if (df.empty or not isinstance(idx, pd.DatetimeIndex) or idx.tz is not None or
                not idx.is_unique or not idx.is_monotonic_increasing or
                not idx.equals(pd.date_range(idx[0], periods=len(idx), freq="MS"))):
            raise PlanningCalendarUnavailable("Observed series must have consecutive month-start dates")
    observation_end = month_start(df.index[-1])
    planning = observation_end if planning_month is None else month_start(planning_month)
    lag = (planning.year - observation_end.year) * 12 + planning.month - observation_end.month
    if lag < 0:
        raise PlanningCalendarUnavailable("Planning month precedes the last observation")
    total = H + lag
    if planning_month is not None and model == "gbm" and len(df) - 7 - total < 1:
        raise PlanningCalendarUnavailable("Insufficient observed targets for the selected model and planning window")
    n = int(n_paths or sim_cfg["n_paths"])
    rng = np.random.default_rng(sim_cfg["seed"] if seed is None else seed)

    log_c = np.log(df["usd_per_unit"])
    log_fx = np.log(df["usdinr"])
    fc_c = fit_series(model, log_c, total, name=f"c:{material}", refit_every=refit_every)
    fc_fx = fit_series(model, log_fx, total, name="fx", refit_every=refit_every)

    pairs = pd.concat([fc_c.residuals.rename("c"), fc_fx.residuals.rename("fx")], axis=1, join="inner").dropna()
    if len(pairs) < 12:
        raise ValueError(f"Not enough residual history for {material} ({len(pairs)} months)")
    draws = pairs.values[rng.integers(0, len(pairs), size=(n, total))]  # (n, H+lag, 2)

    news_start = max(lag - 1, 0) if planning_month is not None else 0
    drift_c, widen = news_overlay(fc_c.drift, news_index, H if news_horizon is None else news_horizon,
                                  start=news_start)
    eps_c = draws[:, :, 0] * fc_c.noise_scale * residual_scale * widen
    eps_fx = draws[:, :, 1] * fc_fx.noise_scale * residual_scale
    cum_c = np.cumsum(drift_c + eps_c, axis=1)
    cum_fx = np.cumsum(fc_fx.drift + eps_fx, axis=1)

    kg = float(material_cfg(material)["kg_per_unit"])
    p0, fx0 = float(df["usd_per_unit"].iloc[-1]), float(df["usdinr"].iloc[-1])
    inr = np.empty((n, total + 1))
    inr[:, 0] = p0 * fx0 / kg
    inr[:, 1:] = p0 * np.exp(cum_c) * fx0 * np.exp(cum_fx) / kg
    axis_origin = df.index[-1] if planning_month is None else observation_end
    months = [axis_origin + pd.DateOffset(months=k) for k in range(total + 1)]
    corr = float(np.corrcoef(pairs["c"], pairs["fx"])[0, 1])
    return SimResult(inr[:, lag:lag + H + 1], months[lag:lag + H + 1], model, news_index,
                     meta={"drift_c": drift_c.tolist(), "drift_fx": fc_fx.drift.tolist(),
                           "noise_scale_c": fc_c.noise_scale, "noise_scale_fx": fc_fx.noise_scale,
                           "residual_scale": residual_scale, "residual_months": len(pairs),
                           "corr_c_fx": corr, "observation_end": observation_end.strftime("%Y-%m-%d"),
                           "planning_month": planning.strftime("%Y-%m-%d"), "bridge_months": lag,
                           "planning_offset_zero_basis": "observed" if lag == 0 else "forecast",
                           "publication_at": None,
                           "calibration_limit": "Forecast horizons beyond 1-6 months from observation are not covered by the current walk-forward calibration."})
