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
    inr_paths: np.ndarray        # (n_paths, H+1), column 0 is the latest known price
    months: list[pd.Timestamp]   # calendar month for each column
    model: str
    news_index: float = 0.0
    meta: dict = field(default_factory=dict)

    def quantiles(self, qs=(0.1, 0.5, 0.9)) -> np.ndarray:
        return np.quantile(self.inr_paths, qs, axis=0)


def news_overlay(drift: np.ndarray, news_index: float, news_horizon: int) -> tuple[np.ndarray, np.ndarray]:
    """Heuristic: shift drift by index * max_news_drift and widen noise for the news horizon."""
    cfg = assumptions()["news"]
    h = len(drift)
    shift = np.zeros(h)
    widen = np.ones(h)
    k = int(min(max(news_horizon, 0), h))
    shift[:k] = news_index * cfg["max_news_drift"]
    widen[:k] = 1.0 + cfg["vol_widen_factor"] * abs(news_index)
    return drift + shift, widen


def simulate(df: pd.DataFrame, material: str, model: str, horizon: int | None = None,
             n_paths: int | None = None, seed: int | None = None, news_index: float = 0.0,
             news_horizon: int | None = None, residual_scale: float = 1.0,
             refit_every: int = 1) -> SimResult:
    """Simulate INR/kg paths using only the rows in `df` (callers truncate for backtests)."""
    sim_cfg = assumptions()["simulation"]
    H = int(horizon or sim_cfg["horizon"])
    n = int(n_paths or sim_cfg["n_paths"])
    rng = np.random.default_rng(sim_cfg["seed"] if seed is None else seed)

    log_c = np.log(df["usd_per_unit"])
    log_fx = np.log(df["usdinr"])
    fc_c = fit_series(model, log_c, H, name=f"c:{material}", refit_every=refit_every)
    fc_fx = fit_series(model, log_fx, H, name="fx", refit_every=refit_every)

    pairs = pd.concat([fc_c.residuals.rename("c"), fc_fx.residuals.rename("fx")], axis=1, join="inner").dropna()
    if len(pairs) < 12:
        raise ValueError(f"Not enough residual history for {material} ({len(pairs)} months)")
    draws = pairs.values[rng.integers(0, len(pairs), size=(n, H))]  # (n, H, 2)

    drift_c, widen = news_overlay(fc_c.drift, news_index, H if news_horizon is None else news_horizon)
    eps_c = draws[:, :, 0] * fc_c.noise_scale * residual_scale * widen
    eps_fx = draws[:, :, 1] * fc_fx.noise_scale * residual_scale
    cum_c = np.cumsum(drift_c + eps_c, axis=1)
    cum_fx = np.cumsum(fc_fx.drift + eps_fx, axis=1)

    kg = float(material_cfg(material)["kg_per_unit"])
    p0, fx0 = float(df["usd_per_unit"].iloc[-1]), float(df["usdinr"].iloc[-1])
    inr = np.empty((n, H + 1))
    inr[:, 0] = p0 * fx0 / kg
    inr[:, 1:] = p0 * np.exp(cum_c) * fx0 * np.exp(cum_fx) / kg
    last = df.index[-1]
    months = [last + pd.DateOffset(months=k) for k in range(H + 1)]
    corr = float(np.corrcoef(pairs["c"], pairs["fx"])[0, 1])
    return SimResult(inr, months, model, news_index,
                     meta={"drift_c": drift_c.tolist(), "drift_fx": fc_fx.drift.tolist(),
                           "noise_scale_c": fc_c.noise_scale, "noise_scale_fx": fc_fx.noise_scale,
                           "residual_scale": residual_scale, "residual_months": len(pairs),
                           "corr_c_fx": corr})
