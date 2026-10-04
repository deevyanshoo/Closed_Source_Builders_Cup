from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from procure.features import add_features, fx_trend, load_series, to_inr_per_kg, volatility_summary


def _frame(n=80, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2015-01-01", periods=n, freq="MS")
    usd = 8000 * np.exp(np.cumsum(rng.normal(0, 0.05, n)))
    fx = 70 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    return pd.DataFrame({"usd_per_unit": usd, "usdinr": fx, "inr_per_kg": usd * fx / 1000}, index=idx)


def test_inr_per_kg_conversion():
    assert to_inr_per_kg(9400, 84.1, "copper") == pytest.approx(9400 * 84.1 / 1000)


def test_features_columns_and_returns():
    df = _frame()
    f = add_features(df)
    for col in ("r_c", "r_fx", "r_inr", "vol_6", "vol_12", "mom_3", "mom_6", "fx_chg", "month_of_year"):
        assert col in f
    # INR log return equals commodity plus FX log return
    np.testing.assert_allclose(f["r_inr"].dropna(), (f["r_c"] + f["r_fx"]).dropna(), atol=1e-12)
    assert f["month_of_year"].iloc[0] == 1


def test_volatility_and_fx_trend():
    df = _frame()
    v = volatility_summary(df)
    assert v["current_annual_vol"] > 0 and v["avg_5y_annual_vol"] > 0
    assert fx_trend(df, 3) == pytest.approx(df["usdinr"].iloc[-1] / df["usdinr"].iloc[-4] - 1)


def test_load_series_from_db(seeded_db):
    df = load_series("copper")
    assert len(df) > 100
    assert (df["inr_per_kg"] > 0).all()
    assert df.index.is_monotonic_increasing
