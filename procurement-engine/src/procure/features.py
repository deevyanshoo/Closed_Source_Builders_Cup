"""Monthly INR series and model features."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db
from .settings import material_cfg


def load_series(material: str) -> pd.DataFrame:
    """Joined monthly USD price, USD/INR and INR per kg, indexed by month start.

    Only months present in both the price and FX tables are kept.
    """
    kg = float(material_cfg(material)["kg_per_unit"])
    p = db.query_df("SELECT month, usd_per_unit, is_synthetic AS price_synth, source AS price_source "
                    "FROM prices_monthly WHERE material = ? ORDER BY month", [material])
    fx = db.query_df("SELECT month, usdinr, is_synthetic AS fx_synth, source AS fx_source "
                     "FROM fx_monthly ORDER BY month")
    if p.empty or fx.empty:
        raise LookupError(f"No price or FX data for {material}. Run POST /ingest/refresh first.")
    df = p.merge(fx, on="month", how="inner")
    df["month"] = pd.to_datetime(df["month"])
    df = df.set_index("month").sort_index()
    df["inr_per_kg"] = df["usd_per_unit"] * df["usdinr"] / kg
    df["is_synthetic"] = df["price_synth"].astype(bool) | df["fx_synth"].astype(bool)
    return df


def to_inr_per_kg(usd_per_unit: np.ndarray | float, usdinr: np.ndarray | float, material: str):
    return np.asarray(usd_per_unit) * np.asarray(usdinr) / float(material_cfg(material)["kg_per_unit"])


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Log returns, rolling volatility, momentum, FX change and month of year."""
    out = df.copy()
    out["r_c"] = np.log(out["usd_per_unit"]).diff()
    out["r_fx"] = np.log(out["usdinr"]).diff()
    out["r_inr"] = np.log(out["inr_per_kg"]).diff()
    for w in (6, 12):
        out[f"vol_{w}"] = out["r_inr"].rolling(w).std()
        out[f"vol_c_{w}"] = out["r_c"].rolling(w).std()
    for w in (3, 6):
        out[f"mom_{w}"] = np.log(out["usd_per_unit"]).diff(w)
        out[f"fx_mom_{w}"] = np.log(out["usdinr"]).diff(w)
    out["fx_chg"] = out["r_fx"]
    out["month_of_year"] = out.index.month
    return out


def volatility_summary(df: pd.DataFrame) -> dict:
    """Current 12-month INR volatility versus its average over the last 5 years (annualised)."""
    f = add_features(df)
    vol = f["vol_12"].dropna()
    current = float(vol.iloc[-1]) * np.sqrt(12)
    avg5 = float(vol.iloc[-60:].mean()) * np.sqrt(12)
    return {"current_annual_vol": current, "avg_5y_annual_vol": avg5,
            "ratio": current / avg5 if avg5 > 0 else float("nan")}


def fx_trend(df: pd.DataFrame, months: int = 3) -> float:
    """Percent change in USD/INR over the last `months` months."""
    s = df["usdinr"]
    return float(s.iloc[-1] / s.iloc[-1 - months] - 1) if len(s) > months else 0.0
