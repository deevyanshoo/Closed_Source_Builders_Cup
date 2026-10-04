"""Calibrated synthetic fallback series. Used only when live and cache both fail.

Every row produced here is flagged is_synthetic downstream.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..settings import assumptions, materials_config

SYNTHETIC_SEED = 20100101


def _months(end: pd.Timestamp | None = None) -> pd.DatetimeIndex:
    start = pd.Timestamp(assumptions()["forecast"]["history_start"])
    end = end or (pd.Timestamp.today().normalize().replace(day=1) - pd.offsets.MonthBegin(1))
    return pd.date_range(start, end, freq="MS")


def mean_reverting_gbm(n: int, start: float, low: float, high: float, vol: float,
                       rng: np.random.Generator, kappa: float = 0.05) -> np.ndarray:
    """Geometric Brownian motion in logs with mild mean reversion toward the band centre."""
    centre = np.log((low * high) ** 0.5)
    x = np.empty(n)
    x[0] = np.log(start)
    for t in range(1, n):
        x[t] = x[t - 1] + kappa * (centre - x[t - 1]) + vol * rng.standard_normal()
    # soft-clip to a slightly widened realistic band so values stay believable
    return np.exp(np.clip(x, np.log(low * 0.85), np.log(high * 1.15)))


def synthetic_prices(end: pd.Timestamp | None = None) -> pd.DataFrame:
    months = _months(end)
    rng = np.random.default_rng(SYNTHETIC_SEED)
    frames = []
    for key, cfg in materials_config()["materials"].items():
        s = cfg["synthetic"]
        vals = mean_reverting_gbm(len(months), s["start"], s["low"], s["high"], s["monthly_vol"], rng)
        frames.append(pd.DataFrame({"material": key, "month": months, "usd_per_unit": vals,
                                    "unit": cfg["source_unit"]}))
    return pd.concat(frames, ignore_index=True)


def synthetic_fx(end: pd.Timestamp | None = None) -> pd.DataFrame:
    months = _months(end)
    cfg = materials_config()["fx"]["synthetic"]
    rng = np.random.default_rng(SYNTHETIC_SEED + 1)
    n = len(months)
    drift = np.log(cfg["end"] / cfg["start"]) / max(n - 1, 1)
    shocks = cfg["monthly_vol"] * rng.standard_normal(n)
    shocks[0] = 0.0
    # anchor the path so it ends near the configured end value
    walk = np.cumsum(shocks)
    walk -= np.linspace(0, walk[-1], n)
    vals = cfg["start"] * np.exp(drift * np.arange(n) + walk)
    return pd.DataFrame({"month": months, "usdinr": vals})
